## Attempt 1 — 2026-11-27

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/service.py` | `REASON_ASSIGNMENT_EXISTS` / `REASON_CHANGED_CONCURRENTLY` constants, `_assignment_conflict()` helper, 4 bare `ConflictError` raises in `assign` / `_grant_locked` now carry `details=[{"reason": …}]` (AC 2). `Final` import. |
| `app/modules/rbac/assignments_router.py` | POST only: real 403/404/409/422 OpenAPI descriptions; 403 overridden on the `require_permission` kwargs in the router (AC 1). `deps.py` untouched. |
| `tests/test_rbac_assignments_api.py` | extended duplicate test; 3 race tests; constants test (AC 2, 4) |
| `tests/test_openapi_contract.py` | `test_assign_role_post_documents_real_outcomes` (AC 1) |
| `docs/spec-v1.md` | §5.15 Part 4: one sentence recording the chosen names and the 422 list |

### Tests added / changed
- tests/test_rbac_assignments_api.py › test_assign_active_duplicate_409 (extended: message + details, both params)
- tests/test_rbac_assignments_api.py › test_assign_racing_insert_409_assignment_exists
- tests/test_rbac_assignments_api.py › test_assign_expired_duplicate_race_409_changed_concurrently
- tests/test_rbac_assignments_api.py › test_assign_row_vanished_409_changed_concurrently
- tests/test_rbac_assignments_api.py › test_assignment_conflict_reason_constants
- tests/test_openapi_contract.py › test_assign_role_post_documents_real_outcomes

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed
- ruff format: formatter applied (1 file reformatted), then check clean
- mypy app: Success: no issues found in 216 source files
- pytest: 4447 passed, 2 warnings (full suite, 875s; run after the final formatting)
- alembic check: No new upgrade operations detected
- risk tier: high (risk_tier.py --files on the changed files; --base origin/main on the uncommitted tree reported low)

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- DELETE `…/role-assignments/{assignment_id}` 409 "Assignment was changed concurrently" has no reason.
- `forbidden=` parameter on `require_permission` if more routes need a custom 403 text.
- GET's `_USER_404` text could name `not_found`.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +144 / -18 lines (incl. tests and one spec line).
- Deviations: none. USR-36's `_grant_locked` split not done.
