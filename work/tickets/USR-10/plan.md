Status: READY

# Plan — USR-10: Staff session management for a user (list, revoke one, revoke all)

## Spec summary
`docs/spec-v1.md` §5.10 (Staff session management, owner 2026-11-16):
> - **List** a user's account sessions (paginated), each with: account session id, created / last used, its device session's last seen, IP and user agent (from `device_sessions`), and revoked state. Default: non-revoked only.
> - **Revoke one** account session, and **revoke all** of the user's account sessions on every device (the same effect as a password reset, USR-04b). Staff revoke **account** sessions, not device sessions: a device session may also hold other people's accounts on a shared browser, and they must stay signed in.
> - **Permission `admin:*`** (owner, 2026-11-16) for list and revoke.
> - **Audited** (R4, §5.4): one entry per revoke action in the same transaction, actor = staff user, target = the user; `changes` carries session ids / counts only, never IPs, user agents, cookies or tokens. New action names are set at PLAN (e.g. `user.session_revoked`, `user.sessions_revoked`).
> - **Known limit (no refresh tokens, D7):** access tokens already issued stay valid until they expire (up to `ACCESS_TOKEN_TTL_SECONDS`: 12 h in production (owner, 2026-11-17); admin role revoke and disable still apply on the next admin request, ADR-0008 §3). Revocation stops new tokens; it does not kill live ones.

§5.8 Admin: "staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4)."

