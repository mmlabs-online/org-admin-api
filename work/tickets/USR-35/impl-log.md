# Implementation log — USR-35

## Attempt 1

### Files touched
- `app/modules/users/repository.py`: `UserRepository.list` gains `app_id` / `role_id`, each one correlated `EXISTS` using USR-34's `active_assignment_clause()` (required by AC1, AC2).
- `app/modules/users/service.py`: `UserService.list` resolves `app_key` / `role_id` via existing `RbacRepository.get_app_by_key` / `role_with_app` (call only), raises `UnprocessableError` (`unknown_app`, `unknown_role`, `role_not_in_app`); new helper `_filter_unprocessable` (AC3).
- `app/modules/users/router.py`: query params `app` (`APP_KEY_RE`) and `role_id` (UUID), 422 description via `require_permission(errors=...)` (AC3f, AC5).
- `tests/test_users_filters.py` (new): all plan tests except OpenAPI (17 test functions, 22 cases incl. parametrization).
- `tests/test_openapi_contract.py`: `test_users_list_filter_params`, import of `APP_KEY_RE`.

### Tests added
23 new test cases (22 in `test_users_filters.py`, 1 OpenAPI).

### Checks
- `ruff check app tests migrations`: clean (after import-sort fix); `ruff format --check`: all files formatted.
- `mypy app`: Success, no issues in 216 source files.
- `pytest -q` (full): 4442 passed, 2 warnings.
- `alembic check` (local dev DB): No new upgrade operations detected.
- `risk_tier.py --base origin/main`: low (pre-commit working tree run; see commit-time report).

### Out-of-plan files
None. No High path touched.

### Deviations
- `docs/spec-v1.md` not edited (plan: only per Ming's answers on Q4; the ticket says defaults were accepted, but the plan's spec step is conditional on a dated answer in the batch round; left to the orchestrator).
- EXPLAIN (ANALYZE, BUFFERS) on ~1000 users not recorded: not run in this session. Both filters use correlated EXISTS over `ix_role_assignments_role_id` / `uq_roles_app_id_key`; recorded as follow-up.
- `test_total_is_distinct` asserts totals of 3 / 2 including fixture holders U1/U2.

### Follow-ups
- Record EXPLAIN for `app=` / `role_id=` on a seeded DB.
- Update spec §5.15 :365 and OQ76 :552 with the dated Q4 answer.

### Scope self-check
Only the plan's files changed; no refactors, no other filters.
