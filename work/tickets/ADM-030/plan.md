Status: READY

# Plan — ADM-030: Bug reports — image attachments (upload from apps, download for staff)

READY because every blocking question below has a default the plan already uses. Ming's answers at the
batch round (batch 2026-11-20-1) only change the items each question names. **Gate: High** (migration,
`app/core/**`, `.env.example`). An agent never merges it.

## Scope summary
Sources:
- `docs/spec-v1.md` §5.12, lines 138–208.
- ADR-0018 (attachment storage), ADR-0019 (submit, D28), ADR-0020 (triage, D29).
- `work/decisions.md`: no entry about attachments.

**Spec quotes this plan implements:**
- §5.12 l.154: "attachments (0..5 images, including a pasted screenshot …)".
- l.151 (staff side): "view its attachments"; "`admin:bug-reports:read` (list, detail, download attachments)".
- l.161: "The reporter submits and gets the created report id back (and the per-attachment outcome, OQ35)."
- l.174: "Staff reads and attachment downloads are **not** audited."
- l.179: "Attachment metadata rows stay after their Drive file is deleted, with a **deleted marker**, until the report itself is deleted."
- l.187 (OQ35): "If the Drive keys are not set (attachments off) or Drive fails at submit time, the report is **still accepted as text**; the response tells the app which attachments failed. In admin-pan, a report whose Drive file is missing shows **"attachment unavailable"** …"
- l.190: "attachment uploads sent with the cookie need the same CORS (`apps.origins`) and CSRF (`Origin` + `X-MM-App`) checks as every other cookie POST (R11)."
- l.194: "**Files always pass through this API.** Upload: raw-body `PUT` per file, streamed with a byte cap and a magic-byte type check (… `python-multipart` stays out). Download: proxied by this API with fixed safe headers (`nosniff`, CSP `sandbox`, `Cross-Origin-Resource-Policy`); Drive file ids never reach the browser; admin-pan fetches with its Bearer and shows `blob:` URLs …"
- l.195: "`<root>/<app_key>/…` … The DB row is written after Drive accepts the file; if the DB write fails the Drive file is deleted; Drive creates are never retried."
- l.204 (OQ24): "**images only — PNG, JPEG, WebP, GIF — at most 5 MB per file and at most 5 files per report** … the API does **not** re-encode images … content-type must be checked by content (magic bytes), not by header; SVG/HTML must not be served inline … downloads served with `Content-Disposition: attachment` / `nosniff`."

**Ticket scope:** all bullets, except the last one ("Wire the ADM-027 sweep CLI to the attachment rows"). That bullet moves to slice 2; see `## Split proposal`.

**Explicitly in scope: the ADM-027 open 🟡.** `work/tickets/ADM-027/security-review.md` finding 1 says one Drive call has no overall deadline, and the token refresh retries while holding the lock. This ticket fixes it, together with 💭2 (`expires_in` clamp).
- Both consults recommend this: api #7, security R-4.
- This ticket is the first to put Drive behind user traffic.
- It touches `app/modules/attachments/drive.py`, which is outside the ticket's original file list. It is recorded here per R7.

