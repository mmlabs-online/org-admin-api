Status: READY

# Plan: USR-14, admin tokens end on logout, password reset and staff session revoke

## Spec summary
- Spec §5.8, Admin: "staff can list, look up, disable/enable users, manage their roles per app and **revoke their sessions** (audited, §5.4). Session list and revocation per user: §5.10."
- Spec §5.10, Known limit: "access tokens already issued stay valid until they expire (up to `ACCESS_TOKEN_TTL_SECONDS`: 12 h in production …; admin role revoke and disable still apply on the next admin request, ADR-0008 §3). Revocation stops new tokens; it does not kill live ones. Whether that is acceptable for admin tokens: §8 OQ8."
- ADR-0005, USR-13 amendment, accepted risk 4: "logout, password reset, staff session revoke, disable-then-re-enable within TTL + leeway … and `set-admin-origins --clear` do **not** end a stolen admin token." The follow-up listed there: "a `tokens_valid_after` or `sid` check in the admin reload".
- ADR-0005 §5: "Adding a claim is not breaking; removing or renaming one is."
- ADR-0008 §3: the per-request reload, which "covers disable, role revoke and assignment expiry; it does not cover logout, password reset, staff session revoke or `--clear`" (§7).
- Ticket goal: "logout, password reset, staff session revoke (USR-10) and disable-then-re-enable must end a live admin token on its next request, not at `exp`."

This ticket closes that gap for `aud=admin` only. Consumer apps keep the D7 behaviour described in §5.10.

