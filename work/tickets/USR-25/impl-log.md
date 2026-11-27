## Attempt 1 — 2026-11-27

### Files touched
| File | Required because |
|---|---|
| app/modules/rbac/service.py | `_role_is_privileged` (new), `_require_privileged_grant`/`_check_grant_request` return the bool, `_grant_user_locked(privileged=)`, `_grant_locked`, `_guard_privileged`, `_assign_batch_locked` thread it, `_forbid_self(privileged, user_id, actor)` (AC 1, 3, 5, 6) |
| app/modules/rbac/assignments_router.py | OpenAPI 403 text for assign and revoke |
| tests/test_rbac_self_assignment.py | new USR-25 matrix (12 tests) |
| tests/test_admin_guards.py | `test_self_grant_non_admin_app_role_ok` flipped to `test_self_grant_product_admin_role_403` |
| tests/test_rbac_assignment_batches_api.py | stub/spy updated, query-count assertion, 2 new batch tests |
| tests/test_audit_denied.py | SITES `assign_self_product`, `revoke_self_product` |
| docs: ADR-0008, ADR-0029, ADR-0030, identity-architecture, spec-v1, deploy.md | rule text, "change together" list, read-only report query |

### Tests added / changed
- tests/test_rbac_self_assignment.py: 12 tests (params app/org counted: 11 functions)
- tests/test_rbac_assignment_batches_api.py: test_batch_self_privileged_failed_one_denied_row, test_batch_self_privileged_without_wildcard_request_level_403; updated test_batch_reuses_single_grant_logic, test_batch_mid_batch_denied_keeps_other_grants, test_batch_query_count_bounded
- tests/test_admin_guards.py: flipped as above
- tests/test_audit_denied.py: 2 new sites

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed
- ruff format --check: 449 files already formatted
- mypy app: no issues in 216 source files
- pytest -q: 4514 passed, 2 warnings
- alembic check (local dev DB): No new upgrade operations detected

### Out-of-plan files
None.

### Deviations
- Test setups capture ids before `commit()` (denied write rolls back and expires ORM objects).
- `org_mode` has no `optional` value (`none|required`): the `perm_scope=org` param uses `org_mode="required"` and an app-scope grant of the role (still refused as expected).

### Follow-ups (noticed, not done)
- None.

### Scope self-check
- Could the diff be smaller? No.
