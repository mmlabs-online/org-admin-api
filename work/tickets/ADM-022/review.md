## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest), run by the reviewer as the only test process:
  - `ruff check`: All checks passed.
  - `ruff format --check`: 330 files already formatted.
  - `mypy app`: Success, no issues found in 149 source files.
  - `pytest -q`: `2599 passed, 1 warning in 400.95s` (the warning is the existing pty DeprecationWarning). Exit 0.
  - This matches impl-log (2599 passed).
- alembic: `alembic upgrade head`, then `alembic check` → `No new upgrade operations detected.` No file under `migrations/` in the diff.
- `git log HEAD..origin/main` is empty, so the branch is up to date with main.

### Acceptance criteria
- [x] AC1: an export endpoint with the same filters as the list, `text/csv`, a 10,000-row cap, and a truncation signal.
  - Code: `app/modules/audit/router.py:107` (declared before `/{audit_log_id}` at :127). The shared `audit_filters` dependency is used by both list and export. `repository.py` `_where` is shared by `list` and `export`. `service.py:288-311` reads cap+1 to detect truncation. `cors.py` exposes `Content-Disposition`, `X-Export-Truncated` and `X-Export-Row-Count`.
  - Tests (all pass): `test_export_happy_path_with_filters`, `test_export_filters_match_list` (anti-drift against the list), `test_export_cap_truncates_newest_first`, `test_export_exact_cap_not_truncated`, `test_export_cap_constant_is_10000`, `test_export_empty_is_header_only`, `test_export_headers`, `test_export_route_not_captured_by_id`, `test_export_ignores_limit_offset`, `test_admin_cors.py::test_export_exposes_truncation_headers`, `test_openapi_contract.py::test_audit_export_operation`.
- [x] AC2: the permission is the existing `admin:audit:read`, with no migration.
  - `require_permission("admin:audit:read")` in `_export_route`.
  - The BEARER map entry is in `tests/test_route_auth_classes.py`.
  - `test_export_401_without_token` and `test_export_403_without_permission` pass.
  - No migration, and `alembic check` is clean.
- [x] AC3: one audit entry per export (filters and row count, no row data), in the same transaction.
  - `service.py:299` calls `AuditLogger.record`, which flushes and never commits. It runs after the CSV is built.
  - `test_export_writes_one_audit_row` checks the exact `changes` keys, that no row id leaks, that the user agent is the request's and not a row's, and that target/org/app are NULL.
  - `test_export_row_not_in_its_own_file` passes.
  - `test_export_audit_failure_returns_no_file` passes: the strict client returns 500, with no CSV and no rows written.
  - `test_export_422_from_after_to_writes_no_row` and the 403 test confirm that no row is written on rejection.
- [x] AC4: no PII beyond the list, and formula-injection protection.
  - The columns are exactly `AuditLogRead.model_fields`, serialized per row through `AuditLogRead.model_validate`, the same path as list and get. `user_agent` is the stored, already sanitized value. There are no joins.
  - `escape_cell` is applied to every cell (`csv_export.py`).
  - Tests: `test_export_columns_equal_read_schema`, `test_audit_csv.py::test_escape_cell_triggers` (including full-width characters and leading spaces), `test_escape_cell_leaves_safe_values`, `test_render_csv_quoting_and_newlines`, `test_export_formula_injection_escaped`.
- [x] AC5: tests for the happy path with filters, the row cap, the audit row, 401/403 and CSV-injection escaping. All are present and passing (see above).

Owner answers respected:
- The export is recorded under §5.4 (spec note added) and is separate from §5.11. It has no definitions, schedules or xlsx.
- `admin:audit:read` is used.
- The cap is 10,000.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router does HTTP only. The service owns the range check, the cap and the audit write. The repository is SQLAlchemy only. CSV formatting is a pure module.
- R2: no schema change. `audit_log.exported` fits the existing action CHECK.
- R3: class 1 Bearer with `require_permission`. It is classified in the deny-by-default test.
- R4: the audit row is written in the request transaction and fails closed.
- R5: no config change. The cap is the owner's fixed value.
- R6: errors use the envelope (contract test for 401/403/422).
- R7: every changed file is in the plan's file list. The backlog and ticket state edits are workflow bookkeeping.
- R8: green, with tests for the happy path, validation and 401/403. The export has no 404 case.
- R9: see the nit below.
- R11: `changes` holds only filter names and counts, so no secrets or emails.

### Nits (do not block)
- `docs/identity-architecture.md:183`: the new row has no backticks around the path, `aud=admin` and `admin:audit:read`, unlike its neighbours (:181-182).
- `docs/adr/0009-audit-log.md:144`: the amendment is a single very long line with unformatted identifiers. The rest of the file wraps at about 100 characters and uses backticks.
- `work/backlog.md:72`: the title still says "(needs owner: §5.11 overlap)" although the owner has answered.
- R9 lists its non-`Page[T]` exceptions explicitly. `/audit-logs/export` is a file download, not a JSON collection, so this is arguably not needed. The owner may still want one line added to the R9 exception list for completeness (a CLAUDE.md change, so the owner's call).
- `escape_cell` strips only ASCII spaces before checking for a trigger. A leading NBSP or other Unicode whitespace followed by `=` is not prefixed. Spreadsheets generally do not treat such cells as formulas, so this is low risk.

### Follow-ups (out of scope)
- These are as listed in the plan:
  - a rate limit or concurrency cap on the export
  - `outcome=denied` rows for 403 export attempts
  - a statement timeout and EXPLAIN review of wide `org_id` exports
  - replacing `_read_route` with `require_permission(errors=...)`
  - admin-pan: regenerate the client and read the exposed headers.
- The plan's non-blocking owner defaults should be surfaced to the owner in the PR description:
  - headers-only truncation signal
  - no BOM
  - `ensure_ascii=True` in the `changes` cell
  - the `'` prefix
