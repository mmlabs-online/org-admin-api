## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| app/modules/rbac/schemas.py | `RoleHolderRead` |
| app/modules/rbac/repository.py | `_not_expired()`, `RoleHolderRow`, `page_role_holders` |
| app/modules/rbac/service.py | `RoleService.list_holders` |
| app/modules/rbac/router.py | `GET /apps/{key}/role-definitions/{role_id}/assignments` (`admin:users:read`, `no-store`) |
| tests/test_rbac_role_holders_api.py | new API tests |
| tests/test_route_auth_classes.py | BEARER entry |
| tests/test_openapi_contract.py | `test_role_holders_operation`, envelope sweep entry |
| docs/identity-architecture.md | section 4 row, D18 sentence |

### Tests added / changed
- tests/test_rbac_role_holders_api.py: all tests listed in the plan (shape, disabled/unverified, paging/order, limit 422, expired default, active filter/partition, org filter, multi-row user, unknown org, combined, bad query/uuid 422, empty, only-this-role, 404 x3, admin app, disabled app, 403, 401, bounded queries).
- tests/test_openapi_contract.py: `test_role_holders_operation` asserts 401/403/422 envelope refs (not 404: `require_permission` documents no 404 on any route, so I did not add one).

### Checks (actual output summary)
- make check: ruff, format, mypy clean; pytest `2420 passed, 1 warning`.
- alembic check: not run (no migration).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- As in plan.md.

### Scope self-check
- Could the diff be smaller? No.
