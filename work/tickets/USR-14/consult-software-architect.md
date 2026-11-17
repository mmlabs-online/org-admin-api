## USR-14 consult: software-architect (read-only)

### Summary
Use an additive `sid` claim, set to the account session id, and check it in the per-request admin reload. This needs no schema change and no migration, and it covers every trigger in the ticket. A `users.tokens_valid_after` column is not needed. It would also be too coarse: logging out one device would kill admin tokens on every device, and without it plain logout is not covered.

### Findings
1. **Where tokens are issued.** `TokenService.issue` (`/home/user/org-admin-api/app/modules/tokens/service.py`) already loads the `account` row (an `AccountSession`) and logs `account_session_id`. Adding `"sid": str(account.id)` there is a one-line change. This is the only code that signs production tokens (`key_set.sign`). The admin SPA mints `aud=admin` through the same path.
2. **Every trigger already sets `account_sessions.revoked_at`**, so "account session active" is a complete test:
   - Logout `{account_id}`: `SessionRepository.revoke_account`.
   - Logout `{all}`: `revoke_device` revokes all accounts on the device.
   - Password reset: `revoke_all_accounts_of_user` (`/home/user/org-admin-api/app/modules/auth/password_reset.py:110`).
   - Staff revoke one or all (USR-10): `revoke_account_of_user` and `revoke_all_accounts_of_user_ids` (`/home/user/org-admin-api/app/modules/sessions/staff_service.py`).
   - Disable: `revoke_all_accounts_of_user` (`/home/user/org-admin-api/app/modules/users/service.py:81`). Re-enable never brings sessions back, so disable then re-enable is covered without extra work.
   - Cookie reuse after the grace window: `revoke_device`. This is a bonus.
3. **Verifier.** `verify_access_token` (`/home/user/org-admin-api/app/core/access_token.py`) is pure and kept copyable into `mmlabs-auth`. Its `REQUIRED_CLAIMS` list is shared with consumers. Requiring `sid` there would be a breaking change for `mmlabs-auth` parity, so it must not change.
4. **Admin reload.** `AdminAuthService.principal(user_id, token_id)` (`/home/user/org-admin-api/app/modules/admin_auth/service.py`) already reloads the user on every request (ADR-0008 §3). That is the natural place for the new check.
5. **Tests.**
   - Most admin endpoint tests override `current_admin` (`/home/user/org-admin-api/tests/conftest.py:149-160`). Only real-token tests use `mint()`/`claims_for()` in `/home/user/org-admin-api/tests/admin_auth_factories.py`: 52 calls across `test_admin_auth.py`, `test_access_token.py`, `test_me_permissions.py`, `test_route_auth_classes.py`, `test_admin_cors.py` and `test_stats_api.py`. None of these tokens carry `sid`, and the users have no account session.
   - `test_auth_token.py:90` asserts the exact claim set (`set(claims) == {...}`). It will need `sid` added.
6. **Docs.**
   - `docs/consumers.md:399` lists the claims.
   - `docs/consumers.md:386-389` (checklist item 11) says a logout reaches a token only at `exp`. That stays true for consumer apps but needs an admin note.
   - ADR-0005 accepted risk 4 and the follow-ups line (lines 88-99), and ADR-0008 §3 / Decision text (lines 40-42), both say these triggers do not end admin tokens. They must be amended.

### Recommendations
1. **Design: `sid` only, no column.** Satisfies AC1 and AC4: no migration, and R2 is not triggered. Record the choice as an ADR-0008 amendment ("Session-bound admin tokens (USR-14)") that changes §3. Add a short cross-reference amendment to ADR-0005 that rewrites risk 4 and strikes the `sid` follow-up. Keep the `set-admin-origins --clear` part of risk 4: `sid` does not cover it and it is not in the ACs.
2. **Issue `sid` on every audience** (one code path). Document it in `consumers.md` as "opaque, informational; consumers may ignore it; it equals the `account_id` the SPA already holds". It reveals nothing new: `account_id` is already given to the app (ADR-0004). The alternative is admin-only `sid`, which means less on the wire but a branch in the issuer. See open question 2.
3. **Where the checks live (R1):**
   - **deps** (`current_admin`): after `verify_access_token`, read `claims.get("sid")` and parse it as a UUID. If it is missing or malformed, log `_deny("sid", jti, user_id)` and return 401 with `invalid_token`, the same response as every other 401. HTTP code only; no ORM here.
   - **service**: `AdminAuthService.principal(user_id, token_id, session_id)` checks the session after the user/status/verified check. If it is not active, raise `UnauthorizedError`. Log a distinct deny reason (for example `session`) for operators; the response is identical.
   - **repository**: a new `SessionRepository.is_account_active(account_id, user_id) -> bool`, built as `select(exists()).where(id==, user_id==, revoked_at IS NULL)`. This is a primary-key lookup. Binding it to `user_id` stops a token for user A from being kept alive by a `sid` belonging to user B. That would need a forged signature anyway, but it costs nothing. Cross-module repository use has precedent: `users/service.py` already uses `SessionRepository`.
   - Leave device idle/max-age expiry out of the check. `revoke_device` already cascades to accounts, and idle is measured in days while the TTL is 12 h at most. Note this in the ADR.
   - Keep the `AdminPrincipal` signature unchanged, or add a field with a default, so the `conftest` override at line 159 keeps working.
