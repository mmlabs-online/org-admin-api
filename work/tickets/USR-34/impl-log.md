# Implementation log — USR-34

## Attempt 1

### Files touched
- `app/modules/users/repository.py` — `active_assignment_clause()` (module level, shared with USR-35), `AccessRow`, `UserRepository.list_access` (one windowed query). Required by AC2-4, R1.
- `app/modules/users/schemas.py` — `UserInclude`, `AccessRole`, `AppAccess`, `UserWithAccessRead`; `UserRead` unchanged. AC1, AC6.
- `app/modules/users/service.py` — `ACCESS_CAP = 20`, `UserService.access_summaries` (cap+1 probe, group by app). AC1-3.
- `app/modules/users/router.py` — `include` query param, union `response_model`. AC1, AC5, AC6.
- `tests/test_users_access.py` (new), `tests/test_openapi_contract.py` (`test_users_list_operation`).
- `docs/spec-v1.md` §5.15 Part 1 — PLAN decisions recorded.
- No High path touched (no rbac, core, migrations, auth, `.env.example`).

### Tests added
`tests/test_users_access.py`: 27 cases (all plan tests; parametrized cap boundary and bad `include` values) + `test_users_list_operation`.

### Checks
- ruff check / format --check (app tests migrations): all passed / 445 files formatted
- mypy app: Success, no issues in 216 source files
- pytest -q (full): 4416 passed, 2 warnings
- alembic check (local dev DB): No new upgrade operations detected
- risk_tier.py --base origin/main: run after commit, see final report

### EXPLAIN (ANALYZE, BUFFERS), test DB, 100 users x 25 assignments (rolled back)
Bitmap Index Scan on `uq_role_assignments_user_role_org` (user_id = users.id), loops=100; roles via `uq_roles_id_app_id`, apps via `pk_apps`; 2100 rows out, execution ~12 ms. No index needed.

### Deviations
- None from the plan. Union `response_model` worked (no Q3 fallback needed). `list_access` and `access_summaries` are defined before the `list` method in their classes because the method name `list` shadows the builtin in annotations (mypy).
- First full run had one failure in my own new test (a loop variable typo); fixed and the full suite re-run green.

### Out-of-plan files
None.

### Follow-ups
- Move rbac's inline active-assignment copies onto `active_assignment_clause` (High, separate ticket).
- Tell admin-pan (PAN-032): the list now has two page types in OpenAPI (`anyOf`).

### Scope self-check
Only plan files changed; no refactors.
