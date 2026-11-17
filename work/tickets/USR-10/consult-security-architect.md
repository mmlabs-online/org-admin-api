USR-10 security consult (security-architect, read-only). Tickets that need a decision before PLAN are marked BLOCKING.

## Findings

**Scope conflicts. The spec wins, so the planner should flag these.**
1. **BLOCKING: who may list a user's sessions.**
   - Spec §5.10 (docs/spec-v1.md:106) says `admin:*` is needed for **list and revoke**.
   - Ticket AC3 says `admin:*` for revoke only.
   - The OQ7 answer ("listing/revoking sessions of a user who holds any admin role requires `admin:*`") reads as if list normally needs less, e.g. `admin:users:read`.
   - If list stays `admin:*` (the spec), the privileged-target guard is automatically satisfied and does nothing on these routes. If the owner meant `admin:users:read` for list, then `require_super` must run when `rbac.user_has_privileged_assignment(target)` is true, the way `UserService.set_status` does it (app/modules/users/service.py:59-60).
   - My recommendation is the spec reading: `admin:*` for all three routes. Raw IP and user agent should not be readable with the broadly granted `users:read`.
2. **Stale TTL text.** AC6 and spec OQ8 still say "~15 min". USR-13 / ADR-0005 amendment (docs/adr/0005-access-token-signing.md:70-99) sets a global 12 h TTL in production (43200), covering every audience including `admin`. The docs must quote `ACCESS_TOKEN_TTL_SECONDS` (12 h in prod) plus 30 s leeway, not 15 min.
3. **Spec Q6 (is IP/UA PII?) is still open.** OQ7/OQ8 are answered, Q6 is not.

**Current code facts that affect the design**
4. **The IP is not a "last seen" IP.**
   - `device_sessions.ip` and `user_agent` are written once, in `add_device`, when the device is created at its first login (sessions/repository.py:38-44, service.py:147-152). They are never updated.
   - `last_seen_at` is refreshed at most hourly. `account_sessions.last_used_at` changes only on upsert (login).
   - Showing "IP" next to "last seen" will mislead incident triage. The IP is the browser's first sign-in IP, and it is shared by every account on that device.
5. **Expired sessions look active.** When a device expires (idle/max days), its `account_sessions.revoked_at` stays NULL; only explicit revokes set it. A list filtered only on `account_sessions.revoked_at IS NULL` will show these dead devices as "active".
6. **Revoke-all already exists.** `SessionRepository.revoke_all_accounts_of_user` does exactly the spec's revoke-all and leaves device sessions alone. Password reset uses it and audits `{"account_sessions_revoked": n}` (auth/password_reset.py:110-122). There is no repository method yet to revoke one account session by `(id, user_id)`: the existing `revoke_account` is scoped by device.
7. **The audit key filter does not catch IP/UA.** `_FORBIDDEN_KEY` (audit/service.py:25) blocks hash/token/password/cookie/email/secret, but **not** `ip` or `user_agent`. Nothing in code would stop a `changes={"ip": …}`.
   - The audit row's own `ip` and `user_agent` columns hold the **staff caller's** request context (AuditContextDep). That is the normal ADM-004 pattern and is not the target's PII.
8. **Which denials get audited.**
   - Route-level `require_permission` refusals are only logged (admin_auth/deps.py:89-97), never audited.
   - `outcome=denied` rows are written only for service guards that raise one of `DENIED_ERRORS` (core/errors.py:85), via `record_denied`, which rolls back and commits the row alone.
   - With routes gated on `admin:*`, the only audited denial in USR-10 is the **self-target guard** on the revoke routes.
9. **Revoke does not end anything already issued.**
   - Admin principals are reloaded every request from user status and roles (admin_auth/service.py:34-40). Nothing is bound to a session: there is no `sid` claim and no `tokens_valid_after`.
   - So revoking a user's sessions ends **no** live access token: not for `aud=admin`, not for app audiences. It only stops new tokens from `/auth/token` (tokens/service.py:68-72 → `session_invalid`).
   - Revoke also does not stop a fresh login with known credentials.
   - ADR-0005 amendment risk 4 already records this for admin.

