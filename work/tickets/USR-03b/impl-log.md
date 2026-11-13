# Implementation log — USR-03b

## Attempt 1 — 2026-11-13

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/service.py` | USR-07 S1: `_build_for_app(app, *, set_default=True)`; `register()` rebuilds with `set_default=False`; docstring fixed |
| `app/core/config.py` | `accounts_rate_limit/_window_seconds` (60/60), `join_rate_limit/_window_seconds` (20/60) (R5) |
| `.env.example` | the four new keys, no values (R5) |
| `app/core/rate_limit.py` | scopes `accounts`, `join` |
| `app/modules/sessions/repository.py` | `list_active_accounts(device_id, *, created_before)`: active sessions of active users, `created_at, id` order, N1 cutoff |
| `app/modules/sessions/service.py` | `DeviceSessionService.list_accounts(device)` passes `accounts_created_before` |
| `app/modules/auth/schemas.py` | `AccountRead`, `AccountList {items}` |
| `app/modules/audit/__init__.py`, `app/modules/audit/recorder.py` | `AuditRecorder` Protocol + `LogAuditRecorder` (ids only) — R4 placeholder |
| `app/modules/membership/__init__.py` | new domain folder |
| `app/modules/membership/errors.py` | `app_mismatch`, `join_closed`, `join_approval_unavailable`, `join_unavailable` (403) |
| `app/modules/membership/schemas.py` | `JoinRequest` (`extra="forbid"`), `JoinResponse` (`status: Literal["member"]`) |
| `app/modules/membership/service.py` | `JoinService.join`: mismatch → admin → `lock_app` → account (N1) / user / verified → membership → policy → default role (+ `is_admin` re-check) → `_grant_locked` → audit |
| `app/modules/auth/router.py` | `GET /auth/accounts`, `POST /auth/apps/{key}/join` (cookie class, `AppContextDep` before `DeviceDep`, `no-store`, rotated cookie), `get_audit_recorder` dependency |
| `app/modules/tokens/service.py` | `join_available = open AND default_role_id IS NOT NULL` |
| `tests/auth_factories.py` | `make_member_role(session, app)` |
| `tests/test_auth_accounts.py` | new (AC 1–3) |
| `tests/test_auth_join.py` | new (AC 4–15) |
| `tests/test_auth_cors.py` | two tests (AC 16) |
| `tests/test_cli_register_app.py` | one regression test (AC 17) |
| `tests/test_auth_token.py` | `test_token_not_member` re-parametrized (AC 18) |
| `docs/consumers.md` | new section "Accounts and joining an app" (fetch examples, consent rule, sequence, error table) |
| `docs/identity-architecture.md` | §2 Join/List, §3 `join_requests` → USR-03g, §4 rows, §6 libraries → USR-08, §7 risks (re-join, revival, O3), §8 D21 (owner) + D22 (proposed, pending owner) |
| `docs/deploy.md` | register-app semantics: a NULL default role stays NULL |

No migration, no new model, no change to `app/db/models.py`.

### Tests added / changed
- `tests/test_auth_accounts.py` (14): test_accounts_lists_own_device, test_accounts_fields_exact, test_accounts_excludes_other_device, test_accounts_excludes_revoked, test_accounts_hides_disabled_user, test_accounts_empty_device, test_accounts_previous_cookie_hides_newer_account, test_accounts_no_store_and_rotation, test_accounts_missing_cookie_401, test_accounts_unknown_cookie_401, test_accounts_missing_app_header_403, test_accounts_bad_origin_403, test_accounts_rate_limited_429, test_accounts_logs_no_email
- `tests/test_auth_join.py` (29 functions, 32 cases): test_join_open_assigns_default_role, test_join_then_token_200, test_join_idempotent_no_write, test_join_existing_other_role_no_write, test_join_org_scope_role_is_not_membership, test_join_revives_expired_assignment, test_join_unverified_403, test_join_policy_refused_403[closed / approval] (= plan's test_join_closed_403 + test_join_approval_unavailable_403), test_join_null_default_role_403, test_join_default_role_with_admin_permission_403, test_join_admin_refused_even_if_open, test_join_app_mismatch_403, test_join_other_device_account_401, test_join_unknown_account_401, test_join_revoked_account_401, test_join_previous_cookie_newer_account_401, test_join_disabled_user_401, test_join_already_member_of_closed_app, test_join_locks_app_before_reads_and_writes, test_join_audit_ids_only, test_join_default_recorder_logs_ids_only, test_join_no_store_and_rotation, test_join_error_after_rotation_rolls_back, test_join_rate_limited_429, test_join_missing_cookie_401, test_join_missing_app_header_403, test_join_bad_origin_403, test_join_body_validation_422 (3 cases), test_join_disabled_app_403
- `tests/test_auth_cors.py`: test_preflight_accounts_and_join_from_registered_origin, test_accounts_and_join_foreign_origin_not_echoed
- `tests/test_cli_register_app.py`: test_null_default_role_kept_after_permission_add
- `tests/test_auth_token.py`: test_token_not_member re-parametrized (open+default → true, open+NULL → false, approval → false, closed → false)

Notes on test mechanics:
- The fake audit recorder replaces the module-level `auth_router._audit_recorder` via `monkeypatch` (what `get_audit_recorder` returns) instead of `app.dependency_overrides`: `make_client` does not expose the app. Same effect, smaller diff.
- The lock-order test asserts the `FROM apps … FOR NO KEY UPDATE` statement precedes every statement touching `role_assignments`, `FROM roles` or `account_sessions`, and that an `INSERT INTO role_assignments` happened.
- The org-scope test was cheap (an `Organization` row), so it is included.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`:
  - `ruff check .` → All checks passed!
  - `ruff format --check .` → 345 files already formatted
  - `mypy app` → Success: no issues found in 85 source files
  - `pytest` → 1038 passed in 39.38s
- `alembic check` (against local `org_admin`, upgraded to head) → No new upgrade operations detected.
- Local Postgres was already running; nothing set up.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Plan F1–F6 stand (USR-03g approval flow, per-device account cap, shared `ErrorEnvelope`/`operation_id`s, origin exclusivity on admin `PATCH /apps`, CLAUDE.md R11 cookie wording vs D20, re-join deny-list).
- `docs/deploy.md` env table does not list the per-IP rate-limit keys (`TOKEN_RATE_LIMIT`, new `ACCOUNTS_*`, `JOIN_*`); they are documented only in `.env.example`.
- D22 (R9 exception for `GET /auth/accounts`) is recorded as "proposed, pending owner"; owner decisions O1–O4 from the plan still need confirmation.

### Scope self-check
- Could the diff be smaller? No — every file is in the plan's list; `JoinService` reuses `lock_app`, `get_active_account`, `AuthzService.effective_roles`, `role_has_admin_permission` and `_grant_locked`.
- Diff size: production (app + .env.example) +309 / -9; tests +883 / -4; docs +83 / -11.
