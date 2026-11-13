Status: READY

# Plan — USR-03b: Accounts list and join flow (API)

## Spec summary
- `docs/spec-v1.md` §5.3 Membership: "each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin` … Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data."
- §5.8 Sign-up: "Unverified users may log in but must verify their email before joining any app or getting an app token."
- §5.8 Per-app access tokens: "Not a member → `not_member` with `join_available`; joining follows the app's `join_policy` (D8–D9)."
- `docs/identity-architecture.md` §2 **Join**: "UI shows 'Continue as <account>' (= consent) → `POST /api/v1/auth/apps/B/join {account_id}` → applies `join_policy`; `open` assigns B's default role at app scope (`org_id NULL`) and writes an audit entry → app retries `/api/v1/auth/token`."
- §2 **Switch / list accounts**: "`GET /api/v1/auth/accounts` lists the device's non-revoked account sessions (id, email, display info). Switching only changes the app's `mm_account_id`."
- §4 table rows `GET /api/v1/auth/accounts` (cookie) and `POST /api/v1/auth/apps/{key}/join {account_id}` (cookie + `X-MM-App`); §5 S4 (Origin + `X-MM-App`), S5 (verified email), S8 (join is an audit event).
- D8: "`POST /auth/apps/{key}/join {account_id}` applies `join_policy` (open/approval/closed), assigns the default role at app scope, writes audit. 'Continue as <account>' = consent." D9: "Default join_policy `open`, except `admin` = `closed`."
- Owner notes in the ticket: USR-02c F4 (join takes `lock_app` first), USR-02d (read `default_role_id` under the app lock), USR-03 N1 (`accounts_created_before`), USR-07 S1 (register-app must not re-set a NULL default role), 2026-11-13 (libraries → USR-08, separate repo).

