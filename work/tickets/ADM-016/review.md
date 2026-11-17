
## Attempt 1
Verdict: PASS

Date: 2026-11-17. Diff: `git diff origin/main...HEAD` (commit 80a3678).

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (ruff, format check, mypy --strict, pytest): green, `2281 passed, 1 warning in 215.80s`. The warning is the existing pty DeprecationWarning in `test_cli_create_account.py`. The known flake did not occur. This matches the impl-log count (2281).
- Targeted run `pytest tests/test_rbac_apps_api.py -k "member_count or query_count or exact_keys"`: 12 passed.
- alembic check: not needed. There is no model or migration change in the diff.

### Acceptance criteria
- [x] `GET /api/v1/apps` and `GET /api/v1/apps/{key}` include an additive `member_count` field (R9). Evidence: `app/modules/rbac/schemas.py:80-103` keeps the same 11 fields in `AppFields` and adds a required `member_count: int (ge=0)` on `AppRead`. `router.py:54-56,79`. Tests: `test_app_read_has_exact_keys` (only `member_count` added to `APP_READ_KEYS`, test line 30), `test_list_apps_member_count`, `test_get_app_member_count`. The OpenAPI component name stays `AppRead`.
- [x] The count follows OQ5. The only query is the shared `RbacRepository.member_counts` (`repository.py:581-602`: distinct active users, non-expired, app scope or any org), called through `AppService.member_counts` (`service.py:336-337`). No second member query is in the diff. Tests: `test_member_count_app_and_org_scope`, `test_member_count_excludes_expired`, `test_member_count_disabled_excluded_unverified_counted`, `test_member_count_admin_app_counts_staff`, `test_member_count_matches_dashboard` (list equals stats `members_per_app` for all apps).
- [x] No N+1 on the list. There is one batched `member_counts(items)` call per page (`router.py:54`). `test_list_apps_query_count` asserts 3 statements with both 1 and 5 extra apps, and exactly one statement references `role_assignments`. `test_get_app_query_count` asserts 2 statements.
- [x] Tests for app/org scope, expired, and disabled/unverified: see the tests above, all passed.
- [x] The existing app API tests still pass. The full suite is green, and the only edit to existing tests is the `APP_READ_KEYS` constant.
- [x] (plan AC 6) The permission stays `admin:apps:read`. Evidence: `test_member_count_visible_with_apps_read_only`. The route decorators are unchanged.

### Issues
None.

### Scope
Changed files: `app/modules/rbac/{schemas,service,router}.py` and `tests/test_rbac_apps_api.py`, all in the plan. The other changed files are workflow files (`work/backlog.md`, the ADM-016 ticket, plan and impl-log). There are no unjustified out-of-plan changes. POST and PATCH also return `member_count`, which is the plan's documented default (owner item 1).

### Rules R1–R11
No violations found.
- R1: the router calls only service methods and Pydantic, and the count lives in the repository.
- R2: no schema change.
- R3: no route or permission change.
- R4: the change is a read only, and the mutation audit is unchanged.
- R6, R9: additive field only.

### Nits (do not block)
- `work/tickets/ADM-016/impl-log.md` says "re-run below", but no second run is recorded. My run confirms the final tree is green.
- `router.py:42-47`: `_app_read` does model_validate, then model_dump, then rebuilds the model, so it validates twice. This is fine at admin volume. `AppRead.model_validate(app, update=...)` is not available in Pydantic v2, so the current approach is reasonable.

### Follow-ups (out of scope)
- If the owner wants it, list `member_count` in the `docs/identity-architecture.md` §4 apps rows (plan owner item 3).
