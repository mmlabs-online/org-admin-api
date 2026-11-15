# Review — ADM-002b

## Attempt 1 — 2026-11-15
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff "All checks passed!"; ruff format "381 files already formatted"; mypy --strict "Success: no issues found in 89 source files"; pytest **1274 passed** in 88.20s. Matches impl-log (1274 passed; the format count differs by one file, 380 vs 381, which does not matter).
- alembic check: "No new upgrade operations detected." (no migration in this ticket, as planned).
- AC4: `git log origin/main..HEAD` has `a31f26f` start planning, `7b5d416` consults, `4f504d3` plan, `2f2231e` code. The guards, user-status guards, origin checks and flag removal are all in `2f2231e`. No commit mounts the routers without the guards. Working tree clean.

### Acceptance criteria
- [x] AC1, guard matrix allowed + refused per site: `tests/test_admin_guards.py:286-319` `test_guard_matrix` runs 23 sites × {leaf, super}. A leaf caller gets 403 `admin_wildcard_required` with `details {"required":"admin:*"}`, and a full snapshot of 7 tables is unchanged (`_STATE_SQL` l.46-60). Each plan §3 table row checked against the code and a test id:
  - `AppService.create` G6: `service.py:295-301` / `test_app_create_origin_taken_409[active|disabled]`, `test_app_create_admin_origin_taken_409`, `test_loopback_origins_exempt_from_uniqueness`.
  - `AppService.update` G1 + admin origins + G6: `service.py:319-362` / `app_admin_patch`, `app_admin_origins_patch`, `test_admin_app_same_value_patch_leaf_ok`, `test_admin_origins_patch_super_validated`, `…_loopback_refused_in_production`, `test_app_patch_origin_taken_409`.
  - `PermissionService.create`: `service.py:605-606` / `permission_create_admin_app`, `…_is_admin`, `…_wildcard`.
  - `PermissionService.update` S + G3: `service.py:633-649` / `permission_update_admin_app`, `permission_is_admin_false_to_true`, `…_true_to_false`, `test_permission_description_only_leaf_ok`, `test_is_admin_flip_on_assigned_role_409`.
  - `PermissionService.delete`: `service.py:667-668` / `permission_delete_admin_app`.
  - `RoleService.create/update/delete`: `service.py:706-707, 726-728, 741-742` / `role_create_admin_app`, `role_rename_admin_app`, `role_delete_admin_app`.
  - `RoleService.replace_permissions` (wildcard 422 → diff → S → 409s → G3): `service.py:776-801` / `role_put_admin_app`, `role_put_adds_is_admin`, `role_put_removes_is_admin`, `test_role_put_unchanged_super_admin_ok`, `test_wildcard_only_on_super_admin_422`, `test_is_admin_onto_assigned_role_409`.
  - `_grant_locked`, insert and renewal: `service.py:937-962`. The guard sits before `find_assignment`, and `granted_by = actor_user_id(actor)` is set on both branches / `grant_super_admin`, `grant_admin_app_role`, `grant_is_admin_role`, `test_grant_renewal_branch_guarded` (leaf refused with the row unchanged; super renews with `granted_by == actor.id`), `test_granted_by_is_actor`, `test_sock_puppet_grant_refused[3]`.
  - `revoke`: `service.py:989-996` / `revoke_admin_app_role`, `revoke_is_admin_role`, `test_self_revoke_admin_role_403`, `test_last_super_admin_revoke_409`.
  - `UserService.set_status`: `users/service.py:30-61` / `disable_privileged_user`, `reenable_disabled_staff`, `reenable_expired_staff`, `test_self_status_change_403[2]`, `test_last_super_admin_disable_409`.
- [x] AC2, self-grant, `is_admin` 409, wildcard only on super-admin, last super-admin, self-disable: the tests listed above, plus `test_unverified_super_admin_not_counted[revoke|disable]`, `test_disable_one_of_two_super_admins_ok`, `test_self_grant_non_admin_app_role_ok`, and in `tests/test_rbac_locking.py` `test_concurrent_disable_pair_serialized`, `test_concurrent_revoke_and_disable_serialized`, `test_user_status_takes_admin_lock_first`. All pass.
- [x] AC3, flags gone:
  - The grep over `app tests .env.example docs` finds only `tests/test_config.py:16-19` (which asserts the flags are absent) and the history line in `docs/adr/0008-admin-api-authentication.md:67-68`.
  - `app/main.py:90-92` mounts the routers unconditionally. `config.py` drops the fields and `_admin_apis_gate`. `.env.example:68-69` was updated.
  - `test_route_auth_classes.py` runs on plain `create_app()`.
  - `tests/conftest.py` `_settings()` rejects unknown override keys.