Owner decisions recorded in the ticket (2026-11-17):
- **OQ7:** staff may NOT list/revoke their own sessions through the admin API (403, like other admin self-changes); a target holding any admin role needs `admin:*`.
- **List and both revokes require `admin:*`** (spec §5.10 wins over the earlier OQ7 reading). The list shows the full IP and user agent, labelled "recorded when this browser first signed in", `Cache-Control: no-store`, never logged.
- **OQ8:** answered by USR-13 (TTL up to 12 h, ADR-0005 amendment). The ticket's "~15 min" docs AC is stale and is replaced by the USR-13 wording (AC6 below).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A Bearer admin endpoint lists a user's active account sessions; each entry has the session id, the device's last-seen time, IP and user agent; the response is `Page[T]`. | `GET /api/v1/users/{user_id}/sessions`. Tests `tests/test_user_sessions_admin.py › test_list_returns_active_sessions`, `› test_list_item_has_exact_keys`, `› test_list_orders_and_paginates`, `› test_list_excludes_inactive_sessions`, `› test_list_unknown_user_404`, `› test_list_bad_uuid_422`. Response header `Cache-Control: no-store`. |
| 2 | Revoke one account session; revoke all of the user's account sessions on every device; device sessions that carry other users' accounts are left alone. | `DELETE /api/v1/users/{user_id}/sessions/{session_id}` → 204, `DELETE /api/v1/users/{user_id}/sessions` → 204. Tests `› test_revoke_one_revokes_only_that_session`, `› test_revoke_all_revokes_every_device`, `› test_revoke_all_shared_device_keeps_other_user` (device row `revoked_at` stays NULL, other user's `/auth/token` still 200). |
| 3 | Both revoke actions (and, per spec §5.10 and owner 2026-11-17, the list) require `admin:*`. Self → 403 (OQ7). | `tests/test_route_auth_classes.py` BEARER map entries with `"admin:*"` (`test_bearer_route_permission_map` checks the dependency and `x-permission`); runtime 401 sweep. Tests `› test_routes_require_admin_wildcard` (a principal with `admin:users:read` + `admin:users:write` gets 403 `permission_denied`, `details.required == "admin:*"`), `› test_list_self_403`, `› test_service_privileged_target_requires_wildcard`. |
| 4 | Each revoke writes an audit row in the same transaction, holding session ids and counts only (no IP, UA or token). Denied attempts follow ADM-004b. | `tests/test_audit_coverage.py`: `AUDITED` gains the two DELETE routes (count 23 → 25), so `test_success_writes_one_row`, `test_failure_writes_no_row`, `test_no_secret_or_email_in_any_row` run on them; new `test_session_self_revoke_writes_denied_row`, `test_session_revoke_noops_write_no_row`. In `tests/test_user_sessions_admin.py`: `› test_revoke_audit_changes_exact_keys`, `› test_revoke_all_audit_caps_ids`. |
| 5 | Tests: list, revoke one, revoke all; another user's session → 404; 401/403; audit rows; after a revoke the user's `/auth/token` returns 401 `session_invalid`. | All tests in "Tests to write". 401: the route sweep in `tests/test_route_auth_classes.py` (`test_every_bearer_route_401_without_token`, `…_with_cookie_only`, `test_credential_on_bearer_routes_401`). `/auth/token`: `› test_token_after_revoke_one_is_session_invalid`, `› test_token_after_revoke_all_is_session_invalid`. `make check` green. |
| 6 | Docs state that already-issued access tokens stay valid until they expire — **reworded per the orchestrator note**: up to `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) + 30 s leeway, not "~15 min"; revoking sessions does not end already-issued tokens, including admin tokens. | Reviewer reads the route `description` in OpenAPI (`/docs`), the new rows in `docs/identity-architecture.md` §4 and S8, the runbook bullets in `docs/deploy.md` "Revoking live tokens (incident)", and the §5.10 note in `docs/spec-v1.md`. No occurrence of "15 min" in the new text. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/sessions/repository.py` | Add `list_active_for_user(user_id, *, idle, max_age, limit, offset) -> tuple[list[Row], int]`, `revoke_account_of_user(user_id, account_id) -> bool`, `revoke_all_accounts_of_user_ids(user_id) -> list[uuid.UUID]`. Existing methods unchanged. | R1: SQL only in the repository; reuse the predicates of `revoke_all_accounts_of_user` / `_usable`, scoped by user. |
| `app/modules/sessions/staff_service.py` (new) | `StaffSessionService`: `list(...)`, `revoke_one(...)`, `revoke_all(...)` with guards, lock and audit. | R1 business rules; OQ7 guards; R4 audit. |
| `app/modules/sessions/schemas.py` (new) | `UserSessionRead` (explicit allowlisted fields). | AC1; R11 data minimisation (no hashes, no `device_session_id`). |
| `app/modules/users/router.py` | Three routes under `/users/{user_id}/sessions`, each `**require_permission("admin:*")`; descriptions with the token-tail wording; `Cache-Control: no-store` on GET; 404 declared. Module docstring line for USR-10. | R3 class 1, R9 paths, AC1–3, AC6. |
| `app/core/errors.py` | `SelfSessionChangeForbiddenError(PermissionDeniedError)`, code `self_session_change_forbidden`; add it to `DENIED_ERRORS`. | OQ7 self guard; ADM-004b denied audit. |
| `app/modules/audit/actions.py` | `USER_SESSION_REVOKED = "user.session_revoked"`, `USER_SESSIONS_REVOKED = "user.sessions_revoked"`. | R4; spec §5.10 names. |
| `tests/test_user_sessions_admin.py` (new) | Endpoint and service tests listed below. | R8, AC1–5. |
| `tests/test_route_auth_classes.py` | 3 BEARER entries (`"admin:*"`); add `"session_id"` to `_fill`. | R3 deny-by-default map; `_fill` otherwise raises `KeyError` in the sweep. |
| `tests/test_audit_coverage.py` | 2 `AUDITED` entries + 4 scenarios; `len(AUDITED) == 25`; two new tests (denied self-guard, no-ops). | ADM-004 coverage harness fails until mapped. |
| `docs/identity-architecture.md` | §4 API table row; self-guard code in the "Privileged writes" paragraph; S8 action list + the token-tail sentence. | AC6; docs follow the API. |
| `docs/deploy.md` | "Revoking live tokens (incident)": add the "lost or shared device" bullet and the session-revoke limit (wording in Approach step 8). | AC6 runbook; owner asked for incident wording. |
| `docs/spec-v1.md` | §5.10: one dated "USR-10" note (routes, permission, fields, actions, no-op rules); mark §8 OQ7 answered with the owner's 2026-11-17 text; §5.10 "Open" line drops OQ7. | Keep the spec in sync with the owner's recorded answers (ticket notes). No scope change. |

No other file. No migration, no `app/db/models.py` change, no `.env.example` change (existing `DEVICE_SESSION_IDLE_DAYS` / `DEVICE_SESSION_MAX_DAYS` are reused).

## Tests to write
Fixture helpers: `make_client(admin_permissions=["admin:*"], admin_user=actor)`; `make_device` / `make_login_user` / `auth_headers` from `tests/auth_factories.py`; `make_user`, `assign`, `super_admin_role` from `tests/rbac_factories.py`; for `/auth/token` reuse the member setup used in `tests/test_auth_token.py` (user with a role on the login app; copy the local helper rather than importing a private one). Set `device.ip` / `device.user_agent` directly on the factory row when a test needs them.

| Test (file › name) | Covers |
|---|---|
| `tests/test_user_sessions_admin.py › test_list_returns_active_sessions` | Two devices for the target; 200, `Page` envelope (`items,total,limit,offset`), `ip` is a string (INET → `str`), `user_agent`, `device_last_seen_at`; `Cache-Control: no-store`. |
| `› test_list_item_has_exact_keys` | Keys == `{id, created_at, last_used_at, device_last_seen_at, ip, user_agent}`; no `device_session_id`, no hash/token key. |
| `› test_list_orders_and_paginates` | Order `account_sessions.created_at DESC, id DESC`; `limit`/`offset`; `total` counts active only. |
| `› test_list_excludes_inactive_sessions` | Not listed: revoked account session; account on a revoked device; device idle > `device_session_idle_days`; device older than `device_session_max_days`; another user's session. |
| `› test_list_unknown_user_404` | Random user id → 404 `not_found` (never an empty page). |
| `› test_list_bad_uuid_422` | Non-UUID `user_id` → 422 `validation_error`. |
| `› test_list_self_403` | `admin_user=target` → 403 `self_session_change_forbidden`; **no** audit row (reads are not audited). |
| `› test_routes_require_admin_wildcard` | Principal with `admin:users:read` and `admin:users:write` (no `admin:*`) → 403 `permission_denied`, `details.required == "admin:*"` on all three routes; no audit row; nothing revoked. |
| `› test_revoke_one_revokes_only_that_session` | 204, empty body; that row has `revoked_at`; the same user's session on device 2 stays active. |
| `› test_revoke_one_other_users_session_404` | User B's session id under user A → 404 `not_found`; B's row still `revoked_at IS NULL` (DB assert); no audit row. |
| `› test_revoke_one_unknown_or_already_revoked_404` | Random session id → 404; already revoked session → 404; no audit rows. |
| `› test_revoke_one_unknown_user_404` | Random user id → 404. |
| `› test_revoke_all_revokes_every_device` | Three devices (one idle-expired, not listed); 204; all three rows revoked; device rows untouched; audit count == 3. |
| `› test_revoke_all_unknown_user_404` | Random user id → 404. |
| `› test_revoke_all_shared_device_keeps_other_user` | Users A and B on one cookie; revoke all of A; A `POST /auth/token` → 401 `session_invalid`; B → 200; device `revoked_at IS NULL`. |
| `› test_token_after_revoke_one_is_session_invalid` | After revoke-one, `/auth/token` with that cookie + `account_id` → 401 `session_invalid`. |
| `› test_token_after_revoke_all_is_session_invalid` | Same after revoke-all, for two devices. |
| `› test_revoke_audit_changes_exact_keys` | Rows: action `user.session_revoked` / `user.sessions_revoked`, outcome `success`, actor = staff user, target (`user`, target id), `app_key` NULL, `changes` keys exactly `{account_session_ids, account_sessions_revoked}`, values `{"from": None, "to": [...]}` / `{"from": None, "to": n}`; no `ip`/`user_agent`/token key in `changes`. |
| `› test_revoke_all_audit_caps_ids` | 101 active sessions → `account_sessions_revoked.to == 101`, `len(account_session_ids.to) == 100`, ids sorted. |
| `› test_service_privileged_target_requires_wildcard` | Service level (`StaffSessionService` with an `AdminPrincipal` lacking `admin:*`) on a target holding the super-admin role: list → `AdminWildcardRequiredError`, no row; revoke-one and revoke-all → `AdminWildcardRequiredError` + one `outcome=denied` row each (`changes.reason == "admin_wildcard_required"`); nothing revoked. (G5 defence in depth; unreachable over HTTP while the route needs `admin:*`.) |
| `tests/test_audit_coverage.py › AUDITED` entries | `("DELETE", f"{USER}/sessions/{{session_id}}")`: `{"user.session_revoked"}`, `_session_revoke` (make_user + make_device), `_session_unknown` (existing user, random session id), 404. `("DELETE", f"{USER}/sessions")`: `{"user.sessions_revoked"}`, `_sessions_revoke_all` (make_user + make_device), `_sessions_unknown_user` (random user id), 404. Parametrised `test_success_writes_one_row`, `test_failure_writes_no_row`, `test_no_secret_or_email_in_any_row` cover them. |
| `tests/test_audit_coverage.py › test_every_mutating_bearer_route_is_mapped` | `len(AUDITED) == 25`. |
| `tests/test_audit_coverage.py › test_session_self_revoke_writes_denied_row` | Actor revokes own session and own all → 403 `self_session_change_forbidden` each; exactly two `outcome=denied` rows (`user.session_revoked`, `user.sessions_revoked`), `changes == {"reason": "self_session_change_forbidden"}`; actor's sessions still active (commit setup first, as `test_denied_guard_writes_row` does). |
| `tests/test_audit_coverage.py › test_session_revoke_noops_write_no_row` | Revoke-all on a user with zero active sessions → 204, no row; revoke-one on an already revoked session → 404, no row. |
| `tests/test_route_auth_classes.py` (BEARER + `_fill`) | Three routes classified with key `admin:*`, `x-permission == "admin:*"`, 401 sweep, CORS matcher, credential-on-bearer 401. |

## Approach
1. **Errors and actions.** Add `SelfSessionChangeForbiddenError` (code `self_session_change_forbidden`) to `app/core/errors.py` and to `DENIED_ERRORS`. Add the two `AuditAction` members.
2. **Repository** (`SessionRepository`, database clock only, explicit columns, never `token_hash`/`prev_token_hash`):
   - `list_active_for_user(user_id, *, idle: timedelta, max_age: timedelta, limit, offset)`: `SELECT a.id, a.created_at, a.last_used_at, d.last_seen_at, d.ip, d.user_agent FROM account_sessions a JOIN device_sessions d ON d.id = a.device_session_id WHERE a.user_id = :uid AND a.revoked_at IS NULL AND d.revoked_at IS NULL AND d.last_seen_at >= now() - :idle AND d.created_at >= now() - :max_age ORDER BY a.created_at DESC, a.id DESC LIMIT/OFFSET`, plus `count(*)` with the same WHERE. Same predicates as `DeviceSessionService._usable`, so the list shows exactly the sessions that can still mint a token.
   - `revoke_account_of_user(user_id, account_id) -> bool`: `UPDATE account_sessions SET revoked_at = now() WHERE id = :sid AND user_id = :uid AND revoked_at IS NULL RETURNING id`. No device filter, no device revoke.
   - `revoke_all_accounts_of_user_ids(user_id) -> list[UUID]`: same predicate as `revoke_all_accounts_of_user`, with `RETURNING id`. The existing count method and its two callers (password reset, disable) stay unchanged (R7).
3. **Service** `StaffSessionService(session, *, idle_days, max_days)` in `app/modules/sessions/staff_service.py`, using `UserRepository`, `RbacRepository`, `SessionRepository`, `AuditLogger`. It returns plain dataclass views (`ip` converted with `str()` when not None), not ORM rows.
   - `list(user_id, *, actor, limit, offset)`: `users.get` → 404 `NotFoundError("User not found")`; self (`user_id == actor_user_id(actor)`) → raise `SelfSessionChangeForbiddenError`; `if await rbac.user_has_privileged_assignment(user_id): require_super(actor)`; then the repository list. **No lock, and no denied row**: it is a read, and reads are not audited (ADR-0009 §8). Never log IP or UA.
   - `revoke_one(user_id, session_id, *, actor, ctx)` and `revoke_all(user_id, *, actor, ctx)`, in the `set_status` shape:
     1. `user = await users.get_for_update(user_id)` (`FOR NO KEY UPDATE`) → 404 if missing. Copy `user.id` into a local before any guard (the denied rollback expires ORM objects).
     2. `try:` self guard → `SelfSessionChangeForbiddenError`; G5 `if await rbac.user_has_privileged_assignment(uid): require_super(actor)`; `except DENIED_ERRORS as exc: await audit.record_denied(action, exc=exc, actor=actor, ctx=ctx, target=(AuditTarget.USER, uid)); raise`.
     3. Write. Revoke-one: `revoke_account_of_user`; `False` → `NotFoundError("Session not found")` (no row). Revoke-all: `revoke_all_accounts_of_user_ids`; empty → return (no-op, 204, no row).
     4. `audit.record(action, actor, ctx, target=(USER, uid), changes={"account_session_ids": change(None, sorted(ids)[:100]), "account_sessions_revoked": change(None, len(ids))})` in the same transaction. Revoke-one uses the same two keys with one id and count 1.
   - **Lock order.** The user row only (`FOR NO KEY UPDATE`), then `account_sessions` rows. This matches password reset (user → revoke) and is compatible with login (user `FOR SHARE` → device → account upsert): a login in flight either commits first, so its new session is revoked too, or waits for the revoke to commit. `set_status` takes the admin app lock → user; these paths never take the admin app lock, so there is no cycle. No admin app lock, because no last-super-admin decision is made (database-optimizer rec. 5).
4. **Schema** `UserSessionRead` (in `app/modules/sessions/schemas.py`): `id: UUID` ("account session id; the `account_id` apps send to `/auth/token`"), `created_at`, `last_used_at` ("last sign-in of this account on this device"), `device_last_seen_at` ("browser last seen; refreshed at most hourly"), `ip: str | None` and `user_agent: str | None` (both described as "recorded when this browser first signed in; shared by every account on that browser"). Built field by field in the router, never `model_validate` of an ORM row.
5. **Router** (`app/modules/users/router.py`, router-level `current_admin` already present):
   - `GET /{user_id}/sessions`: `response_model=Page[UserSessionRead]`, `**require_permission("admin:*")` + 404 in `responses`, `PageDep`, `AdminDep`, `Response` for `Cache-Control: no-store`. Settings via `request.app.state.settings` (`device_session_idle_days`, `device_session_max_days`), passed into the service.
   - `DELETE /{user_id}/sessions/{session_id}`: `status_code=204`, `AuditContextDep`, 404 declared.
   - `DELETE /{user_id}/sessions`: `status_code=204`, 404 declared.
   - Each route gets a `description` with the known limit (step 8 wording). R1: the router only maps HTTP ↔ service and builds the schema.
6. **Tests** as listed; `tests/test_route_auth_classes.py` BEARER + `_fill`; `tests/test_audit_coverage.py` AUDITED/count/scenarios/new tests.
7. **Rules.** R1 (layering above); R2 (no schema change, so `alembic check` stays clean); R3 class 1 with a declared permission on each route; R4 (success row in the request transaction, denied via `record_denied`); R5 (no new config; reuses existing settings); R6 (404/403/422/204 in the envelope); R7 (no `sid`, no device revoke, no `include_revoked`); R8 (tests + `make check`); R9 (plural nested paths, `Page[T]`, UUID ids, 204 deletes, no verb path); R11 (no hashes exposed, no IP/UA in logs or audit `changes`).
8. **Docs (AC6).** Use this wording (adapt the grammar to each place; do not use "~15 min"):
   - Route descriptions and `docs/identity-architecture.md`: "Revoking sessions stops the user from getting **new** access tokens (`/auth/token` → 401 `session_invalid`). It does **not** end tokens already issued, including admin (`aud=admin`) tokens: they stay valid until `exp`, at most `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway. It also does not stop a new sign-in with the password. IP and user agent are recorded when the browser first signed in and are shared by every account on that browser."
   - `docs/deploy.md` "Revoking live tokens (incident)", new first bullet: "**Lost or shared device:** staff session revoke (`DELETE /api/v1/users/{id}/sessions[/{session_id}]`, `admin:*`) signs the user out on that browser or everywhere; other accounts on a shared browser stay signed in. Tokens already issued stay valid until `exp` (≤ `ACCESS_TOKEN_TTL_SECONDS`, 12 h in production, + 30 s). Session revoke is **not** a compromise response: for a suspected compromise use the consumer/staff steps below (disable, or revoke admin roles, held for at least TTL + 60 s; password reset before re-enabling; emergency key rotation for an immediate global cut)." Keep the existing compromised-staff bullet (it already says staff session revoke does not end an admin token).
   - `docs/identity-architecture.md` §4: table row `Admin: GET|DELETE /api/v1/users/{id}/sessions`, `DELETE /api/v1/users/{id}/sessions/{session_id}` | Bearer `admin:*` (self → 403 `self_session_change_forbidden`) | list active account sessions (`Page`, `no-store`), revoke one / all (account sessions only; device sessions stay) | USR-10. Add `self_session_change_forbidden` to the self-guard codes sentence; add `user.session_revoked`, `user.sessions_revoked` to S8.
   - `docs/spec-v1.md`: dated note under §5.10 with the decisions in this plan; §8 OQ7 → `[x]` with the owner's 2026-11-17 answer.

## Specialist input used
Adopted:
- **Routes `/api/v1/users/{user_id}/sessions[/{session_id}]`; revoke-all as `DELETE` on the collection** (api-platform §1). It follows R9 (no verb paths outside the documented exceptions) and R6 (204 delete), and the count goes to the audit row. Not adopted: `POST …/revoke-all` (security, identity left it open). It would need a new R9-style verb path and gains nothing, since the response has no body.
- **`admin:*` on all three routes** (identity R-d, security R1). This is now the owner's decision. Not adopted: api-platform's `admin:users:read` for list, superseded by the owner.
- **404 for unknown, other user's and already-revoked session, no audit row** (all four consults agree). This avoids an ownership oracle and matches "no-op logout writes nothing".
- **Revoke-all with zero sessions: 204, no audit row** (api-platform Q2, identity "be consistent"). This is consistent with `test_noop_writes_no_row`, ADR-0009 amendment ("no-ops write nothing") and `test_success_writes_one_row` asserting `row.changes` is truthy. Rejected: security R6 and database rec. 9 ("write a row with n = 0"). Recording staff intent on a no-op would break the harness's no-op rule; it is listed for the owner below.
- **Audit content: ids + exact count, ids capped at 100 (sorted)** (identity R-e, database Risk "count without ids would not meet the AC"). The ticket AC says "session ids and counts". Rejected: security R6 (count only), because it contradicts the AC; the cap addresses its "unbounded" concern. The values use the `{from: null, to: …}` shape because ADR-0009 amendment keeps `{field: {from, to}}` for admin mutations (precedent: `email_retention.purged` counts).
- **List filters idle/max-expired and revoked devices** (identity R-a, security R5, database rec. 2): the list shows exactly what can still mint a token. Revoke-all still revokes rows behind expired devices, as password reset does (identity R-c).
- **No `include_revoked` flag / no `revoked_at` field** (ticket AC1 "active", the narrower reading). The spec's "Default: non-revoked only" implies an option; it is listed as a follow-up. This keeps the PII window small (security Q4).
- **Fields**: `id`, `created_at`, `last_used_at`, `device_last_seen_at`, `ip`, `user_agent`; no `device_session_id` (security R4, api-platform §4). IP/UA labelled "first signed in" (owner + identity F1).
- **Lock: user row `FOR NO KEY UPDATE` only, no admin app lock** (database rec. 3–5). This serialises with login (`FOR SHARE`) and password reset, with no cycle against `set_status`.
- **Guards: 404 → self → G5 inside `except DENIED_ERRORS`**, the `set_status` shape. Self is checked after the lock/404; this is equivalent for self (the actor always exists) and keeps one try-block. Rejected: database rec. 3 "self before any lock" (a micro-optimisation that would split the pattern). **G5 kept** as defence in depth (identity R-d, security R1), tested at service level.
- **List self-guard: 403, not audited** (identity R-e, security R2, api-platform §3).
- **New repository methods, existing ones untouched** (identity R-c option 2, database rec. 3): minimal diff (R7).
- **Docs/runbook wording** (security R8, identity R-g) condensed into Approach step 8; `deploy.md` already holds the compromise steps, so only the lost-device bullet and the TTL wording are added.
- **No migration, no index** (database rec. 1).

Not adopted (out of scope, follow-ups):
- `sid` claim / `tokens_valid_after` admin check (identity Q5, security R9).
- Revoke the device session when its last account is revoked (api-platform Q5): not asked; `start_for_login` already handles empty devices.
- Concurrency test for revoke-all vs login (database rec. 9): the lock order matches password reset, which is already covered; listed as a follow-up.
- Cleanup of stale `account_sessions` rows (identity Risk 4).

## Assumptions
- `require_permission("admin:*")` is valid: `_PERMISSION_RE` accepts `admin:*` and `permits` matches it exactly. This is the first route keyed on the wildcard; `x-permission` shows `admin:*`.
- Disabled targets are listable and revocable (their sessions are already revoked by disable, so the list is empty and revoke-all is a no-op).
- The revoke-one 404 message is "Session not found" for every not-found case (no distinction).
- `user_agent` is returned as stored (already truncated to 512 on write); no extra sanitising in the response.
- The list has no extra filters (no `?device=` etc.).

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **Revoke-all with nothing to revoke**: default 204 with **no** audit row (consistent with the no-op rule). Alternative: one row with count 0.
2. **Revoke-all audit ids capped at 100** (count always exact). Alternative: no cap, or count only.
3. **List shows active sessions only**, with no `include_revoked` option or `revoked_at` field (ticket wording); spec "Default: non-revoked only" option deferred to a follow-up.
4. **Full IP** (not truncated) shown to `admin:*` holders. This follows the owner's 2026-11-17 note; spec Q6 (PII) is still open.
5. **Revoke-all is `DELETE /users/{id}/sessions`** (no response body; the count is in the audit row), not `POST …/revoke-all`.

## Blocking questions
None

## Split proposal
None. This is one spec feature (§5.10). Production code is ≈ 250–300 lines (repository ≈ 60, service ≈ 110, schema ≈ 30, router ≈ 70, errors/actions ≈ 8); the rest is tests and docs. Splitting list from revoke would churn the auth-class and audit-coverage maps twice for no gain.

Follow-ups (not in this diff): `sid` claim + account-session / `tokens_valid_after` check in the admin per-request reload (ADR-0005 amendment risk 4), so staff revoke actually ends an admin token; `?include_revoked=` on the list; a revoke-all vs login concurrency test; cleanup of stale `account_sessions` rows behind expired devices; spec Q6 (PII/IP truncation, read-audit of who viewed sessions).

## Data model / migration
None. Uses `account_sessions` (`ix_account_sessions_user_id`, partial unique `uq_account_sessions_active`) and `device_sessions` (PK join). `alembic check` must stay clean.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users/{user_id}/sessions` | `limit` (≤ 100), `offset` | `Page[UserSessionRead]` (`id`, `created_at`, `last_used_at`, `device_last_seen_at`, `ip`, `user_agent`); active only; `created_at DESC, id DESC`; `Cache-Control: no-store` | 200, 401, 403 (`permission_denied`, `self_session_change_forbidden`, `admin_wildcard_required`†), 404 user, 422 | `admin:*` |
| DELETE | `/api/v1/users/{user_id}/sessions/{session_id}` | — | empty | 204, 401, 403 (`permission_denied`, `self_session_change_forbidden`††, `admin_wildcard_required`†), 404 (unknown user, unknown / other user's / already revoked session), 422 | `admin:*` |
| DELETE | `/api/v1/users/{user_id}/sessions` | — | empty (no-op also 204) | 204, 401, 403 (as above), 404 unknown user, 422 | `admin:*` |

† Service-level G5, unreachable over HTTP while the route needs `admin:*`. †† Denied revokes write one `outcome=denied` row (ADM-004b); the list's 403 writes none.

Audit: `user.session_revoked` / `user.sessions_revoked`, actor = staff user, target `(user, user_id)`, `changes = {"account_session_ids": {"from": null, "to": [<≤100 sorted uuids>]}, "account_sessions_revoked": {"from": null, "to": n}}`.
