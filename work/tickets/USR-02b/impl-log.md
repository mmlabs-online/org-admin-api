# Implementation log — USR-02b

## Attempt 1 — 2026-11-11

Slice 02b-1 only (gate, errors, carry-overs, apps CRUD + origins). 02b-2/02b-3 not implemented.

### Files touched
| File | Required because |
|---|---|
| `app/core/config.py` | `admin_rbac_api_enabled`; `_admin_users_api_not_public` replaced by `_admin_apis_gate` (both flags refused in staging/production; any flag on needs explicit `APP_ENV`) — AC 1 |
| `.env.example` | `ADMIN_RBAC_API_ENABLED=` (no value) — R5, AC 1 |
| `app/core/errors.py` | `UnprocessableError` (422, `unprocessable`) — AC 2 |
| `app/main.py` | conditional `include_router(rbac_router)` when the flag is on — AC 1 |
| `app/modules/rbac/authz.py` | `permits` `AbstractSet \| Sequence` + `str` → `TypeError`; `effective_roles` prefix filter — AC 3, 4 |
| `app/modules/rbac/models.py` | `ADMIN_APP_KEY = "admin"` constant (no schema change) — AC 5, 8 |
| `app/modules/rbac/origins.py` (new) | pure `normalize_origin` / `normalize_origins`, generic error message — AC 9 |
| `app/modules/rbac/schemas.py` (new) | `AppCreate`, `AppUpdate` (`extra="forbid"`, null only for `default_role_id`), `AppRead` — AC 6, 7, 9 |
| `app/modules/rbac/repository.py` | `list_apps`, `add_app`, `save`, `delete_app` (savepoints), `app_has_assignments`, `set_default_role_if_null`, `get_role`, `role_has_admin_permission`; `add_role` in a savepoint; `add_role_permissions` via `ON CONFLICT DO NOTHING` — R1, AC 2, 5–8 |
| `app/modules/rbac/service.py` | `build_default_role(app_key, actor=None)` → `_build_for_app(app)`; refuses `admin`; `IntegrityError` → 409; conditional default set + refresh. New `AppService` (list/get/create/update/delete, `admin` locks, default-role 422s) — AC 2, 5–8, 10 |
| `app/modules/rbac/router.py` (new) | 5 routes under `/apps`, `openapi_extra=permission(...)`, HTTP only — AC 6, 10 |
| `docs/adr/0003-rbac-keys-and-matching.md` | amended §1, §3 (str guard + edge-case table), §4, §7, §8, new §9 (default role never `is_admin`; origins normal form) |
| `tests/test_config.py` (new) | config gate tests — AC 1 |
| `tests/test_errors.py` | `test_unprocessable_error_envelope` — AC 2 |
| `tests/test_rbac_authz.py` | permits rows, str test, foreign-prefix roles — AC 3, 4 |
| `tests/test_rbac_default_role.py` | admin refusal, insert race, conditional default — AC 5 |
| `tests/test_rbac_origins.py` (new) | origins unit tests — AC 9 |
| `tests/test_rbac_apps_api.py` (new) | apps endpoint tests — AC 1, 2, 6–10 |

### Tests added / changed
- `test_config.py` › `test_rbac_flag_refused_in_staging_and_production`, `test_admin_flag_requires_explicit_app_env` (both flags), `test_admin_flag_empty_app_env_refused` (both flags), `test_no_flag_without_app_env_is_ok`, `test_flags_with_explicit_app_env_ok`
- `test_errors.py` › `test_unprocessable_error_envelope`
- `test_rbac_authz.py` › `test_permits` (+6 rows), `test_permits_rejects_str_granted`, `test_effective_roles_drop_foreign_prefix`
- `test_rbac_default_role.py` › `test_build_refuses_admin_app`, `test_build_role_insert_race_is_409`, `test_build_does_not_overwrite_concurrent_default`
- `test_rbac_origins.py` › `test_normalize_origin_accepts` (10), `test_normalize_origin_rejects` (23, asserts the exact generic message), `test_normalize_origins_dedupes_and_limits`
- `test_rbac_apps_api.py` › `test_apps_api_not_mounted_by_default`, `test_rbac_routes_declare_permissions`, `test_list_apps_paginated_and_sorted`, `test_get_app`, `test_get_app_unknown_404`, `test_app_read_has_exact_keys`, `test_create_app_builds_member_role`, `test_create_app_duplicate_key_409`, `test_create_app_integrity_race_is_409`, `test_create_app_validation_422` (13), `test_create_app_origins_normalized`, `test_patch_app_fields`, `test_patch_app_rejects_key_and_unknown_fields` (3), `test_patch_app_null_only_for_default_role` (5), `test_patch_app_bad_origin_422`, `test_patch_unknown_app_404`, `test_patch_default_role_set_and_clear`, `test_patch_default_role_unknown_or_foreign_422`, `test_patch_default_role_with_admin_permission_422`, `test_admin_app_cannot_be_deleted`, `test_admin_app_locked_fields_409` (4), `test_admin_app_same_value_patch_ok`, `test_delete_app`, `test_delete_unknown_app_404`, `test_delete_app_with_assignments_409`

### Checks (actual output summary)
Local Postgres: `service postgresql start` (cluster already had `org_admin` / `org_admin_test`). Run with `APP_VERSION`, `DATABASE_URL`, `APP_ENV` unset.
- make check (ruff, format, mypy, pytest):
  - `ruff check .` → `All checks passed!`
  - `ruff format --check .` → `147 files already formatted`
  - `mypy app` → `Success: no issues found in 48 source files`
  - `pytest -q` → `242 passed in 6.37s` (baseline before the change: 142 passed)
- alembic check (`DATABASE_URL=…/org_admin_test uv run alembic check`): `No new upgrade operations detected.` (no migration, as planned)
- `grep -n rbac app/api/router.py` → nothing; `grep -n "# R4" app/modules/rbac/service.py` → builder, create, update, delete.

### Out-of-plan files
- `work/tickets/USR-02b/plan.md` — whitespace-only: `ruff format --check .` formats the Python code block in Approach §1, and the plan as committed made `make check` fail at lint. Reformatted that one block with `ruff format` (content unchanged) so `make check` is green.

### Follow-ups (noticed, not done)
- `ruff format` checks Python blocks inside Markdown under `work/`/`docs/`; planners writing code blocks can break `make check`. Either exclude `*.md` in `pyproject.toml` (`[tool.ruff] extend-exclude`) or have the planner run `ruff format` on its plan.
- The `admin` same-value PATCH with `name` is accepted as a normal rename (only `join_policy`/`default_role_id`/`status` are locked, per plan A5).
- From the plan (unchanged): USR-03 origin binding/read-time re-validation; ADM-002 release checklist; DB-level `app_key` + composite FK; `RuntimeError` instead of `ValidationError` for refused flags.
- Note (plan A9): a local `.env` with an admin flag on and no `APP_ENV` now refuses to start; `.env.example` has `APP_ENV=local`.

### Scope self-check
- Could the diff be smaller? No — every file is in the plan's list (plus the whitespace-only plan.md fix).
- Diff size: ~+1050 / -37 lines (production ≈ +420, ADR +37, tests ≈ +590).
