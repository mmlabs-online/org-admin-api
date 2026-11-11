# Implementation log — USR-01b

## Attempt 1 — 2026-11-11

### Files touched
| File | Required because |
|---|---|
| `app/core/config.py` | `admin_users_api_enabled` (default false) + `_admin_users_api_not_public` validator refusing it in staging/production (AC 5, R5) |
| `.env.example` | `ADMIN_USERS_API_ENABLED=` without value (R5) |
| `app/core/permissions.py` (new) | `permission(key)` → `{"x-permission": key}` for `openapi_extra` (AC 4, R3) |
| `app/modules/users/repository.py` | `escape_like`, `get`, `list` (email ILIKE with `\`/`%`/`_` escaping, status filter, `created_at desc, id`, filtered `total`) (AC 1, 2) |
| `app/modules/users/schemas.py` (new) | `UserRead` (exact 7 fields, `from_attributes`), `UserStatusUpdate` (`extra="forbid"`, required `status`) (AC 2, 3) |
| `app/modules/users/service.py` (new) | `UserService.get/list/set_status`; `NotFoundError("User not found")`; same-status PATCH does no write; R4 / USR-03 marker comments (AC 1, 2) |
| `app/modules/users/router.py` (new) | `GET /users`, `GET /users/{user_id}`, `PATCH /users/{user_id}` with declared permissions (AC 1, 2, 4) |
| `app/main.py` | Mount `users_router` only when `settings.admin_users_api_enabled` (AC 5); `app/api/router.py` unchanged |
| `tests/test_users_admin.py` (new) | All planned tests (AC 6, R8) |

### Tests added / changed
- tests/test_users_admin.py › test_users_api_not_mounted_by_default
- tests/test_users_admin.py › test_users_api_flag_refused_in_staging_and_production[staging|production]
- tests/test_users_admin.py › test_users_api_flag_allowed_in_local_and_test[local|test]
- tests/test_users_admin.py › test_list_orders_by_created_at_desc_then_id
- tests/test_users_admin.py › test_list_paginates_and_total_respects_filters
- tests/test_users_admin.py › test_list_email_filter_is_case_insensitive_contains
- tests/test_users_admin.py › test_list_email_filter_escapes_like_wildcards
- tests/test_users_admin.py › test_list_status_filter
- tests/test_users_admin.py › test_list_rejects_bad_query (6 cases)
- tests/test_users_admin.py › test_get_user
- tests/test_users_admin.py › test_get_missing_user_returns_404
- tests/test_users_admin.py › test_get_bad_uuid_returns_422
- tests/test_users_admin.py › test_patch_disables_and_reenables
- tests/test_users_admin.py › test_patch_same_status_is_noop
- tests/test_users_admin.py › test_patch_missing_user_returns_404
- tests/test_users_admin.py › test_patch_rejects_invalid_bodies (5 cases)
- tests/test_users_admin.py › test_user_read_has_exact_keys
- tests/test_users_admin.py › test_users_routes_declare_permissions

Deviations inside the test file (no scope change):
- `test_users_routes_declare_permissions` enumerates operations via `app.openapi()["paths"]` (checking each operation's `x-permission`) instead of `app.routes`: the installed FastAPI 0.162.2 exposes included routers as lazy `_IncludedRouter` wrappers, so `app.routes` does not list their `APIRoute`s. The assertion is the same (exact set of (method, path) → permission).
- `test_users_api_flag_refused_in_staging_and_production` passes `argon2_memory_kib=65536, argon2_time_cost=3` so the existing Argon2-floor validator (tests run with cheap Argon2 env vars) does not fail first; the test then matches "ADMIN_USERS_API_ENABLED".
- In `test_patch_rejects_invalid_bodies` the extra-field cases use `"status": "disabled"` (not `"active"`) so a wrongly accepted body would visibly change the row.

### Checks (actual output summary)
Postgres: local cluster already running; no setup needed. Run with `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`.
- make check (ruff, format, mypy, pytest):
  - `uv run ruff check .` → `All checks passed!`
  - `uv run ruff format --check .` → `110 files already formatted`
  - `uv run mypy app` → `Success: no issues found in 40 source files`
  - `uv run pytest -q` → `79 passed in 2.19s`
- alembic check (no model change; run as a sanity check): `No new upgrade operations detected.`

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- FU1–FU6 from plan.md stand (ADM-002 auth + remove flag, USR-03/03b disabled-user enforcement, ADM-004 audit, owner confirmation of §6.1, pg_trgm index, `/openapi.json` in production).
- ADM-002: any route-enumeration tooling must account for FastAPI ≥ 0.16x lazy `_IncludedRouter` (iterate the OpenAPI schema or the original routers, not `app.routes`).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +487 / -1 lines (app ≈ 170, tests ≈ 317)
