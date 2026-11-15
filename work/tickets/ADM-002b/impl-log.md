# Implementation log — ADM-002b

## Attempt 1 — 2026-11-15

Order followed: `tests/test_admin_guards.py` was written first and run against the flags-on app
(`ADMIN_USERS_API_ENABLED=true ADMIN_RBAC_API_ENABLED=true`, before any guard): **49 failed,
29 passed** (every leaf/refusal case red, super cases green). Then the guards were implemented
(78/78 green), then the flags were removed and the suite was adjusted. Everything is one
uncommitted change (AC4: the orchestrator makes the single `ADM-002b: …` commit).

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/actor.py` (new) | G0: `SystemActor`, `SYSTEM_CLI` (privileged), `SYSTEM_JOIN` (not), `Actor`, `is_super`, `require_super`, `actor_user_id` |
| `app/core/errors.py` | `AdminWildcardRequiredError` (403, `details {"required": "admin:*"}`), `SelfAssignmentForbiddenError`, `SelfStatusChangeForbiddenError` (403), `LastSuperAdminError`, `RoleHasAssignmentsError` (409) |
| `app/modules/rbac/service.py` | Required `*, actor: Actor` on every mutator and `_grant_locked`; guard table §3 (G1–G6); `_validate_admin_origins` extracted from `set_admin_origins` and reused by `update` (`app_env` arg); `_origin_taken` shared by create/update/register/set-admin-origins; `granted_by = actor_user_id(actor)` on insert and renewal; `_guard_privileged` for grant + revoke; all eight `# ADM-002 guard` comments replaced |
| `app/modules/rbac/repository.py` | `permission_held_by_assigned_role`, `user_has_privileged_assignment` (expired rows included); `_counting_super_admin` requires `email_verified_at IS NOT NULL` |
| `app/modules/users/service.py` | `set_status(..., *, actor)`: `lock_app("admin")` → `get_for_update` → 404 → no-op → self 403 → G5 `admin:*` → last-super-admin 409 → write → `revoke_all_accounts_of_user` on disable |
| `app/modules/users/router.py` | `principal: AdminDep` → `actor=principal`; 409 in `responses`; docstring |
| `app/modules/rbac/router.py` | `principal: AdminDep` on every mutating route; `update_app` passes `app_env` from `request.app.state.settings`; 409 in `responses` where new 409s occur (`_with_409`); docstring |
| `app/modules/rbac/assignments_router.py` | `principal: AdminDep` on POST/DELETE; 409 in DELETE `responses`; docstring |
| `app/modules/rbac/catalog_router.py` | Docstring: flag reference dropped |
| `app/modules/membership/service.py` | `_grant_locked(..., actor=SYSTEM_JOIN)` |
| `app/core/config.py` | Flags and `_admin_apis_gate` deleted; `api_docs_enabled: bool \| None` + `docs_enabled` property |
| `app/main.py` | Three routers mounted unconditionally; `docs_url`/`openapi_url` from `settings.docs_enabled`; Swagger comment replaced |
| `.env.example` | Flag keys removed; `API_DOCS_ENABLED=` with comment |
| `tests/conftest.py` | `admin_user` on `make_client` / `make_strict_app` / `make_strict_client`; `_settings()` asserts every override key is in `Settings.model_fields` |
| `tests/test_admin_guards.py` (new) | Guard matrix + invariants (AC1, AC2) |
| `tests/test_users_admin.py` | Flag tests (not-mounted, staging/prod refusal, local allowed) and flag kwarg removed |
| `tests/test_route_auth_classes.py` | `FLAGS` removed, `_app()` = `create_app()`; `flags_client` → `client`; `test_docs_off_in_production` |
| `tests/test_config.py` | 5 flag-gate tests deleted; `test_admin_api_flags_removed`, `test_docs_enabled_defaults`, `test_docs_enabled_override` |
| `tests/test_rbac_apps_api.py`, `test_rbac_permissions_api.py` | Flag kwarg dropped; "not mounted by default" tests deleted |
| `tests/test_rbac_roles_api.py`, `test_auth_cors.py`, `test_admin_auth.py` | Flag kwargs dropped |
| `tests/test_role_catalog_api.py` | Flag kwarg dropped; `test_post_roles_flag_off_is_405` and `test_only_catalog_route_listed_by_default` deleted (both asserted the routers are unmounted; covered by `test_route_auth_classes.py`); two tests renamed to drop "flag" |
| `tests/test_rbac_assignments_api.py` | `actor` fixture (real user; `granted_by` is an FK) behind `rbac_client`; `test_flag_off_404` deleted; `granted_by is None` → actor id; revoke 409 code → `last_super_admin`; `_super_admin` marks the holder verified (A4) |
| `tests/test_rbac_locking.py` | `rbac_client` with a real actor; verified users in the guard-count test; `test_user_status_takes_admin_lock_first`, `test_concurrent_disable_pair_serialized`, `test_concurrent_revoke_and_disable_serialized` |
| `tests/test_cli_grant_super_admin.py` | Flag asserts removed; `test_grant_with_rbac_flag_off` → `test_grant_with_default_settings` |
| `docs/adr/0008-admin-api-authentication.md` | §6 wording; `## Amendment — admin RBAC guards (ADM-002b, 2026-11-15)` (actor model, G1–G6, codes, check order, accepted risks A7/A8, docs off in production) |
| `docs/identity-architecture.md` | §4: three admin rows no longer "flag-gated"; paragraph on the `admin:*` guards and codes |
| `docs/deploy.md` | "Admin access": users/RBAC live, docs off in production; "Pre-ADM-002b audit (read-only)" with the §8 SQL; Bootstrap: `ADMIN_RBAC_API_ENABLED` line removed; `API_DOCS_ENABLED` under "Required runtime config"; Register-app wording |

