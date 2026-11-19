# Security review — ADM-027 (appsec-engineer)

## Attempt 1 (commit 2b3a1a6)

**Verdict: no blockers. One should-fix (🟡) and a few notes (💭).** I ran the focused suites: `uv run pytest tests/test_attachments_drive.py tests/test_attachments_sweep.py tests/test_cli_attachments.py tests/test_config.py` gave 191 passed. I did not run the full `make check`; the main REVIEW still needs it for R8.

### Findings

**🟡 1. A single Drive call has no overall time limit, and token refresh is retried while the shared lock is held.** Files: `app/modules/attachments/drive.py:188-206, 208-224, 226-265, 291-304`.
- Each attempt has its own `asyncio.timeout(60)` (lines 155, 215). Nothing bounds the whole call:
  - `_run` allows up to 3 attempts × 60 s, plus one extra refresh cycle after a 401.
  - `_access_token` retries the token fetch 3 × 60 s inside `self._lock`.
- **Risk:** during a Google outage or slow-drip, the first caller holds the lock for about 3 minutes. Each queued caller then takes the lock and does its own 3 × 60 s. So N concurrent uploads in ADM-030 wait about N × 3 min, which ties up request workers. That is availability, not confidentiality.
- Streamed reads only have httpx's per-read 30 s timeout (line 136). There is no deadline for the whole body, so a 5 MB body sent slowly can hold a connection for a very long time.
- The security consult asked for "an overall deadline per call" with retries kept under it (consult lines 43 and 57). That is not implemented.
- **Fix:**
  - In `_run`, wrap the retry loop in `async with asyncio.timeout(_CALL_DEADLINE_SECONDS)` (for example 60 s). Turn `TimeoutError` into `DriveError(UNAVAILABLE)`, raised outside the `except` as elsewhere.
  - Bound `_access_token` the same way (for example 20 s total).
  - In `_capped_stream`, track a monotonic deadline and raise `UNAVAILABLE` once it passes.
  - Add a test with a fake transport that sleeps past the deadline.

**💭 2. `expires_in` from Google has no bounds check** (`drive.py:180, 199`). A value like `"inf"` would cache the access token forever. Fix: `expires_in = min(max(expires_in, 0), 3600)` and reject values that are not finite.

**💭 3. The sweep matches references by `attachment_id` only, and duplicate ids overwrite each other** (`sweep.py:72-89`). This fails safe: files are kept, never deleted wrongly. But a second Drive file with the same `attachment_id` is never considered. **Follow-up for ADM-030:** the `ReferenceChecker` should compare `(attachment_id, file_id)` pairs, so an orphan whose attachment id also exists on a row is still found.

**💭 4. The sweep prints `app_key` exactly as stored in Drive** (`drive.py:506-509` → `cli.py:870`). Only this OAuth client can write `appProperties`, so the risk is low. Still, checking the value against `_APP_KEY_RE` in `list_files` (otherwise set it to `None`) would keep terminal control characters out of the CLI output.

**💭 5.** `DriveApi.__init__` sets the `httpx` and `httpcore` loggers to WARNING for the whole process (`drive.py:130-131`), and only when Drive is enabled. That is fine for this ticket. Note that other httpx users in the API get no such protection when Drive is off.

**💭 6.** The app folder id is cached for the life of the process (`drive.py:396-428`). If the folder is deleted outside the app, creates fail with `not_found` until a restart. This affects availability only.

### Checked and OK
- **Secrets:** client secret, refresh token and root folder id are `SecretStr` (`config.py`). `hide_input_in_errors=True` (`config.py:35`) means a failed check never echoes values, and the error names the keys only. The settings check covers all-or-none, client id format, printable-ASCII secret and token (no spaces or control characters), and folder id `[A-Za-z0-9_-]{10,128}`. The refresh token goes only in the form body.
- **Error and log hygiene:**
  - `DriveError` carries only a code, and every `raise DriveError` is outside an `except` block, so there is no cause or context.
  - `_error_reasons` only compares Google's reasons against an allow-list and never copies its text.
  - Log lines hold op, code, status and attempt only.
  - The CLI prints fixed messages and swallows `Exception` without calling `str(exc)`.
  - The sentinel test (`tests/test_attachments_drive.py:417`) checks logs, `str`/`repr`, `__cause__` and `__context__`.
- **No SSRF:** all three hosts are constants (`drive.py:29-31`) and redirects are off (`follow_redirects=False`, line 138). A 3xx response becomes `rejected`.
- **Query and id injection:**
  - `_q_literal` escapes `\` and `'`.
  - `app_key` is checked against `[a-z][a-z0-9-]{1,31}` before use.
  - The root folder id is checked by config; listed folder ids and file ids are checked by `_FILE_ID_RE` before they go into a URL path or `q`. `/`, `.`, `?` and `%` are blocked.
- **Upload body:** the multipart boundary comes from `secrets.token_hex`, metadata is built with `json.dumps`, and the MIME type comes from the `ImageType` enum.
- **5 MB cap:** enforced on create before any request (line 439), on read by `_capped_stream`, and by `read_capped`. The streamed response is always closed.
- **Retries:** folder and file creates use `idempotent=False` and are never retried, including after a 401. GET, list and DELETE are retried at most 3 times on 429, 5xx or network errors, with jitter and Retry-After capped at 5 s. A DELETE that returns 404 counts as success.
- **Token refresh:** the token lives in memory only, with a 60 s margin. Refresh runs once at a time behind the lock. On a 401 there is one refresh, and only for idempotent calls. A 400 or 401 from the token endpoint becomes `auth_failed` and is not retried.
- **Sweep fails closed:**
  - `--apply` is refused without a reference checker (`sweep.py:67`), and the production `main()` never wires one.
  - It is refused if the reference check fails (`sweep.py:87`).
  - The `--max-deletes` check runs before any delete (`sweep.py:94`).
  - The grace period has a 24 h floor in both the service and the CLI.
  - Candidates must be under the configured root, inside a folder carrying the marker, not be folders, and have `kind=attachment` with a valid UUID; anything else is skipped.
  - An unparsable `createdTime` is treated as young, so the file is kept.
  - CLI numbers accept ASCII digits only.
- **`attachments-init-root`:**
  - Runs only when stdin is a TTY (`cli.py:911`).
  - The secret and token are read with `getpass`, and the command refuses if echo cannot be turned off.
  - Nothing is written to disk or settings.
  - Errors print a fixed message with the code only, and the API client is closed in `finally`.
  - The folder id is printed once to stdout, as the plan intends. Running it twice simply creates a second root, which does no harm.

### Follow-ups (out of scope)
- ADM-030: the reference checker should compare file ids as well (finding 3). The upload route should cap the request stream and `Content-Length` before buffering, because the semaphore in `create` only limits uploads after the data is already in memory.
- Consider protecting the httpx logger for the whole process (not only when Drive is enabled) for other outbound httpx clients.
