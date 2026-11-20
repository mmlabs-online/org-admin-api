# Consult — security-architect — ADM-030 (2026-11-20)

### Findings
1. **ADM-027's open 🟡 is still open.** `drive.py` has only per-attempt `asyncio.timeout` calls (lines 155 and 215). There is no deadline for a whole call, and the token retry runs while the lock is held. ADM-030 is the first ticket that puts Drive behind user traffic, so a Google outage would stack up as N × ~3 min of tied-up workers.
2. **The memory cap does not exist yet.** The upload `Semaphore` (`drive.py:341, 442`) only applies after the 5 MB body is already in memory. `read_capped` (`store.py:37`) grows a bytearray with no deadline. Nothing limits how many request bodies are buffered at once.
3. **Read order is a trap.** If the route declares a body parameter (`bytes`/`Body`), FastAPI reads the whole body before the route runs. The 413 would then come only after buffering, and the auth checks would no longer run before the body is read.
4. **Spec and ticket disagree on the method.** Spec §5.12 says "raw-body `PUT` per file". The ticket says `POST`. The cookie CORS policy (`app/core/cors.py:36`) allows only GET and POST, so PUT would also mean changing `app/core/` (a High path). Spec wins, so this needs flagging. POST is the lower-risk choice.
5. **Download failure timing.** `open_read` is a context manager. If the route returns a `StreamingResponse` before the first Drive read, a Drive 404 arrives after a 200 has already been sent, and the "stable unavailable error" acceptance criterion cannot be met.
6. **Upload count race.** The service checks the count, uploads to Drive, then inserts the row. Two concurrent uploads against a report that already has 4 files can both pass the check, giving 6 files.

### Recommendations
- **R-1 (AC "> 5 MB → 413 without reading the body")** Declare no body parameter. Run in this order:
  1. Run the auth deps (cookie, Origin, `X-MM-App`).
  2. Run the rate limit.
  3. Check the report: reporter, same `app_id` as `X-MM-App`, `open`, inside the window (DB clock), count < 5.
  4. Check that `Content-Length` is present, ≤ 5 MB and ≥ 12. Reject a missing length or chunked body; PLAN picks 411 or 413.
  5. Take a process-wide inbound upload semaphore (try-acquire, otherwise 429 + `Retry-After`).
  6. Read `request.stream()` with a byte cap and an overall deadline (for example 30 s, to stop slowloris).
  7. Check the final length equals `Content-Length`.
- **R-2 (AC 415)** Parse the declared `Content-Type`: strip its parameters, lowercase it, and require an exact `ImageType` match. Sniff the type from the first bytes:
  - PNG: `89 50 4E 47 0D 0A 1A 0A`
  - JPEG: `FF D8 FF`
  - GIF: `GIF87a` or `GIF89a`
  - WebP: `RIFF` at bytes 0–3 and `WEBP` at bytes 8–11

  Return 415 if the sniffed type differs from the declared one. Store the sniffed type and serve that stored DB value, not Drive's mime type. Do not accept or store a client filename.
- **R-3 (AC Drive off/failing)** Keep the DB transaction short:
  1. Check (tx 1) and release the connection.
  2. Upload to Drive with no DB connection held.
  3. In tx 2: `SELECT … FOR UPDATE` the report, re-check status, window and count, then insert. On a count overflow or any DB error, delete the Drive file (best effort, ids not logged) and return 409.
  4. Map every `DriveError` to one stable code (for example `attachment_storage_unavailable`). Do not echo `quota_exceeded`/`auth_failed` to apps.
- **R-4 (prerequisite)** Land the ADM-027 🟡 fix in this plan or as a blocking ticket before it:
  - a deadline on the whole `_run` call
  - a bounded `_access_token`
  - a deadline in `_capped_stream`
  - the `expires_in` clamp

  It touches `app/modules/attachments/`, which is outside the ticket's file list (R7), so record it explicitly.
- **R-5 (AC download headers)**
  - Probe first: enter `open_read` and pull the first chunk before building the response.
  - Map a Drive `NOT_FOUND`, a deleted-marker row, or Drive being off to one stable 404 `attachment_unavailable`.
  - Close the Drive stream in a generator `finally` so a client disconnect releases it.
  - Release the DB session before streaming starts.
  - Headers:
    - `Content-Type` from the DB
    - `Content-Length` from the DB size (so truncation is visible)
    - `Content-Disposition: attachment; filename="<attachment_id>.<ext>"`
    - `X-Content-Type-Options: nosniff`
    - `Content-Security-Policy: default-src 'none'; sandbox`
    - `Cache-Control: no-store`
    - `Cross-Origin-Resource-Policy: same-origin` (CORP does not block admin-pan's CORS fetch)
  - Abort the connection if a stream breaks mid-way. A 200 has already been sent, so it cannot be changed.
- **R-6 (IDOR)**
  - Download: query `WHERE id=:aid AND report_id=:rid`, and return the same 404 when the attachment does not exist and when it belongs to another report.
  - Upload: send unknown, not-yours and other-app reports to the same 403, so there is no oracle.
  - Add tests for a cross-report attachment id and for another reporter on the same app.
- **R-7 (AC no Drive ids / logs)**
  - Keep `drive_file_id` only on the ORM model, never in a schema or in error `details`.
  - Add a sentinel test over every response body, the logs and the audit rows.
  - Make the sweep `ReferenceChecker` compare `(attachment_id, file_id)` pairs (ADM-027 💭3).
- **R-8 (abuse)** Give uploads their own per-IP and per-user rate limit. Failed 415/413 attempts must count, otherwise the cap is bypassed. Worst-case storage is 10 reports × 5 × 5 MB = 250 MB per user per day. The Drive quota is shared with TuDoCuaTui.

### Risks
- **Polyglot files** (for example a valid PNG header with HTML appended) pass the magic-byte check. They are contained only by the download headers plus admin-pan rendering through `blob:` in `<img>`. Admin-pan must never `window.open` or navigate to the blob URL.
- **No malware scan and no re-encoding.** EXIF and GPS leak if an app skips client-side stripping (PII, spec §5.12).
- **Shared Drive quota:** filling it denies TuDoCuaTui as well.
- **Orphan Drive files:** an upload that succeeds but whose DB row fails to write, and whose cleanup delete also fails, leaves a file only the sweep removes. ADM-027's sweep `--apply` is refused until a reference checker is wired in.

### Open questions
1. Which status for "Drive off or failing" on upload? R6 has no 502 or 503. Options are adding 503 at the gate (alongside 413 and 415) or reusing 409.
2. How long should the upload window be? 10–15 min after `created_at` is suggested.
3. Is the 6th file a 409 or a 422? 409 is suggested because the request is valid and only the report's state conflicts.
4. Should an attachment upload write an audit row (`bug_report.attachment_added`, ids only)? The `bug_report.created` audit row always records `attachment_count: 0` now, so it is stale once uploads follow creation.
5. POST or PUT (finding 4)? This needs a spec edit or the owner's note.
6. Should a reporter's upload rotate the `mm_device` cookie? Five parallel uploads straight after creation may hit the 60 s grace race. Confirm `DeviceDep` handles concurrent use.