## Acceptance criteria → verification
| # | Criterion (ticket) | How the reviewer verifies it |
|---|---|---|
| 1 | Design chosen and recorded in an ADR-0005/0008 amendment (`sid` and/or `tokens_valid_after`) | The plan chooses **`sid` only** (see Approach). `docs/adr/0008-admin-api-authentication.md` gains the amendment "Session-bound admin tokens (USR-14)". `docs/adr/0005-access-token-signing.md` gains a USR-14 amendment that adds `sid` to the §5 claims contract (optional, admin-only), rewrites risk 4, strikes the `sid`/`tokens_valid_after` follow-up and records why `tokens_valid_after` was rejected. |
| 2 | For `aud=admin`: after logout, password reset, staff revoke of that session or of all sessions, or disable→re-enable, the previously issued token gets 401 on the next admin request | `tests/test_admin_token_sessions.py`: `test_logout_account_ends_admin_token`, `test_logout_all_ends_admin_token`, `test_password_reset_ends_admin_token`, `test_staff_revoke_one_ends_admin_token`, `test_staff_revoke_all_ends_admin_token`, `test_disable_then_reenable_ends_admin_token`. Each one gets 200 on an admin route, fires the trigger through its real endpoint, then asserts the exact 401 envelope and `WWW-Authenticate` on both `GET /api/v1/organizations` and `GET /api/v1/me/permissions`. |
| 3 | Consumer-app tokens unchanged (valid until `exp`); docs (consumers.md, deploy.md runbook, ADR) updated | `test_consumer_token_has_no_sid_and_survives_logout`. The existing consumer claim-set assertion `test_auth_token.py::test_token_happy_path` (`set(claims) == {…}`) stays **unchanged** and still passes. Doc diffs as listed under Files. |
| 4 | Additive claim only; migration with downgrade only if a column is added | `app/core/access_token.py` is not in the diff (`REQUIRED_CLAIMS` unchanged). No file under `migrations/` changes and `alembic check` stays clean: no column is added, so R2 is not triggered. `test_token_admin_audience_issued` asserts that the admin claim set equals the consumer set plus `sid`. |
| 5 | Tests for each trigger, plus a token issued after the cutoff still works | The six trigger tests in row 2. Each one ends by getting a **new** session (login or new device), minting through `/auth/token` and asserting 200 on the admin route. Negative, isolation and race tests: see Tests. `make check` is green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/tokens/service.py` | Add `"sid": str(account.id)` to the claims only when `app.key == ADMIN_APP_KEY` (import from `app.modules.rbac.models`). Keep the `token_issued` log line as it is (it already logs `account_session_id`). | AC1, AC2, AC3 |
| `app/modules/sessions/repository.py` | New `is_account_active(account_id, user_id) -> bool`: `SELECT EXISTS` over `account_sessions JOIN device_sessions` where `account_sessions.id = :account_id`, `account_sessions.user_id = :user_id`, `account_sessions.revoked_at IS NULL` and `device_sessions.revoked_at IS NULL`. Database clock only, no idle or max-age predicates. | AC2 (R1: SQLAlchemy only in the repository) |
| `app/modules/admin_auth/service.py` | `principal(user_id, token_id, *, session_id)`: keep the existing user check (it raises `UnauthorizedError`), then call `SessionRepository.is_account_active(session_id, user_id)`. If that returns false, raise the new `AdminSessionEndedError(UnauthorizedError)`, defined in this module. Update the docstring. `AdminPrincipal` is unchanged. No try/except around the lookup. | AC2 |
| `app/modules/admin_auth/deps.py` | After `sub` is parsed: read `claims.get("sid")` and parse it with `uuid.UUID(str(...))`. If it is missing or malformed: `_deny("sid", jti, user_id)` and the same 401. Pass `session_id=` to `principal`. Catch `AdminSessionEndedError` before `UnauthorizedError` and call `_deny("session", jti, user_id)`. Every path raises `_unauthorized(token_sent=True)`. | AC2 |
| `app/modules/users/router.py` | Rewrite `_REVOKE_LIMITS`: revoke ends the user's admin (`aud=admin`) tokens bound to those sessions on their next admin request. Tokens of other apps stay valid until `exp` (≤ `ACCESS_TOKEN_TTL_SECONDS`, 12 h in production, + 30 s). Keep the sentences "does not stop a new sign-in with the password" and the IP/UA sentence. | AC3 (route docs) |
| `tests/admin_auth_factories.py` | Add `async def admin_session(session, user) -> uuid.UUID`: `make_device(session, user)`, record `_SIDS[user.id] = account.id`, return the id. `make_staff` calls it. `claims_for(User)` defaults `sid` to `_SIDS.get(user.id)` (omitted if unknown). `claims_for(str)` adds no `sid`. An explicit `sid=` or `sid=DROP` overrides. | Test churn kept to the factory (see Approach §5) |
| `tests/test_admin_auth.py` | `test_super_admin_wildcard_allows_all` and `test_other_app_wildcard_does_not_grant_admin` (both use `make_login_user` plus `mint`) call `await admin_session(db_session, user)` before `mint`. Add the negative and logging tests listed under Tests. | Existing tests keep their meaning; AC2 negatives |
| `tests/test_admin_token_sessions.py` (new) | Trigger, isolation, race, consumer-unchanged and "fresh token works" tests, all on real `/auth/token` tokens | AC2, AC3, AC5 |
| `tests/test_auth_token.py` | `test_token_admin_audience_issued`: assert `set(claims) == {iss, aud, sub, email, email_verified, roles, iat, exp, jti, sid}` and `claims["sid"] == str(account.id)`. `test_token_happy_path` stays unchanged (it pins that a consumer token has no `sid`). | AC3, AC4 |
| `docs/adr/0008-admin-api-authentication.md` | New section "Amendment: session-bound admin tokens (USR-14, 2026-11-17)". It amends §3 (the reload also checks the token's account session), §7 (remove "does not cover logout, password reset, staff session revoke"; keep `--clear`) and Consequences (one more indexed lookup per call). Add a one-line pointer after the ADM-002b bullet "issued app tokens last until `exp`": admin tokens end with the sessions (USR-14). | AC1 |
| `docs/adr/0005-access-token-signing.md` | New section "Amendment: `sid` for admin tokens (USR-14, 2026-11-17)". (a) §5: `sid` is an optional claim, present only when `aud=admin`. It equals the account-session id (`account_id`, ADR-0004); `sub` is unchanged and is still never the account-session id; verifiers must not require `sid`. (b) Risk 4 rewritten: for `aud=admin`, logout, reset, staff revoke and disable→re-enable now end the token on the next request. Still open: role revoke→re-grant within TTL + leeway, and `--clear`. (c) Risk 2 (consumers) unchanged. (d) Follow-ups line: strike the `sid`/`tokens_valid_after` item. (e) Rejected alternative: `tokens_valid_after`, with the reasons. Mark "*(Amended by USR-14)*" inline at risk 4 and at the follow-ups line. | AC1 |
| `docs/adr/0004-device-sessions-and-login.md` | Line ~93: append "(admin tokens: ended on the next admin request, ADR-0008 USR-14 amendment)". | AC3 consistency |
| `docs/consumers.md` | §"Calling the admin API from the admin UI" (~74–79): admin tokens end when their account session ends (logout of that account in **any** app on that browser, logout-all, reset, staff revoke, disable). The next admin call is 401; re-mint once, and on `session_invalid` go to login (the flow is unchanged). Do not decode `sid`. Reset "Effects" (~300): admin tokens end at once; app tokens remain until `exp`. Checklist item 11 (~386): unchanged for apps, plus one sentence saying only `aud=admin` tokens are session-bound, server-side. Claims list (~399): add `sid` (only in `aud=admin` tokens; consumer tokens never carry it; never require it). | AC3 |
| `docs/deploy.md` | The incident runbook (~251–269) per Approach §7. Line ~404 (reset after grant-super-admin): admin tokens end at once, other tokens up to the TTL. Lines ~520–521: the runbook's `EXIT` trap logout now ends that admin token on its next request. Lines ~524–526: add "logout, password reset and session revoke" to what applies on the next request. A new paragraph "**After the USR-14 deploy:**" covering the one forced re-mint. Lines ~641–644 and ~660 (`--clear`, origin changes): **unchanged**, still true. | AC3 |
| `docs/identity-architecture.md` | §4 USR-10 row (~176): same wording as `_REVOKE_LIMITS`. Admin-auth paragraph (~179–185): the reload also checks the token's `sid` account session (and its device) is not revoked. Claims paragraph (~195–198): add `sid` (only for `aud=admin`). S8 (~224): replace "…including admin tokens" with "…for app tokens; admin tokens end on the next admin request (USR-14)". | AC3 |
| `docs/spec-v1.md` | §5.10 only: in the "Known limit" bullet (~108) and the USR-10 bullet (~109), replace the admin-token sentences with the new behaviour (factual update, no scope change; USR-10 edited the same lines). §8 OQ8 is left as it is. | Spec matches the code (the spec is the source of truth) |

No other files. `app/core/access_token.py`, `tests/jwt_verifier.py`, `migrations/`, `.env.example` and `app/core/config.py` must **not** change.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_admin_token_sessions.py › test_logout_account_ends_admin_token` | Logout `{account_id}` (cookie, `X-MM-App: admin`, admin Origin) → 401 on `/organizations` and on `/me/permissions`; then a new device + `/auth/token` → 200 |
| `… › test_logout_all_ends_admin_token` | Logout `{all: true}` → 401; a fresh token afterwards works |
| `… › test_password_reset_ends_admin_token` | Issue a reset token (as `test_password_reset_api._issue`), `POST /auth/password/reset` → old admin token 401. `/auth/login` with the new password plus `/auth/token` → 200 |
| `… › test_staff_revoke_one_ends_admin_token` | Actor `make_staff("admin:*")` → `DELETE /users/{id}/sessions/{account_id}` 204 → target token 401; new session token → 200 |
| `… › test_staff_revoke_all_ends_admin_token` | `DELETE /users/{id}/sessions` 204 → 401; new session → 200 |
| `… › test_disable_then_reenable_ends_admin_token` | Target is `make_login_user` plus a non-super admin role (e.g. `admin:organizations:read`) so the last-super-admin guard is not hit. Actor `admin:*`: PATCH `{status: disabled}`, then `{status: active}`, both 200 → old token still 401. A new device + `/auth/token` → 200 |
| `… › test_other_session_of_same_user_survives` | The user has device A and device B with an admin token on each. Staff revoke-one of A → token A 401, token B 200 |
| `… › test_shared_device_other_user_survives` | `make_device(db, a, b)`: logout `{account_id: a}` → token a 401, token b 200 |
| `… › test_revoke_between_session_read_and_sign_401` | Race. Monkeypatch `SessionRepository.get_active_account` to call the original and then revoke that account row (`UPDATE … revoked_at = now()`) before returning. `/auth/token` returns 200, and the token gets 401 on its first admin call |
| `… › test_consumer_token_has_no_sid_and_survives_logout` | A `stoktok` member mints a consumer token, logs out `{all}`. `verify_access_token(token, aud="stoktok")` still succeeds and `"sid" not in claims` (AC3) |
| `test_admin_auth.py › test_missing_sid_401` | `mint(staff, sid=DROP)` → exact 401 (the pre-deploy token shape; fail closed) |
| `… › test_malformed_sid_401` (parametrized: `"not-a-uuid"`, `123`, `""`) | 401 |
| `… › test_unknown_sid_401` | `sid=str(uuid4())` → 401 (a missing row is treated as revoked) |
| `… › test_foreign_sid_401` | Staff A's token with `sid` = staff B's live account id → 401 (`user_id` binding) |
| `… › test_revoked_device_with_active_account_401` | Set `device_sessions.revoked_at = now()` directly, leaving the account active → 401 (device join) |
| `… › test_session_lookup_error_is_not_allowed` | Monkeypatch `SessionRepository.is_account_active` to raise `RuntimeError` → `pytest.raises(RuntimeError)` on the request: no fallback-allow (the `client` fixture re-raises app exceptions) |
| `… › test_session_denials_logged` | caplog: a revoked `sid` logs `admin_auth_denied reason=session jti=<jti> user_id=<id>`; a missing `sid` logs `reason=sid …`. No token or email in the log text |
| `test_auth_token.py › test_token_admin_audience_issued` (extended) | Admin claim set = consumer set ∪ {`sid`}; `sid == str(account.id)` |
| Existing suites (`test_admin_auth`, `test_me_permissions`, `test_route_auth_classes`, `test_admin_cors`, `test_stats_api`, `test_access_token`) | Must pass unchanged apart from the two `admin_session` calls above (the factory supplies `sid`) |