- [x] AC4, one commit: `2f2231e` (see Checks).
- [x] AC5, `make check` green: run by the reviewer, above.
- [x] AC6, audit recorded: the impl-log has the §8 output (0 rows, user ids only), and `docs/deploy.md:348+` has the same SQL in "Pre-ADM-002b audit (read-only)".
- [x] AC7, docs off in production by default:
  - `config.py` `docs_enabled` (`api_docs_enabled` if set, else `app_env != "production"`).
  - `main.py:40-45` sets `docs_url`/`openapi_url` to None when docs are off.
  - Tests: `test_config.py` `test_docs_enabled_defaults[4]`, `test_docs_enabled_override[2]`; `test_route_auth_classes.py` `test_docs_off_in_production` (no doc routes, 404 envelope, every API router still mounted); `docs/deploy.md:98`.

Specific checks requested:
- **`actor` required, no default:** every mutator and `_grant_locked` take `*, actor: Actor` (mypy --strict passes). Callers pass it:
  - routers: `actor=principal`;
  - `register` / `bootstrap_super_admin`: `SYSTEM_CLI` (`service.py:463, 474, 487, 901`);
  - join: `SYSTEM_JOIN` (`membership/service.py:90-92`).
  The only remaining `actor: uuid.UUID | None = None` is `RoleCatalogService.build_default_role` (`service.py:240`). It has no production caller, the plan allows this ("if it has callers"), it is listed as a deviation, and it does not write by actor.
- **Renewal branch guarded and sets `granted_by`:** yes (`service.py:937-946`).
- **Diff-based PUT, both directions:** `added`/`removed` at `service.py:782-791`; tests `role_put_adds_is_admin`, `role_put_removes_is_admin`, `test_role_put_unchanged_super_admin_ok`.
- **`is_admin` flips, both directions:** `service.py:633-637`; matrix rows `permission_is_admin_false_to_true` and `…_true_to_false`.
- **Re-enable of a privileged user needs `admin:*`:** `users/service.py:42-43` runs for any status change after the no-op return; tested by `reenable_disabled_staff` and `reenable_expired_staff`.
- **Last super-admin counts only active + verified:** `repository.py:441-451` adds `email_verified_at IS NOT NULL` next to `status='active'`.
- **Lock order app → user:** `users/service.py:34-35`, then `test_user_status_takes_admin_lock_first`. This matches `bootstrap_super_admin` (`service.py:884`).
- **Disable revokes sessions in the same transaction:** `users/service.py:57-59` calls `revoke_all_accounts_of_user` on the request session after the flush. `test_disable_revokes_account_sessions` checks both devices' account sessions are revoked, the other user's session and the device sessions are kept, and `/auth/token` returns 401.
- **Origin collision on create and update, including `admin`:** `app_keys_holding_origins` covers any status, includes `admin`, and excludes only its own key (`repository.py:41-50`).
- **`test_health.py` change justified:** the old `test_docs_enabled_in_production` asserted the opposite of plan A1. It is now `test_docs_disabled_in_production_by_default` (it also checks the override) and is listed under Out-of-plan files in the impl-log.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the actor is an HTTP-free dataclass (`app/modules/rbac/actor.py`). Services raise `AppError` subclasses (`errors.py:62-84`). The new EXISTS queries live in the repository.
- R3: router-level `Depends(current_admin)` is kept; enumeration runs with every router mounted.
- R5: `API_DOCS_ENABLED=` is in `.env.example`.
- R6: the new codes use the envelope.
- R11: no emails appear in errors.

### Nits (do not block)
- No matrix row re-enables a disabled **super-admin**. The path is the same as `reenable_expired_staff` (an admin-app role), so coverage holds, but the plan names that case.
- `tests/test_admin_guards.py` defines `_PRODUCTION` (l.~620) after the test that uses it. This works at runtime but reads oddly.
- Every `set_status` now serializes on `lock_app("admin")`, even for ordinary users. That is fine at staff scale, and worth knowing.

### Follow-ups (out of scope)
- `build_default_role`: drop the unused `actor` parameter, or make it a required `Actor` once USR-05 calls it.
- product-manager: spec §6.1 line 85, since docs are now off in production by default (A1).
- A DB-level cross-app origin-uniqueness guard (A8), and an optional re-read of `admin:*` after the lock (A7).
- ADM-004 audit log next; ADM-002d scope as already split.
