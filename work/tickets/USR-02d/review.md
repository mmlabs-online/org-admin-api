## Attempt 1 — 2026-11-11
Verdict: PASS

### Checks (run by reviewer)
- make check (APP_VERSION/DATABASE_URL/APP_ENV unset; local Postgres already accepting connections): `ruff check` → All checks passed!; `ruff format --check` → 158 files already formatted (impl-log says 157: the count includes every `.py` in the tree, no file is unformatted, not a mismatch in outcome); `mypy app` → Success: no issues found in 48 source files; `pytest -q` → **348 passed** in 12.40s (matches impl-log).
- alembic check (no migration in the diff; run as a guard): `DATABASE_URL=…/org_admin_test uv run alembic check` → `No new upgrade operations detected.`

### Acceptance criteria
- [x] AC1 — 8 routes, permissions per route, flag-gated, `# R4` markers + `actor=None` — evidence: `app/modules/rbac/router.py:70-165` (8 handlers, HTTP only); `tests/test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (exact 13-entry map, passes); `test_rbac_permissions_api.py › test_permissions_api_not_mounted_by_default`; `# R4` markers in `service.py` on permission create/update/delete and role create/update/delete/PUT; every new mutating method takes `actor: uuid.UUID | None = None`.
- [x] AC2 — prefix 422 `prefix_mismatch`, wildcard forced/422, `key`/`is_system`/`id`/`app_id` refused — evidence: `service.py` `_prefix_mismatch`/`_wildcard_must_be_admin` and `PermissionService.create/update`, `RoleService.create`; schemas `extra="forbid"` (`schemas.py:376-446`); tests `test_create_permission_prefix_mismatch_422` (×2), `test_create_wildcard_forced_admin` (×2, absent and null), `test_create_wildcard_not_admin_422`, `test_patch_wildcard_not_admin_422` (`admin:*` and `stoktok:*`), `test_create_permission_validation_422` (×11), `test_create_role_prefix_mismatch_422`, `test_create_role_validation_422`, `test_patch_permission_rejects_key`, `test_patch_role_rejects_key_and_is_system`.
- [x] AC3 — PUT dedupes, lists every unknown/other-app key, `[]` allowed, idempotent, shared helper — evidence: `RoleService.replace_permissions` (`dict.fromkeys`, sorted `missing` → one `UnprocessableError` with `key` per detail); `_set_role_permissions` used by `_build_for_app` and PUT (`service.py`); tests `test_put_role_permissions_unknown_keys_422` (2 unknown + 1 other-app key, sorted, role unchanged), `test_put_role_permissions_dedupes_and_is_idempotent` (SQL capture: no INSERT/DELETE/UPDATE on repeat, capture proven live by the lock statement), `test_put_role_permissions_empty_list`, `test_put_role_permissions_replaces_set`, `test_put_role_permissions_format_422` (×5).
- [x] AC4 — 409 guards, rows unchanged — evidence: `test_delete_admin_wildcard_409`, `test_delete_permission_held_by_role_409`, `test_delete_system_role_409`, `test_delete_default_role_409`, `test_delete_role_with_assignments_409`, `test_put_super_admin_without_wildcard_409` (plus 200 with `admin:*` kept), `test_put_admin_permission_on_default_role_409` (`stoktok:*` and an `is_admin` key), `test_patch_permission_admin_while_default_holds_409`; each re-reads the rows.
- [x] AC5 (ticket) / plan AC8 — D19 origins, no echo — evidence: `app/modules/rbac/origins.py:37-41` (suffix rule with leading dot, https only, default port checked before the default-port drop, `api.mmlabs.online` refused, loopback any scheme/port); `test_normalize_origin_accepts` (11), `test_normalize_origin_rejects` (38, exact generic message incl. `evilmmlabs.online`, `mmlabs.online.attacker.com`, `0x7f.1`, `a.123`, `127.0.0.2`), `test_normalize_origins_dedupes_and_limits`, `test_patch_app_non_mmlabs_origin_422` (`"example.com" not in resp.text`, row unchanged).
- [x] Carry-over (1) TOCTOU / plan AC5 — evidence: `repository.py` `lock_app` (`with_for_update(key_share=True)` → `FOR NO KEY UPDATE`, `populate_existing`); `_locked_app` first in all 7 new mutations, `AppService.update/delete`, `build_default_role`; `tests/test_rbac_locking.py › test_lock_app_blocks_lockers_not_key_share` (real second engine: 55P03 on `FOR NO KEY UPDATE NOWAIT`, `FOR KEY SHARE NOWAIT` succeeds), `test_mutations_lock_app_first` (×9, first statement is the lock), `test_build_default_role_locks_app`; sequential race sides `test_default_then_put_admin_permission_409`, `test_put_admin_permission_then_set_default_422`.
- [x] Carry-over (2) rename — evidence: `AppService.list_apps` (`service.py:121`), router call site updated; no `def list(` left in `rbac/service.py`; mypy green.
- [x] Carry-over (3) savepoint + `IntegrityError` → 409 — evidence: `repository.apply_role_permission_diff` (`begin_nested`), `_set_role_permissions` maps to `ConflictError`; `test_rbac_default_role.py › test_build_permission_fk_race_is_409` and `test_put_role_permissions_integrity_race_is_409` (session usable afterwards); existing default-role tests green.
- [x] Carry-over (4) numeric/hex last label — evidence: covered by the suffix rule; `https://0x7f.1`, `https://a.123` in the reject list.
- [x] R8 per-endpoint tests (happy, 404 incl. other-app id and unknown id, validation, conflict) — evidence: test list above plus `test_list_permissions_paginated_and_sorted` (`limit=101` → 422, only this app's rows), `test_list_permissions_unknown_app_404`, `test_patch_permission_404` (non-UUID → 422), `test_delete_permission_404`, `test_patch_role_404`, `test_delete_role_404`, `test_put_role_permissions_404`, `test_create_*_duplicate_409`, `test_create_*_integrity_race_is_409`. 401/403 not applicable until ADM-002.
- [x] No migration; `alembic check` clean — evidence: above.

### Issues
None (no blocker, no should-fix).

Scope: every changed source/test/doc file is in the plan's file list; `work/` files are workflow artifacts. Out-of-plan files: none.

### Rules R1–R11
No violations found.
- R1: ORM only in `repository.py`; services raise `NotFoundError`/`ConflictError`/`UnprocessableError`; router builds schemas and calls services only.
- R2: no schema change; `alembic check` clean.
- R3: class 1, `openapi_extra=permission(...)` on all 8 routes, mounted only with `ADMIN_RBAC_API_ENABLED`; `# ADM-002 guard` markers at the escalation sites.
- R4: `# R4` markers on every new mutation (ADM-004 not landed).
- R5/R11: nothing new in config; origin errors never echo; 422 details carry only format-validated catalog keys (documented in `errors.py` and ADR-0003 §10).
- R6: envelope via `AppError`s; 201 create, 204 delete, 404/409/422.
- R7: diff stays inside the plan.
- R9/D18: `{key}` paths, UUID ids, `Page[PermissionRead]` with `PageDep`, PATCH for partial updates, PUT for full replacement of a sub-collection.

### Nits (do not block)
- `_capture_sql` is duplicated in `tests/test_rbac_roles_api.py` and `tests/test_rbac_locking.py` (already in impl-log follow-ups).
- `test_mutations_lock_app_first` indexes `statements[0]`; an empty capture would raise `IndexError` instead of a readable assertion (`test_build_default_role_locks_app` already guards with `statements and …`).
- `PermissionService.update` detects the wildcard with `perm.key.endswith(":*")`; equivalent to `== f"{app.key}:*"` given the key format, but the explicit form would mirror `create`.

### Follow-ups (out of scope)
- As listed in plan.md: ADM-002 guard sites (incl. `admin:*` only on `admin:super-admin`, all `admin`-app permissions `is_admin`, reserving `<app>:member`); USR-02e catalog route-map entry and own schema; USR-02c / USR-05 / CLI writers must call `lock_app` first (consider a DB trigger for the default-role invariant); USR-03 loopback echo per environment; `OrganizationService.list` / `UserService.list` rename.
- A role PUT does not bump `roles.updated_at` (plan assumption 9).
