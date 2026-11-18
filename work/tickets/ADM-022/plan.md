Status: READY

# Plan: ADM-022, server-side CSV export of the audit log

## Spec summary
- Spec §5.4 Audit log: "Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors."
- Owner answers (ticket, 2026-11-18):
  - "In scope separately from §5.11. This is a synchronous CSV export on the same filters as the list: no saved definitions, no schedules, no xlsx. §5.11 stays blocked, and the export is recorded under spec §5.4 as part of the audit log."
  - "Permission: the existing `admin:audit:read`, so no migration."
  - "Row cap: 10,000 rows. The response says when the export was truncated."
- Spec §5.11 (for the boundary): "no ticket may implement reports before then". This ticket implements no report type, no definitions, no schedules and no xlsx. It is an audit-log feature under §5.4, as the owner decided.
- ADR-0009 §8 says "Reading is not audited." That stays true for list and get. The export is the one audited read (ticket AC3), and a one-line ADR amendment records this.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/audit-logs/export` takes the same filters as the list and returns `text/csv` with a 10,000-row cap. The response says when it was truncated (headers `X-Export-Truncated`, `X-Export-Row-Count`, both exposed through CORS). | `tests/test_audit_logs_export.py` › `test_export_happy_path_with_filters`, `test_export_cap_truncates_newest_first`, `test_export_exact_cap_not_truncated`, `test_export_cap_constant_is_10000`, `test_export_empty_is_header_only`, `test_export_filters_match_list`, `test_export_route_not_captured_by_id`, `test_export_headers`. `tests/test_admin_cors.py` › `test_export_exposes_truncation_headers`. |
| 2 | Permission is the existing `admin:audit:read`; no migration. | `tests/test_route_auth_classes.py` BEARER map has the `/export` entry (the existing `test_every_route_is_classified` passes). `test_export_401_without_token`, `test_export_403_without_permission`. `alembic check` reports no drift, and no file is added under `migrations/versions/`. |
| 3 | Each export writes one audit entry (filters and row count, no row data) in the same transaction. | `test_export_writes_one_audit_row` (exact `changes` keys, no row ids, no UA from rows), `test_export_row_not_in_its_own_file`, `test_export_audit_failure_returns_no_file` (strict client: audit write raises → not 200, no row, no CSV), `test_export_422_from_after_to_writes_no_row`. |
| 4 | No PII beyond the list: same columns as `AuditLogRead`, no joins, stored (already stripped) `user_agent`. CSV cells are protected against formula injection. | `test_export_columns_equal_read_schema`. `tests/test_audit_csv.py` › `test_escape_cell_triggers` (parametrised over `= + - @ \t \r \n ＝ ＋ － ＠` and leading spaces), `test_escape_cell_leaves_safe_values`, `test_render_csv_quoting_and_newlines`. API test `test_export_formula_injection_escaped` (via `user_agent` and an org `name` inside `changes`). |
| 5 | Tests: happy path with filters, row cap, audit row written, 401/403, CSV-injection escaping. | All tests above, plus green `make check`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/audit/actions.py` | Add `AUDIT_LOG_EXPORTED = "audit_log.exported"` after `AUDIT_LOG_PURGED`. | AC3: the export's own audit row. It matches the DB CHECK regex, so no migration. |
| `app/modules/audit/repository.py` | Extract the WHERE build into `_where(filters) -> list[ColumnElement[bool]]` (behaviour unchanged) and use it in `list`. Add `export(filters, *, limit) -> list[AuditLog]` with the same `order_by(occurred_at.desc(), id.desc())`, `.limit(limit)`, no count. Update the module docstring to name `export` as a read. | AC1: the same filters as the list, without copying the WHERE clause (both consults). |
| `app/modules/audit/csv_export.py` (new) | Pure functions: `escape_cell(str) -> str`, `COLUMNS` (= `AuditLogRead.model_fields` keys, in order), `render_csv(rows: Sequence[AuditLog]) -> str`. No I/O, no DB. | AC4: one testable escaper and one serializer. Keeps CSV formatting out of the router (R1). |
| `app/modules/audit/service.py` | Add module constant `EXPORT_MAX_ROWS = 10_000` and dataclass `AuditExport(content: str, row_count: int, truncated: bool)`. Add a `_check_range(filters)` helper and use it in both `list` and `export`. Add `AuditLogService.export(filters, *, actor, ctx) -> AuditExport`. | AC1, AC3, R1 (business rules and audit write in the service). |
| `app/modules/audit/router.py` | Move the 10 filter `Query` params into a dependency `audit_filters(...) -> AuditLogFilter` (`AuditFilterDep`). Use it in `list_audit_logs` and the new `export_audit_logs`. Declare `/export` **before** `/{audit_log_id}` with a comment saying why. Update the module docstring: list and get are not audited; the export is. | AC1 (same filters, no drift), R3, R9. |
| `app/core/cors.py` | `ADMIN_POLICY.expose_headers` becomes `"Retry-After, ETag, X-Request-ID, Content-Disposition, X-Export-Truncated, X-Export-Row-Count"`. | AC1: without this, admin-pan (cross-origin `fetch`) cannot read the truncation signal or the filename. |
| `tests/test_audit_logs_export.py` (new) | API tests listed below. | AC1–AC5, R8. |
| `tests/test_audit_csv.py` (new) | Unit tests for `escape_cell` and `render_csv`. | AC4. |
| `tests/test_route_auth_classes.py` | Add `("GET", f"{V1}/audit-logs/export"): "admin:audit:read"` to `BEARER`. | R3: the deny-by-default classification test fails without it. |
| `tests/test_admin_cors.py` | Update the `EXPOSE` constant. Add `test_export_exposes_truncation_headers`. | The expose list changes. AC1 must be visible to the browser. |
| `tests/test_cors.py` | Update the literal expose string at line 346 to the new value. | The same constant change. |
| `tests/test_openapi_contract.py` | Add `test_audit_export_operation`: 200 is `text/csv` with documented headers, `x-permission` is `admin:audit:read`, and 401/403/422 use the envelope. | AC1 contract for admin-pan's generated client. |
| `docs/spec-v1.md` | One dated note under §5.4 (exact text in Approach step 9). | The owner said the export is recorded under §5.4. |
| `docs/adr/0009-audit-log.md` | One-line amendment at the end of the file. | §8 says "Reading is not audited". The export is the audited exception. |
| `docs/identity-architecture.md` | One endpoint-table row after line 182 for `GET /api/v1/audit-logs/export`. | Keeps the endpoint/auth-class table complete (R3 source of truth). |

