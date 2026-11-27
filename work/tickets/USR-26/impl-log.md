# Impl log — USR-26

## Attempt 1
Files touched (all per plan): `app/modules/rbac/{schemas,repository,service,router}.py`,
`tests/test_rbac_role_definitions_api.py`, `tests/test_rbac_roles_api.py`,
`tests/test_rbac_super_admin_role.py`, `tests/test_openapi_contract.py`, `docs/consumers.md`,
`docs/identity-architecture.md`. `tests/test_admin_guards.py` unchanged (as planned).

Deviations: none of substance. Reason tuples placed in the USR-37 block (after
`REASON_CHANGED_CONCURRENTLY`) as planned; `_role_conflict` added next to `_assignment_conflict`
rather than near `_role_has_assignments`. The `is_org_owner` test uses an `org_mode=required` app
(DB check `ck_apps_org_owner_requires_org_mode`). Existing super-admin tests only checked
`details[0].reason`; added exact `details` assertions.

Out-of-plan files: none. Follow-ups: none.

Checks: ruff check clean, ruff format --check clean, mypy app "no issues in 216 files",
`pytest -q` 4522 passed, `alembic check` no drift (local dev DB).