**Threat model (STRIDE, short)**
- **Elevation / IDOR:** revoking `/users/{A}/sessions/{S}` where session S belongs to user B. Low risk if the UPDATE is scoped by `id AND user_id AND revoked_at IS NULL`.
- **Information disclosure:** IP and UA of every device a user signs in from, available to any `admin:*` holder. The device id would also let staff link accounts that share a browser.
- **Repudiation:** a staff member signing out a colleague or super-admin. Covered by the audit row, and by the self-guard plus `admin:*`.
- **DoS / abuse:** a rogue `admin:*` holder could sign out every staff member repeatedly. The impact is small, since live tokens keep working.
- **False assurance (highest real risk):** staff press "revoke all" on a compromised account and believe it is contained. It is not, for up to 12 h, and the attacker can log in again.

## Recommendations
- **R1 (AC1/AC3, R3, spec §5.10): permissions.** All three routes use `require_permission("admin:*")`, under `/api/v1/users/{user_id}/sessions`:
  - `GET` → `Page[T]`
  - `DELETE /{session_id}` → 204
  - `DELETE` (or `POST …/revoke-all`) → 204
  - Keep a service-level `require_super(actor)` when the target holds a privileged assignment anyway, as defence in depth in case the route permission is later relaxed. It costs one query.
- **R2 (OQ7, ADM-004b): self-target guard.**
  - Add `SelfSessionChangeForbiddenError(PermissionDeniedError)`, code `self_session_change_forbidden`, to `DENIED_ERRORS`.
  - Check `target_user_id == actor_user_id(actor)` in the service before any write. On revoke: `record_denied(action, exc=…, target=(USER, user_id))`, then raise 403.
  - On **list**: 403 with the same code, logged but not audited. Reads are not audited anywhere, and `record_denied` is for privileged writes. Say this explicitly in the plan.
  - Order: user lookup (404), then self (403), then the work.
- **R3 (AC5, BOLA): IDOR.**
  - Add a repository method `revoke_account_of_user(session_id, user_id) -> bool`: `UPDATE … WHERE id=:sid AND user_id=:uid AND revoked_at IS NULL RETURNING id`.
  - Return an identical 404 for unknown user, unknown session, another user's session and an already-revoked session. No audit row when nothing changed.
  - Tests: user B's session id under user A gives 404 **and** B's session is still active afterwards (assert in the DB); a random UUID gives 404; an already-revoked one gives 404.
- **R4 (AC1, Q6 / R11): data minimisation in the list.**
  - Use an explicit response schema with only the allowlisted fields: `id`, `created_at`, `last_used_at`, `device_last_seen_at`, `ip`, `user_agent`, `revoked_at`. Never build from the ORM row wholesale; `DeviceSession` carries `token_hash` and `prev_token_hash`.
  - Do **not** expose `device_session_id`. It is not in spec §5.10, and it would let staff link users who share a browser.
  - Name or document `ip` / `user_agent` as "recorded when this browser first signed in", not last seen (Finding 4).
  - Set `Cache-Control: no-store`, like join_requests and stats.
  - Never log IP or UA in service or router logs.
  - Ask the owner (Q6) whether a truncated IP (/24 or /48) would do. Default: full IP, since the spec asks for IP.
- **R5 (AC1): what "active" means.** By default list only sessions with `account_sessions.revoked_at IS NULL AND device_sessions.revoked_at IS NULL AND device within idle/max days` (use the same `SESSION_IDLE_DAYS`/`MAX_DAYS` settings that `DeviceSessionService._usable` uses), so staff never see ghost sessions. Revoke-all may still revoke the rows behind expired devices; that is harmless. Order by `created_at, id` for stable paging.
- **R6 (AC4, R4/R11): audit content.**
  - Actions: `user.session_revoked` and `user.sessions_revoked`. Target `(AuditTarget.USER, user_id)`, actor = staff principal, `record()` in the same transaction.
  - `changes`: `{"account_session_id": "<uuid>"}` for revoke-one, `{"account_sessions_revoked": n}` for revoke-all.
  - Do not store the list of ids for revoke-all: it is unbounded, and the count matches the password-reset precedent.
  - Write the revoke-all row even when n = 0, because staff intent is worth recording.
  - Tests must assert the **exact key set** of `changes`: no `ip`, `user_agent`, `token`, `cookie` or `email` (Finding 7).