No other files. In particular: no migration, no `config.py` or `.env.example` change, no `rate_limit.py` change, and `app/modules/audit/deps.py` is untouched.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_audit_csv.py` › `test_escape_cell_triggers` (parametrised: `=`, `+`, `-`, `@`, `\t`, `\r`, `\n`, U+FF1D, U+FF0B, U+FF0D, U+FF20, and `"  =1+1"` with leading spaces) | AC4: the value is prefixed with `'`. |
| `tests/test_audit_csv.py` › `test_escape_cell_leaves_safe_values` (UUID, ISO timestamp, `::1`, `203.0.113.5`, `organization.created`, `""`, `{"a":1}`) | AC4: real data is not corrupted. |
| `tests/test_audit_csv.py` › `test_render_csv_quoting_and_newlines` | Header row = `COLUMNS`. A value with `,`, `"` and an embedded newline round-trips through `csv.reader`. `\r\n` terminators. `None` → empty cell. `changes` is compact sorted JSON. |
| `tests/test_audit_logs_export.py` › `test_export_happy_path_with_filters` | AC1/AC5: `org_id`, `from`, `to` and `action` combined. Only matching rows, newest first, status 200. |
| › `test_export_filters_match_list` | AC1: for several filter sets, the export ids equal the `GET /audit-logs?limit=100` ids. This is the anti-drift guard. |
| › `test_export_cap_truncates_newest_first` | AC1: monkeypatch `app.modules.audit.service.EXPORT_MAX_ROWS = 3` with 4 matching rows → 3 data rows, the newest 3, `X-Export-Truncated: true`, `X-Export-Row-Count: 3`. |
| › `test_export_exact_cap_not_truncated` | AC1: 3 rows with cap 3 → `false`, `3`. |
| › `test_export_cap_constant_is_10000` | Owner value pinned: `EXPORT_MAX_ROWS == 10_000`. |
| › `test_export_empty_is_header_only` | 200, header line only, `false`, `0`. |
| › `test_export_headers` | `Content-Type: text/csv; charset=utf-8`; `Content-Disposition` matches `^attachment; filename="audit-log-\d{8}T\d{6}Z\.csv"$`; `Cache-Control: no-store`; `X-Content-Type-Options: nosniff`; `X-Request-ID` present; no BOM (the body starts with `id,`). |
| › `test_export_columns_equal_read_schema` | AC4: header == `list(AuditLogRead.model_fields)` (the same set as `READ_KEYS` in the list tests). Cells equal the list JSON values for one row. |
| › `test_export_formula_injection_escaped` | AC4: a row with `user_agent='=HYPERLINK("x")'` and one with `changes={"name": {"from": None, "to": "@SUM(1)"}}`. Parse with `csv.reader`: the `user_agent` cell starts with `'=`. The `changes` cell starts with `{` and is valid JSON. |
| › `test_export_writes_one_audit_row` | AC3: exactly one new row: `action=audit_log.exported`, `outcome=success`, actor = the staff user, `target_type`/`target_id`/`org_id`/`app_key` all NULL. `changes` keys are exactly `{"row_count", "truncated", "max_rows"}` plus the non-null filter names. Values are scalars. No row ids from the exported rows appear in `changes`. |
| › `test_export_row_not_in_its_own_file` | AC3: the first export does not contain its own row. A second unfiltered export contains the first export's row. |
| › `test_export_audit_failure_returns_no_file` | AC3 (fail closed, same transaction). `make_strict_client` with `AuditLogger.record` patched to raise: the response is not 200, there is no CSV body, and the row count is unchanged. |
| › `test_export_401_without_token` | R8/AC5. |
| › `test_export_403_without_permission` | R8/AC5: token with e.g. `admin:users:read` only → 403 envelope, no audit row. |
| › `test_export_422_from_after_to_writes_no_row` | R8: `from > to` → 422 `unprocessable` with `details[0].field == "from"`. Also a bad enum (`outcome=nope`) → 422. No audit row in either case. |
| › `test_export_route_not_captured_by_id` | `/audit-logs/export` reaches the export handler (200 `text/csv`), not the 422 from UUID parsing. |
| › `test_export_ignores_limit_offset` | `?limit=1&offset=5` has no effect (documented: the cap is server-side). |
| `tests/test_admin_cors.py` › `test_export_exposes_truncation_headers` | AC1: with the allowed admin origin, `Access-Control-Expose-Headers` includes `Content-Disposition, X-Export-Truncated, X-Export-Row-Count`. |
| `tests/test_openapi_contract.py` › `test_audit_export_operation` | The 200 content is `text/csv` with `{"type": "string"}`. The 200 headers include `Content-Disposition`, `X-Export-Truncated`, `X-Export-Row-Count` and `X-Request-ID`. `x-permission` is `admin:audit:read`. 401/403/422 use the envelope. No `limit`/`offset` params. |