4. **Tokens issued before deploy (no `sid`): fail closed for `aud=admin`.**
   - The admin SPA re-mints silently through `/auth/token` on a 401 (the cookie session is still valid), so the cost is one silent re-mint. Hand-copied developer admin tokens need a new copy. A grace path ("accept no-`sid` tokens if `iat` < deploy time") brings back exactly the gap this ticket closes, for up to 12 h. It also needs a configured cutoff, so I advise against it.
   - Consumer verifiers are unaffected: the claim is additive, PyJWT ignores unknown claims, and `REQUIRED_CLAIMS` is unchanged. This satisfies AC3 and AC4.
   - Add a line to the `deploy.md` runbook: "after deploy, admins get one re-login/re-mint; copied admin tokens must be re-minted."
5. **Tests** (AC5; real tokens, no `current_admin` override):
   - **Helpers.** Add an async helper, for example `make_admin_session(session, user) -> account_id`, that inserts a `DeviceSession` and an `AccountSession`. Have `mint()`/`claims_for()` accept `sid=`. Update the 52 call sites, or have `make_staff` create a session and return or record its id. Keep `DROP` working for `sid`.
   - **One test per trigger.** Each test mints with a real `sid`, gets 200 on an admin endpoint (`GET /api/v1/me/permissions` is cheapest), applies the trigger through its real endpoint or service, then expects 401 with `WWW-Authenticate ... invalid_token`:
     - logout `{account_id}`
     - logout `{all}`
     - password reset complete
     - staff revoke-one
     - staff revoke-all
     - disable → re-enable
   - **Negative cases** (all 401): token with no `sid`; malformed `sid`; `sid` of another user's active session; unknown `sid`.
   - **Positive cases:**
     - After the trigger, a fresh login plus `/auth/token` with `audience=admin` gives a new `sid` that works ("token issued after the cutoff still works").
     - Logging out another device does not affect this token.
     - A consumer-audience token minted before logout still verifies with `verify_access_token` (consumer behaviour unchanged).
     - The issuer test asserts `sid == account_id`, and the claim-set assertion at `test_auth_token.py:90` gets `sid`.
   - **Optional end-to-end test:** cookie login → `/auth/token` (`aud=admin`) → admin call → logout → admin call returns 401.

### Risks
- **One in-flight request may pass.** A request that loaded the session just before a concurrent revoke commits can still succeed, as with the existing disable/role-revoke race (ADR-0008 ADM-002b). Accept it and document it.
- **Slightly more database work.** One extra primary-key lookup per admin request. It can be folded into the user query as a join if needed; not needed now.
- **The test-helper churn is the largest part of the diff.** If `make_staff` silently creates sessions, a test could pass for the wrong reason. Prefer explicit `sid` in the `test_admin_auth.py` trigger tests.
- **Consumer apps still keep tokens alive until `exp`.** This is by design (AC3), but `docs/consumers.md` item 11 must say clearly that only `aud=admin` is session-bound. Otherwise app teams may assume logout ends their tokens.
- **Still not covered:** `set-admin-origins --clear`. Keep it in ADR-0005 risk 4 as residual.

### Open questions
1. **Fail closed for `sid`-less admin tokens at deploy** (my recommendation; one re-mint for everyone), or a time-boxed grace? This needs the owner's OK, since it is a visible behaviour change for anyone holding a copied admin token.
2. **`sid` on all audiences** (recommended: simpler, informational) **or admin only?**
3. **Should email change** (if or when it exists) or **removing the last admin role** also end sessions? Out of scope here; they are already immediate through the reload or not in the ACs. Confirm they are not expected.