- **R7 (AC5): regression test.** After revoke, `/auth/token` with the device cookie and that `account_id` returns 401 `session_invalid`. Add a second test: on a shared device, another user's account session still mints a token after revoke-all (spec: device sessions left alone).
- **R8 (AC6, ADR-0005 amendment): docs and runbook wording** (docs/deploy.md or the API description). Suggested text:
  > "Revoking sessions stops the user from getting **new** access tokens. Tokens already issued, including admin-pan (`aud=admin`) tokens, stay valid until they expire: up to `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway. Revoke does not stop the user, or anyone holding the password, from signing in again. **Suspected account compromise:** (1) disable the user (`PATCH /users/{id}` status=disabled). This ends admin access on the next request and revokes all sessions. (2) Keep the user disabled for at least TTL + leeway (≥ 12 h 1 min). Re-enabling earlier revives a stolen admin token, and app tokens stay valid until `exp` regardless. (3) Have the user reset their password before re-enabling. (4) For a staff account, revoking the admin role has the same immediate effect on admin requests. (5) To kill every live token at once, use emergency signing-key rotation (docs/deploy.md), bounded by consumer JWKS caches. Use session revoke for 'signed in on a lost or shared device', not for a credential compromise."
- **R9 (R7): follow-ups, not in this diff.** ADR-0005 already lists `tokens_valid_after` / `sid` checking in the admin reload. Add "staff session revoke also bumps `tokens_valid_after` for `aud=admin`" as a candidate follow-up.

## Risks
- **High (accepted by owner, ADR-0005 risk 4):** a stolen admin or app token survives revoke for up to 12 h. The UI and API docs must not imply revoke contains a compromise. The runbook (R8) is the mitigation.
- **Medium:** IP/UA PII is exposed to every `admin:*` holder while Q6 (Decree 13/2023) is open. Minimisation (R4) and audit cover it partly. Listing is not audited, so who viewed whose IPs is not traceable.
- **Medium:** the "IP" shown is the first-login IP of the browser, not current. Staff may draw wrong conclusions.
- **Low:** a race between revoke-all and a concurrent login leaves the new session alive. It is outside the ticket; disable is the tool for that case.
- **Low:** a misbehaving `admin:*` holder could repeatedly sign out other staff. The audit row and the low impact make this acceptable.

## Open questions
1. **BLOCKING:** list permission. Spec §5.10 says `admin:*`; the OQ7 wording suggests `admin:users:read` plus `admin:*` for privileged targets. Which one?
2. Q6: may staff see the full IP and user agent, or a truncated IP? Is a "who viewed sessions" read-audit wanted?
3. Revoking an already-revoked session: 404 (my recommendation, same answer as IDOR) or an idempotent 204?
4. Should the list offer `?include_revoked=true`? The spec says "Default: non-revoked only", which implies an option. If yes, revoked rows keep their IP/UA, which widens the PII window.

Files checked:
- /home/user/org-admin-api/app/modules/sessions/{models,repository,service}.py
- /home/user/org-admin-api/app/modules/users/{router,service}.py
- /home/user/org-admin-api/app/modules/admin_auth/{deps,service}.py
- /home/user/org-admin-api/app/modules/rbac/actor.py
- /home/user/org-admin-api/app/modules/audit/service.py
- /home/user/org-admin-api/app/core/errors.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/auth/password_reset.py
- /home/user/org-admin-api/docs/spec-v1.md (§5.10, §8)
- /home/user/org-admin-api/docs/adr/0005-access-token-signing.md (USR-13 amendment)
