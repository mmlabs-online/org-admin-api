# Security review — ADM-030 (appsec-engineer)

## Attempt 1 (commit 2f2cb34, slice 1)

**Verdict: no blockers. Two should-fix findings and five notes.**

I read the product diff, the ADR (`docs/adr/0023-bug-report-attachments.md`), the full `app/modules/attachments/drive.py`, the device-session rotation code and the session lifecycle. As instructed, I did not run pytest, alembic or `make check`. The reviewer still owns the green-check gate (R8).

### Findings

**🟡 1. A slow upload can deliver the rotated cookie after its 60 s grace has expired, so other requests can revoke the whole device session.**
- **Evidence:**
  - `app/modules/bug_reports/auth_router.py:126`: `DeviceDep` may rotate.
  - `app/modules/sessions/repository.py:58`: `prev_valid_until = func.now() + GRACE`. `GRACE` is 60 s, and `now()` is the time tx 1 started.
  - `app/modules/bug_reports/service.py:239-240`: the rotation is committed in tx 1.
  - The new `Set-Cookie` only reaches the browser with the final response, after the body read (up to 30 s, `service.py:166/246`), the Drive create (see finding 2: more than 45 s is possible) and tx 2.
- **Risk:** While the upload is in flight, any other cookie request from the same browser still carries the old value: `/auth/token` refresh, a 6th upload, or another tab. Once the 60 s grace has passed, that request hits the reuse branch (`app/modules/sessions/service.py:101`), which revokes the device session, signs out every account on it and writes a false `device_session.reuse_revoked` security event. The ADR residual-risk list only covers the tx-2 DB-error case, not this slow-success case.
- **Fix:** Never rotate on this long-running route. Add a non-rotating resolve, for example `DeviceSessionService.resolve(..., allow_rotate=False)`, which only touches the session. Expose it as a `DeviceNoRotateDep` and use it in `upload_bug_report_attachment`. Rotation then happens on the next ordinary cookie request. The `service.committed` and `Set-Cookie`-on-error plumbing can then be removed. This touches `app/modules/sessions` and auth deps, which are High paths. If the owner prefers to keep rotation here, add this case to ADR-0023's known residual risks.

**🟡 2. The upload path has no overall deadline on `store.create`; only each Drive call is bounded.**
- **Evidence:**
  - `drive.py:39/265`: the 45 s deadline applies to one `_run` call.
  - `drive.py:495-518`: `GoogleDriveStore.create` first waits on `self._uploads`, which has 4 slots while the service allows 8 (`service.py` `MAX_INFLIGHT_UPLOADS`). It then runs `ensure_app_folder` (`drive.py:447-481`): a paged find, a folder POST and a second find. Each of those is its own 45 s `_run`. Only after that does the 45 s upload call start.
  - `service.py:244-256`: the global upload slot is held for all of this.
- **Risk:** The worst case is several minutes per request. Meanwhile all 8 slots can be held, so every user gets `attachment_upload_busy`, and finding 1's window gets much longer. ADR-0023 §11 ("one Drive call … 45 s") is met literally, but the user-facing upload is not bounded.
- **Fix:** Wrap the call at `service.py:256` (inside `_create`) in `async with asyncio.timeout(60):`. Map `TimeoutError` to `AttachmentStorageUnavailableError(retryable=True)` and log `code=unavailable`. A create that times out may leave an orphan file, which the sweep (ADM-030b) collects. Optionally set `MAX_INFLIGHT_UPLOADS` to `_MAX_CONCURRENT_UPLOADS`, or explain the gap in a comment. Add a test: a hanging `ensure_app_folder` should give 503 within the deadline and free the slot.

**💭 3. A few users can tie up all upload slots.** There are 8 global slots, each held for up to 30 s while the body is read (`service.py:243-256`). With a per-user limit of 10 per minute, one or two verified accounts with their own open reports can trickle bodies and keep everyone at 429. Only the attachment feature is affected; there is no memory risk (8 × 5 MB). **Fix (later):** a per-user in-flight cap of about 2, using an in-memory dict of counters next to the semaphore.

**💭 4. Drive disabled still reads the body first.** With `DisabledAttachmentStore`, the request commits tx 1, takes a slot, reads up to 5 MB and only then returns 503. **Fix:** after the checks and before `repo.commit()` (`service.py:239`), add `if not self.store.configured: raise AttachmentStorageUnavailableError(retryable=False)`.