**This plan covers:** accounts list, join for `open` (with `approval` answering 403 until USR-03g), the USR-07 S1 fix, `join_available` accuracy, consumer docs. See Split proposal.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/auth/accounts` (cookie class: `AppContextDep` + `DeviceDep`) returns `{"items":[{account_id, email, email_verified, display_name}]}` for this device's active account sessions only, ordered `created_at, id`; disabled users hidden; never another device's or a revoked session; never `user_id` | `test_auth_accounts.py` › `test_accounts_lists_own_device`, `test_accounts_excludes_other_device`, `test_accounts_excludes_revoked`, `test_accounts_hides_disabled_user`, `test_accounts_fields_exact` |
| 2 | Accounts list applies the N1 cutoff when the previous cookie value is used | `test_accounts_previous_cookie_hides_newer_account` |
| 3 | Accounts list: `Cache-Control: no-store`, rotated cookie re-set, 401 without/with bad cookie, 403 without `X-MM-App`/bad Origin, 429 over limit | `test_accounts_no_store_and_rotation`, `test_accounts_missing_cookie_401`, `test_accounts_missing_app_header_403`, `test_accounts_bad_origin_403`, `test_accounts_rate_limited_429` |
| 4 | `POST /api/v1/auth/apps/{key}/join {account_id}` (cookie class): path `{key}` ≠ `X-MM-App` → 403 `app_mismatch`, no write | `test_join_app_mismatch_403` |
| 5 | `admin` refused (403 `join_closed`) before policy is read, even if the row was changed to `open` with a default role | `test_join_admin_refused_even_if_open` |
| 6 | `open`: assigns the app's default role at app scope (`org_id NULL`, `granted_by NULL`, `expires_at NULL`) → 200 `{"status":"member"}`; `/token` then succeeds | `test_join_open_assigns_default_role`, `test_join_then_token_200` |
| 7 | Idempotent: already an active app-scope member (any role) → 200 same body, no write, no audit | `test_join_idempotent_no_write`, `test_join_existing_other_role_no_write` |
| 8 | Expired default-role assignment is revived in place (same id, `expires_at NULL`) | `test_join_revives_expired_assignment` |
| 9 | Unverified email → 403 `email_unverified`, no write | `test_join_unverified_403` |
| 10 | `closed` → 403 `join_closed`; `approval` → 403 `join_approval_unavailable` (until USR-03g); no write | `test_join_closed_403`, `test_join_approval_unavailable_403` |
| 11 | `open` + NULL `default_role_id` → 403 `join_unavailable` (never 500, never silent 200); default role holding an `is_admin` permission → 403 `join_unavailable` + warning log | `test_join_null_default_role_403`, `test_join_default_role_with_admin_permission_403` |
| 12 | Account resolution with `get_active_account(..., created_before=...)`: foreign/other-device/revoked/N1-newer account or disabled user → 401 `session_invalid`, cookie kept | `test_join_other_device_account_401`, `test_join_unknown_account_401`, `test_join_previous_cookie_newer_account_401`, `test_join_disabled_user_401` |
| 13 | `lock_app` is taken before any write and before the default role is read | `test_join_locks_app_before_reads_and_writes` (statement recorder, as `test_register_locks_app_first`) |
| 14 | Audit recorder called once per write, inside the service (before commit), ids only (no email) | `test_join_audit_ids_only` (fake recorder + caplog: no email, no cookie) |
| 15 | Join: `no-store`, rotated cookie re-set, rate limit 429, 401 without cookie, 403 bad Origin / no `X-MM-App`, 422 extra body field / bad UUID | `test_join_no_store_and_rotation`, `test_join_rate_limited_429`, `test_join_missing_cookie_401`, `test_join_bad_origin_403`, `test_join_missing_app_header_403`, `test_join_body_validation_422` |
| 16 | CORS: preflight for `GET /accounts` and `POST /apps/{key}/join` from a registered origin is echoed with credentials; from an unregistered origin not echoed | `test_auth_cors.py` › `test_preflight_accounts_and_join_from_registered_origin`, `test_accounts_and_join_foreign_origin_not_echoed` |
| 17 | USR-07 S1: `register-app` adding a permission to an existing app with `default_role_id NULL` keeps it NULL; creating a new app still sets `<key>:member` | `test_cli_register_app.py` › `test_null_default_role_kept_after_permission_add`; existing `test_create_app_and_member_role` stays green |
| 18 | `/token` `not_member.details.join_available` is true only for `open` with a non-NULL default role | `test_auth_token.py` › `test_token_not_member` (re-parametrized: open+default → true, open+NULL → false, approval → false, closed → false) |
| 19 | `docs/consumers.md` has the accounts list + join section with plain `fetch` examples and an error table; `docs/identity-architecture.md` / `docs/deploy.md` updated | Reviewer reads the diff |
| 20 | `make check` green; `alembic check` clean (no schema change) | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/membership/__init__.py` | new, empty | New domain folder (join), like `app/modules/tokens/` |
| `app/modules/membership/service.py` | new `JoinService.join(app, device, key, data) -> JoinResult` | Business rules of the join (R1) |
| `app/modules/membership/schemas.py` | new `JoinRequest` (`extra="forbid"`, `account_id: UUID`), `JoinResponse` (`status: Literal["member"]`) | Request/response contract |
| `app/modules/membership/errors.py` | new `AppMismatchError` (403 `app_mismatch`), `JoinClosedError` (403 `join_closed`), `JoinApprovalUnavailableError` (403 `join_approval_unavailable`), `JoinUnavailableError` (403 `join_unavailable`) | R6 error codes |
| `app/modules/audit/__init__.py`, `app/modules/audit/recorder.py` | new `AuditRecorder` Protocol (`async record(event: str, *, actor_user_id, app_id, **ids: UUID)`) + `LogAuditRecorder` (logs `audit event=… key=value` with ids only) | R4 placeholder until ADM-004 (ticket AC "recorded via an interface") |
| `app/modules/auth/router.py` | add `GET /auth/accounts` and `POST /auth/apps/{key}/join` | HTTP layer only (R1, R3 class 2) |
| `app/modules/auth/schemas.py` | add `AccountRead`, `AccountList {items: list[AccountRead]}` | Accounts list contract |
| `app/modules/sessions/repository.py` | add `list_active_accounts(device_id, *, created_before) -> list[tuple[AccountSession, User]]` | Repository-only SQL (R1) |
| `app/modules/sessions/service.py` | add `DeviceSessionService.list_accounts(device: ResolvedDevice)` | Service entry point for the list |
| `app/modules/tokens/service.py` | `join_available = app.join_policy == open and app.default_role_id is not None` | USR-07 S1 / orchestrator: hint must match what join would do |
| `app/modules/rbac/service.py` | `_build_for_app(app, *, set_default: bool = True)`; `register()` passes `set_default=False` on its rebuild (a new app already got its default from `create()`); docstring fixed | USR-07 S1 |
| `app/core/rate_limit.py` | add scopes `accounts`, `join` | Rate limits (R11, consults) |
| `app/core/config.py` | `accounts_rate_limit=60`, `accounts_rate_window_seconds=60`, `join_rate_limit=20`, `join_rate_window_seconds=60` | R5: config from env |
| `.env.example` | the four keys, no values | R5 |
| `tests/test_auth_accounts.py` | new | AC 1–3 |
| `tests/test_auth_join.py` | new | AC 4–15 |
| `tests/test_auth_cors.py` | two tests | AC 16 |
| `tests/test_cli_register_app.py` | one regression test | AC 17 |
| `tests/test_auth_token.py` | re-parametrize `test_token_not_member` (make the `open` case build a default role) | AC 18 |
| `tests/auth_factories.py` | optional helper `make_member_role(session, app)` = role `<key>:member` + set `app.default_role_id` | Shared by join/token tests |
| `docs/consumers.md` | new section "Accounts and joining an app" | Ticket AC3 |
| `docs/identity-architecture.md` | §2 Join/List (codes, approval deferred to USR-03g), §3 `join_requests` → USR-03g, §4 rows (auth = cookie + Origin + `X-MM-App`), §6 libraries → USR-08 separate repo (owner 2026-11-13) | Decisions of this plan change it |
| `docs/deploy.md` | register-app semantics: "a NULL default role stays NULL (join answers `join_unavailable`); only app creation sets `<key>:member` as default" | USR-07 S1 docs |

