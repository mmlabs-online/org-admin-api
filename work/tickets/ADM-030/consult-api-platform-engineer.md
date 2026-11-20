# Consult — api-platform-engineer — ADM-030 (2026-11-20)

**ADM-030 consult (api)**

**Findings**
- The ADM-027 store port in `app/modules/attachments/store.py` already provides `create(app_key, report_id, attachment_id, content_type, data)`, `open_read`, `delete`, `read_capped` (5 MB cap, raises TOO_LARGE) and `DisabledAttachmentStore`. It has no attachment table, no `ReferenceChecker` implementation and no routes.
- `app/cli.py` takes an injectable `reference_checker`. `--apply` is refused until one is wired (cli.py ~157/163). The sweep logic in `sweep.py` is sound.
- `BugReportAttachmentRead` (`schemas.py:74`) is a placeholder with `id`, `content_type`, `size_bytes`, `created_at`. `BugReportRead.attachments` is documented as "always empty". It is additive, so the contract is safe. Keep those four fields and update the description. Do not add a Drive id or filename.
- `auth_router.py` (prefix `/auth/bug-reports`) is the right home for upload. The create endpoint is the pattern to copy: `ReportAppContextDep`, `DeviceCookieDoc`, `DeviceDep`, `rate_limit(...)`, `Cache-Control: no-store`, device-cookie rotation.
- The create audit row writes `attachment_count: 0` (`service.py:129`).
- `app/core/` is a High path. Adding R6 codes touches `errors.py`, so the plan gate applies.

**Recommendations**
1. **Contract.**
   - Upload returns `201` with `BugReportAttachmentRead`.
   - One raw body per request, with `Content-Type` as the declared type. Require `Content-Length`: a missing header gives 411 or 413, and a chunked body is still stream-capped.
   - Do not accept a filename, so none is stored.
   - Add the upload and download to the OpenAPI `responses` (401, 403, 404, 409, 413, 415, 422, 429, 502 or 503).
2. **Status codes.**
   - 413 `payload_too_large` and 415 `unsupported_media_type`. Add both to R6 and to `app/core/errors.py`. Note that 411 and 502/503 are also not in R6.
   - The 6th file should be **409** `attachment_limit_reached` (state conflict), not 422. Reserve 422 for a malformed request.
   - The window or status failure and a non-reporter are both 403, per the ticket. A report that does not exist, or that belongs to another app, is 404. Do not leak a report's existence to a non-reporter: return 404 for anyone who is not the reporter, and 403 only for a reporter who is out of window or on a closed report. Planner: pick one and state it.
3. **Drive failure.**
   - Use 503 `attachments_unavailable` with `Retry-After` for NOT_CONFIGURED or UNAVAILABLE.
   - Use the same stable code for a missing file on download (410 or 404 `attachment_unavailable`). I suggest 404 `attachment_unavailable`, to be distinct from "no such attachment id".
   - Map every DriveError to a fixed message. Never pass through Drive text.
4. **Download.**
   - The permission is `admin:bug-reports:read` and the route is under `/api/v1/bug-reports/{id}/attachments/{aid}/content`.
   - Look up the row by both `id` and `aid`, so there is no IDOR across reports.
   - Send the ticket's headers, with `Content-Type` taken from the stored sniffed type.
   - Set `Content-Length` from `size_bytes`. If Drive returns fewer bytes, abort the stream.
   - Open the Drive stream before sending headers, so a Drive failure yields a clean error rather than a truncated 200.
   - Include `Content-Disposition: attachment; filename="attachment-<aid>.<ext>"`, with the extension derived from the type.
5. **Model and migration.**
   - Table `bug_report_attachments`: `id` UUID, `report_id` FK with ON DELETE CASCADE, `drive_file_id` (never serialized), `content_type` CHECK in the four types, `size_bytes` CHECK 1..5242880, `created_at`, and an index on `report_id`. Optionally add `sha256`.
   - Enforce the "≤ 5" limit under concurrency: lock the report row with `SELECT … FOR UPDATE`, then count. Otherwise parallel uploads exceed the cap.
   - Add a hand-reviewed downgrade and import the model in `db/models.py`.
   - Deleting a report cascades the rows but orphans the Drive files. The sweep covers that, and ADM-031 purge should delete the Drive files first.
6. **Write order.**
   - Generate the `attachment_id` up front and call Drive `create`. Then insert and commit the row.
   - If the DB write fails, call `store.delete` in a best-effort `finally`. If that also fails, leave the file for the sweep and log only the attachment id.
   - Use a plain `ReferenceChecker` (`SELECT id WHERE id = ANY(...)`) in `cli.py`, and update the stale messages (cli.py 157, 163).
7. **Per-call deadline (the ADM-027 🟡).**
   - The upload and download hold a request open on Drive. Either fix `drive.py` as the security review proposes (an overall `asyncio.timeout` in `_run` and a monotonic deadline in `_capped_stream`), or wrap the service calls in `asyncio.timeout(~45 s)` mapped to `attachments_unavailable`.
   - I recommend doing the `drive.py` fix in this ticket as an explicit in-plan item. Otherwise a slow Drive pins workers on the user-facing path.
8. **Abuse limits.**
   - Add `rate_limit("bug_report_attachment")` per user and per IP. Run it before any body read, along with the Content-Length check.
   - Check authorization, window and count before reading the body, so the 5 MB read only happens for an eligible upload.
   - Add a global in-flight upload semaphore, because each upload buffers up to 5 MB in memory.
9. **Validation.**
   - Check magic bytes: PNG signature, JPEG `FF D8 FF`, GIF87a/89a, and WebP `RIFF....WEBP`.
   - Do a 415 on mismatch, and on a declared type outside the allowlist, before reading the full body.
   - Strip parameters from `Content-Type` (e.g. `; charset`) before comparing.
   - Store the sniffed type.
10. **Window.** Add a setting `bug_report_attachment_window_seconds` (suggest 15 min after creation), in `.env.example`.
11. **Audit and logs.**
   - Audit the upload with `attachment_count` and the attachment id (R4), in the same transaction as the row insert. Include no filename or Drive id.
   - Staff download is a read. Decide at PLAN whether to audit it; I suggest no.
   - `create_bug_report`'s `attachment_count: 0` stays.

**Risks**
- The raw-body route on FastAPI needs `request.stream()` with a manual cap. `Request.body()` reads everything. Test 413 with a lying or missing Content-Length.
- A Drive upload succeeds but the response is lost. This leaves an orphan, which the sweep handles after 48 h.
- Cookie-class CSRF: confirm that the Origin and `X-MM-App` checks apply to the new POST route.
- A stored image served via the admin UI. The CSP sandbox, `nosniff` and `attachment` disposition cover this.
- The Alembic migration and R6 edit are High paths. The merge needs the owner.

**Open questions**
1. 404 or 403 for a non-reporter?
2. Upload window length?
3. Missing Drive file on download: 404 or 410?
4. Is the `drive.py` deadline fix in scope here?
5. Should the staff download be audited?
6. Is a sha256 column wanted?

Relevant files: app/modules/bug_reports/auth_router.py, app/modules/bug_reports/schemas.py, app/modules/attachments/store.py, app/modules/attachments/sweep.py, app/cli.py, app/main.py.
