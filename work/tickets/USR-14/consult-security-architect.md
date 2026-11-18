USR-14 security-architect consult (read-only).

**Bottom line:** use an additive `sid` claim, set to the account-session id, and check it in the per-request admin reload. Every trigger in the ticket already revokes account sessions in code, so `sid` alone closes all four gaps. It needs no clock comparison, no new column and no migration. Do not use `tokens_valid_after` for this ticket. It is only worth adding later, for gaps `sid` cannot see (role revoke then re-grant).

## Findings

**F1. Every trigger already revokes account sessions.** So "the token's account session is still active" is enough to close the gap. Code paths:
- **Logout one account:** `LogoutService.logout` → `revoke_account` (`app/modules/auth/service.py:247`).
- **Logout `{all}`:** → `revoke_device`, which also revokes that device's account sessions (`sessions/repository.py:79-102`).
- **Password reset:** → `revoke_all_accounts_of_user` (`auth/password_reset.py:110`).
- **Staff revoke one / all (USR-10):** → `revoke_account_of_user` / `revoke_all_accounts_of_user_ids` (`sessions/staff_service.py:110-122`).
- **Disable:** `set_status` → `revoke_all_accounts_of_user` (`users/service.py:79-81`). Re-enable does not bring sessions back, so a pre-disable `sid` stays dead. This closes the disable→re-enable gap.
- **Bonus:** reuse detection after the cookie grace (`sessions/service.py:101-105`) revokes the device, so a detected cookie theft would now also kill admin tokens minted from that device.

**F2. Today nothing ties a token to a session.** `TokenService.issue` (`app/modules/tokens/service.py:101-113`) signs `sub/iat/exp/jti` but no session reference. It already logs `account_session_id` next to `jti` (line 116). `AdminAuthService.principal` (`admin_auth/service.py:34-40`) reloads only the user and its permissions.

**F3. `tokens_valid_after` vs `iat` is weaker here:**
- It needs a migration.
- It is user-wide, so logging out on one device or revoking one session would kill admin tokens on every device (wrong granularity for revoke-one and logout).
- It compares the app clock (`time.time()` → `iat`, whole seconds) with the DB clock (`now()` everywhere in the repositories). That creates a mint race: `/auth/token` reads the account as active, a concurrent revoke commits and sets the cutoff, then the token is signed with an `iat` in the same second or later (skew). The token survives unless the check is `iat <= cutoff` and the clocks are bounded.
- `sid` has no such race: validity is decided when the token is used, not when it is minted. A token minted in the gap between "read active" and "revoke commits" carries a `sid` that is already revoked, so its next admin request gets 401.

**F4. Per-request cost is negligible.** Today the reload is one PK user lookup plus the permission query. Add one PK lookup on `account_sessions` joined to `device_sessions`, or fold it into the user query (`users JOIN account_sessions ON id=:sid AND user_id=:sub AND revoked_at IS NULL JOIN device_sessions … revoked_at IS NULL`). That keeps the round trips the same.

