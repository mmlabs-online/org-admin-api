
## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| `app/modules/audit/repository.py` | `AuditLogFilter.org_id` + OR clause in `list`; `get`; docstring (AC1, AC2) |
| `app/modules/audit/service.py` | `AuditLogService.get` raises `NotFoundError` (AC2) |
| `app/modules/audit/router.py` | `org_id` query param; `GET /{audit_log_id}` with 404 declared (AC1, AC2) |
| `app/modules/audit/models.py` | `ix_audit_log_org_id` partial index (AC3, R2) |
| `migrations/versions/20261118_d9e4b2a61f73_adm021_audit_log_org_id_index.py` (new) | plain create/drop index, `down_revision = c7a3e91f5d28` (AC3, R2) |
| `tests/test_audit_logs_api.py` | filter, single read, 401/403/404/422 tests (AC4) |
| `tests/test_audit_schema.py` | `test_org_filter_uses_indexes` (AC3) |
| `tests/test_audit_org_index_migration.py` (new) | downgrade/upgrade of the index (R2) |
| `tests/test_route_auth_classes.py` | new route in `BEARER`; `audit_log_id` in `_fill` (R3) |
| `docs/identity-architecture.md` | route catalogue: `org_id` filter and new route row |

### Tests added / changed
- `tests/test_audit_logs_api.py` › `test_filter_org_id_target_or_column`, `test_filter_org_id_row_matching_both_once`, `test_filter_org_id_other_target_type_not_matched`, `test_filter_org_id_with_other_filters`, `test_unknown_org_id_empty_page`, `test_get_audit_log_matches_list_item`, `test_get_audit_log_404`, `test_get_audit_log_422_malformed_id`, `test_get_audit_log_401`, `test_get_audit_log_403_without_permission`; `test_422_validation` gets an `org_id` case
- `tests/test_audit_schema.py` › `test_org_filter_uses_indexes`
- `tests/test_audit_org_index_migration.py` › `test_org_index_down_up`
- `tests/test_route_auth_classes.py`: new route covered by the parametrized 401 and permission-map tests

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): green; pytest `2551 passed, 1 warning in 367.25s`
- alembic check (after `make migrate`): `No new upgrade operations detected.`; `alembic heads`: `d9e4b2a61f73 (head)`

### EXPLAIN ANALYZE evidence (AC3)
Throwaway database `adm021_scratch` (dropped afterwards), migrated to head, 200,000 `audit_log` rows (10,000 with `org_id`, ~4,000 organization targets, 300 org UUIDs), then `ANALYZE audit_log`. Org with 667 matching rows.

Page query (`ORDER BY occurred_at DESC, id DESC LIMIT 20`): `Execution Time: 3.714 ms`, `Planning Time: 0.697 ms`.
```
Limit -> Sort (top-N heapsort) -> Bitmap Heap Scan on audit_log (rows=667, Heap Blocks exact=667)
  -> BitmapOr
       -> Bitmap Index Scan on ix_audit_log_target  (rows=667)
       -> Bitmap Index Scan on ix_audit_log_org_id  (rows=0)
Buffers: shared hit=686
```
Count query: `Execution Time: 0.993 ms`, `Planning Time: 0.125 ms`, same BitmapOr over `ix_audit_log_target` and `ix_audit_log_org_id`, `Buffers: shared hit=680`. No `Seq Scan`. Both far under the 200 ms target (§6). Note: in this seed the column arm matched 0 rows for the chosen org (seed artifact); the index is still used in the BitmapOr.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Rows written via `record_in_savepoint` (hard-coded `org_id=None`) and identity-side rows that record the org only in `changes` will not appear in the org filter (plan's owner question 3).
- Plain `CREATE INDEX` used; switch to `CONCURRENTLY` if production `audit_log` is already large (plan's owner question 1).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: roughly +330 / -10 lines (about 250 of them tests and the migration; the working tree is not a git repo here, so not measured).

## Attempt 2 (REWORK: review Attempt 1, Issue 1)

### Files touched
- `tests/test_audit_org_index_migration.py`: the down/up cycle now runs in a throwaway database (`CREATE DATABASE adm021_<random>` via an AUTOCOMMIT connection to the `postgres` maintenance DB, `DROP DATABASE ... WITH (FORCE)` in `finally`). Required because the review asked that the shared test DB's `audit_log` is not touched by this test.
- `tests/test_stats_repository.py` (OUT OF PLAN, see below): `test_login_count_uses_action_index` now runs `DROP INDEX ix_audit_log_occurred_at_id` inside its rolled-back transaction before the EXPLAIN.

### Finding
The throwaway database alone did NOT fix the flake: 4 full runs with only that change failed the same test (1 failed, 2550 passed), while the suite with the new migration test ignored passed once. Adding the extra database/test shifts autovacuum timing on the shared test DB, and the test's plan depends on that timing (competing `ix_audit_log_occurred_at_id` bitmap scan vs `ix_audit_log_action`; failing plan: Bitmap Heap Scan on `ix_audit_log_occurred_at_id`, cost 11.80). `ANALYZE audit_log` in the test (reviewer's alternative) still failed (1 full run); `REINDEX TABLE audit_log` in the test passed alone but still failed in a full run. Dropping the competing index inside the test transaction (rolled back by the fixture) is deterministic: the test still proves the action index serves the login-count query.

### Checks (three back-to-back full `make check` runs, test DB otherwise idle)
```
RUN1: All checks passed! / 327 files already formatted / Success: no issues found in 148 source files / 2551 passed, 1 warning in 373.97s
RUN2: All checks passed! / 327 files already formatted / Success: no issues found in 148 source files / 2551 passed, 1 warning in 358.36s
RUN3: All checks passed! / 327 files already formatted / Success: no issues found in 148 source files / 2551 passed, 1 warning in 357.98s
```

### Out-of-plan files
- `tests/test_stats_repository.py`: the review's preferred fix alone was insufficient (see Finding); this is the review's stated alternative (make the EXPLAIN test independent of statistics), justified by Issue 1.

### Follow-ups
- Other EXPLAIN-based tests (`test_outcome_partial_index_used`, `test_org_filter_uses_indexes`) may be similarly timing-sensitive; consider a shared approach.

### Scope self-check
- Two test files changed, nothing else; no commit made.
