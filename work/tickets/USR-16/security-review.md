# USR-16 security review

## Attempt 1

**Verdict: no blockers.** I found no blocking or should-fix issues. The diff does what the security-architect consult recommended (recommendations 1–5, 7 and 8).

Files reviewed: `app/modules/rbac/{repository,router,schemas,service}.py`, `docs/identity-architecture.md`, `tests/test_rbac_role_definitions_api.py`, `tests/test_route_auth_classes.py`, `tests/test_openapi_contract.py`.

### What I verified
- **Auth class (R3 class 1):** both routes are on the existing Bearer `rbac/router.py`, which has `current_admin` at router level. Each declares `**require_permission("admin:apps:read")` (`app/modules/rbac/router.py:183-205`).
  - Both routes are registered in `BEARER` (`tests/test_route_auth_classes.py:96-97`). That means the classification test, the 401 sweeps and `test_admin_cors.py::test_no_allow_credentials_on_any_bearer_route` cover them automatically, so admin CORS without credentials and `no-store` apply.
  - There is no `rate_limit` on the routes and no CORS change.
- **Error order:** an unauthenticated request returns 401 before the 404 for an unknown key (`tests/test_rbac_role_definitions_api.py:218`). A caller without the permission gets 403 for both `stoktok` and the unknown key `nope` (`:204-215`). An unknown or malformed key returns 404 only for an authorised caller (`:193-201`). An authenticated caller therefore cannot use the endpoint to check whether an app key exists.
- **IDOR / scoping:** the detail route uses `RoleService._get(app, role_id)` → `repo.get_role(app.id, role_id)` (`service.py:1036`), so a `role_id` from another app returns 404 (test at `:231`).
  - `role_permission_rows` (`repository.py:222`) does not filter by app. It is only called with role ids that came from the app-scoped query, and the composite foreign keys `fk_role_permissions_*_same_app` (`models.py:169-188`) stop cross-app links in the database.
- **Injection:** every query is built with SQLAlchemy Core and bound parameters. `limit`/`offset` come from `PageDep` (≤100, test at `:103`).
- **N+1 / denial of service:** each page runs three queries (app, count + page, one batched permission query), and `test_role_definitions_bounded_queries` (`:248`) checks the count.
- **D17 public catalog kept separate:** there is a new schema (`RoleDefinitionRead`) and new service methods, and `RoleCatalogService`, `RoleCatalogEntry` and `catalog_router` are unchanged. A regression test (`:264-275`) checks that `GET /apps/admin/roles` and `GET /apps/oldapp/roles` (the disabled-app fixture) still return 404 and that the catalog body has no `id`.
- **Data exposure:** the endpoint returns only role metadata and permission keys. These were already readable under `admin:apps:read` through `/apps/{key}/permissions`, `AppRead.default_role_id` and `AssignmentRead`. It returns no assignee identities, emails or secrets (R5/R11). Reads are not audited, which matches R4 and spec OQ4.
- **The `privileged` flag:** `role_is_privileged` (`service.py:1024-1026`) uses the same predicate as `_guard_privileged` (`service.py:1498-1500`). `test_privileged_flag_matches_assignment_guard` (`:148`) checks that the flag and the guard agree. The schema says the flag is advisory only, and the server still enforces every write.

### Findings
- 🔴 Blockers: none.
- 🟡 Should-fix: none.
- 💭 `app/modules/rbac/service.py:1024` — `role_is_privileged` is not called from `_guard_privileged` (`service.py:1500` has its own inline `app.key != ADMIN_APP_KEY and not await self.repo.role_has_admin_permission(...)`). The two are tied together only by a docstring and a test, so the UI flag can drift from the server guard if the privilege rule changes (consult risk R-b). Suggested fix in a later ticket: have `_guard_privileged` call `role_is_privileged(app.key, await self.repo.role_has_admin_permission(role_id))`. Out of scope for USR-16 (R7). The existing test catches drift in the meantime.
- 💭 Accepted residual risk (consult R-c): a stolen token with `admin:apps:read` now shows which admin-app roles hold `admin:*`. Most of this could already be worked out from `/apps/admin/permissions` and assignments, and tokens expire after 12 h. No change needed. The owner may want to note it in the PR.

### make check
The full `make check` run (lint, format, mypy, tests) ended with **7 failed, 1 error, 2337 passed**. The failures were in `test_me_permissions.py`, `test_invitations_create.py`, `test_join_requests_api.py` and `test_audit_identity.py`, all with SQLAlchemy errors, and none of these files is touched by the diff.
- Re-running those files together with `tests/test_rbac_role_definitions_api.py` gave **116 passed**.
- This looks like flakiness or a clash on the shared test database (another run may have been going at the same time), not a regression from this diff. The reviewer must still confirm a green `make check` before PASS (R8).

### Follow-ups (out of scope)
1. Make `_guard_privileged` call `role_is_privileged`, so the flag and the guard share one predicate (above).
2. Find out why tests intermittently fail with SQLAlchemy errors when the full suite runs (`test_me_permissions.py`, `test_audit_identity.py::test_logout_one_account_row`, and the others listed above). Possibly isolation of the shared `org_admin_test` database between concurrent runs.

_Orchestrator note: the appsec and reviewer subagents ran `make check` concurrently against the same `org_admin_test` database, which is the likely cause of the 7 failures above._
