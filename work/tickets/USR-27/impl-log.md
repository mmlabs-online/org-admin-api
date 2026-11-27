## Attempt 1 — 2026-11-27

### Files touched
| File | Required because |
|---|---|
| app/modules/rbac/schemas.py | `AssignmentRead` gains `role_name`, `org_name` (RoleHolderRead inherits) — AC 1 |
| app/modules/rbac/repository.py | Row tuples -> frozen dataclasses; `_assignment_rows()` selects `Role.name`, `Organization.name` with an outer join; one `_assignment_row` helper; `Role.name` dropped from `page_org_members` extras — AC 2-6 |
| app/modules/rbac/service.py | `_assignment_read`, `_role_holder_read`, `_org_member_read`, `revoke` read rows by attribute; names filled — AC 2-4, 6, 7 |
| tests/test_rbac_assignments_api.py | Key set, happy path, assign tests; new org-scope names, bounded-queries, two-statements tests |
| tests/test_rbac_role_holders_api.py | `KEYS`, item shape; new `test_holders_org_scope_names` |
| tests/test_openapi_contract.py | RoleHolderRead required set; new `test_assignment_read_component` |

### Tests added / changed
- test_rbac_assignments_api › test_list_org_scope_names, test_list_bounded_queries, test_page_assignments_two_statements (new); ASSIGNMENT_KEYS, test_list_happy_path, test_assign_app_scope_201, test_assign_org_scope_201 (extended)
- test_rbac_role_holders_api › test_holders_org_scope_names (new); KEYS, test_holders_item_shape (extended)
- test_openapi_contract › test_assignment_read_component (new); test_role_holders_operation (extended)

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed
- ruff format: clean
- mypy app: Success: no issues found in 216 source files
- pytest -q: 4527 passed, 2 warnings (777 s)
- alembic check (local dev DB): No new upgrade operations detected

### Out-of-plan files
None.

### Deviations
- `_assignment_row` takes `Sequence[Any]` (typed Row tuples made mypy reject index access / `Row[Any]`).
- `revoke` unpacking had been left as a 4-tuple by USR-25/26; now uses `row.assignment` / `row.role_key`; `row[1]` lock lookups in `revoke` use `row.app_key`.

### Follow-ups (noticed, not done)
- None.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: see `git diff --shortstat` of the commit.
