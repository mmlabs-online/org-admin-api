## Attempt 1 — 2026-11-27

### Files touched
| File | Required because |
|---|---|
| app/modules/rbac/service.py | `GrantOutcome`; `_grant_locked` split into `_check_grant_request` + `_grant_user_locked` (wrapper keeps signature and behaviour); `_guard_privileged` split into `_require_privileged_grant` + `_forbid_self` (revoke keeps the combined one); `assign_batch` + helpers |
| app/modules/rbac/repository.py | `ASSIGNMENT_BATCH_LOCK_TIMEOUT`, `set_lock_timeout`, `is_lock_timeout`, `savepoint`, `existing_user_ids`, `find_assignments` (R1: service never touches the session) |
| app/modules/rbac/schemas.py | `AssignmentBatchCreate`, `BatchItemStatus`, `AssignmentBatchError/Item/Read` |
| app/modules/rbac/router.py | `POST /apps/{key}/roles/{role_id}/assignment-batches` (200, `read_json_body` 16 KiB cap, OpenAPI) |
| app/modules/audit/service.py | `record_denied_in_tx` + shared `_denied_changes` |
| tests/test_rbac_assignment_batches_api.py, tests/test_rbac_assignment_batches_concurrency.py | new tests per plan |
| tests/test_route_auth_classes.py, tests/test_audit_coverage.py, tests/test_audit_denied.py, tests/test_audit_logger.py, tests/test_openapi_contract.py | registry entries and new cases per plan |
| docs/spec-v1.md | `super_admin_role` in §5.15 Part 3 + PLAN (USR-36) sentence |

### Tests added / changed
- test_rbac_assignment_batches_api.py: 36 cases (results, request-level 404/422/403/411/413/415, reuse spy, audit rows, mid-batch denied, unexpected error rollback, query count)
- test_rbac_assignment_batches_concurrency.py: same-user race, other-users race, lock timeout 409
- test_audit_logger.py: 3 `record_denied_in_tx` tests; test_audit_denied.py: SITES `batch_wildcard`
- test_audit_coverage.py (entry, 200, count 35), test_route_auth_classes.py (BEARER entry), test_openapi_contract.py (`_EXPECTED_ERRORS`, `_SUPER`, `test_assignment_batches_documented`)

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed
- ruff format --check: 448 files already formatted
- mypy app: Success, no issues found in 216 source files
- pytest -q (full): 4498 passed, 2 warnings
- alembic check (local dev DB): No new upgrade operations detected
- risk_tier.py: see commit report

### Out-of-plan files
None.

### Deviations
- Service reads the lock timeout via `rbac_repository.ASSIGNMENT_BATCH_LOCK_TIMEOUT` (module attribute) so the test can monkeypatch it.
- `is_lock_timeout` checks `sqlstate` then `pgcode` on `exc.orig`.
- Concurrency tests leave append-only `audit_log` rows behind (cannot be deleted); queries are scoped by user id / request id. All other rows are cleaned up.
- API tests scope audit rows by `request_id` because committed tests leave rows behind in a full run.

### Follow-ups (noticed, not done)
- As in plan: OQ79 audit filter, bulk revoke, G5 race, per-item `details.reason`.

### Scope self-check
- Could the diff be smaller? No; app code is the plan's design.
- Diff size: see `git diff --stat`.