Shared helper inside `test_admin_token_sessions.py`: `_issued(client, db, user) -> (raw_cookie, account_id, token)`. It sets `apps[admin].origins = [ORIGIN]` (as `_admin_with_origin` does), calls `make_device(db, user)`, then `POST /auth/token` `{audience: "admin", account_id}` with `auth_headers(app_key="admin", cookie=raw)`. Trigger tests use **real issued tokens**, not `mint`, so they cannot pass because of the factory registry.

## Approach
1. **Mechanism: `sid` only, no `tokens_valid_after`.** Every trigger in the AC already sets `account_sessions.revoked_at`:
   - logout `{account_id}`: `revoke_account`;
   - logout `{all}` and cookie reuse: `revoke_device`, which cascades to the accounts;
   - reset: `revoke_all_accounts_of_user`;
   - staff revoke one/all: `revoke_account_of_user` and `revoke_all_accounts_of_user_ids`;
   - disable: `revoke_all_accounts_of_user`. Re-enable never un-revokes, so disable→re-enable is covered.

   So "the token's account session is still active" is a complete check. It is decided when the token is used, so there is no app-vs-DB clock comparison and no same-second `iat` boundary, and the mint race (identity F6) is covered. It also needs no column or migration (AC4, R2 not triggered).
2. **Mint** (`TokenService.issue`): `sid` only when `app.key == ADMIN_APP_KEY`. Consumer tokens stay byte-for-byte the same claim set (literal AC3, minimal claims).
3. **Verify** (R3 class 1, `current_admin`): `verify_access_token` is unchanged (shared and copyable into `mmlabs-auth`; `REQUIRED_CLAIMS` untouched). Then:
   - `sub` is parsed;
   - `sid` is parsed (missing or malformed → 401, reason `sid`);
   - `AdminAuthService.principal(user_id, jti, session_id=sid)` runs the user check (reason `user`), then `is_account_active` (reason `session`).

   The check lives in `current_admin`/`principal`, not in `require_permission`, so the self-scoped `GET /api/v1/me/permissions` is covered too. R1: the deps only parse and map to HTTP, the service raises `AppError` subclasses, and the repository holds the SQL. Cross-module use of `SessionRepository` has precedent (`users/service.py`, `tokens/service.py`).