No migration, no new model, no change to `app/db/models.py`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| test_auth_accounts.py › test_accounts_lists_own_device | two accounts on one device → both, order `created_at, id`, 200 |
| › test_accounts_fields_exact | keys exactly `{account_id, email, email_verified, display_name}`; `account_id` = `account_sessions.id`; no `user_id`; unverified shows `email_verified: false`; `display_name` null when unset |
| › test_accounts_excludes_other_device | account of another device never listed (cross-device) |
| › test_accounts_excludes_revoked | revoked account session not listed |
| › test_accounts_hides_disabled_user | disabled user not listed |
| › test_accounts_empty_device | device whose accounts are all revoked → `{"items": []}` (cookie still valid) |
| › test_accounts_previous_cookie_hides_newer_account | N1: previous value within grace → account created at/after rotation hidden, older shown, no Set-Cookie |
| › test_accounts_no_store_and_rotation | `Cache-Control: no-store`; cookie older than 24 h → rotated `Set-Cookie` |
| › test_accounts_missing_cookie_401 / test_accounts_unknown_cookie_401 | 401 `session_invalid` |
| › test_accounts_missing_app_header_403 / test_accounts_bad_origin_403 | 403 `app_header_required` / `origin_not_allowed`, before device lookup (no rotation) |
| › test_accounts_rate_limited_429 | limit 1 via settings → second call 429 + `Retry-After` |
| › test_accounts_logs_no_email | caplog contains no email |
| test_auth_join.py › test_join_open_assigns_default_role | 200 `{"status":"member"}`; one `role_assignments` row: default role, `org_id NULL`, `expires_at NULL`, `granted_by NULL` |
| › test_join_then_token_200 | full flow: `/token` 403 `not_member` (join_available true) → join → `/token` 200 with `roles == ["stoktok:member"]` |
| › test_join_idempotent_no_write | second join → 200 same body; row count 1; recorder called once overall |
| › test_join_existing_other_role_no_write | user holds `stoktok:viewer` at app scope → 200, no default-role row added |
| › test_join_org_scope_role_is_not_membership | user holds a role only with `org_id` set → join writes the app-scope default role (membership = app-scope only, same test as token for `org_id=None`) — only if an org row is cheap to create; otherwise skip with note |
| › test_join_revives_expired_assignment | expired default-role row → same id, `expires_at NULL`, 200, audited |
| › test_join_unverified_403 | 403 `email_unverified`, no row |
| › test_join_closed_403 | 403 `join_closed`, no row |
| › test_join_approval_unavailable_403 | 403 `join_approval_unavailable`, no row, no `join_requests` table needed |
| › test_join_null_default_role_403 | `open`, `default_role_id NULL` → 403 `join_unavailable`, no row |
| › test_join_default_role_with_admin_permission_403 | default role given an `is_admin` permission directly in the DB → 403 `join_unavailable`, warning log with app id / role id only |
| › test_join_admin_refused_even_if_open | `admin` row set to `open` + default role + an origin in-test; `X-MM-App: admin`, path `admin` → 403 `join_closed`, no row |
| › test_join_app_mismatch_403 | header `stoktok`, path `ricotdin` (existing app) and path `nope` → 403 `app_mismatch`, no row |
| › test_join_other_device_account_401 | account of another device (foreign account) → 401 `session_invalid`, no Set-Cookie clearing |
| › test_join_unknown_account_401 | random UUID → 401 |
| › test_join_revoked_account_401 | revoked account → 401 |
| › test_join_previous_cookie_newer_account_401 | N1: previous value + newer account → 401; older account → 200 |
| › test_join_disabled_user_401 | 401 |
| › test_join_already_member_of_closed_app | member of a `closed` app → 200, no write (membership checked before policy) |
| › test_join_locks_app_before_reads_and_writes | statement recorder: first `FROM apps … FOR NO KEY UPDATE` precedes every `INSERT`/`UPDATE` on `role_assignments` and any `roles`/`role_assignments` read |
| › test_join_audit_ids_only | fake recorder receives `("app.join", actor_user_id=user.id, app_id, role_id, assignment_id)`; default `LogAuditRecorder` log line contains no email / cookie |
| › test_join_no_store_and_rotation | `no-store`; rotated cookie re-set on 200 |
| › test_join_error_after_rotation_rolls_back | 403 `join_closed` with a cookie due for rotation → rotation rolled back (like `test_token_error_after_rotation_rolls_back`) |
| › test_join_rate_limited_429 | limit 1 → second call 429 |
| › test_join_missing_cookie_401 | 401 |
| › test_join_missing_app_header_403 / test_join_bad_origin_403 | 403 `app_header_required` / `origin_not_allowed` |
| › test_join_body_validation_422 | extra field, missing `account_id`, non-UUID → 422 `validation_error` |
| › test_join_disabled_app_403 | disabled app → 403 `origin_not_allowed` (AppContextDep) |
| test_auth_cors.py › test_preflight_accounts_and_join_from_registered_origin | `OPTIONS` with `GET` / `POST` → ACAO echoed + credentials |
| › test_accounts_and_join_foreign_origin_not_echoed | `EVIL` origin → no ACAO |
| test_cli_register_app.py › test_null_default_role_kept_after_permission_add | existing app, default set to NULL, `--permission ricotdin:app:use` → member role rebuilt, `default_role_id` still NULL |
| test_auth_token.py › test_token_not_member (re-parametrized) | (open, default set) → true; (open, NULL) → false; approval → false; closed → false |