Test notes:
- Use `pytestmark = pytest.mark.backdate_audit` and the `_add` helper pattern from `tests/test_audit_logs_api.py` for distinct `occurred_at`.
- Scope cap and count assertions with a filter, e.g. a fresh `actor_user_id`, so setup rows written by fixtures do not interfere.
- The audit-row tests use `make_client(admin_permissions=["admin:audit:read"], admin_user=actor)`.

## Approach
1. **Action.** Add `AuditAction.AUDIT_LOG_EXPORTED`. It matches `^[a-z_]+(\.[a-z_]+)+$` and is ≤ 64 characters, so no migration (R2 is not triggered). It also appears in the `action` filter enum of the list. This is additive (R9).
2. **Repository.** Add `_where(filters)` with the exact current conditions, including the `org_id` OR. Then:
   - `list` keeps its signature and its count query.
   - `export(filters, *, limit)` is a single SELECT with the list's order and `.limit(limit)`. It has no `count(*)` and no offset.
   - Only SQLAlchemy here (R1).
3. **CSV module (`csv_export.py`).**
   - `COLUMNS = tuple(AuditLogRead.model_fields)`.
   - `render_csv(rows)`: for each row, `AuditLogRead.model_validate(row).model_dump(mode="json")`, the same serialization as list and get. Each cell then becomes:
     - `None` → `""`
     - a dict (`changes`) → `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
     - anything else → `str(v)`
   - Then `escape_cell` on every cell, uniformly.
   - Write with `csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")` into `io.StringIO`, header row first.
   - `escape_cell(v)` returns `"'" + v` if `v[:1]` or `v.lstrip(" ")[:1]` is in `{"=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠"}`. Otherwise it returns `v` unchanged.