4. **Errors (R6, ADR-0008 §4):** every new failure is the existing 401: `unauthorized` / "Authentication required" / `details: null` / `WWW-Authenticate: Bearer realm="admin", error="invalid_token"`. The cause goes only to the log (`_deny` with `jti` and `user_id`; never the token or email; R11). **Fail closed:** no try/except, no cache, so a DB error is a 500, never an allow. Add a one-line comment above the check saying so.
5. **Test factory, minimal churn:** `make_staff` creates a real device and account session through `admin_session`. `claims_for(User)` picks up its `sid` from a module dict, so the ~50 existing `mint(staff)` calls are unchanged. Only the two `make_login_user` + `mint` tests call `admin_session` explicitly. `mint(str)` (used by `test_access_token.py`, the pure verifier) carries no `sid` and is unaffected. The dict is keyed by `user.id` (fresh uuid per test), so stale entries after rollback are harmless.
6. **Rollout: fail closed.** Admin tokens minted before the deploy have no `sid` and get 401 after deploy. admin-pan re-mints once silently (consumers.md ~78: on a 401, call `/auth/token` once). Curl runbook users rerun the login. Grandfathering would leave every pre-deploy token unrevocable for up to 12 h, which is the gap this ticket closes.
7. **deploy.md runbook** (~251–269), rewritten:
   - **Lost or shared device:** staff session revoke ends that session's **admin** tokens on the next admin request. App tokens stay until `exp`. It is still not a compromise response, because the password still logs in.
   - **Compromised staff account:**
     - (1) Disable the user. This is immediate and revokes every account session, so re-enabling **no longer** revives an old admin token.
     - (2) Reset the password and check the inbox before re-enabling.
     - (3) If you only revoke the admin role (sessions left alive), do **not** re-grant it within TTL + 60 s; that gap remains (follow-up).
     - (4) `--clear` still does not end issued admin tokens.
     - (5) Keep the "revoke all admin roles" sentence.
   - **Compromised consumer account:** unchanged.
   - Delete the sentence "Logout, password reset, staff session revoke and `--clear` do not end an issued admin token"; keep only the `--clear` part.
   - **Scope** line: also map `jti` → `account_session_id` (the `token_issued` log) for a targeted revoke-one.