**💭 5. The stream deadline is checked only between chunks.** At `drive.py:350` the true bound is about 60 s plus one httpx read timeout (30 s, `drive.py:141`), so 90 s. That is acceptable; update the ADR §11 wording or the comment at `drive.py:41`.

**💭 6. Two checks are not repeated at insert time.** Tx 2 (`service.py:303-310`) re-checks status and count, but not the upload window or the session/account state. A sign-out or password reset during the up-to-75 s upload still writes the row. App membership is never checked on upload, only report ownership. Both are acceptable within the 15-minute window and the ADR states the window is "checked when the request starts". Recording it here.

**💭 7. A tx-2 DB error would log the Drive file id.** The request engine at `app/db/session.py:14-20` has no `hide_parameters=True`. A `DBAPIError` on the tx-2 INSERT would put `drive_file_id` into the 500 traceback log, against ADR-0023 §7 "never in a log". The CLI engines already set it (`app/cli.py:236/253`). This predates the ticket and `app/db` is a High path, so it is a follow-up.

### Checked and correct (🟢)
- **No body is read before the checks.** The route has no body parameter, and the per-IP `rate_limit` dependency, Origin/`X-MM-App` (not `admin`), cookie, reporter (active and verified), per-user limit, ownership, status, window, count, Content-Type and Content-Length are all checked before `read_exact`. 411 for missing, non-ASCII or non-digit lengths; 413 when the declared length exceeds the cap; 415 under 12 bytes. Tested (`test_upload_413_before_body_read`, `test_upload_411_chunked`).
- **Body read is bounded.** `read_exact` caps at the smaller of `expected` and 5 MB, with a 30 s `asyncio.timeout` for the whole body. A short body or client disconnect gives 422.
- **Semaphore.** `locked()` then `acquire()` with no await between is race-free on a single loop, and the slot is released in `finally`.
- **Type check.** The magic-byte sniff must equal the declared type, and the stored type is the sniffed one. The DB has CHECKs on content type and size. No client filename is used: the Drive name is the server-generated `attachment_id` (`drive.py:498`), and the download filename is `<uuid>.<ext>`.
- **No IDOR.** Unknown report, another user's report and another app's report all give the same 403. `attachment_upload_closed` is only reachable by the reporter. Download looks up both ids together (`repository.get`) under `admin:bug-reports:read` plus router-level `current_admin`, and both routes are registered in `tests/test_route_auth_classes.py`.
- **Cap race.** Tx 2 takes `FOR NO KEY UPDATE` with `populate_existing`, then re-checks status and count. Tested (`test_concurrent_uploads_respect_cap`).
- **Drive failures.** These map to 503 `attachment_storage_unavailable` with only `details.retryable`, and `Retry-After: 30` only for unavailable or rate-limited. Logs carry only the report id, attachment id and code; no Google text and no file id.
- **Orphan cleanup.** If tx 2 fails for any reason (`except BaseException`), it rolls back and does a best-effort `_discard`.
- **Download headers.** nosniff, CSP `default-src 'none'; sandbox`, `attachment`, `no-store`, CORP `same-origin`, and `Content-Length` from the DB. The first chunk is read before the 200 is sent. A short or long body aborts the response. The DB session is function-scoped, so it is released before streaming starts.
- **Drive deadline fix.** 45 s per call including retries; 20 s for the token refresh inside the lock. Waiting for the lock is still bounded by the 45 s call deadline, and the lock is not held across Drive-call retries. Nested `asyncio.timeout` cancellation is handled correctly. `expires_in` is checked for non-finite values and clamped to 0..3600.
- **Cookie across the two transactions.** Errors before the tx-1 commit roll back the rotation and send no cookie. `AppError`s after the commit carry `Set-Cookie`, merged with existing headers such as `Retry-After`. The tx-2 DB-error residual is documented.
- **R6 codes.** 411, 413, 415 and 503 are added to `errors.py`, the `_HTTP_CODES` map and CLAUDE.md R6. 429 `attachment_upload_busy` sends `Retry-After: 5`.
- **Audit.** `bug_report.attachment_added` carries `{attachment_id, attachment_count}` only, written in the same transaction as the row (R4).

