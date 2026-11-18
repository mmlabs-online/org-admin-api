## Attempt 1
Verdict: PASS

Date: 2026-11-18. Reviewed `git diff origin/main...HEAD` (commits c982a59, d9b7df6, 73d674a, 6861872). Working tree clean.

### Checks (run by reviewer)
- `service postgresql start`, then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff check passed, format check passed ("325 files already formatted"), `mypy app` passed, pytest gave **2534 passed, 1 warning in 384.45s**. The warning is the existing pty DeprecationWarning in `test_cli_create_account.py`. No flaky failure: `test_stats_repository.py::test_login_count_uses_action_index` passed on the first run, so no rerun was needed.
- `alembic upgrade head && alembic check`: "No new upgrade operations detected." No migration in the diff (none expected).
- These results match the impl-log (2534 passed).

### Acceptance criteria
- [x] AC1, filters `status`, `join_policy`, `q` (trimmed, 1..100, case-insensitive on key OR name, wildcards escaped). Evidence: `app/modules/rbac/repository.py:92-120` (one `conditions` list, `ilike_any(q, App.key, App.name)`) and router params with `SearchTerm` (`app/modules/rbac/router.py:62-78`). Tests that pass: `test_list_apps_filter_status`, `test_list_apps_filter_join_policy`, `test_list_apps_filter_q_key_or_name_case_insensitive`, `test_list_apps_filter_q_is_trimmed`, `test_list_apps_filter_q_escapes_like_wildcards[%|a_b|_|\]` and `test_list_apps_filters_combined`. `org_mode` is also added; it is in the plan as additive and pending the owner's decision (see Follow-ups). Tested by `test_list_apps_filter_org_mode`.
- [x] AC2, sort allow-list with default = today's order and `id` tie-breaker. Evidence: `AppSort` Literal and `DEFAULT_APP_SORT = "key"` (`app/modules/rbac/schemas.py:29-30`), and `sort_clauses(sort, _APP_SORT_COLUMNS, App.id)`. The default `key ASC, id ASC` returns the same rows as the old `ORDER BY key`, because `key` is unique. Tests: `test_list_apps_sort_orders[6 values]`, `test_list_apps_sort_tie_breaker_is_id_asc[4 values]`, `test_list_apps_default_order_unchanged`, `test_apps_sort_allow_list_matches_repository_mapping`, and the unchanged `test_list_apps_paginated_and_sorted`.
- [x] AC3, filtered `total`, `member_count` still present, 422 on invalid values, unknown names ignored, no breaking change. Count and page share the same conditions. Tests: `test_list_apps_total_is_filtered_count`, `test_list_apps_member_count_on_filtered_page`, `test_list_apps_query_count_with_filters` (3 statements whether 1 or 5 matching apps), `test_list_apps_rejects_bad_query[10 cases]` and `test_list_apps_ignores_unknown_params`. The response model is still `Page[AppRead]` and every new param is optional (R9).
- [x] AC4, same design as ADM-020. The code imports `SearchTerm`, `ilike_any` and `sort_clauses` from `app.core.listing`, which is unchanged. It adds no new LIKE or sort helper. The param names and description wording match the organizations list. `tests/test_openapi_contract.py::test_apps_list_query_params` and `::test_apps_list_422_is_envelope` pass.
- [x] AC5, tests cover each filter alone and combined, escaping, sort and tie-breaker, 422 cases, and 403. `test_list_apps_without_permission_403` asserts 403 `permission_denied` with `details == {"required": "admin:apps:read"}`, both for a valid query and for `sort=bogus` (403 before 422).

### Scope
Every changed code, test and doc file is in the plan's file list. The `work/` files are ticket bookkeeping (consult, plan, impl-log, security-review, ticket AC3 note, backlog state). There are no unjustified out-of-plan changes. No migration.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router only declares params, the service passes them through, and the SQL is in the repository. The repository imports `AppSort` from `rbac.schemas`. This follows the existing precedent (`organizations/repository.py:10` imports `OrganizationSort`).
- R3: the route stays Bearer + `admin:apps:read`.
- R4: the change is read-only.
- R6: 403 and 422 responses use the envelope.
- R9: the change is additive.
- R11: `q` is not logged.

The appsec review (`security-review.md`, attempt 1) also returned PASS with no findings.

### Nits (do not block)
- `test_list_apps_filter_q_is_trimmed` and the escaping test do not assert `status_code == 200` before reading `items`. A 422 would still fail them, but with a less clear message.
- `test_apps_sort_allow_list_matches_repository_mapping` only asserts that the result is truthy. That is enough, because `sort_clauses` raises on an unknown key.

### Follow-ups (out of scope)
- Owner decision: keep the additive `org_mode` filter, which goes beyond ticket AC1 (plan "Needs owner's decision"). If rejected, the plan lists exactly what to remove.
- From the plan: `member_count` as a sort key, rejecting unknown query params on all list routes, adding the apps rows to the README endpoint table, and checking the production collation for the `name` sort.