## Approach
1. **USR-07 S1 (first, small).** `RoleCatalogService._build_for_app(app, *, set_default=True)`; only call `set_default_role_if_null` when `set_default`. `AppService.register` calls `_build_for_app(app, set_default=False)` (a new app already got its default in `create()`; an existing app's default is staff data). `build_default_role` (seed/explicit rebuild) is unchanged. Fix the `register` docstring ("never touches `default_role_id`" is now true). Update `docs/deploy.md`.
2. **Accounts list.**
   - Repository: `select(AccountSession, User).join(User).where(device_session_id == d, revoked_at IS NULL, User.status == 'active'[, AccountSession.created_at < created_before]).order_by(AccountSession.created_at, AccountSession.id)`. Uses `uq_account_sessions_active` (database-optimizer).
   - Service: `DeviceSessionService.list_accounts(device)` passes `device.accounts_created_before` (N1).
   - Router: `GET /auth/accounts`, `dependencies=[rate_limit("accounts")]`, parameters in order `_app: AppContextDep`, `_cookie: DeviceCookieDoc`, `device: DeviceDep` (403 before device lookup, as `resend_verification`). Re-set rotated cookie; `Cache-Control: no-store`. `response_model=AccountList`.
3. **Join service** (`JoinService(session, recorder)`), order:
   1. `key != app.key` (app = `AppContextDep`, i.e. `X-MM-App`) → `AppMismatchError`. Only an app can join users into itself (security-architect F1).
   2. `key == ADMIN_APP_KEY` → `JoinClosedError`, before any policy read (security-architect F2).
   3. `app = await rbac.lock_app(key)` (`FOR NO KEY UPDATE`, `populate_existing`). `None` or not active → `OriginNotAllowedError` (same answer `AppContextDep` gives a moment later). Lock order app → users, as `bootstrap_super_admin` (ADR-0003 §10).
   4. `account = sessions.get_active_account(device.device_id, data.account_id, created_before=device.accounts_created_before)`; `None` → `SessionInvalidError` (no cookie clearing, as `/token`). `user = users.get(account.user_id)`; missing or not active → `SessionInvalidError`. `email_verified_at is None` → `EmailUnverifiedError` (reuse `tokens.errors`).
   5. `rbac.effective_role_keys(user.id, key, None)` non-empty → return `JoinResult(created=False)`: no write, no audit (idempotent; "member" = any active app-scope role, the same test `/token` uses).
   6. Policy from the locked row: `closed` → `JoinClosedError`; `approval` → `JoinApprovalUnavailableError`.
   7. `open`: `app.default_role_id` read from the locked row (USR-02d). `None` → `JoinUnavailableError`. `role = rbac.get_role(app.id, default_role_id)`; `None` → `JoinUnavailableError`. `rbac.role_has_admin_permission(role.id)` → log warning `join_default_role_admin app_id=… role_id=…` and `JoinUnavailableError` (defence in depth).
   8. `ra, _ = await AssignmentService(session)._grant_locked(app, role, user.id, None, None, None)`: inserts, or revives an expired row in place (`expires_at=None`, `granted_by=None`). It cannot hit the "already assigned" 409, because step 5 returned for an active app-scope row and every writer holds the same lock. Calling the private helper follows the existing precedent `AppService` → `catalog._build_for_app`.
   9. `await recorder.record("app.join", actor_user_id=user.id, app_id=app.id, role_id=role.id, assignment_id=ra.id)`, inside the request transaction before commit. Ids only, never email (R11, security-architect). Then `logger.info("app_joined user_id=… app=… role_id=…")`.
4. **Join router:** `POST /auth/apps/{key}/join`, `response_model=JoinResponse`, `status_code=200`, `dependencies=[rate_limit("join")]`, params `_app: AppContextDep` first, `DeviceDep` after; set the rotated cookie, `Cache-Control: no-store`. `responses={401,403,422,429: _ERR}`. The recorder comes from a `get_audit_recorder` dependency that returns a module-level `LogAuditRecorder()`; tests override it with `app.dependency_overrides`.
5. **`/token` hint:** `join_available = app.join_policy == JoinPolicy.OPEN.value and app.default_role_id is not None`. The NULL-default case and `approval` now answer false. Add a comment that USR-03g will make `approval` true again. A default role carrying an `is_admin` permission still shows true; that state cannot be reached through the API (`replace_permissions`/permission PATCH guards), so join's check is defence only.
6. **Docs.**
   - `docs/consumers.md` "Accounts and joining an app": `GET /accounts` example, the "Continue as <account>" consent rule, the sequence `/token` → 403 `not_member` (`join_available`) → join → `/token`, the join error table (below), the idempotency note, and the note that a staff-removed member can re-join under `open`.
   - `docs/identity-architecture.md` updates as listed.
   - `docs/deploy.md` S1 line.

**Rules.**
- R1: router has HTTP only; services raise `AppError`s; SQL stays in repositories.
- R2: no schema change.
- R3: both endpoints are cookie class (Origin + `X-MM-App` + `__Host-mm_device`); GET included because it reads the cookie and returns personal data.
- R4: recorder placeholder.
- R5: rate-limit keys come from env.
- R6: envelope via `AppError`; every error is 401/403/422/429.
- R9: exceptions are recorded below.
- R11: verified email is required, no secrets or emails in logs, `no-store`, rotation.

**Status code decision (R6).** 200 for both a new membership and an existing one. R6's "201 create" is for creating an addressable resource (`Location`, id in body). Join is an idempotent action on the caller's own membership: it returns no resource id, and a repeat must give the same answer (identity-access-engineer Q2). A 201/200 split would make clients branch on a status for the same outcome. When USR-03g adds approval it will answer 202 `{"status":"pending"}`, so `status` is already a `Literal` field.

**403 `join_unavailable` vs 409 for a NULL default (decision).** 403. The request is well formed and the caller cannot fix it by retrying or changing the request. It is the app's configuration refusing joins (staff deliberately cleared the default, or it was never set), which is the same class as `join_closed`, and `/token` already reports it as `join_available: false`. 409 would suggest a transient conflict worth retrying. It is never a 500 and never a silent 200.

**Join error table (for consumers.md):**

| Status / code | Meaning |
|---|---|
| 200 `{"status":"member"}` | Joined now or already a member: call `/token` |
| 401 `session_invalid` | Account not signed in on this device: re-list accounts / sign in |
| 403 `email_unverified` | Verify email first |
| 403 `join_closed` | App does not accept joins (incl. `admin`) |
| 403 `join_approval_unavailable` | App uses approval; not available yet (USR-03g) |
| 403 `join_unavailable` | App misconfigured (no default role): contact staff |
| 403 `app_mismatch` | Bug: path key must equal `X-MM-App` |
| 403 `app_header_required` / `origin_not_allowed` | Bug/config: header or origin |
| 422 `validation_error` | Bad body |
| 429 `rate_limited` | Back off `Retry-After` |

## Specialist input used
- **api-platform-engineer.**
  - Adopted: cookie class for both endpoints, check order, `items` document, `no-store` + rotated cookie, rate-limit scopes `accounts`/`join`, 403 `join_closed`, 403 `join_unavailable`, the docs sequence and error table.
  - Rejected for now: 201-vs-200 split (see status decision); staff join-request endpoints and `join_requests` writes (moved to USR-03g); shared `ErrorEnvelope` model and `operation_id`s (cross-cutting OpenAPI change → follow-up, R7).
- **database-optimizer.**
  - Adopted: `lock_app` first, then read `default_role_id` from the locked row (no extra `FOR SHARE` needed); the accounts query and its order; no new index; the `join_requests` design is handed to USR-03g as is.
  - Rejected: the single `INSERT … ON CONFLICT ON CONSTRAINT … DO UPDATE … WHERE expired` upsert. Under the app lock, the "member?" read plus `_grant_locked` is race-free (every RBAC writer takes the same lock) and reuses tested code. A partial upsert would be new SQL with the same effect.
  - Rejected: `FOR KEY SHARE` read of `users`. The `role_assignments` FK insert already takes `KEY SHARE`; a concurrent disable is handled by `/token`, which refuses disabled users (Assumption A3).
- **identity-access-engineer.**
  - Adopted almost entirely: path/header match, lock-first, N1 resolution, `email_unverified`, idempotent membership test, `_grant_locked` with expired-row revival, the `AuditRecorder` protocol with a logging no-op, the S1 fix via a `set_default` flag, `join_available` false for NULL default, the approval split to USR-03g with 403 `join_approval_unavailable`, the accounts fields without `user_id`, the `role_has_admin_permission` re-check.
  - Changed: order by `created_at, id` instead of `last_used_at desc`. `last_used_at` only moves on login, and a stable order is easier for "Continue as" lists.
- **security-architect.**
  - Adopted: F1 `app_mismatch`, F2 admin refused before policy, F3 lock + admin-permission re-check, F4/S1 fix, F5 N1 on both endpoints, POST-only consent with `extra="forbid"` and only `account_id` in the body, `no-store`, rate limit, ids-only audit.
  - Not adopted: email masking. The spec (identity-architecture §2: "id, email, display info") and D4/D8 "Continue as <account>" need a recognisable account. The list only shows accounts the person signed into on this same browser. The XSS-linkage risk is recorded for the owner (O3).
  - Moved to USR-03g: approval cooldown and TTL.
  - Follow-ups: origin exclusivity on admin `PATCH /apps`; a deny-list or ban for re-join after removal.

## Assumptions
- A1. "Member" for idempotency = any active app-scope role (`org_id NULL`, not expired), the same test `/token` uses for `org_id=None`. A member of a `closed` app who calls join gets 200 with no write. This is harmless: the state is accurate and nothing is granted.
- A2. `granted_by = NULL` for self-join. The audit record carries the actor (`actor_user_id = user.id`).
- A3. A user disabled concurrently with their own join may end up holding the default role. `/token` refuses disabled users, so this grants nothing usable.
- A4. Reviving an expired default-role row makes it permanent (`expires_at NULL`). Staff can set a new expiry. This is documented.
- A5. Under `open`, a member that staff removed can re-join (no ban). Staff use `closed` or tighten the role (D9 "policy is data"). This is documented in consumers.md / identity-architecture §7.
- A6. The audit placeholder logs when the service calls it, which happens before commit. If the commit later fails, a log line exists for a write that rolled back. This is acceptable for a no-op placeholder; ADM-004 replaces it with an in-transaction insert.
- A7. Rate limits are per IP only (existing limiter). Join is also serialized per app by the lock and is idempotent, so a per-account limit is not needed now.
- A8. Accounts list size: there is no per-device cap today. The list is bounded in practice, because every entry needs that account's password on this browser. A cap at login (e.g. 10 accounts per device) is a follow-up; it is not added here (R7: it changes login behaviour).
- A9. CLAUDE.md R11 still describes the cookie as `mm_device`, `Path=/api/v1/auth`. D20 superseded this (`__Host-mm_device`, `Path=/`). The code follows D20. CLAUDE.md is not edited here (follow-up F5).

## Blocking questions
None.

### Needs owner's decision (non-blocking; plan proceeds with the stated default)
- **O1. Approval split.** `approval` → 403 `join_approval_unavailable` in this ticket. `join_requests` (table + migration), the user-side 202 pending answer, and staff list/approve/reject (Bearer, flag-gated, `lock_app`, re-check verified email, active user and default role at approval time) move to the proposed **USR-03g**. This changes ticket AC2 (approval part) and AC4 (approval tests). Reason: the ticket would otherwise exceed ~400 production lines + 1 migration + 3 staff endpoints. No current consumer uses `approval` (ricotdin is `open`). Three of four consults recommend the split.
- **O2. R9 exception for `GET /auth/accounts`.** A single document `{"items": [...]}`, not `Page[T]` (like D18's role catalog). It is bounded per device (A8). Proposed as **D22** (implementer records it in identity-architecture §8 marked "proposed, pending owner").
- **O3. Unmasked emails in `/accounts`.** Any registered app origin on the device can read the emails of every account signed in on that browser, including accounts never used with that app. This is intended by D4/D8 ("Continue as"). The risk: XSS in one app exposes all of them. Default: unmasked (spec wording). Alternative: masked emails for accounts with no membership in the calling app.
- **O4. Join status code 200** (not 201) for a new membership. Rationale is in Approach.
- **O5. Libraries** → USR-08 in a separate repo (owner decision 2026-11-13). Recorded in identity-architecture §6 and §8 as **D21** (owner-decided).

### Follow-ups (not in this diff, R7)
- F1. **USR-03g** — approval flow (`join_requests`, 202 pending, staff approve/reject, cooldown after rejection, TTL purge, `join_available` true for approval).
- F2. Per-device account-session cap at login (A8).
- F3. Shared `ErrorEnvelope` OpenAPI model and stable `operation_id`s for typed clients (USR-08 benefits).
- F4. Origin exclusivity also on admin `PATCH /apps` (security-architect F1).
- F5. Align CLAUDE.md R11 cookie wording with D20 (A9).
- F6. Re-join deny-list after staff removal (A5), if the owner wants bans.

## Split proposal
- **Slice 1 (this plan, USR-03b):**
  - Accounts list.
  - Join for `open`, with `closed`/`admin`/`approval`/NULL default refused.
  - USR-07 S1 fix.
  - `join_available` accuracy.
  - Audit recorder placeholder.
  - Consumer docs.
  - About 300–350 production lines, no migration.
- **Slice 2 (proposed USR-03g):**
  - `join_requests` table and migration. Columns: `id`, `app_id`, `user_id`, `status`, `decided_by`, `decided_at`, `created_at`. CHECK `(status='pending') = (decided_at IS NULL)`. Partial unique index on `(app_id, user_id) WHERE status='pending'`. Indexes for the staff list and for `user_id` (database-optimizer).
  - Join under `approval` answers 202 `{"status":"pending","request_id"}`, idempotent.
  - Staff `GET /api/v1/apps/{key}/join-requests` (`admin:apps:read`) and `POST …/{id}/approve|reject` (`admin:roles:assign`), mounted behind `ADMIN_RBAC_API_ENABLED`.
  - `join_available` true for `approval`.
- **Slice 3 (already USR-08):** client libraries, separate repo.

## Data model / migration
None. No new table or column. `join_requests` is deferred to USR-03g. Reads use the existing `uq_account_sessions_active` index and `uq_role_assignments_user_role_org`.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/auth/accounts` | headers `X-MM-App`, `Origin`, cookie `__Host-mm_device` | 200 `{"items":[{"account_id": uuid, "email": str, "email_verified": bool, "display_name": str\|null}]}`; `Cache-Control: no-store`; `Set-Cookie` when rotated | 200, 401 `session_invalid`, 403 `app_header_required`/`origin_not_allowed`, 429 `rate_limited` | Cookie class (R3 class 2); rate limit `accounts` (60/60 s per IP) |
| POST | `/api/v1/auth/apps/{key}/join` | headers as above; body `{"account_id": uuid}` (`extra="forbid"`) | 200 `{"status":"member"}` (new or existing membership); `Cache-Control: no-store`; `Set-Cookie` when rotated | 200; 401 `session_invalid`; 403 `app_header_required`, `origin_not_allowed`, `app_mismatch`, `join_closed`, `email_unverified`, `join_approval_unavailable`, `join_unavailable`; 422 `validation_error`; 429 `rate_limited` | Cookie class (R3 class 2); `{key}` must equal `X-MM-App`; rate limit `join` (20/60 s per IP) |
| POST | `/api/v1/auth/token` (changed) | unchanged | unchanged; `not_member.details.join_available` = `join_policy == open AND default_role_id IS NOT NULL` | unchanged | unchanged |
