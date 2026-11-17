**Consult report for USR-14, API and contract side**

I read `app/modules/admin_auth/deps.py`, `app/modules/tokens/service.py` (the claims block), `docs/consumers.md` and the test names in `tests/test_admin_auth.py`. I did not open `tests/test_auth_token.py`, `AdminAuthService.principal`, the OpenAPI export or the logout, reset and revoke paths. I found no admin-pan code in this repo, so the admin-pan points below come from `consumers.md` and `CLAUDE.md`.

## Findings

- **`sid` is additive and v1-safe.** `REQUIRED_CLAIMS` in `app/core/access_token.py` is only `exp, iat, sub, jti, iss, aud`. The `consumers.md` verifier checklist (lines 371–397) requires the same set and does not reject unknown claims. The PyJWT sketch (lines 437–445) ignores extra claims. `consumers.md` line 399 lists the claims `iss, aud, sub, email, email_verified, roles, org, org_roles, iat, exp, jti`. `sid` is not on that list yet.
- **Today `sid` is only logged.** `tokens/service.py` line 116 logs `account_session_id=account.id` but does not put it in the token. The id is already in hand at signing time, so no new lookup is needed.
- **A cut token can reuse the existing 401.** `current_admin` (`deps.py` lines 38–42) returns `UnauthorizedError("Authentication required")` with `WWW-Authenticate: Bearer realm="admin", error="invalid_token"`. Every other rejection of a sent token (bad signature, expiry, unknown or disabled user) already looks the same. Disabled-after-issue already returns this 401 (`test_disabled_after_issue_401`).
- **Cutting a token creates no refresh loop.** Per `consumers.md` line 78, admin-pan calls `/auth/token` once after a 401 and, if that fails, sends the user to login.
  - After logout or a staff revoke, the account session is gone, so `/token` returns 401 `session_invalid` (`tokens/service.py` lines 68–72). admin-pan goes to login, which is correct.
  - After a password reset, a mint cutoff check must not make `/token` fail for a legitimate new session. A token issued after the cutoff must work.
  - After disable then re-enable, the old session may still be valid for `/token`. admin-pan then re-mints once and recovers, which is acceptable. If the old account session is also revoked, the user logs in again.
- **A `sid` check and a cutoff do different jobs.**
  - A `sid` check covers logout and staff revoke of one session or all sessions, assuming revoke deletes or flags the `AccountSession`.
  - It does not cover disable then re-enable, because the session row may survive. It also does not cover a password reset unless reset revokes account sessions. I did not verify what reset does today.
  - A per-user `tokens_valid_after` compared with `iat` covers the rest. `iat` has one-second resolution (`int(self.clock())`), so use `iat < cutoff` and not `<=`.

## Recommendations

1. **(AC1, AC4)** Add `sid` as the `AccountSession.id` (string UUID) on every token, or only when `aud=admin`.
   - Putting it in all tokens is simpler, but it hands consumer apps a revocable-session handle, so I lean towards admin-only.
   - Whichever you choose, document the rule so verifiers do not require `sid`.
2. **(AC1)** Use the hybrid design. Check `sid` in the admin reload for the session-ending triggers, and add a nullable `users.tokens_valid_after timestamptz` for reset and disable-then-re-enable.
   - The migration needs a working downgrade (R2).
   - Treat a token with a missing `sid`, or with `iat` before the cutoff, as 401 for `aud=admin`. Tokens issued before deploy are at most 12 h old, so decide deliberately whether a missing `sid` means reject or tolerate, and record it in the ADR.
3. **(AC2)** Keep the existing 401 code and `WWW-Authenticate`. Do not add a new code, because a distinct admin 401 gives clients nothing to branch on and could leak the reason. Log the reason through `_deny` (`sid_revoked`, `before_cutoff`) alongside the existing `jti` and `user_id` fields, with no secrets.
4. **(AC3)** Update `docs/consumers.md`:
   - Add `sid` to the claims list (line 399).
   - Rewrite checklist item 11 (lines 386–389). Logout and disable are no longer lag-only for admin tokens. Consumer-app tokens are unchanged, and the admin API checks server-side.
   - Note that `sid` is admin-only if you choose that.
   - Update the admin-pan paragraph at line 78 if its behaviour changes.
   - Update the `docs/deploy.md` runbook and add an ADR-0005/0008 amendment.
5. **OpenAPI:** the 401 description in `_ERROR_RESPONSES` ("Missing or invalid Bearer token") can say "or ended by logout, reset or revoke". No schema change is needed, so there is no breaking diff.
6. **(AC5)** Extend `tests/test_admin_auth.py`, following the existing patterns `test_disabled_after_issue_401` and `test_long_lived_token_disabled_401`:
   - A 401 after logout.
   - A 401 after password reset.
   - A 401 after staff revoke of one session, and after revoke of all sessions.
   - A 401 after disable then re-enable.
   - A fresh token issued after the cutoff returns 200 (watch the one-second `iat` boundary by controlling the clock).
   - A token for another account session of the same user survives a single-session revoke.
   - `WWW-Authenticate` is unchanged.
   - A denial log with no secrets, extending `test_denials_logged_without_secrets`.
7. **Tests to add in `tests/test_auth_token.py`:** `sid` is present and equals the account session id for an admin audience. Consumer-app tokens are still accepted until `exp`, and `sid` is absent from them if you go admin-only. `tests/jwt_verifier.py` still passes with the extra claim. After a revoke, `/token` returns 401 `session_invalid`, which is the no-loop proof.

## Risks

- A revoke that deletes the `AccountSession` row relies on the reload query treating "row missing" as 401. Make sure it does not rely on a cascade that leaves a stale row.
- The reload now needs one extra indexed lookup per admin request, which is small.
- Clock skew between mint and check could reject a legitimate fresh token if the cutoff uses a different clock source than `iat`. Use the same `clock`.
- Rejecting a missing `sid` at deploy forces every admin user to re-login once. This is acceptable, but announce it in the runbook.

## Open questions

- Should `sid` go in all tokens or only `aud=admin`? (My lean is admin-only.)
- Is a tolerated missing `sid` allowed during a transition window, or a hard reject on deploy?
- Does password reset already revoke account sessions, or does it only revoke the device session? This decides whether the cutoff is needed for resets.
- Does staff revoke (USR-10) delete or flag the `AccountSession`, and does re-enable leave sessions intact?
- Does admin-pan retry on a repeated 401 after a successful re-mint? Its code is not in this repo, so please confirm.