8. **R4:** no new mutation. The triggering actions already write audit rows, and a rejected request is a read-side denial (log only, ADR-0009 §8). **R5:** no config. **R9:** no endpoint or schema change. **R10/R11:** no new token type, no refresh, nothing secret logged.
9. Run `make check`. Grep the docs for the "including admin" and "do not end an issued admin token" wording and confirm none remains outside `work/`.

## Specialist input used
- identity-access-engineer: **adopted** Rec 1 (`sid` only; reject `tokens_valid_after` for granularity, clock and race reasons), Rec 2 (admin-only, `REQUIRED_CLAIMS` unchanged), Rec 3 (missing row = revoked; no idle/max-age check), Rec 4 (reject `sid`-less tokens), Rec 5 tests, Rec 6 docs, Rec 7 (no migration). **Not adopted:** the bonus cookie-reuse test. That path is `revoke_device`, already exercised by logout `{all}`, so it would duplicate coverage.
- security-architect: **adopted** Rec 1 including the device `revoked_at` join (defence in depth: free on the PK join, and protects against any future device-only revoke path), Rec 2 (same 401, fail closed, no cache, reject legacy tokens), Rec 3 (no extra mint gate), Rec 4, Rec 5 (no audit row for denials), Rec 6 tests, Rec 7 docs, Rec 8 follow-ups kept out (R7).
- software-architect: **adopted** the `is_account_active` repository method bound to `user_id`, `AdminPrincipal` unchanged (the conftest override keeps working), the distinct `session` log reason, explicit `sid` in trigger tests, and the `test_auth_token.py` claim-set update. **Rejected** Rec 2 (`sid` on every audience): AC3 says consumer tokens are "unchanged", and consumers have no use for it.
- api-platform-engineer: **adopted** admin-only `sid`, the same 401 and `WWW-Authenticate`, log-only reasons, the consumers.md items, and the trigger and isolation tests. **Rejected** the hybrid `tokens_valid_after` column. Its premise ("a `sid` check does not cover disable→re-enable or reset") does not hold: reset (`password_reset.py:110`) and disable (`users/service.py:81`) already revoke every account session, and re-enable does not restore them. A column would add a migration, user-wide over-kill on single-session logout, and the second-granularity `iat` boundary, and close no extra AC. **Rejected** editing the `_ERROR_RESPONSES` 401 description: no contract value, and it churns the OpenAPI.

