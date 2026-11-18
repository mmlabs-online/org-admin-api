
## Attempt 1

### Files touched
- `app/modules/rbac/schemas.py`: `AppSort` Literal and `DEFAULT_APP_SORT = "key"` (AC2).
- `app/modules/rbac/repository.py`: `_APP_SORT_COLUMNS`; `list_apps` takes the filters and `sort`, uses one `conditions` list, `ilike_any` and `sort_clauses` (AC1-3).
- `app/modules/rbac/service.py`: `AppService.list_apps` pass-through (R1).
- `app/modules/rbac/router.py`: `status`, `join_policy`, `org_mode`, `q`, `sort` query params on `list_apps` (AC1-4).
- `tests/test_rbac_apps_api.py`: USR-18 section, `_seed_app` helper and all listed tests.
- `tests/test_openapi_contract.py`: `test_apps_list_query_params`, `test_apps_list_422_is_envelope`.
- `docs/identity-architecture.md`: apps row notes the list filters.
- `docs/spec-v1.md`: dated note under §5.3.

### Tests added
All tests listed in the plan (filters alone and combined, escaping, sort orders, tie-breaker, default order, member_count, query count, 422 cases, unknown params, 403, OpenAPI contract).

### Checks
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (run after `ruff format`, final tree): ruff, format, mypy and `alembic check` passed; pytest: `2534 passed, 1 warning in 365.88s`.

### Out-of-plan files
None.

### Follow-ups
As in plan: `member_count` sort, reject unknown params, README apps rows, collation check.

### Scope self-check
Only plan files touched; `app/core/listing.py` unchanged; no migration; not committed.
