# ADR-0023: Bug-report attachments (upload and download)

Status: Accepted on merge (owner approved the plan 2026-11-20, batch 2026-11-20-1)
Date: 2026-11-20

## Context
Spec §5.12 (OQ24, OQ35): a report may carry 0..5 images. ADR-0018 chose Google Drive as the store
(`AttachmentStore` port, fake-able); ADR-0019 is the text submit; ADR-0020 the staff triage.
ADM-030 adds the table, the upload and the staff download. The ADM-027 sweep wiring is a separate
ticket (ADM-030b): until then `attachments-sweep --apply` stays refused (fail-closed).

## Decision
1. **Method (spec said `PUT`).** `POST /api/v1/auth/bug-reports/{report_id}/attachments?account_id=`,
   raw body, one image per request. A `PUT` would have widened the cookie-route CORS methods
   (High path) and has no client-chosen id to be idempotent on; spec l.194 now says `POST`.
   Cookie class (R3 class 2): `__Host-mm_device` + `Origin` + `X-MM-App` (not `admin`).
2. **Body.** `Content-Type` must be PNG, JPEG, WebP or GIF (parameters stripped, case-insensitive;
   `image/jpg` is not accepted). `Content-Length` is required (missing or chunked: 411
   `length_required`); more than 5 MB is 413 `payload_too_large` before any body is read; fewer
   than 12 bytes or a declared/sniffed type mismatch is 415 `unsupported_media_type`; a body that
   is shorter than declared, or stalls past 30 s, is 422 `attachment_incomplete`. The stored type
   is the one the magic bytes say. No filename is accepted or stored; the image is never
   re-encoded.
3. **Who and when.** Only the reporter of an open report, in the report's own app, within a window
   after `created_at` (default 900 s, `BUG_REPORT_ATTACHMENT_WINDOW_SECONDS`, 60..86400, DB clock,
   checked when the request starts). Unknown report, another user's report and another app's
   report all give the same 403 `attachment_upload_denied` (no existence oracle). The reporter
   alone sees 403 `attachment_upload_closed` with `details.reason` `report_done` or
   `window_expired`. The 6th file is 409 `attachment_limit_reached` (`details.limit` 5).
4. **Write order, two transactions.** Tx 1 resolves the reporter, counts the per-user hit, runs
   every check above and commits (this also makes a rotated device cookie durable, so an error
   after this point still carries `Set-Cookie`). The body is then read (a global slot semaphore of
   8 bounds buffering; no free slot is 429 `attachment_upload_busy`, `Retry-After: 5`), sniffed,
   and created in Drive (never retried). Tx 2 locks the report `FOR NO KEY UPDATE` (serializes
   uploads of one report without blocking FK `KEY SHARE`), re-checks status and count, inserts the
   row and writes the audit entry in the same transaction (R4), and commits. If tx 2 fails for any
   reason, the Drive file is deleted again (best effort; the sweep is the safety net).
5. **Drive failure.** The report is unaffected (OQ35). The upload answers 503
   `attachment_storage_unavailable` with `details.retryable`; `Retry-After: 30` only when Drive
   was `unavailable` or `rate_limited`. Drive text and ids never appear.
6. **Download.** `GET /api/v1/bug-reports/{report_id}/attachments/{attachment_id}/content`, Bearer
   `admin:bug-reports:read`. Both ids are in the lookup (another report's attachment is 404
   `not_found`, same body as unknown). A missing Drive file, a deleted marker or Drive off is 404
   `attachment_unavailable`; a Drive outage is 503. The first chunk is read before the headers
   are sent (no half-sent 200); a body whose length differs from `size_bytes` aborts the response.
   Headers: `Content-Type` from the DB, `Content-Length`, `Content-Disposition: attachment;
   filename="<attachment id>.<ext>"`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy:
   default-src 'none'; sandbox`, `Cache-Control: no-store`, `Cross-Origin-Resource-Policy:
   same-origin`. Not audited (spec OQ28).
7. **Table** `bug_report_attachments`: `id` (set before the Drive call, also the Drive file name),
   `report_id` (FK, `ON DELETE CASCADE`), `drive_file_id` (unique, ORM-only, never in a schema,
   error or log), `content_type`, `size_bytes` (12..5242880, CHECKed), `file_deleted_at` (the
   spec's deleted marker, set by ADM-031), `created_at`. The staff detail lists
   `id, content_type, size_bytes, created_at` only.
8. **Audit.** `bug_report.attachment_added` (target the report, actor the reporter) with
   `changes = {attachment_id, attachment_count}` only.
9. **Rate limits (R5).** Per IP `BUG_REPORT_ATTACHMENT_RATE_LIMIT` / `_RATE_WINDOW_SECONDS`
   (60/h); per user `BUG_REPORT_ATTACHMENT_USER_RATE_LIMIT` / `_USER_RATE_WINDOW_SECONDS` (10/min),
   counted before any body is read, so rejected attempts count too.
10. **R6.** New codes: 411 `length_required`, 413 `payload_too_large`, 415 `unsupported_media_type`,
    503 (`service_unavailable` generic, `attachment_storage_unavailable` here). The OpenAPI
    "no 5xx documented" contract allows 503 on the two attachment routes only.
11. **Drive deadlines (ADM-027 review finding 1, here because this is the first user-facing use).**
    One Drive call, retries and backoff included, is bounded to 45 s; a token refresh, which holds
    the lock, to 20 s; a streamed body to 60 s; `expires_in` is clamped to 0..3600 and a
    non-finite value is `unavailable`. All map to `DriveError(unavailable)`.

## Known residual risks
- A valid image header followed by HTML (polyglot) passes the magic-byte check; the download
  headers (`nosniff`, CSP `sandbox`, `attachment`) contain it. Admin-pan must render through
  `<img src=blob:>` only and never navigate to the blob.
- EXIF and other metadata are not stripped; no malware scan; the Drive quota is shared with other
  services (spec l.200, l.204-205).
- A database error in tx 2 on the rare request that also rotated the device cookie returns no
  `Set-Cookie`; the client then reuses the old value after the 60 s grace, which revokes the
  device session (ADR-0004).

## Consequences
- Slice 2 (ADM-030b) wires the sweep to these rows. ADM-031 sets `file_deleted_at` and deletes
  the Drive files on retention purge.