**Conflict flagged (spec wins):** the spec says `PUT` (l.194); the ticket says `POST`. See Blocking question Q1. The default is `POST`, with a one-word spec edit.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Each allowed type (PNG, JPEG, WebP, GIF) uploads and downloads byte-identical. | `test_bug_report_attachments_upload.py › test_upload_each_type_roundtrip[png,jpeg,webp,gif]`: upload, then staff download through the fake Drive; `sha256` equal. 201 body has the 4 fields; stored `content_type` = the sniffed type. |
| 2 | A disallowed or mismatched type gets 415 `unsupported_media_type`. | Unit: `test_image_types.py` (all cases). API: `test_upload_rejects_type[svg,html,pdf,jpg_alias,missing,mismatch_png_as_jpeg,too_short]`. Each gives 415, with no Drive create and no row. |
| 3 | > 5 MB gets 413 `payload_too_large` without reading the body. | `test_upload_413_before_body_read`: a raw ASGI harness counts `receive()` body messages and must see 0. `test_upload_413_lying_length` / `test_upload_411_chunked`: a missing `Content-Length` gives 411 `length_required`. |
| 4 | The 6th file gets 409 `attachment_limit_reached` (PLAN picks 409, Q3). | `test_upload_sixth_is_409`. `test_concurrent_uploads_respect_cap`: two parallel uploads at count 4 give one 201 and one 409; the loser's Drive file is deleted; 5 rows. |
| 5 | Only the reporter, on an open report, within the window, can upload (403 otherwise). | `test_upload_denied_403[other_user_same_app,other_app,unknown_report]` each give the same code `attachment_upload_denied`, with byte-identical bodies. `test_upload_closed_403[done,window_expired]` give `attachment_upload_closed`. |
| 6 | Drive off or failing: the report still exists and the response is a per-file failure. | `test_upload_drive_disabled_503`, `test_upload_drive_failure_503[unavailable,quota,auth,rate]`: 503 `attachment_storage_unavailable`; the report row is unchanged; no attachment row. A retryable failure has `Retry-After`. |
| 7 | Download of a missing file gives a stable error. | `test_download_unavailable_404[drive_not_found,deleted_marker,drive_disabled]`: 404 `attachment_unavailable`, the same body each time. `test_download_drive_transient_503`. |
| 8 | Download headers are asserted. | `test_download_headers`: `Content-Type` comes from the DB; `Content-Length` = `size_bytes`; `Content-Disposition: attachment; filename="<aid>.<ext>"`; `X-Content-Type-Options: nosniff`; `Content-Security-Policy: default-src 'none'; sandbox`; `Cache-Control: no-store`; `Cross-Origin-Resource-Policy: same-origin`. |
| 9 | Drive ids are absent from every response. | `test_drive_ids_never_exposed`: the fake Drive returns sentinel ids. Assert they are absent from every upload, detail and download response body and header, and from error bodies. |
| 10 | No file content, filenames or Drive ids in logs or audit rows. | `test_no_content_or_ids_in_logs_and_audit`: caplog plus every `audit_log` row. Check for the sentinel Drive id, a sentinel byte string embedded in the image, and the `Content-Disposition` filename sent by the client (ignored). |
| 11 | ADM-027 🟡: one Drive call has an overall deadline. | `test_attachments_drive.py › test_call_deadline_bounds_retries`, `test_token_fetch_deadline_releases_lock`, `test_stream_deadline`, `test_expires_in_clamped[inf,nan,-5,1e9]`. |
| 12 | Staff detail lists attachments. | `test_bug_reports_staff_api.py › test_detail_lists_attachments` (ordered by `created_at, id`; no Drive id). |
| 13 | Auth classes, OpenAPI and audit coverage hold. | `make check`: `test_route_auth_classes.py`, `test_openapi_contract.py`, `test_audit_coverage.py` are extended. |

## Ming verification after merge
- Against the real Drive (staging, keys set):
  - Upload one PNG from a browser app (e.g. ricotdin dev origin) right after creating a report.
  - In admin-pan, open the report and view the image via the Bearer fetch to `blob:`.
  - In the Drive UI, the file is under `<root>/<app_key>/`, with name = the attachment id.