### Follow-ups (out of scope)
- Set `hide_parameters=True` on the request engine in `app/db/session.py` (finding 7).
- Add a per-user in-flight upload cap (finding 3).
- Check whether a 30 s read deadline is too tight for 5 MB on slow mobile uplinks (it needs about 1.4 Mbps); product decision.
- EXIF metadata is not stripped and there is no malware scan (already an ADR residual).
- On disconnect during a download, the httpx response is only closed by the async-generator finaliser; consider closing it explicitly if the httpx pool (10 connections) shows pressure.

## Attempt 2 (commit 8dbc491, rework requested by Ming)

**Verdict: PASS. No 🔴 or 🟡 findings.** Read-only review (no pytest/alembic); the reviewer owns the green-check gate (R8).

**Finding 1 (no cookie rotation on the upload route): fixed.**
- `app/modules/sessions/service.py:74,93`: the new `allow_rotate` flag only gates the rotate branch: `if allow_rotate and (force_rotate or device.rotated_at <= now - ROTATE_AFTER)`. Every other check stays on the same path: missing or multiple cookies rejected (78-82); unknown hash rejected (86-87); `_usable` (61-66, called at 89-90) still enforces revocation, idle expiry and max age; the previous value is still accepted only inside its grace window (102-103); reuse after the grace window still revokes the whole device session, records the audit entry and commits before the 401 (104-112). With rotation off, the current value is only touched (98-99), committed in tx 1 (`app/modules/bug_reports/service.py:242`).
- `app/modules/auth/deps.py`: `get_device` now calls the shared `_resolve_device(..., allow_rotate=True)`, so its behaviour is the same as before. `get_device_no_rotate` / `DeviceNoRotateDep` go through the same helper, so a 401 still clears the cookie when one was sent.
- `allow_rotate` defaults to True. `DeviceNoRotateDep` is used only at `app/modules/bug_reports/auth_router.py:125`; no other route's rotation changed. `DeviceDep` and `set_device_cookie` are still used for the other handler in that file (line 68).
- The upload route no longer contains any `Set-Cookie` code (`committed` flag and error header injection removed), which removes the original lost-rotation risk instead of patching around it.
- Tests: `tests/test_bug_report_attachments_upload.py:550-563` (no `Set-Cookie` on 201 or 503 with a stale `rotated_at`, row unchanged, same value keeps working); 566-575 (`/auth/accounts` still rotates); 578-587 (revoked session → 401, Drive never called).

**Finding 2 (overall `store.create` deadline): fixed.**
- `app/modules/bug_reports/service.py:170,275-286`: `asyncio.timeout(STORE_DEADLINE_SECONDS=60)` wraps the whole `store.create`, including the Drive upload-slot wait (`drive.py:495`, `_MAX_CONCURRENT_UPLOADS = 4`) and `ensure_app_folder`. `TimeoutError` → `DriveErrorCode.UNAVAILABLE` → `AttachmentStorageUnavailableError(retryable=True)`.
- The in-process upload slot is released in `finally` (259-260).
- `drive.py` has no `CancelledError` handler; its inner `asyncio.timeout` blocks (160, 205, 237, 265) only catch their own expiry, so the outer deadline cannot be swallowed.
- Test at 590-607: a hang in `ensure_app_folder` gives 503 `attachment_storage_unavailable`, the slot count is restored, and no row is written.

**503 timeout path leaks nothing.** Fixed message "Attachment storage is unavailable", `details={"retryable": true}`, `Retry-After: 30` (`app/modules/bug_reports/errors.py:76-84`). The log line (`service.py:287-292`) has only `report_id`, `attachment_id` and the enum code. `test_drive_ids_never_exposed` (613) still covers the error shapes.

**🟢 Notes (no change needed for merge):**
1. `service.py:275-286`: if the deadline fires after Drive stored the file but before the id returns, the file stays in Drive with no DB row; it carries the `mm_service`/`attachment_id` appProperties. The sweep ticket (ADM-030b) must cover files with no DB row.
2. With rotation off, an upload only refreshes `last_seen_at`. Bounded by idle and max-age limits, the report window, and the next ordinary cookie request rotating as usual. Recorded in ADR-0023 §4.
3. No test exercises reuse after the grace window through the upload route. The code path is shared and unchanged; a regression test would pin it.