4. **Service (`AuditLogService.export(filters, *, actor: AdminPrincipal, ctx: AuditContext)`).**
   1. Run `_check_range(filters)`, the existing `from > to` 422 logic, now shared with `list`.
   2. Read `EXPORT_MAX_ROWS` as a module global at call time, so the monkeypatch works.
   3. `rows = await repo.export(filters, limit=cap + 1)`; `truncated = len(rows) > cap`; `rows = rows[:cap]`.
   4. `content = render_csv(rows)`.
   5. Call `AuditLogger(session).record(AuditAction.AUDIT_LOG_EXPORTED, actor=actor, ctx=ctx, changes=...)`. It takes no `target`, `org_id` or `app_key`. `changes` is the flat object `{"row_count": n, "truncated": bool, "max_rows": cap, **{k: to_json(v) for k, v in asdict(filters).items() if v is not None}}`. Every key passes `_check_keys` (`hash|token|password|cookie|email|secret` matches none). This is the "flat object of allowlisted scalars" shape (ADR-0009 amendment).
   6. Return `AuditExport(content, len(rows), truncated)`.
   - The service needs an `AsyncSession` for the logger: keep `self.session` alongside `self.repo` in `__init__`.
   - The audit row is written last, so the file never contains its own row. Any error before or during the write raises, `get_session` rolls back, and no file is sent: fail closed, same transaction (R4 spirit, AC3).
5. **Router.**
   - Add `audit_filters(...)` with the 10 params copied verbatim from `list_audit_logs`, the same order, aliases and description. It returns `AuditLogFilter`. `AuditFilterDep = Annotated[AuditLogFilter, Depends(audit_filters)]`. `list_audit_logs` uses it. The OpenAPI query params must stay identical, which `test_openapi_contract` and the existing list tests guard.
   - New handler, declared above `/{audit_log_id}`:
     - `@router.get("/export", response_class=Response, **kw)`.
     - `kw = require_permission("admin:audit:read", errors={422: "Invalid filter, or from after to"})`, then `kw["responses"][200] = {"description": ..., "content": {"text/csv": {"schema": {"type": "string"}}}, "headers": {Content-Disposition, X-Export-Truncated, X-Export-Row-Count, Cache-Control}}`.
     - Signature: `session: SessionDep, principal: AdminDep, ctx: AuditContextDep, filters: AuditFilterDep`.
     - It builds `Response(content=result.content, media_type="text/csv; charset=utf-8", headers={...})` with:
       - `Content-Disposition: attachment; filename="audit-log-<UTC %Y%m%dT%H%M%SZ>.csv"` (server time only, never user input)
       - `Cache-Control: no-store`
       - `X-Content-Type-Options: nosniff`
       - `X-Export-Truncated: true|false`
       - `X-Export-Row-Count: <n>`
     - The description says: newest first; at most 10,000 rows; when truncated, narrow with `to` = the last row's `occurred_at`; `limit`/`offset` are not accepted; the export is audited (`audit_log.exported`).
   - HTTP only (R1). Auth class 1 Bearer + permission (R3). Errors via the envelope (R6).
6. **CORS.** Extend `ADMIN_POLICY.expose_headers` (additive) and update the two test constants.
7. **Memory and transaction.**
   - Build in memory with a plain `Response`, not `StreamingResponse`. The session commits before the body would stream (`app/db/session.py:24-35`), which would break AC3.
   - 10,000 rows is a few MB to roughly 15 MB worst case, which is acceptable for an `admin:*`-only endpoint.
8. **Rules.**
   - R1: the layering above.
   - R2: no schema change.
   - R3: Bearer + `admin:audit:read`.
   - R4: audit row in the same transaction.
   - R5: no new config. The cap is a code constant per the owner's value; it is not an env setting.
   - R6: envelope errors.
   - R7: scope in this table only.
   - R8: the tests listed above.
   - R9: `/api/v1/audit-logs/export` is a sub-resource action on the plural noun. It is additive, and the non-`Page[T]` body is a file. It is not a JSON collection, so no new R9 exception is needed. A reviewer may still want a note; see follow-ups.
   - R11: no emails or secrets in CSV or `changes`.