- With Drive keys unset on staging: an upload returns 503 `attachment_storage_unavailable` and the report is listed in admin-pan.
- Real-device check: a 5 MB image passes through Cloudflare Tunnel.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261120_<rev>_adm030_bug_report_attachments.py` | create | New table, with a hand-reviewed `downgrade()` (R2) | **High** |
| `app/db/models.py` | modify (l.7 area) | Import `BugReportAttachment` (R2) | **High** |
| `app/core/errors.py` | modify | Base classes `LengthRequiredError` (411), `PayloadTooLargeError` (413), `UnsupportedMediaTypeError` (415), `ServiceUnavailableError` (503). Extend the `ErrorBody.code` description (l.151–155). Add `411: "length_required"` and `503: "service_unavailable"` to `_HTTP_CODES` (l.202). | **High** |
| `app/core/config.py` | modify (after l.83) | 5 settings: `bug_report_attachment_rate_limit` (60), `bug_report_attachment_rate_window_seconds` (3600), `bug_report_attachment_user_rate_limit` (10), `bug_report_attachment_user_rate_window_seconds` (60), `bug_report_attachment_window_seconds` (900, `ge=60, le=86400`) | **High** |
| `app/core/rate_limit.py` | modify (l.71–123) | New per-IP scope `bug_report_attachment` | **High** |
| `.env.example` | modify (after l.49) | The 5 keys, without values (R5) | **High** |
| `app/modules/attachments/drive.py` | modify | The ADM-027 🟡 fix: call, token and stream deadlines, plus the `expires_in` clamp | no (security-review trigger: none by path; reviewed under area `security`) |
| `app/modules/bug_reports/models.py` | modify | `BugReportAttachment` model | no |
| `app/modules/bug_reports/image_types.py` | create | Pure: `parse_declared()`, `sniff()`, `extension()` | no |
| `app/modules/bug_reports/upload.py` | create | Pure-ish: `read_exact(chunks, expected, cap, deadline)` (byte cap, deadline, exact length) | no |
| `app/modules/bug_reports/errors.py` | modify | Module errors (see API contract) | no |
| `app/modules/bug_reports/repository.py` | modify | Attachment queries: report brief, lock, count, insert, get, list | no |
| `app/modules/bug_reports/service.py` | modify | Extract `_reporter` into module-level `resolve_reporter()` (shared, no behaviour change). Add `BugReportAttachmentService.upload` | no |
| `app/modules/bug_reports/staff_service.py` | modify | `get()` also returns attachments. Add `open_download()` | no |
| `app/modules/bug_reports/schemas.py` | modify (l.74–102) | `BugReportAttachmentRead` docstring; `content_type` typed as `ImageType`; the `attachments` description no longer says "always empty" | no |
| `app/modules/bug_reports/auth_router.py` | modify | `POST /{report_id}/attachments` (raw body, cookie class) | `**/auth*` glob: appsec review trigger |
| `app/modules/bug_reports/router.py` | modify | `GET /{report_id}/attachments/{attachment_id}/content`; `_read` fills `attachments` | no |
| `app/modules/audit/actions.py` | modify (l.80–83) | `BUG_REPORT_ATTACHMENT_ADDED = "bug_report.attachment_added"` (Q6) | no |
| `app/main.py` | modify (l.94–103) | `app.state.attachment_upload_slots = asyncio.Semaphore(MAX_INFLIGHT_UPLOADS)` | no |
| `tests/test_image_types.py` | create | Unit tests for the pure module | no |
| `tests/test_bug_report_upload_read.py` | create | Unit tests for `read_exact` | no |
| `tests/test_bug_report_attachments_upload.py` | create | API tests for upload | no |
| `tests/test_bug_report_attachments_download.py` | create | API tests for download | no |
| `tests/test_bug_report_attachments_migration.py` | create | Upgrade/downgrade, CHECKs, cascade | no |
| `tests/test_attachments_drive.py` | modify | Deadline and clamp tests | no |
| `tests/test_bug_reports_staff_api.py` | modify | Detail lists attachments | no |
| `tests/test_route_auth_classes.py` | modify (l.47–64, 121–123) | `COOKIE` gets the upload route; `BEARER` maps the download to `admin:bug-reports:read` | no |
| `tests/test_openapi_contract.py` | modify (l.340–343) | Expected errors for the download (`404`) | no |
| `tests/test_audit_coverage.py` | modify (l.758–790) | Classify the upload in a new `IDENTITY_AUDITED_ELSEWHERE` set (raw body + Drive; covered by the upload tests) and include that set in the completeness assertion | no |
| `tests/fake_drive.py` | modify (if needed) | Add a sleep/hang outcome for the deadline tests | no |
| `docs/adr/0023-bug-report-attachments.md` | create | Upload/download contract, status codes, write order, window (outlives the ticket) | no |
| `docs/identity-architecture.md` | modify (decision table after l.321) | Row **D32** pointing to ADR-0023 | no |
| `CLAUDE.md` | modify | R6: add 411, 413, 415, 503 (Q2). R3 class 2: "bug reports and their attachment upload — D28, D32". | no (needs owner approval at the gate per ticket) |
| `docs/spec-v1.md` | modify l.194 only if Q1 = POST | "raw-body `PUT` per file" becomes "raw-body `POST` per file" | no |

Not touched:
- `app/core/cors.py`: POST is already allowed and `Content-Type` is an allowed header.
- `app/modules/auth/**`: the cookie header for error responses is built locally from `set_device_cookie`.
- `app/modules/attachments/store.py`, `sweep.py`, `app/cli.py`, `docs/deploy.md`: slice 2.

## Data model / migration
Table **`bug_report_attachments`**. One migration; `down_revision = "c4e8a2f7b916"` (ADM-029), which must be re-pointed if another batch migration merges first.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `uuid` | no | app-side `uuid4` (set before the Drive call) | PK `pk_bug_report_attachments` |
| `report_id` | `uuid` | no | — | FK `fk_bug_report_attachments_report_id_bug_reports` → `bug_reports.id` `ON DELETE CASCADE` |
| `drive_file_id` | `varchar(128)` | no | — | ORM-only, never in a schema, error or log. CHECK `drive_file_id_format`: `drive_file_id ~ '^[A-Za-z0-9_-]{1,128}$'`. UNIQUE `uq_bug_report_attachments_drive_file_id` |
| `content_type` | `varchar(20)` | no | — | CHECK `content_type_valid`: `content_type IN ('image/png', 'image/jpeg', 'image/webp', 'image/gif')` |
| `size_bytes` | `integer` | no | — | CHECK `size_bytes_range`: `size_bytes BETWEEN 12 AND 5242880` |
| `file_deleted_at` | `timestamptz` | yes | NULL | The spec l.179 "deleted marker". It is set by ADM-031 (purge), so ADM-031 needs no migration. Download treats a non-null value as unavailable. |
| `created_at` | `timestamptz` | no | `now()` | No `updated_at`: rows are immutable apart from the marker |

- Index `ix_bug_report_attachments_report_id_created_at (report_id, created_at)`. It serves the FK cascade, the count and the detail listing.
- The model's `CheckConstraint` texts must match the migration exactly (ADM-028 note at `models.py:3`).
- `downgrade()`: `op.drop_table("bug_report_attachments")`, which drops the index and constraints with the table.
- `uv run alembic check` must be clean.
- No `sha256` column (consult api Q6): there is no use case in scope.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST (Q1) | `/api/v1/auth/bug-reports/{report_id}/attachments?account_id=<uuid>` | See notes 1–3 below the table | `201` `BugReportAttachmentRead`; `Cache-Control: no-store`; `Set-Cookie` if rotated | 201; 401 `session_invalid`/`email_unverified`; 403 `origin_not_allowed` (app context), `attachment_upload_denied`, `attachment_upload_closed`; 409 `attachment_limit_reached`; 411 `length_required`; 413 `payload_too_large`; 415 `unsupported_media_type`; 422 `validation_error` (bad `account_id`/`report_id`), `attachment_incomplete`; 429 `rate_limited` (IP/user), `attachment_upload_busy`; 503 `attachment_storage_unavailable` | Cookie class (R3 class 2): `__Host-mm_device` + `Origin` + `X-MM-App` (not `admin`). No `admin:*`. |
| GET | `/api/v1/bug-reports/{report_id}/attachments/{attachment_id}/content` | — | `200` streamed bytes with the headers in AC 8 | 200; 401; 403; 404 `not_found` (unknown report or attachment id, or an attachment of another report: same body); 404 `attachment_unavailable`; 503 `attachment_storage_unavailable` | Bearer `aud=admin`, `admin:bug-reports:read` |
| GET | `/api/v1/bug-reports/{report_id}` (existing) | — | `attachments: list[BugReportAttachmentRead]`, now filled (additive) | unchanged | `admin:bug-reports:read` |

Request notes for the POST:
1. **Raw body, one image.** There is no body parameter; `openapi_extra` declares `requestBody` `string/binary` for the 4 types.
2. **Required headers.** `Content-Type` must be one of the 4 types; parameters are stripped and the value lowercased. `Content-Length` is required.
3. **No filename** is accepted or stored.

`BugReportAttachmentRead` keeps exactly `id`, `content_type` (`ImageType`), `size_bytes` and `created_at`; no Drive id, no filename.

Error details (never values, never Drive text):
- `attachment_upload_closed`: `{"reason": "report_done" | "window_expired"}`.
- `attachment_limit_reached`: `{"limit": 5}`.
- `attachment_storage_unavailable`: `{"retryable": bool}`, with `Retry-After: 30` only when retryable. Retryable means Drive `unavailable` or `rate_limited`. It is not retryable for `not_configured`, `auth_failed`, `quota_exceeded`, `rejected`, `invalid_input` or `too_large`.
- 411, 413, 415, `attachment_incomplete`, `attachment_upload_denied`, `attachment_unavailable`: `details: null`.

Errors live in `bug_reports/errors.py`:
- `AttachmentUploadDeniedError(PermissionDeniedError)`
- `AttachmentUploadClosedError(PermissionDeniedError)`
- `AttachmentLimitError(ConflictError)`
- `AttachmentIncompleteError(UnprocessableError)`
- `AttachmentUploadBusyError(RateLimitedError)` (`Retry-After: 5`)
- `AttachmentUnavailableError(NotFoundError)`
- `AttachmentStorageUnavailableError(ServiceUnavailableError)`

The 411, 413 and 415 errors use the new core classes with codes `length_required`, `payload_too_large` and `unsupported_media_type`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_image_types.py › test_parse_declared[...]` | 4 allowed; `image/PNG`, `image/png; charset=x`, and surrounding whitespace are accepted. `image/jpg`, `image/svg+xml`, `text/html`, `application/pdf`, empty and `None` are rejected. |
| `test_image_types.py › test_sniff[...]` | PNG 8-byte signature; JPEG `FF D8 FF`; `GIF87a`/`GIF89a`; WebP `RIFF????WEBP`. Rejected: `GIF88a`, `RIFF????WAVE`, a truncated PNG, `FF D8` only, `<svg`, `<!DOCTYPE html`, `%PDF`. |
| `test_image_types.py › test_polyglot_png_with_html_passes_sniff` | Documents the known residual risk (headers contain it) |
| `test_image_types.py › test_extension` | `png`, `jpg`, `webp`, `gif` |
| `test_bug_report_upload_read.py › test_exact_length_ok`, `test_short_body_incomplete`, `test_over_cap_too_large`, `test_deadline_incomplete` | `read_exact` (cap, exact length, deadline via a slow async iterator) |
| `test_bug_report_attachments_upload.py › test_upload_each_type_roundtrip` | AC 1; 201 shape; `no-store`; one audit row `bug_report.attachment_added` with `changes={"attachment_id", "attachment_count"}` only |
| `› test_upload_rejects_type` | AC 2 (415), before and after the body read (mismatch) |
| `› test_upload_413_before_body_read`, `test_upload_413_lying_length`, `test_upload_411_chunked`, `test_upload_incomplete_422` | AC 3 |
| `› test_upload_sixth_is_409`, `test_concurrent_uploads_respect_cap` | AC 4. Strict client, real commits, `asyncio.gather` (pattern from `tests/test_bug_reports_concurrency.py`) |
| `› test_upload_denied_403`, `test_upload_closed_403` | AC 5. No oracle: identical bodies for the three "denied" cases |
| `› test_upload_requires_cookie_401`, `test_upload_unverified_401`, `test_upload_origin_and_app_header_403[no_origin,foreign_origin,no_x_mm_app,admin_app]` | The cookie-class CSRF checks (R11) apply to the new POST |
| `› test_upload_bad_ids_422` | Non-UUID `report_id` or `account_id` |
| `› test_upload_rate_limited_ip`, `test_upload_rate_limited_user_counts_rejected_attempts` | 429. A 415 or 413 attempt still counts against the user limit |
| `› test_upload_busy_429` | All upload slots held, so 429 `attachment_upload_busy` + `Retry-After` |
| `› test_upload_drive_disabled_503`, `test_upload_drive_failure_503` | AC 6 |
| `› test_upload_db_failure_deletes_drive_file` | Inject an insert failure after a successful Drive create. Drive `delete` is called; no row is left. |
| `› test_upload_report_closed_between_check_and_insert` | Staff marks the report done during the Drive call. Result: 403 `attachment_upload_closed`, and the Drive file is deleted. |
| `› test_upload_rotated_cookie_survives_post_commit_error` | Force a rotation with Drive failing. The 503 carries `Set-Cookie` with the new value, and that value works on the next request. |
| `› test_drive_ids_never_exposed`, `test_no_content_or_ids_in_logs_and_audit` | AC 9, AC 10 |
| `› test_cors_preflight_allows_upload` | An `OPTIONS` preflight with `Content-Type: image/png` from a registered origin is allowed (no `cors.py` change needed) |
| `test_bug_report_attachments_download.py › test_download_headers` | AC 8 |
| `› test_download_not_found_404[unknown_report,unknown_attachment,attachment_of_other_report]` | IDOR: the same body for all three |
| `› test_download_unavailable_404`, `test_download_drive_transient_503` | AC 7; no partial 200 (the first chunk is probed before the headers are sent) |
| `› test_download_truncated_stream_aborts` | Drive returns fewer bytes than `size_bytes`: the stream raises (connection abort), with no silent short 200 |
| `› test_download_closes_drive_stream` | `fake.streams_closed` is incremented after a full read and after an early client close |
| `› test_download_requires_permission_401_403` | 401 without a Bearer; 403 without `admin:bug-reports:read` |
| `› test_download_not_audited` | No `audit_log` row |
| `test_bug_report_attachments_migration.py › test_upgrade_downgrade_roundtrip`, `test_checks_reject[bad_type,size_0,size_over,bad_file_id]`, `test_report_delete_cascades` | R2 |
| `test_attachments_drive.py › test_call_deadline_bounds_retries` | `_run` raises `DriveError(UNAVAILABLE)` within the deadline (constants monkeypatched small) |
| `› test_token_fetch_deadline_releases_lock` | A second caller is not blocked beyond the token deadline |
| `› test_stream_deadline` | A slow-drip body raises `UNAVAILABLE` |
| `› test_expires_in_clamped` | `inf`/`nan` → `UNAVAILABLE`; negative → 0; huge → 3600 |
| `test_bug_reports_staff_api.py › test_detail_lists_attachments` | AC 12 |
| `test_route_auth_classes.py`, `test_openapi_contract.py`, `test_audit_coverage.py` (extended) | AC 13 |

## Approach
1. **Drive deadline fix (ADM-027 🟡, `drive.py`).**
   - Add the constants `_CALL_DEADLINE_SECONDS = 45`, `_TOKEN_DEADLINE_SECONDS = 20` and `_STREAM_DEADLINE_SECONDS = 60`.
   - `_run` (l.226): wrap the `while` loop in `async with asyncio.timeout(_CALL_DEADLINE_SECONDS)`. Catch `TimeoutError` into a flag, then `raise DriveError(UNAVAILABLE)` outside the `except`, keeping the file's hygiene rule (l.3–4).
   - `_access_token` (l.188): wrap the retry loop inside the lock with the token deadline, the same way.
   - `_capped_stream` (l.291): check a `time.monotonic()` deadline per chunk, giving `UNAVAILABLE` once it passes.
   - In `_fetch_token` (l.180), clamp `expires_in`: non-finite gives `UNAVAILABLE`; otherwise `min(max(x, 0), 3600)`.
   - `stream()` (l.282) is bounded by `_run` until the headers arrive, and by `_capped_stream` for the body.
2. **Migration and model.** Write the model, then `make migration m="adm030 bug report attachments"`. Hand-edit the names to the table above and write the downgrade. Register the model in `app/db/models.py`. Run `alembic check`.
3. **Pure modules** (Domain focus: pure logic first).
   - `image_types.py`: `parse_declared(header: str | None) -> ImageType | None`, `sniff(head: bytes) -> ImageType | None`, `extension(t) -> str`, `MIN_IMAGE_BYTES = 12`.
   - `upload.py`: `async read_exact(chunks, *, expected: int, cap: int, deadline_s: float) -> bytes`. It raises `AttachmentIncompleteError` on a short body or timeout, and `PayloadTooLargeError` beyond `expected`/`cap`.
4. **Errors.** Add the core classes and the module classes (API contract).
5. **Repository** (`BugReportAttachmentRepository` in `repository.py`, SQLAlchemy only, R1):
   - `report_brief(report_id)`: one query for `user_id, app_id, status, created_at, now(), count(attachments)`.
   - `lock_report(report_id)`: `FOR NO KEY UPDATE`, like `get_for_update` l.182. It serializes the uploads of one report and does not block FK `KEY SHARE`.
   - `count(report_id)`, `add(row)` (flush), `get(report_id, attachment_id)` (both ids in `WHERE`), `list_for_report(report_id)`, and `commit()` (like `join_requests/repository.py:186`).
6. **Upload service** (`BugReportAttachmentService.upload`, HTTP-free). It receives the declared type and length strings, an `AsyncIterator[bytes]`, the store, the slots semaphore, the limiter and the settings. Order (security R-1/R-3):
   1. `resolve_reporter(device, account_id)`: active and verified, else 401 (shared with submit).
   2. Count the per-user hit `bug_report_attachment_user` (before any body read, so rejected attempts count).
   3. Call `report_brief`.
      - A missing report, `user_id` ≠ reporter or `app_id` ≠ `X-MM-App` app gives 403 `attachment_upload_denied` (Q5).
      - If `status != open`, or `created_at <= now() - window` on the DB clock, the result is 403 `attachment_upload_closed`.
      - `count >= 5` gives 409.
   4. `parse_declared` → 415. A missing `Content-Length` gives 411; > 5 MB gives 413; < 12 gives 415.
   5. `repo.commit()` ends tx 1, which releases the connection and any device-rotation row lock before the slow part. Set `self.committed = True`.
   6. Try-acquire a slot (`if slots.locked(): raise AttachmentUploadBusyError`, then `await slots.acquire()` with no await in between). Release it in `finally` after the Drive create.
   7. `read_exact(…, deadline 30 s)`, then `sniff(head)`, which must equal the declared type, else 415.
   8. `attachment_id = uuid4()`, then `store.create(app_key, report_id, attachment_id, sniffed, data)`. A `DriveError` maps to 503 with the retryable flag. Log only `report_id`, `attachment_id` and the code.
   9. tx 2:
      - `lock_report`, then re-check `status == open` and `count < 5`. The window is checked once, at step 3: a request that started inside the window may finish.
      - Insert the row and write the audit `bug_report.attachment_added`: target bug_report, `changes={"attachment_id", "attachment_count"}`, `app_key`, actor `EndUser`.
      - On a re-check failure or any exception: best-effort `store.delete(file_id)` (swallow `DriveError`; log ids only, not the file id), then re-raise. The request-scoped `get_session` commits at the end (R4: audit in the same tx as the insert).
7. **Upload router** (`auth_router.py`). The dependencies follow `create_bug_report` l.25–59: `rate_limit("bug_report_attachment")`, `ReportAppContextDep`, `DeviceCookieDoc` and `DeviceDep`.
   - The route has **no body parameter**. It reads `request.headers` and passes `_stream(request)`, a generator over `request.stream()` that turns starlette `ClientDisconnect` into `AttachmentIncompleteError`.
   - On success, set the rotated cookie as in create.
   - On an `AppError` raised after `service.committed` while `device.new_cookie` is set, add the `Set-Cookie` header, built through `set_device_cookie` on a scratch `Response`, then re-raise. This keeps a committed rotation from being lost (ADR-0004 reuse detection).
   - `responses=` lists every code in the contract.
8. **Download.**
   - `BugReportStaffService.open_download(report_id, attachment_id, store)`:
     1. `get` gives `not_found` when the row is absent.
     2. `file_deleted_at` set gives `attachment_unavailable`.
     3. Open the Drive stream: `AsyncExitStack` → `store.open_read(file_id)`, wrapped, because `DisabledAttachmentStore` raises synchronously. Pull the first chunk.
     4. `NOT_FOUND`, `NOT_CONFIGURED`, `INVALID_INPUT` or `TOO_LARGE` give 404 `attachment_unavailable`; other errors give 503.
     5. It returns a dataclass `(content_type, size_bytes, attachment_id, body)`. `body` is an async generator that yields the first chunk and the rest, raises if the total ≠ `size_bytes`, and closes the stack in `finally`.
   - The router returns a `StreamingResponse` with the fixed headers. `SessionDep` (function scope) commits or closes the DB session before streaming starts. No audit (spec l.174).
9. **Staff detail.** `get()` also calls `list_for_report`; `router._read` fills `attachments`. The `BugReportRead` description is updated.
10. **Config and rate limit.** Add the 5 settings plus `.env.example`, and the scope in `rate_limit.py`. `MAX_INFLIGHT_UPLOADS = 8` (8 × 5 MB ≈ 40 MB worst-case buffering) is a module constant; the semaphore is created in `create_app`.
11. **Docs.**
    - ADR-0023 "Bug-report attachments: upload and download" records: the method (Q1), status codes (Q2–Q5), the two-transaction write order, the window, the slots, the Drive deadline values, the audit action (Q6), the deleted-marker column, and the known residual risks (polyglot files, EXIF, the shared quota).
    - D32 row; CLAUDE.md R3/R6 edits; the spec l.194 edit if Q1 = POST.
12. Run `make check` and `uv run alembic check`.

Rules:
- **R1:** routers do HTTP only (headers, stream adapter, `StreamingResponse`); services raise `AppError`s; repositories use SQLAlchemy only.
- **R2:** migration + downgrade + models registry.
- **R3:** the upload is class 2; the download is class 1 with `admin:bug-reports:read`.
- **R4:** the upload audit is in the insert tx.
- **R5:** settings from env plus `.env.example`.
- **R6:** new codes with owner approval (Q2).
- **R7:** the `drive.py` edit is recorded above.
- **R8:** tests per endpoint.
- **R9:** nested plural path; `account_id` as a query parameter, following `organizations/auth_router.py:76`.
- **R11:** `Origin` + `X-MM-App` via `ReportAppContextDep`, verified email via `resolve_reporter`, and no file ids or content in logs.

## Specialist input used
**api-platform-engineer:**
- Adopted:
  - #1: 201 + `BugReportAttachmentRead`, required `Content-Length`, no filename.
  - #2: 409 for the 6th file. A 404 for non-reporters is **rejected** in favour of the ticket's AC "403 otherwise" and security R-6 (one 403, no oracle).
  - #3: 503 with a stable code, and 404 `attachment_unavailable` (not 410).
  - #4: lookup by both ids, probe before headers, `Content-Length` from the DB.
  - #5: table shape plus a row lock. `sha256` is rejected (no use case).
  - #6: write order and best-effort delete. Its `ReferenceChecker` part goes to slice 2.
  - #7: the deadline fix in this ticket.
  - #8: rate limit, checks before the body, global semaphore.
  - #9: magic bytes and parameter stripping.
  - #10: window setting, 15 min.
  - #11: upload audit (Q6); no download audit (the spec mandates it).
**security-architect:**
- Adopted:
  - R-1: order, with 411 for a missing length or chunked body; slots; 30 s read deadline; exact length.
  - R-2.
  - R-3: two transactions with `FOR NO KEY UPDATE` instead of `FOR UPDATE`, so the FK `KEY SHARE` is not blocked (the existing ADM-029 pattern). One stable code `attachment_storage_unavailable`, plus only a `retryable` boolean.
  - R-4: deadlines and the clamp in this ticket.
  - R-5.
  - R-6.
  - R-7: the sentinel test. The pair-based `ReferenceChecker` goes to slice 2.
  - R-8: rejected attempts count.
- Open question 6 (cookie rotation and parallel uploads): `sessions/service.py:90–100` already handles a concurrent rotation ("a concurrent request rotated first") plus a 60 s grace. The new risk created by the early commit is covered in step 7.

## Assumptions
- **Builds on merged ADM-027** (`app/modules/attachments/`: `AttachmentStore` port, `GoogleDriveStore`, `DisabledAttachmentStore`, `ImageType`, `MAX_ATTACHMENT_BYTES`, the sweep), **ADM-028** (`bug_reports` table, submit route, `ReportAppContextDep`, ADR-0019/D28) and **ADM-029** (staff router and service, `admin:bug-reports:read`/`:write`, migration head `c4e8a2f7b916`, ADR-0020/D29). All are MERGED (batch file).
- The `attachment_count: 0` in `bug_report.created` (`service.py:129`) stays correct: at creation, a report has no attachments.
- Folder layout is `<root>/<app_key>/<attachment_id>`, flat per app as `GoogleDriveStore.create` does today. There is no per-report subfolder (spec l.195 leaves it to PLAN).
- **Residual risk (documented in ADR-0023, not fixed):** a 500 from the database in tx 2, on the rare request that also rotated the device cookie (at most about once every 24 h), returns no `Set-Cookie`. The client then reuses the old value after the 60 s grace, which revokes the device session.
- **Residual risk:** polyglot files, EXIF that the app did not strip, no malware scan, and the Drive quota shared with TuDoCuaTui (spec l.200, l.204–205). Admin-pan must render through `<img src=blob:>` only and never navigate to the blob. This goes to Follow-ups for admin-pan and ADM-032 (guide).
- Rate-limit numbers are PLAN's (spec l.141): per IP 60/h; per user 10/min (5 parallel uploads plus retries). Worst-case storage is 10 reports × 5 × 5 MB = 250 MB per user per day.
- **Hotspots with batch 2026-11-20-1** (ADM-033, ADM-035, ADM-039, ADM-041; all PLANNING): likely shared edits are `migrations/` (same head `c4e8a2f7b916`, so re-point `down_revision` on rebase), `app/db/models.py`, `app/core/config.py`, `.env.example`, `app/core/rate_limit.py`, `app/core/errors.py`, `CLAUDE.md`, `docs/identity-architecture.md` (D30/D31/D32 rows), `tests/test_route_auth_classes.py`, `tests/test_openapi_contract.py` and `tests/test_audit_coverage.py`. Expect a rebase.
- Numbering: ADR-0023 and D32 are reserved for this ticket in the batch. Note: there are two ADR-0018 files, a question the batch already raised; this plan cites the attachment one (`0018-attachment-storage-google-drive.md`).
- Follow-ups (not in this diff):
  - In `list_files`, check `app_key` against `_APP_KEY_RE` (ADM-027 💭4).
  - Protect the process-wide httpx logger (ADM-027 💭5).
  - Admin-pan: show "attachment unavailable" on 404 `attachment_unavailable`, render through `blob:` only, and show the per-file 503 in the app guide (ADM-032).

## Blocking questions
All have defaults the plan already uses (Status READY).
1. **Q1, method (spec vs ticket conflict).** The spec §5.12 l.194 says "raw-body `PUT` per file"; the ticket says `POST`. A `PUT` would need `app/core/cors.py` `IDENTITY_POLICY.allow_methods` (l.36) widened to all cookie routes (High path). A `PUT` without a client-chosen id is also not idempotent. **Default: `POST /api/v1/auth/bug-reports/{id}/attachments`**, and edit spec l.194 to "raw-body `POST`". Alternative: `PUT …/attachments/{attachment_id}` with a client-generated UUID, plus the CORS change.
2. **Q2, new R6 codes.** Add 411 `length_required`, 413 `payload_too_large`, 415 `unsupported_media_type` and 503 (`attachment_storage_unavailable` on these routes; `service_unavailable` generic) to R6 in CLAUDE.md and to `app/core/errors.py`. **Default: approve all four.** Without 411, a missing `Content-Length` gets 413; without 503, a Drive failure gets 409 (worse semantics).
3. **Q3, the 6th file.** **Default: 409 `attachment_limit_reached`** (state conflict); 422 is kept for malformed requests.
4. **Q4, upload window.** **Default: 15 min after the report's `created_at`** (env `BUG_REPORT_ATTACHMENT_WINDOW_SECONDS=900`, range 60–86400), checked on the DB clock when the request starts.
5. **Q5, non-reporter answer.** **Default:** unknown report, another user's report and another app's report all get the same 403 `attachment_upload_denied` (no existence oracle; matches AC "403 otherwise"). Only the real reporter can see the reason: 403 `attachment_upload_closed` with `report_done` or `window_expired`. Alternative: 404 for non-reporters.
6. **Q6, audit the upload.** Spec OQ28 (l.174) lists audit for creation and staff changes only. **Default: yes.** Write `bug_report.attachment_added` with ids and count only (no content type, filename or Drive id), in the insert tx. The download is not audited (the spec mandates it).
7. **Q7, split.** **Default: split** as in `## Split proposal`. Sweep wiring becomes a follow-up ticket; `attachments-sweep --apply` stays refused (fail-closed) until then. Alternative: one bigger PR, roughly +150 product lines.

## Split proposal
Estimated size of the whole ticket: about 750 product lines and 900 test lines, well over one implement/review cycle.
- **Slice 1, this plan (ADM-030):** the Drive deadline fix, the table and migration, upload, download, staff detail attachments, the R6 codes, ADR-0023/D32. It covers every acceptance criterion of the ticket. Estimate: about 550 product lines plus tests.
- **Slice 2, new ticket (the ID is the orchestrator's; title "Bug reports: wire the attachment sweep to attachment rows"; Depends on ADM-030; P2; Gate plan-approval):**
  - Change `ReferenceChecker` (`sweep.py:20`) to `(attachment_id, file_id)` pairs, keying `old` by `file_id` (ADM-027 💭3, security R-7).
  - Add a DB-backed checker over `bug_report_attachments` (rows with `file_deleted_at IS NULL`), and wire it in `app/cli.py` `_attachments_sweep` (l.815) through `_cli_session_scope`.
  - Update `MSG_SWEEP_NO_CHECKER`/`MSG_SWEEP_UNCHECKED` (cli.py l.156–163), `docs/deploy.md` l.1013–1015 and the sweep/CLI tests.
  - Estimate: about 150 product lines plus tests.
