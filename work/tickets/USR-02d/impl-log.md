# Implementation log — USR-02d

## Attempt 1 — 2026-11-11

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/origins.py` | AC 8 / D19: mmlabs hosts on https + default port only, `api.mmlabs.online` refused, loopback http(s) any port; new generic message; docstring |
| `app/modules/rbac/schemas.py` | AC 1–3: `PermissionCreate/Update/Read`, `RoleCreate/Update/Read`, `RolePermissionsReplace` (`extra="forbid"`, no control chars, explicit-null refusal) |
| `app/modules/rbac/repository.py` | R1, AC 2–6: `lock_app` (`FOR NO KEY UPDATE`, `populate_existing`), `page_permissions`, `get_permission`, `get_permission_by_key`, `permissions_by_keys`, `add_permission`/`delete_permission`/`delete_role` (savepoints), `permission_held_by_any_role`, `role_holds_permission`, `role_has_assignments`, `apply_role_permission_diff` (savepoint) |
| `app/modules/rbac/service.py` | AC 1–7: `_set_role_permissions` shared by `_build_for_app` and PUT; `_locked_app`; `build_default_role`, `AppService.update/delete` lock first; `AppService.list` → `list_apps`; new `PermissionService`, `RoleService` (`# R4` markers, `# ADM-002 guard` markers, `actor=None`) |
| `app/modules/rbac/router.py` | AC 1, 7: 8 routes, `list_apps` call site, docstring |
| `app/core/errors.py` | AC 3: `UnprocessableError` docstring — details may carry a format-valid catalog `key` |
| `docs/adr/0003-rbac-keys-and-matching.md` | Amended header; §1/§2 prefix + wildcard 422s; §7 shared diff helper / PUT on member overwritten by rebuild; §9 D19 origins + invariant on every path; new §10 lock + guard catalogue |
| `tests/test_rbac_origins.py` | AC 8: new accept/reject lists and message |
| `tests/test_rbac_apps_api.py` | AC 1, 8: 13-route map; `test_patch_app_non_mmlabs_origin_422` |
| `tests/test_rbac_default_role.py` | AC 6: `test_build_permission_fk_race_is_409` |
| `tests/test_rbac_permissions_api.py` (new) | AC 1, 2, 4, 9 |
| `tests/test_rbac_roles_api.py` (new) | AC 2–6, 9 |
| `tests/test_rbac_locking.py` (new) | AC 5 |

### Tests added / changed
- `test_rbac_origins.py` › `test_normalize_origin_accepts` (11 cases), `test_normalize_origin_rejects` (23 existing + 15 new), `test_normalize_origins_dedupes_and_limits`
- `test_rbac_apps_api.py` › `test_rbac_routes_declare_permissions` (13 entries), `test_patch_app_non_mmlabs_origin_422`
- `test_rbac_default_role.py` › `test_build_permission_fk_race_is_409`
- `test_rbac_permissions_api.py` › `test_permissions_api_not_mounted_by_default`, `test_list_permissions_paginated_and_sorted`, `test_list_permissions_unknown_app_404`, `test_permission_read_has_exact_keys`, `test_create_permission`, `test_create_wildcard_forced_admin` (×2), `test_create_wildcard_not_admin_422`, `test_create_permission_prefix_mismatch_422` (×2), `test_create_permission_validation_422` (×11), `test_create_permission_duplicate_409`, `test_create_permission_unknown_app_404`, `test_create_permission_integrity_race_is_409`, `test_patch_permission`, `test_patch_permission_null_is_admin_422`, `test_patch_permission_rejects_key` (×2), `test_patch_wildcard_not_admin_422`, `test_patch_permission_admin_while_default_holds_409`, `test_patch_permission_404`, `test_delete_permission`, `test_delete_admin_wildcard_409`, `test_delete_permission_held_by_role_409`, `test_delete_permission_404`
- `test_rbac_roles_api.py` › `test_role_read_has_exact_keys`, `test_create_role`, `test_create_role_prefix_mismatch_422`, `test_create_role_validation_422` (×9), `test_create_role_duplicate_409`, `test_create_role_unknown_app_404`, `test_create_role_integrity_race_is_409`, `test_patch_role_name`, `test_patch_role_rejects_key_and_is_system` (×3), `test_patch_role_404`, `test_delete_role`, `test_delete_system_role_409`, `test_delete_default_role_409`, `test_delete_role_with_assignments_409`, `test_delete_role_404`, `test_put_role_permissions_replaces_set`, `test_put_role_permissions_dedupes_and_is_idempotent` (choice: **SQL capture** — `before_cursor_execute` on the test connection; the repeat PUT issues no INSERT/DELETE/UPDATE, and the capture is shown live by asserting the lock statement was seen), `test_put_role_permissions_empty_list`, `test_put_role_permissions_unknown_keys_422`, `test_put_role_permissions_format_422` (×5), `test_put_role_permissions_404`, `test_put_super_admin_without_wildcard_409`, `test_put_admin_permission_on_default_role_409`, `test_put_system_member_role_allowed`, `test_default_then_put_admin_permission_409`, `test_put_admin_permission_then_set_default_422`, `test_put_role_permissions_integrity_race_is_409`
- `test_rbac_locking.py` › `test_lock_app_blocks_lockers_not_key_share` (second engine: `FOR NO KEY UPDATE NOWAIT` → SQLSTATE 55P03; `FOR KEY SHARE NOWAIT` succeeds), `test_mutations_lock_app_first` (×9: 7 new mutations + PATCH/DELETE `/apps/{key}`; the request's very first statement is the app lock), `test_build_default_role_locks_app`

### Checks (actual output summary)
- Postgres: local cluster already running (`pg_isready` → accepting connections); nothing created.
- make check (APP_VERSION/DATABASE_URL/APP_ENV unset):
  - `uv run ruff check .` → `All checks passed!`
  - `uv run ruff format --check .` → `157 files already formatted`
  - `uv run mypy app` → `Success: no issues found in 48 source files`
  - `uv run pytest -q` → `348 passed in 11.27s`
- alembic check (no migration; run as a guard): `DATABASE_URL=…/org_admin_test uv run alembic check` → `No new upgrade operations detected.`

### Out-of-plan files
None. (No factory helper added to `tests/rbac_factories.py`.)

### Follow-ups (noticed, not done)
- The `_capture_sql` helper is duplicated in `test_rbac_roles_api.py` and `test_rbac_locking.py` (two copies; move to a shared test helper if a third user appears).
- Plan follow-ups stand (ADM-002 guard sites, USR-02e catalog map entry, USR-02c/USR-05/CLI must call `lock_app` first, USR-03 loopback echo, `OrganizationService.list` / `UserService.list` rename).
- A PUT does not touch `roles.updated_at` (plan assumption 9); the admin UI may want it later.

### Scope self-check
- Could the diff be smaller? No — every file and test is in the plan's list; ~640 production/doc lines (service ~250 of them, mostly the two new services), ~1030 test lines.
- Diff size: +1672 / -48 lines (13 files, before this log)