9. **Docs (one line each).**
   - `docs/spec-v1.md`, under §5.4 after line 53: `> 2026-11-18 (ADM-022, owner): staff export the filtered audit log as CSV with GET /api/v1/audit-logs/export (admin:audit:read; same filters as the list; newest first, at most 10,000 rows, truncation signalled by X-Export-Truncated; each export writes an audit_log.exported entry). Part of the audit log, separate from the §5.11 reports, which stay undecided.`
   - `docs/adr/0009-audit-log.md`, at the end: `- **2026-11-18 (ADM-022):** the CSV export (GET /api/v1/audit-logs/export) is the one audited read: one audit_log.exported row (filters, row_count, truncated, max_rows; no row data) in the request transaction. List and get stay unaudited.`
   - `docs/identity-architecture.md`, a new row: `| Admin: GET /api/v1/audit-logs/export (same filters as the list; text/csv, ≤ 10,000 rows, X-Export-Truncated) | Bearer aud=admin + require_permission (admin:audit:read) | audited CSV export of the audit log | ADM-022 |`.
10. Run `make check`.

## Specialist input used
Disagreements between the two consults, and how each is resolved:
- **Truncation detection: cap+1 (api-platform) vs `count(*)` with `X-Total-Count` (security). Adopted cap+1.** The owner asked only that the response say *when* it was truncated, not how many rows there were in total. `count(*)` adds a second scan on the slowest filter path (the `org_id` OR) for no AC. A client that wants the total can call the list with `limit=1`. So the headers are `X-Export-Truncated` and `X-Export-Row-Count`, with no total.
- **`org_id`/`app_key` on the export's own row: set them (api-platform) vs NULL (security). Adopted NULL.** The export row is about the audit log, not about an organization or app. Setting `org_id` would make an org-filtered list (ADM-021: "rows about this organization") show an export as an event *of that org*. The filters are recorded in full in `changes`, so traceability is kept: auditors find exports with `action=audit_log.exported`. It also matches the CHECK pairing: no target, so `target_type` and `target_id` are both NULL.
- **`QUOTE_MINIMAL` (api-platform) vs `QUOTE_ALL` (security). Adopted `QUOTE_MINIMAL`.** Quoting does not stop formula evaluation (spreadsheets evaluate a quoted `"=..."`); the `'` prefix does. `QUOTE_MINIMAL` is standard RFC 4180 output and is smaller. Embedded commas, quotes and newlines are still quoted correctly. The defence is `escape_cell`, which is unit-tested.
- **Rate-limit scope: none (api-platform, follow-up) vs new `audit_export` scope (security). Adopted none; recorded as a follow-up.**
  - No AC asks for it.
  - Admin routes have no rate limit today, and `admin:audit:read` is held only through `admin:*` (ADR-0009 §8).
  - Every export is audited, and each request is capped at 10,000 rows.
  - Adding it needs new config keys, an `.env.example` change and a rate-limit scope, which is beyond the ticket (R7).
- **Escape set: adopted the security superset**: `= + - @ \t \r \n`, the full-width `＝ ＋ － ＠`, and a check after leading spaces. It costs nothing and is applied uniformly to every cell, including `changes` (both consults agree on uniform application).
- **`changes` JSON: adopted security's `sort_keys=True, ensure_ascii=True`.** Output is deterministic, and bidi or invisible characters in org names cannot spoof the sheet. Trade-off: Vietnamese text inside `changes` shows as `\uXXXX`; see Needs owner's decision. Other columns are written as stored.
- **Audit `changes` shape: adopted security's flat object with the dataclass filter names** (`occurred_from`/`occurred_to`, not `from`/`to`), which avoids confusion with the `{field: {from, to}}` diff shape. Adopted api-platform's flat keys rather than a nested `filters` object, to match ADR-0009's "flat object of allowlisted scalars". No `total`, because of cap+1.
- **Adopted from both:**
  - route order before `/{audit_log_id}`
  - shared filter dependency and a shared WHERE helper
  - in-memory build, not streaming (session commit timing)
  - `AuditLogRead.model_validate` per row
  - server-generated ASCII filename
  - `Cache-Control: no-store` and `nosniff`
  - CORS expose list
  - 200 header-only CSV when empty
  - no BOM
  - newest-first with a documented `to` continuation
  - cap as a code constant
  - no audit of 403 denials, consistent with the list
  - router docstring update