`app/cli.py`: **no change needed** — `register`, `set_admin_origins` and `bootstrap_super_admin` pass `SYSTEM_CLI` internally; `test_cli_register_app.py` / `test_cli_set_admin_origins.py` stay green unchanged.

### Tests added / changed
- `tests/test_admin_guards.py › test_guard_matrix[<site>-leaf|super]` — 23 sites × 2 = 46: `app_admin_patch`, `app_admin_origins_patch`, `permission_create_admin_app`, `permission_create_is_admin`, `permission_create_wildcard`, `permission_update_admin_app`, `permission_is_admin_false_to_true`, `permission_is_admin_true_to_false`, `permission_delete_admin_app`, `role_create_admin_app`, `role_rename_admin_app`, `role_delete_admin_app`, `role_put_admin_app`, `role_put_adds_is_admin`, `role_put_removes_is_admin`, `grant_super_admin`, `grant_admin_app_role`, `grant_is_admin_role`, `revoke_admin_app_role`, `revoke_is_admin_role`, `disable_privileged_user`, `reenable_disabled_staff`, `reenable_expired_staff`. Leaf → 403 `admin_wildcard_required` + `details == {"required": "admin:*"}` + full-table snapshot (`apps`, `permissions`, `roles`, `role_permissions`, `role_assignments`, `users`, `account_sessions`) unchanged; super → success code.
- `… › test_leaf_allowed_on_ordinary_objects`, `test_role_put_unchanged_super_admin_ok`, `test_permission_description_only_leaf_ok`, `test_admin_app_same_value_patch_leaf_ok`
- `… › test_is_admin_flip_on_assigned_role_409`, `test_is_admin_onto_assigned_role_409`, `test_wildcard_only_on_super_admin_422`
- `… › test_grant_renewal_branch_guarded`, `test_granted_by_is_actor`, `test_sock_puppet_grant_refused[super_admin|admin_app_leaf|app_wildcard]`, `test_self_grant_admin_role_403`, `test_self_grant_non_admin_app_role_ok`, `test_self_revoke_admin_role_403`
- `… › test_last_super_admin_revoke_409`, `test_unverified_super_admin_not_counted[revoke|disable]`, `test_last_super_admin_disable_409`, `test_disable_one_of_two_super_admins_ok`
- `… › test_admin_origins_patch_super_validated`, `test_admin_origins_patch_loopback_refused_in_production`, `test_app_create_origin_taken_409[active|disabled]`, `test_app_create_admin_origin_taken_409`, `test_app_patch_origin_taken_409`, `test_loopback_origins_exempt_from_uniqueness`
- `… › test_self_status_change_403[active-disabled|disabled-active]` (plan's `test_disable_self_403` / `test_enable_self_403`), `test_disable_revokes_account_sessions`, `test_enable_does_not_touch_sessions`, `test_disabled_user_pending_reset_token_unusable`
- Plan names covered by matrix ids: `test_role_put_adds/removes_is_admin_leaf_403` → `role_put_adds/removes_is_admin`; `test_permission_is_admin_*_leaf_403` → `permission_is_admin_*`; `test_admin_app_patch_leaf_403` → `app_admin_patch`; `test_disable_privileged_user_leaf_403` / `test_reenable_disabled_staff_leaf_403` → same-named sites.
- `tests/test_rbac_locking.py › test_user_status_takes_admin_lock_first`, `test_concurrent_disable_pair_serialized`, `test_concurrent_revoke_and_disable_serialized` (committed data + cleanup; a blocker holds the `admin` lock until both requests wait on it; exactly one 409 `last_super_admin`, one super-admin left)
- `tests/test_config.py › test_admin_api_flags_removed`, `test_docs_enabled_defaults[4]`, `test_docs_enabled_override[2]`
- `tests/test_route_auth_classes.py › test_docs_off_in_production`; `test_every_route_is_classified` etc. now on plain `create_app()`
- `tests/test_health.py › test_docs_disabled_in_production_by_default` (was `test_docs_enabled_in_production`)
- `tests/test_rbac_assignments_api.py › test_revoke_*_super_admin*` now expect `last_super_admin`; `test_assign_app_scope_201` expects `granted_by == actor`

### Pre-removal audit (Approach §8, read-only, local)
Seed list in 2a matches migration `5e2a9c7d1f04` (`admin:*` + the 8 leaf keys). Run with `psql -f` against both local databases (`BEGIN READ ONLY … ROLLBACK`):

```
##### org_admin
-- 1 admin-app assignments: (0 rows)
-- 2a admin-app permissions outside seed / not is_admin: (0 rows)
-- 2b admin-app roles other than super-admin: (0 rows)
-- 3 is_admin roles with assignments / default roles holding one: (0 rows)
-- 4 admin app:  key=admin | status=active | join_policy=closed | org_mode=none | default_role_id=NULL | origins={}
--   origins held by >1 app: (0 rows)
-- 5 disabled users with admin-app assignments: (0 rows)
##### org_admin_test (after migration)
identical: 0 rows in 1, 2a, 2b, 3, 4b, 5; admin = active | closed | none | NULL | {}
```

Nothing to clean up locally. The same block is in `docs/deploy.md` › "Pre-ADM-002b audit (read-only)" for the owner (no ops command in this ticket).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`:
  - `ruff check`: All checks passed!
  - `ruff format --check`: 380 files already formatted
  - `mypy --strict`: Success: no issues found in 89 source files
  - `pytest`: **1274 passed** in 78.76s
- `uv run alembic check`: No new upgrade operations detected (no migration in this ticket).
- AC3 grep (`admin_users_api_enabled|admin_rbac_api_enabled|ADMIN_USERS_API_ENABLED|ADMIN_RBAC_API_ENABLED|_admin_apis_gate` over `app tests .env.example docs`): only `tests/test_config.py › test_admin_api_flags_removed` (asserts absence) and the ADM-002b history line in the ADR-0008 amendment.
- Local Postgres was already running; nothing started or created.

### Out-of-plan files
- `tests/test_health.py` — `test_docs_enabled_in_production` asserted Swagger is on in production, which contradicts A1; replaced by `test_docs_disabled_in_production_by_default` (also checks `API_DOCS_ENABLED=true` re-enables).

### Deviations from the plan
- `RoleCatalogService.build_default_role` left unchanged: it has no production caller (tests only), so per the plan's "if it has callers" it gets no required actor. Its stale, unused `actor: uuid.UUID | None = None` parameter remains (follow-up).
- Two more "unmounted" tests deleted than the four the plan names (`test_post_roles_flag_off_is_405`, `test_only_catalog_route_listed_by_default` in `test_role_catalog_api.py`); both asserted the flag-off state.
- Fixtures that grant through the API now pass a real `admin_user` (`test_rbac_assignments_api.py` `actor` fixture, `test_rbac_locking.py`, guard matrix): `granted_by` = actor id is an FK to `users`, so a random-uuid principal can no longer grant. Existing super-admin revoke tests mark holders verified (A4).
- New 409s are declared via small local helpers (`_with_409` in the RBAC router; `_WRITE`/`_REVOKE` dicts in the users/assignments routers) because `**require_permission(...)` already supplies `responses`.
- Not committed (caller's instruction); AC4's single commit is for the orchestrator.

### Follow-ups (noticed, not done)
- `build_default_role`: drop the unused `actor` parameter or make it a required `Actor` when it gets a production caller (USR-05 manifest).
- product-manager: spec §6.1 line 85 (docs now off in production by default, A1).
- DB-level cross-app origin-uniqueness guard / advisory lock (A8); optional post-lock re-read of `admin:*` (A7).
- ADM-004 audit log right after this ticket (R4 comments still mark every site).
- ADM-002d scope (strict_aud, seed `is_admin` fix, `--clear` on drift, runbook hardening, refuse signup/forgot for `admin`) untouched here.

### Scope self-check
- Could the diff be smaller? No — every production change maps to a row of the plan's file table; test churn is the flag removal plus the planned tests.
- Diff size: production (`app/` + `.env.example`) **+384 / −168** (incl. new `actor.py`, 39 lines); tests **+1050 / −182** (incl. new `test_admin_guards.py`, 797 lines); docs +114 / −8. Production is at the plan's A9 estimate (330–380).