**F5. Gaps `sid` does not close (outside the ticket's ACs):**
- **Admin role revoke then re-grant within the TTL:** the token works again. ADR-0008 already says "do not re-grant".
- **`set-admin-origins --clear`:** still does not end issued tokens.
- **A concurrent revoke during an in-flight request:** that one request passes (same accepted risk as ADR-0008 §ADM-002b).

**F6. Behaviour change on shared browsers.** An account session is shared by every app on that browser (`identity-architecture.md:80`). Logging out of that account in any app (e.g. ricotdin) on the same browser will now also end the admin-pan token. That is correct semantics. admin-pan must treat 401 → re-mint → `session_invalid` → go to login.

## Recommendations

**Rec 1. Mechanism (AC1, AC2).**
- **Mint:** `TokenService.issue` adds `"sid": str(account.id)` for `aud=admin`.
- **Verify:** `current_admin` / `AdminAuthService.principal(user_id, sid, jti)` requires:
  - a valid UUID `sid`;
  - an `account_sessions` row with that id, `user_id == sub`, `revoked_at IS NULL`;
  - its `device_sessions` row with `revoked_at IS NULL`.
- **Device idle/max-age:** optional; on the 12 h scale it hardly matters. If included, reuse the same settings as `list_active_for_user`. The planner should pick one and record it.
- Do the check in `current_admin`, not in `require_permission`, so the self-scoped `GET /api/v1/me/permissions` is covered too.
- **Layering (R1):** the repository method stays in `sessions`; the service raises `UnauthorizedError`.

**Rec 2. Fail closed.**
- A missing `sid`, a malformed `sid`, an unknown or foreign or revoked session, or a revoked device each gives the same 401 envelope with the same `WWW-Authenticate` value. Log reason `session` (or `sid`) with `jti` and `user_id` only.
- A DB error must propagate as 500/503. No `try/except` around the lookup may fall back to allow, and nothing may be cached. If caching is ever added, the cache key must include `sid` and the TTL must be a few seconds at most.
- **Legacy tokens:** admin tokens without `sid` (minted before the deploy) must be rejected, not grandfathered. Otherwise a stolen pre-deploy token lives up to 12 h unchecked. Cost: one silent re-mint per staff browser. Record this in the ADR and the release notes.

**Rec 3. Cutoff for cookie `/auth/token` minting.** No new check is needed. Minting already requires an active account session from the cookie (`get_active_account`, `tokens/service.py:68-72`), and every trigger revokes it. A `tokens_valid_after` value must never gate minting: the fresh login that follows a reset or re-enable must work.

**Rec 4. Claim scope (AC3, AC4).**
- Emit `sid` only for `aud=admin`. That reads AC3 ("consumer-app tokens unchanged") literally and keeps consumer tokens byte-compatible.
- Add `sid` to the ADR-0005 §5 claims contract as optional and admin-only. ADR-0005 §5 says `sub` is "never the account-session id"; the ADR must note that `sid` is a separate claim and `sub` is unchanged.
- The value is the same opaque id as `account_id`, which is already in that origin's localStorage, so it exposes nothing new.
- No migration.

**Rec 5. Audit (R4).**
- The revoking actions are already audited (logout, logout-all, reset, staff revoke, disable). The rejected request is a read-side denial: log it, do not write an audit row (ADR-0009 §8).
- Keep `account_session_id` in the `token_issued` log, so the incident runbook can map a `jti` to a session and then to a targeted staff revoke-one.

**Rec 6. Tests (AC5).** One test per trigger: logout `{account_id}`, logout `{all}`, password reset, staff revoke-one, staff revoke-all, disable→re-enable. Each issues an admin token, fires the trigger, then calls one admin route and `/me/permissions` and expects 401. Also cover:
- a token minted after re-login still works;
- a missing `sid` gives 401;
- a `sid` of another user's session (forged in the test with the test key) gives 401;
- revoking user A's session on a shared device leaves user B's admin token working;
- the race test: read the session as active, commit the revoke, then sign the token → the token gets 401;
- a consumer-audience token has no `sid` claim (pins AC3).

**Rec 7. Docs.**
- **ADR-0005 amendment risk 4:** only role revoke→re-grant and `--clear` remain gaps for admin. Risk 2 stays unchanged for consumers.
- **ADR-0008:** update §3 and §7 (the reload now also checks the session), the `--clear` amendment text, and Consequences (one more indexed lookup per call).
- **`docs/deploy.md:251-268` incident runbook:**
  - **Lost or shared device:** staff session revoke now ends that device's admin tokens on the next request.
  - **Compromised staff account:** (1) disable the user. This immediately kills admin access and every session. (2) Reset the password and check the inbox. (3) Re-enabling no longer revives an old admin token, so the TTL wait is gone **for admin**. It still applies to the user's consumer-app tokens, and to role re-grant if only the role was revoked.
  - Drop "logout, password reset, staff session revoke … do not end an issued admin token". Keep `--clear`.
- **Other places in `deploy.md` (about lines 404, 521, 525, 643, 660):** fix the TTL wording.
- **Also update:** `docs/consumers.md` (consumer tokens unchanged; `sid` admin-only; consumers must not rely on it), the OpenAPI description of the USR-10 routes, `identity-architecture.md` D7, and spec §5.10.

**Rec 8. Follow-ups (R7, not in this diff).**
- Bump a per-user admin cutoff on privileged role revoke, to close revoke→re-grant.
- Optionally have `current_admin` require the `admin` app to have at least one origin, which would make `--clear` an immediate admin kill switch without key rotation.
- A per-audience admin TTL.

## Risks

- **Medium, residual:** consumer apps still keep live tokens until `exp`, up to 12 h + leeway, after logout, reset, revoke or disable. The emergency rotation playbook is unchanged.
  - Options for consumers later: a shorter per-audience TTL; a session-status or introspection endpoint keyed by `sid` (that needs `sid` in consumer tokens plus an authenticated back-channel such as the app-credential class, ADR-0016); or a revocation feed.
  - None of these are v1 scope without an owner decision.
- **Medium:** an attacker who knows the password can still log in again after a revoke. The runbook must keep "disable + reset", not "revoke sessions", as the compromise response.
- **Low:** role revoke→re-grant inside the TTL revives the token (F5).
- **Low:** in-flight request race (one request).
- **Low (UX):** all staff re-mint once after the deploy. Logging out of any app on a shared browser now ends admin-pan's session (F6), and admin-pan must handle that path.
- **Low:** if a later change adds a fallback "on DB error allow" or a principal cache, revocation silently becomes eventual. Pin this with a test or a comment.

## Open questions (for the owner, via the planner)

1. **Admin-only `sid` (my default, literal AC3) or `sid` in every audience?** Every audience would prepare a future consumer session check, but changes consumer token contents.
2. **Reject admin tokens without `sid` from day one (my default), or grandfather them until they expire?**
3. **Should the session check also apply device idle/max-age expiry, or only `revoked_at`?**
4. **Do `mmlabs-auth` and admin-pan verifiers accept unknown claims?** Confirm this before release. The ADR says additive claims are non-breaking, but a strict schema validator in a consumer library would break.
5. **Scope:** fold the role-re-grant cutoff and the "`--clear` = immediate admin kill" option into this ticket, or keep them as follow-ups (my default, R7)?

Files read:
- /home/user/org-admin-api/work/tickets/USR-14/ticket.md
- /home/user/org-admin-api/docs/adr/0005-access-token-signing.md
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/work/tickets/USR-10/security-review.md
- /home/user/org-admin-api/work/tickets/USR-10/consult-security-architect.md
- /home/user/org-admin-api/docs/deploy.md
- /home/user/org-admin-api/app/modules/admin_auth/deps.py
- /home/user/org-admin-api/app/modules/admin_auth/service.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/sessions/models.py
- /home/user/org-admin-api/app/modules/sessions/repository.py
- /home/user/org-admin-api/app/modules/auth/service.py
