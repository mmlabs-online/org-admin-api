## Attempt 1 — 2026-11-27

### Environment check (plan Approach 0)
Orchestrator checked local Postgres 16: `und-x-icu` and `vi-x-icu` exist, encoding UTF8. `test_icu_collation_available` passes against the test DB.

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/repository.py` | `NAME_SORT_COLLATION`, `name_sort_key`, `member_count_select()` (shared OQ5 query), `_APP_SORT_EXPRESSIONS`, `list_apps` order + LEFT JOIN for `member_count`; `member_counts` reuses the select |
| `app/modules/rbac/schemas.py` | `AppSort` adds `member_count`, `-member_count` |
| `app/modules/rbac/router.py` | sort description |
| `app/modules/organizations/repository.py` | `_SORT_EXPRESSIONS`, `_order_by` using `name_sort_key`; dropped `sort_clauses` import |
| `app/modules/organizations/router.py` | sort description |
| `docs/spec-v1.md` | dated ADM-026 notes |
| `tests/test_name_sort.py` (new) | ICU guard, compile check, accent/case order, case-only ties (orgs, apps) |
| `tests/test_rbac_apps_api.py` | member_count sort tests, rewritten allow-list test, removed `sort=member_count` from bad-query list |
| `tests/test_organizations.py` | rewritten allow-list test, `sort=member_count` -> 422 |

### Tests added / changed
- tests/test_name_sort.py: test_icu_collation_available, test_name_sort_key_compiles_to_icu_lower, test_{orgs,apps}_name_sort_case_and_accents[name,-name], test_{orgs,apps}_name_case_only_tie_broken_by_id[name,-name] (10 tests)
- tests/test_rbac_apps_api.py: test_list_apps_sort_member_count[x2], ..._ties_by_id[x2], ..._excludes_expired_and_inactive, ..._with_filters_and_paging, ..._query_count (7); allow-list test rewritten
- tests/test_organizations.py: allow-list test rewritten; bad-query adds member_count
- tests/test_openapi_contract.py: apps `sort` enum updated (see out-of-plan)

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed
- ruff format --check app tests migrations: 451 files already formatted
- mypy app: Success: no issues found in 216 source files
- pytest -q (full): 4585 passed, 1 failed (test_openapi_contract::test_apps_list_query_params, enum of apps sort); fixed, re-ran that file plus test_name_sort and test_organizations: 102 passed
- alembic check (local dev DB): No new upgrade operations detected

### Deviations
- Plan's expected ascending order `alpha, Ánh, Beta, Dung, đức, Zed` is wrong under ICU root: đ is d with a stroke (secondary difference), so `đức` < `Dung` (c < n). Tests expect `alpha, Ánh, Beta, đức, Dung, Zed`. Same property (accents/case folded, not byte order).
- `member_count_select()` is annotated `Select[uuid.UUID, int]` (the installed stubs reject `Select[tuple[...]]`); sort-expression dicts typed `ColumnElement[Any] | QueryableAttribute[Any]` for mypy strict.

### Out-of-plan files
- `tests/test_openapi_contract.py` - existing contract test pinned the apps `sort` enum; extended with the two new values.

### Follow-ups (noticed, not done)
- `app/core/listing.py::sort_clauses` has no callers in `app/` now; remove or generalise (High).
- Consider `name_sort_key` for other name-sorted lists (roles, users).

### Scope self-check
- Could the diff be smaller? No.
