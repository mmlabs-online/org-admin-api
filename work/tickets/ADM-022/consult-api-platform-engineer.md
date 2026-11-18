ADM-022 consult (api-platform-engineer, read-only). The cap-plus-one query, no-store headers and CSV escaping are straightforward. Two things need care: the CORS expose list, and the fact that the session commits before a streamed body would run.

## Findings

1. **Route order.** `GET ""` is at router.py:28 and `GET /{audit_log_id}` (uuid) is at router.py:75. If `/export` is declared after the uuid route, `/audit-logs/export` is captured by it and returns 422 instead of reaching the export handler. Declare `/export` before line 75, with a comment saying why.
2. **Filters are inlined in the list handler** (router.py:32-50, built into `AuditLogFilter` at 52-63). The WHERE clause is inlined inside `AuditRepository.list` (repository.py:57-85). Copying either would let list and export drift.
3. **Session commit timing.** `get_session` commits after the handler returns (db/session.py:24-35, docstring says "before the response is sent"). A `StreamingResponse` generator would run after that, on a closed session. That makes streaming incompatible with "audit row in the same transaction as the read".
4. **`require_permission(errors=...)` forbids 401 and 403** (admin_auth/deps.py:128). These two are added automatically from `_ERROR_RESPONSES`. Only 422 needs adding, for `from > to`, which `AuditLogService.list` already raises at service.py:263-270. `_read_route` in router.py:21-25 is an older local helper doing the same thing by mutating the kwargs. Prefer `require_permission("admin:audit:read", errors={422: "..."})`.
5. **OpenAPI post-processing (core/openapi.py:25-43).**
   - It only rewrites responses with code >= 400, so a declared `200 text/csv` survives untouched.
   - It adds an `X-Request-ID` header to every response, including the 200.
   - Declare `response_class=Response` plus `responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}, "headers": {...}}}`. Otherwise FastAPI documents `application/json` with an empty schema. Use the pattern at auth/router.py:151 (`response_class=Response`).
   - The 200 `headers` dict must list `Content-Disposition`, the truncation headers and `Cache-Control`, so admin-pan's generated client sees them.
6. **CORS (core/cors.py:47).** Admin `expose_headers` is `"Retry-After, ETag, X-Request-ID"`. A browser (admin-pan) cannot read `Content-Disposition` or any custom truncation header cross-origin unless they are added there. Without this, the truncation signal is invisible to the actual requester. This is the main trap.
7. **Action catalogue.** `AuditAction` (actions.py:32-78) has no export action. `audit_log.action` is constrained only by the regex `^[a-z_]+(\.[a-z_]+)+$` and a length limit (models.py:33-35), not by an enum. A new `audit_log.exported` needs no migration, which matches the owner's "no migration".
8. **Allowed audit keys.** `_check_keys` (service.py:68-79) rejects change keys matching `hash|token|password|cookie|email|secret`. Flat scalar keys such as `row_count`, `truncated`, `max_rows`, `outcome`, `from` and `to` pass.
9. **Same permission as the list.** The `denied` path is already handled for the read permission by `RequirePermission`, which returns 403 and logs. `record_denied` (service.py:195-219) is only for service-level guards. A plain 403 by permission needs no audit row, the same as the list today.
10. **`user_agent` stripping happens at write time** (`_clean_user_agent`, service.py:55-58). The export reads the stored column, so "same stripping as the list" is automatic.

## Recommendations

**Endpoint contract**
- Path: `GET /api/v1/audit-logs/export`, auth class 1 (Bearer) with `admin:audit:read`, per the owner answer. A GET is acceptable despite the audit write: the side effect is only the audit entry, so it is idempotent in effect.
- Query params: extract the 10 filter params into one shared dependency, for example `AuditFilterDep` in `audit/deps.py` returning `AuditLogFilter`. Use it in both handlers so `from`, `to` and `org_id` cannot drift (AC1, R9 consistency).
- Do not accept `limit` or `offset`; the cap is fixed server-side. FastAPI ignores unknown query params silently, so document this in the endpoint description.
- Responses:
  - `200 text/csv; charset=utf-8`.
  - `Content-Disposition: attachment; filename="audit-log-YYYYMMDDTHHMMSSZ.csv"`. The name is server-generated, ASCII only, with no user input.
  - `Cache-Control: no-store`, as at me_router.py:24 and users/router.py:116.
  - Add `X-Content-Type-Options: nosniff`, since the body carries user-influenced text.
  - 401, 403 and 422 via `require_permission(errors={422: ...})`.
  - Keep the usual `X-Request-ID`.
