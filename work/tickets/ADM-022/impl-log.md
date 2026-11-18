
## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| app/modules/audit/actions.py | `AUDIT_LOG_EXPORTED` action (AC3) |
| app/modules/audit/repository.py | shared `_where`, new `export` (cap+1 select, no count) |
| app/modules/audit/csv_export.py (new) | `escape_cell`, `COLUMNS`, `render_csv` (AC4) |
| app/modules/audit/service.py | `EXPORT_MAX_ROWS`, `AuditExport`, `_check_range`, `AuditLogService.export` (audit row written last, same transaction) |
| app/modules/audit/router.py | `audit_filters` dependency shared by list and export; `/export` declared before `/{audit_log_id}` |
| app/core/cors.py | expose Content-Disposition, X-Export-Truncated, X-Export-Row-Count |
| tests/test_audit_logs_export.py, tests/test_audit_csv.py (new) | plan tests |
| tests/test_route_auth_classes.py, test_admin_cors.py, test_cors.py, test_openapi_contract.py | BEARER map entry, expose constant, CORS and contract tests |
| docs/spec-v1.md, docs/adr/0009-audit-log.md, docs/identity-architecture.md | one-line notes per plan |

### Tests added / changed
- tests/test_audit_logs_export.py: all plan tests (happy path, filters match list, cap, exact cap, cap constant, empty, headers, columns, injection, one audit row, row not in own file, audit failure fail-closed, 401, 403, 422, route order, limit/offset ignored)
- tests/test_audit_csv.py: test_escape_cell_triggers, test_escape_cell_leaves_safe_values, test_render_csv_quoting_and_newlines
- tests/test_admin_cors.py: test_export_exposes_truncation_headers; EXPOSE updated
- tests/test_cors.py: expose literal updated
- tests/test_openapi_contract.py: test_audit_export_operation

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): ruff clean, format clean, mypy clean, 2599 passed
- alembic check: not run separately; no migration or model change

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- As in plan: export rate limit, denied-attempt audit row, statement timeout/EXPLAIN review, admin-pan client regeneration.
- Full-width trigger characters are written as `\uXXXX` escapes in source (ruff RUF001).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +400 / -35 lines (tests ~300), no git repo to measure.