## Assumptions
- No production code path revokes a device session without its accounts (all go through `revoke_device`). The device join is defence in depth, not load-bearing.
- Device idle (days) and max age (365 d) are far longer than the 12 h TTL, and minting already requires a usable device, so they are not checked per admin request. This is recorded in the ADR-0008 amendment.
- The admin-pan and `mmlabs-auth` verifiers accept unknown claims. ADR-0005 §5 says additive claims are not breaking, and the consumers.md checklist does not reject them. Only `aud=admin` tokens change, so at most admin-pan is affected. This is listed in the release note as a pre-deploy check, not verified here (those repos are not in this workspace).
- One extra PK lookup per admin request is acceptable; it is not folded into the user query (simplest diff). This is recorded in ADR-0008 Consequences.
- An in-flight request that passed the check before a concurrent revoke commits completes (the same accepted risk as ADR-0008 ADM-002b). This is documented, not tested.
- Behaviour change on shared browsers: logging an account out in any app on that browser ends that account's admin-pan token. This is correct semantics, and it is documented in consumers.md.

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **`sid` only on `aud=admin`** (default), not on every audience. Reason: AC3 says "consumer tokens unchanged", and minimal claims.
2. **Fail closed on `sid`-less admin tokens right after deploy** (default): one silent re-mint per admin-pan tab, and curl users log in again. Alternative: grandfather tokens until `exp` (≤ 12 h unrevocable).
3. **Check account `revoked_at` plus device `revoked_at`; no idle or max-age check** (default).
4. **`tokens_valid_after` rejected** for this ticket. Kept as a possible follow-up only for role revoke→re-grant.
5. **Spec §5.10 lines updated** to state the new admin behaviour (factual; OQ8 is left as answered).

## Blocking questions
None.

## Split proposal
None. About 60 lines of production code, about 300 lines of tests and about 80 lines of docs; one feature.

Follow-ups (R7, not in this diff):
- A per-user admin cutoff bumped on privileged role revoke (closes role revoke→re-grant within TTL).
- `--clear` as an immediate admin kill switch (`current_admin` requires the admin app to have an origin).
- Consumer-side session introspection or a shorter per-audience TTL (needs `sid` in consumer tokens and an owner decision).
- Optionally fold the session check into the user query if admin latency matters.

## Data model / migration
None. No new table, column or index: the lookup uses the `account_sessions` primary key, and the join uses the `device_sessions` primary key. `alembic check` must report no drift.

## API contract
None changed. No new endpoint, path, schema or status code. Behaviour change, documented in the ADRs and consumers.md:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| any | every Bearer admin route under `/api/v1/*`, including `GET /api/v1/me/permissions` | `Authorization: Bearer <aud=admin token>` | unchanged | 401 now also when `sid` is missing or malformed, or its account session (or device) is revoked, unknown or belongs to another user; same envelope and `WWW-Authenticate` | unchanged |
| POST | `/api/v1/auth/token` (`audience=admin` only) | unchanged | the token now carries `sid` = `account_id` | unchanged | cookie class (unchanged) |
