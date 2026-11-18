
## Attempt 1

### Done
- `RoleDefinitionRead` schema, `RbacRepository.page_roles` / `role_permission_rows`, `role_is_privileged` + `RoleService.list_definitions` / `get_definition` / `_definitions`, two GET routes `/apps/{key}/role-definitions[/{role_id}]` (`admin:apps:read`) per plan.
- Tests: `tests/test_rbac_role_definitions_api.py` (new), `BEARER` entries, OpenAPI contract test; docs row + D18 sentence in `docs/identity-architecture.md`.
- No migration, no catalog change.

### Deviations
- `test_role_definitions_operations` checks 401/403 envelope refs only: existing Bearer routes in this repo (e.g. `GET /apps/{key}`) do not declare 404 in OpenAPI, so the new routes follow that convention.
- In `test_privileged_flag_matches_assignment_guard` the 201 grant runs before the 403 denial, because a denied request rolls back the shared test transaction.

### Out-of-plan files
None.

### Checks
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff ok, format ok, mypy "no issues found in 146 source files", pytest "2345 passed".

### Follow-ups
As listed in plan.md.
