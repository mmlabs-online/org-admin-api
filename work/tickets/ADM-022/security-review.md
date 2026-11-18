# ADM-022 — Security review

## Attempt 1

Static review only; pytest and `make check` were not run.

**Overall: PASS.** No blockers. The two YELLOW notes need no code change in this ticket.

### Findings

- **GREEN: authz on the new route** (`app/modules/audit/router.py:106-111`).
  - The route has the router-level `Depends(current_admin)` and `**require_permission("admin:audit:read")`, which carries both the enforcement dependency and `x-permission`.
  - It is declared before `/{audit_log_id}`, so the id route cannot capture it; `test_export_route_not_captured_by_id` checks this.
  - It is in the R3 route registry (`tests/test_route_auth_classes.py:110`).
  - Tests cover 401, 403 and 422.
- **GREEN: formula/CSV injection** (`app/modules/audit/csv_export.py:14-23, 34-41`).
  - `escape_cell` runs on every cell, `changes` included.
  - The trigger set is `= + - @ \t \r \n` plus the full-width `＝ ＋ － ＠`, checked again after leading spaces.
  - Exercised without a DB: `=1+1`, `"  =1"`, `" \t=1"` and `＝1` are escaped; JSON, `::1`, UUIDs and timestamps are left alone.
  - `QUOTE_MINIMAL` still quotes embedded `,`, `"` and newlines, so cells and rows cannot be broken out of. The `'` prefix is the real defence.
- **YELLOW (note, no change required): a `user_agent` starting with U+00A0 is not escaped** (`app/modules/audit/csv_export.py:21`).
  - Starlette decodes headers as latin-1, so an anonymous caller can store a UA such as `"\xa0=HYPERLINK(...)"`.
  - `_CONTROL_CHARS` (`service.py:34-37`) does not remove NBSP, and `lstrip(" ")` only strips ASCII spaces, so that cell is exported unescaped.
  - Excel and LibreOffice treat a cell starting with NBSP as text, so there is no known working exploit today.
  - Optional hardening: check after `value.lstrip()` with no argument.
- **GREEN: header injection via `Content-Disposition`** (`router.py:113-117`).
  - The filename is fixed ASCII plus a server-side UTC stamp; no request input reaches any response header.
  - `X-Export-*` values are computed by the server.
  - The response also sets `Cache-Control: no-store` and `nosniff`.
- **GREEN: PII compared with the list.**
  - Exactly the `AuditLogRead` columns, via `model_validate`, with no joins. `user_agent` is the stored value, already cleaned on write.
  - No emails: `_FORBIDDEN_KEY` and the allowlisted diffs keep them out of `changes`.
  - `test_export_columns_equal_read_schema` guards the equivalence.
  - `ensure_ascii=True` in the `changes` cell neutralises bidi/invisible-character spoofing.
- **GREEN: audit row atomicity and content** (`service.py:288-310`).
  - Order: `_check_range` first (a 422 writes no row), then the cap+1 query and CSV build, then `AuditLogger.record` flushes in the request session.
  - `SessionDep` commits before the response is sent, so a failed commit means an error and no file. `test_export_audit_failure_returns_no_file` covers this.
  - `changes` holds only `row_count`, `truncated`, `max_rows` and the non-null filters; no row data, and no keys matching `_FORBIDDEN_KEY`.
  - `target`, `org_id` and `app_key` on the row are NULL, so the export does not appear as an org or app event in filtered lists.
- **GREEN: CORS change** (`app/core/cors.py:47-50`). It only adds names to `expose_headers` on `ADMIN_POLICY` (`credentials=False`). The exposed headers carry nothing sensitive, and the origins are unchanged.
- **YELLOW (accepted risk, follow-up): bulk extraction cost** (`service.py:27`).
  - There is no rate limit, and the body is up to 10k rows held in memory.
  - Mitigations: `admin:audit:read` is held only via `admin:*`, every export is audited, and the rate limit is deferred by the plan (R7).
  - Denied (403) export attempts are not audited, the same as the list.

### Follow-ups (out of scope)
1. Rate limit or concurrency cap for `/audit-logs/export`, with config keys and `.env.example` entries.
2. Widen `escape_cell` to check after `lstrip()` (all Unicode whitespace), so NBSP and U+3000 are covered.
3. `outcome=denied` audit row for 403 export attempts via `record_denied`.
4. Statement timeout, and EXPLAIN on export queries that use the `org_id` OR filter over wide date ranges.
5. Optional `Content-Security-Policy: sandbox` on downloads.