- Truncation signal (AC1). Query `cap+1` rows and emit `cap`.
  - Headers: `X-Export-Truncated: true|false` and `X-Export-Row-Count: <n>` (n is at most 10,000). Never emit a total; a COUNT over a filtered audit table is wasted work.
  - Expose these headers plus `Content-Disposition` in `cors.py:47`, and document them in the OpenAPI 200 response.
  - Do not append a sentinel row or comment line to the CSV. It would corrupt parsers and import flows.
  - Because the list is ordered newest first (repository.py:90), a truncated export contains the newest 10,000 rows. Say so in the description. A client can narrow by setting `to` to the last row's `occurred_at`.
  - Use a module constant `EXPORT_MAX_ROWS = 10_000`. Do not add a settings key unless the owner wants it tunable (R5 only applies to env config).
- Return 200 with a header-only CSV when no rows match, not 204 or 404. That keeps the client's download path uniform.

**Streaming vs memory**
- Build in memory. Per finding 3, streaming would break the same-transaction audit row, and the commit would happen before the body is produced.
- 10,000 rows of mostly short text plus `changes` JSON is roughly 5–15 MB. That is acceptable. Cap the cell sizes implicitly: `user_agent` is at most 512 characters, and `changes` is small and flat.
- Fetch with `execute(select(AuditLog).where(...).order_by(occurred_at.desc(), id.desc()).limit(cap+1))`. Add `AuditRepository.export(filters, limit)` and refactor the WHERE into a shared `_where(filters)` used by both `list` and `export` (finding 2).
- Run the audit insert after the SELECT, before returning. If the audit write raises, the request fails with 500 and returns no data (fail closed). The session commit is then atomic with the read.
- Use `AuditLogger.record(AuditAction.AUDIT_LOG_EXPORTED, actor=principal, ctx=ctx, ...)`.
  - It needs a service method `AuditLogService.export(filters, principal, ctx)`, which keeps R1 layering.
  - The `from > to` check should be shared with `list`.
  - No `target`. Set `org_id=filters.org_id` and `app_key=filters.app_key` on the row so an org-scoped export shows up in that org's list (ADM-021).
  - `changes` is a flat scalar object, no row data: `row_count`, `truncated`, `max_rows`, and the filters actually applied, with `from` and `to` as ISO strings and `outcome` as the string value. Omit unset filters.
  - Exports of the audit log are themselves visible in the audit log. That is intended.
- Update the router docstring (router.py:1), which currently says "Not audited itself". The list stays unaudited and the export is audited.
- Add a test asserting that `audit_log.exported` shows up in the next list call.

**Columns and CSV formatting**
- Columns, in the `AuditLogRead` order (schemas.py:14-31): `id, occurred_at, actor_kind, actor_user_id, actor_label, action, outcome, target_type, target_id, app_key, org_id, changes, ip, user_agent, request_id`.
- Reuse `AuditLogRead.model_validate(r)` per row so list and export serialize identically.
- `occurred_at` is ISO 8601 UTC, for example `2026-11-18T09:30:00.123456+00:00`, which is stable and parseable.
- `changes` is compact JSON (`json.dumps(separators=(",", ":"), ensure_ascii=False)`) in one cell; empty string for null.
- Use stdlib `csv.writer` into `io.StringIO`:
  - RFC 4180 quoting.
  - `lineterminator="\r\n"`.
  - Use `QUOTE_MINIMAL` and let `csv` handle embedded quotes, commas and newlines.
- Encoding:
  - UTF-8, and `Content-Type: text/csv; charset=utf-8`.
  - Decision for the owner: a BOM helps Excel with non-ASCII text but pollutes machine consumers. I recommend no BOM.
