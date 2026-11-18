
## Attempt 1

### Files touched
- `app/modules/rbac/schemas.py` — `RoleHolderRead(AssignmentRead)` (+ `UserStatus` import); required by AC2.
- `app/modules/rbac/repository.py` — `_is_active()`, `_assignment_rows()` uses it, `RoleHolderRow`, `page_role_holders()` (2 statements), `not_` import; AC2/AC3.
- `app/modules/rbac/service.py` — `RoleService.list_holders`, `_role_holder_read`; R1 business layer.
- `app/modules/rbac/router.py` — `GET /{key}/role-definitions/{role_id}/assignments`, `admin:users:read`, `Cache-Control: no-store`; small `_with_404` helper (same shape as `_with_409`) so the 404 envelope is documented in OpenAPI.
- `tests/test_rbac_role_holders_api.py` (new) — all plan tests.
- `tests/test_route_auth_classes.py` — BEARER row.
- `tests/test_openapi_contract.py` — `test_role_holders_operation`.
- `docs/identity-architecture.md` — §4 table row.

### Tests added
All tests listed in the plan (API + repository + contract + route class row).

### Check results
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree: `2456 passed, 1 warning` (ruff, format, mypy clean).
Note: one earlier full run failed once on `tests/test_stats_repository.py::test_login_count_uses_action_index` (planner index choice; passes in isolation 3/3 and in the final full run); unrelated to this ticket, looks flaky.

### Out-of-plan files
None.

### Follow-ups
- As in plan.md (scope filter, email_verified_at, index, other inline `active` copies, no-store on `GET /users*`).
- `test_login_count_uses_action_index` appears order/stat-dependent (flaky).

### Scope self-check
Only plan files touched; `_with_404` is the one small addition inside router.py. No migration.