- **Rejected (deferred):**
  - `Content-Security-Policy: sandbox` (security, Informational): not needed for an `attachment` download.
  - The 10,001-row insert test (security): replaced by a monkeypatched small cap plus a constant-pin test; equivalent coverage, faster CI.
  - Statement timeout, EXPLAIN review, per-principal limits, required date range: follow-ups.

## Assumptions
- ADM-021 (shared filters, `org_id` filter, single-entry read) and ADM-004b are DONE (backlog line 69, code present).
- `AdminPrincipal` is an accepted `actor` for `AuditLogger.record`, as in `app_credentials/router.py`.
- Moving the list's query params into a dependency leaves the list's OpenAPI params and behaviour unchanged. If FastAPI reorders params, only the order in the document changes, which is not breaking.
- In-memory size (≤ ~15 MB per request) is acceptable for an endpoint only `admin:*` holders can call.

### Needs owner's decision (non-blocking; defaults applied)
1. **Truncation signal:** response headers only (`X-Export-Truncated`, `X-Export-Row-Count`), with no total and no in-band marker row. Default: as planned.
2. **UTF-8 BOM** for Excel. Default: none (cleaner for machine consumers). admin-pan can prepend one client-side if Excel users need it.
3. **Truncation keeps the newest 10,000 rows**, consistent with the list order. Default: as planned.
4. **Cap as a code constant** (`EXPORT_MAX_ROWS`), not an env setting. Default: constant.
5. **`changes` cell uses `ensure_ascii=True`**: Vietnamese text inside `changes` appears as `\uXXXX`. Default: as planned (safer). The owner may prefer readable UTF-8.
6. **Formula escape prefix `'`** (OWASP), which is visible in tools that do not interpret it. admin-pan should not post-process cells.
7. **No rate limit and no denied-attempt (403) audit row** for the export. Default: none, consistent with the list; listed as follow-ups.

## Blocking questions
None.

## Split proposal
None. Estimated diff:
- Production code: ~180 lines (repository ~25, csv module ~50, service ~50, router ~50, cors 1, actions 1).
- Docs: 3 lines.
- Tests: ~300 lines.

That is one feature (§5.4 export), so no split.

## Data model / migration
None. `audit_log.exported` is a new value of the `action` text column. It satisfies the existing CHECK (`^[a-z_]+(\.[a-z_]+)+$`, ≤ 64 characters). No new permission row: it reuses `admin:audit:read`. `alembic check` must report no drift.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/audit-logs/export` | Query: the same filters as `GET /api/v1/audit-logs` (`actor_user_id`, `actor_kind`, `action`, `target_type`, `target_id`, `app_key`, `org_id`, `outcome`, `from`, `to`). No `limit`/`offset`. | 200 `text/csv; charset=utf-8`, no BOM, `\r\n`. Header row: `id,occurred_at,actor_kind,actor_user_id,actor_label,action,outcome,target_type,target_id,app_key,org_id,changes,ip,user_agent,request_id`. Newest first, ≤ 10,000 data rows. Headers: `Content-Disposition: attachment; filename="audit-log-YYYYMMDDTHHMMSSZ.csv"`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Export-Truncated: true\|false`, `X-Export-Row-Count: n`, `X-Request-ID`. CORS exposes `Content-Disposition, X-Export-Truncated, X-Export-Row-Count`. Side effect: one `audit_log.exported` audit row. | 200, 401, 403, 422 (`from` after `to`, bad enum/uuid/`app_key` pattern) | `admin:audit:read` (Bearer `aud=admin`, R3 class 1) |

## Follow-ups (outside this ticket, R7)
- Rate limit or concurrency cap for the export (per principal or per IP; new config keys). Security consult.
- `outcome=denied` audit rows for 403 export attempts (needs a hook in `RequirePermission`).
- Statement timeout and EXPLAIN review of the export SELECT for wide `org_id` ranges (database-optimizer).
- Replace the older `_read_route` helper in `audit/router.py` with `require_permission(errors=...)`.
- Optional `Content-Security-Policy: sandbox` on file downloads.
- admin-pan: regenerate the OpenAPI client (new `audit_log.exported` action enum value, export operation), read the exposed headers, and tell users about the `'` prefix.