- Formula injection (AC4):
  - Apply the standard OWASP rule to every string cell: if it starts with `=`, `+`, `-`, `@`, tab (`\t`) or CR (`\r`), prefix it with `'`. Add leading `\n` for good measure.
  - Apply it after serialization. The `changes` JSON starts with `{`, but it can contain a string beginning with `=` only inside, which Excel does not evaluate; the cell-start check is sufficient.
  - Real exposure is `actor_label`, `user_agent`, `request_id`, `app_key` and `action`, which are mostly constrained, and `user_agent`, which is attacker-controlled.
  - Do not apply the prefix to timestamp cells starting with a digit. A leading `-` applies only to negative numbers, and none are emitted.
  - Put the escaper in one helper and unit test it directly.
- Test the control-char case. `_clean_user_agent` strips controls at write, but legacy rows might still have a leading tab or CR. The cell escaper covers both.

**Tests (R8, AC5)**
- Happy path with several filters, including `org_id`, `from` and `to`.
- Cap: monkeypatch `EXPORT_MAX_ROWS` to a small value; assert `X-Export-Truncated: true`, count == cap, and newest-first. Also an exact-cap case (rows == cap gives `false`) and a cap-plus-one case.
- Audit row written with `row_count`, `truncated` and filters, and with no row data and no UA.
- 401 (no token), 403 (a token without `admin:audit:read`), and 422 for `from > to` or a bad enum.
- `/audit-logs/export` is not captured by the `{audit_log_id}` route.
- Headers: `Content-Type`, `Content-Disposition`, `Cache-Control: no-store` and CORS `Access-Control-Expose-Headers` for an allowed origin.
- OpenAPI snapshot: 200 is `text/csv` and the route carries the permission extension.
- Formula-injection cells for each of `= + - @ \t \r`.
- Audit-failure rollback: patch the logger to raise and assert 500 with no CSV body.

**Docs and spec.** Record the export under spec §5.4 as the owner said. Add a changelog or contract note that the export is an additive endpoint in v1, not a breaking change (R9). Mention the new `audit_log.exported` action in the `GET /audit-logs` `action` filter. The `AuditAction` enum is the filter, so the new value is additive for list clients that tolerate unknown values; admin-pan's TypeScript union will need it regenerated.

## Risks
- **Hidden truncation.** If the CORS expose list (cors.py:47) is not updated, a browser client cannot see the truncation flag. This is the main way AC1 fails in practice.
- **Memory.** A malicious or very large `changes` JSON can inflate rows, though entries are flat and small. 10k rows in memory per request, times concurrency, could be significant. The existing rate limiter or concurrency may not apply to Bearer admin routes; a per-principal rate limit or semaphore would be a follow-up, not in scope (R7).
- **Slow unindexed filters.** The export SELECT has no total count, but a filter that matches few rows on an unindexed column scans the table. `ix_audit_log_occurred_at_id` serves the ordering; filters on `org_id` or `actor_user_id` should be checked with EXPLAIN. database-optimizer could look at this if ADM-021 did not.
- **Export as an exfiltration path.** `admin:audit:read` now allows bulk download, which was possible by paging at limit 100 anyway. The audit row and the cap mitigate it. A separate permission was rejected by the owner.
- **Transaction held open.** The read and the audit insert in one transaction holds a pool connection for the CSV build time, a few hundred ms at most. Build the CSV after the audit flush, or before, but do not do slow work in between.
- **Enum drift.** The new `AuditAction` value is visible in OpenAPI via the `action` query enum and the contract file, so regenerate any committed OpenAPI snapshot.

## Open questions
1. Truncation signalling: are custom headers (`X-Export-Truncated`, `X-Export-Row-Count`) acceptable, or does the owner want it in-band? I recommend headers only, with the CORS expose-list change.
2. UTF-8 BOM for Excel: yes or no? I recommend no.
3. Is "newest 10,000" the intended truncation, or should the export be oldest-first so that a continuation by `from` works? I recommend newest-first, consistent with the list, plus a documented continuation by `to`.
4. Should the cap be a constant, or a setting in `app/core/config.py` and `.env.example`? I recommend a constant.
5. Does the owner want the denied attempts (missing permission) audited? The list today does not, so I recommend no, for consistency.
6. Formula-injection prefix: `'` (OWASP standard) vs a leading space. I recommend `'`; confirm that admin-pan does not post-process cells.

Files referenced: `app/modules/audit/{router,service,repository,schemas,actions,deps,models}.py`, `app/modules/admin_auth/deps.py`, `app/core/openapi.py`, `app/core/cors.py`, `app/db/session.py`.
