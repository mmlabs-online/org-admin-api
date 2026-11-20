# ADM-027 security consult (security-architect, read-only) — 2026-11-19

The reference repo (TuDoCuaTui) is not in this container. This report draws only on `docs/spec-v1.md` §5.12 (lines 175–193), `app/core/config.py`, `app/main.py`, `app/modules/auth/turnstile.py` (the only outbound `httpx` client in the repo) and `app/cli.py`.

## Findings

1. **httpx can leak Drive ids through logs (AC3).** Nothing in the app configures logging; uvicorn's default leaves the root logger at WARNING. If anyone raises the level, the `httpx` logger prints `HTTP Request: GET https://www.googleapis.com/drive/v3/files/<file_id>?alt=media` at INFO, and the folder lookup URL carries the root folder id in `q=`. Separately, `httpx.HTTPStatusError` (from `raise_for_status()`) and `RequestError.request` include the full URL in `str(exc)`.
2. **Unhandled exceptions get logged with their full chain.** There is no catch-all handler in `app/core/errors.py` (only AppError, validation and HTTPException), so Starlette's ServerErrorMiddleware logs the traceback with `__cause__`/`__context__`. An httpx exception that escapes, or an AppError raised `from exc`, puts URLs or ids in logs.
3. **Existing patterns to copy:**
   - `turnstile.py` logs only `type(exc).__name__` and status codes, and takes an injectable `transport` (fits the MockTransport test plan).
   - `build_email_sender` plus the `email_sender=gmail|disabled` startup log in `main.py:86-90` is the right shape for "configured / not configured".
   - Config already has `hide_input_in_errors=True` and `env_ignore_empty=True`, so empty means unset, which matches all-or-nothing.
   - The `_email_gate` validator names variables only.
   - CLI convention: fixed messages, never `str(exc)`, counts only.
4. **No SSRF surface if hosts are constants.** The only hosts needed are `oauth2.googleapis.com/token` and `www.googleapis.com` (`/drive/v3`, `/upload/drive/v3`).
5. **Drive `q` query injection.** The folder lookup builds a `q` string (`appProperties has { key='…' and value='<app_key>' } and '<root>' in parents and trashed=false`). `app_key` is constrained upstream (2–32 chars, `a-z0-9-`), but the client takes it as a parameter. The client should not rely on callers.
6. **`drive.file` is the right least-privilege scope, but its boundary is the Google Cloud project, not the OAuth client.** My understanding is that "files created by the app" means per Cloud project; please verify. If the new OAuth client sits in the same Cloud project as TuDo's, this API's token can likely see and delete TuDo's files. OQ34 ("separate OAuth client") does not by itself isolate the apps.
7. **Sweep before ADM-030 would delete everything.** With no tables yet, the reference set is empty, so `--apply` would treat every file older than the grace period as unreferenced.

## Recommendations

**Config (AC1, R5)**
- `SecretStr` for client secret and refresh token. Also treat the root folder id as sensitive (`SecretStr` or never logged) because AC3 bans Drive ids.
- One `model_validator`:
  - 0 of 4 set: off. 1–3 set: error listing the missing names.
  - Format checks with fixed messages: client id ends `.apps.googleusercontent.com`; folder id matches `^[A-Za-z0-9_-]{10,128}$`; token and secret are non-empty with no whitespace or control characters.
- No network call at startup. Log `drive=configured|disabled` with no ids.
- Suggested names (planner decides): `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`.

**Error and log hygiene (AC3, R5, R11)**
- Define a fixed `DriveError(code)` enum: `not_configured`, `auth_failed` (token refresh 400/401, e.g. `invalid_grant`), `quota_exceeded` (403 `storageQuotaExceeded`), `rate_limited` (429 or 403 rate-limit reasons), `not_found`, `too_large`, `rejected` (other 4xx), `unavailable` (5xx, timeout, network).
- Map Google's JSON `error.errors[].reason` through an allow-list only. Never copy Google's text.
- Always `raise DriveError(...) from None`. Never call `raise_for_status()`. Log only code, HTTP status and exception type name.
- Pin `logging.getLogger("httpx")` and `"httpcore"` to WARNING when the client is built, or add a redacting filter.
- AC3 test:
  - Setup: caplog at DEBUG on the root logger. The fake returns error bodies with sentinel strings, a sentinel file id, a sentinel access token, and a sentinel refresh token in config.
  - Assert no sentinel appears in `caplog.text`, in `str()`/`repr()` of the raised error, in the error envelope `details`, or in CLI stdout/stderr.

**Token handling**
- Keep the access token in memory only, with an expiry margin of about 60 s.
- Refresh single-flight behind an `asyncio.Lock`. The refresh token goes only in the form body, never in a URL.
- `follow_redirects=False` (the httpx default; state it explicitly).
- Explicit timeouts (connect ~5 s, read ~30 s) plus an overall deadline per call, and bounded pool limits.

**Input hardening inside the client (defense in depth)**
- Check `app_key` against `^[a-z][a-z0-9-]{1,31}$` and `report_id` as a UUID before they go into `q` or names.
- Escape `\` and `'` in `q` literals.
- Check every file id against `^[A-Za-z0-9_-]{1,128}$` before building a URL path. That blocks `/`, `?`, `..` and `%` even if a DB row is tampered with.
- File and folder names are generated by the server (UUID or app key), never a user filename. `appProperties` carry ids only, plus a fixed owner marker such as `mm_src=org-admin-api` (Drive allows 124 bytes per key+value).

**Upload (AC2)**
- Count bytes on the incoming stream and abort at 5 MB + 1. Never trust `Content-Length`; reject early only if it is present and already too large.
- Build the `multipart/related` body with a random boundary (`secrets.token_hex`), `json.dumps` metadata, and the server-detected `mimeType` (the magic-byte check is the caller's job in ADM-029; the client takes a pre-validated type from an allow-list enum).
- A process-wide semaphore caps buffered concurrent uploads (memory DoS: 5 MB × 5 files × concurrent requests).
- Retries:
  - Creates (file and folder) are never retried on timeout or 5xx, because the file may already exist.
  - GET, list and DELETE get bounded retries (e.g. 3 attempts, jittered backoff, under the overall deadline) on 429/5xx/network only.
  - DELETE returning 404 counts as success.

**Read (AC2)**
- Expose an async-iterator or context manager that always closes the upstream response, including on client disconnect.
- Apply the same byte cap on read (a file changed out of band could be larger).
- Return bytes only. Never pass Google's `Content-Type`, `Content-Disposition` or other headers through; the ADM-030 route sets the fixed safe headers from §5.12.

**Folder find-or-create**
- Per-`app_key` `asyncio.Lock` plus an in-memory cache of folder ids.
- After a create, re-list and pick a deterministic winner (oldest `createdTime`) to absorb races from multiple processes. Duplicate empty folders are harmless but must not break the sweep.

**Sweep (AC4)**
- Default is dry-run; `--apply` deletes.
- Hard minimum grace period (e.g. ≥ 24 h) so a file Drive accepted but whose DB row is still in flight is never removed.
- Candidates are only files under the configured root, carrying the owner marker, not folders, listed via `'<id>' in parents` per known subfolder. Never search the whole Drive.
- Fail closed:
  - If the reference provider is not wired (before ADM-030) or the DB cannot be read, `--apply` refuses with a fixed message and deletes nothing.
  - Add a safety cap (`--max-deletes`, default small) beyond which it aborts.
- Output counts only; no ids or names. Follows the `cleanup` / `audit-purge` patterns in `app/cli.py`.

**Layout:** `<root>/<app_key>/<file>` with `report_id` in `appProperties` is enough for security. A per-report subfolder adds create calls (never retried) and race surface for no access-control gain, since Drive ids never reach browsers.

**Placement (R1):** `app/modules/attachments/drive.py` holds a `DriveClient` Protocol, a real client and a disabled client. Build it in `create_app` and put it on `app.state`, like the email sender. Routers never touch it directly.

**Docs (AC5):** in `docs/deploy.md`, cover:
- The four secrets and the `printf` env-file writing, as for SMTP.
- The rotation runbook: mint a new refresh token → update the deployment secret → redeploy → revoke the old token (Google account → third-party access, or the `oauth2.googleapis.com/revoke` endpoint) → confirm with the smoke script.
- The emergency step on leak: revoke first and accept that attachments are off (OQ35 keeps reports flowing).

## Risks

- **High – cross-app blast radius (Finding 6).** If both OAuth clients share a Cloud project, a leaked token or a buggy sweep here can permanently delete TuDo files. Mitigation: a separate Cloud project for this API, plus the root-scoped, owner-marked sweep above.
- **High – sweep deleting everything before ADM-030** (Finding 7), unless `--apply` fails closed.
- **Medium – refresh tokens expiring every 7 days** if the OAuth consent screen stays in "Testing". The owner should publish it ("In production"); I believe `drive.file` does not require Google verification, but please confirm. Otherwise attachments silently turn off weekly.
- **Medium – ids or tokens leaking via httpx logs or exception chains** (Findings 1–2) if the logger pinning and `from None` rules are not enforced and tested.
- **Medium – memory and worker exhaustion** from buffered 5 MB multipart bodies and slow Drive calls. Needs the semaphore and deadlines.
- **Low – root folder unusable under `drive.file`** if the owner creates it by hand in the Drive UI (the app cannot see files it did not create or open). M-1 should create the root through this OAuth client (e.g. a `drive-init` step in the smoke script).
- **Accepted (spec):** a new long-lived third-party secret, shared quota with TuDo, content under Google's terms, no malware scanning, EXIF left to apps.
- **Process:** `app/core/config.py` is a High path, so plan approval by Ming is required (already gated). Ask for an appsec-engineer `security-review.md`.

## Open questions (for Ming / planner)

1. Is this API's OAuth client in its own Google Cloud project, separate from TuDo's? If not, can it be moved? (Risk 1.)
2. Is the consent screen published (not "Testing") so the refresh token does not expire after 7 days?
3. Should the sweep trash files (recoverable for about 30 days) instead of deleting them permanently? §5.12 says permanent for retention; the sweep could reasonably be softer.
4. After a 401 on a create, may the client refresh the token and retry once (no file was created), or is "creates never retried" absolute? Safe either way if the token is refreshed before expiry.
5. Must staging/production require Drive keys, or is "off" acceptable there? The spec allows off; a startup warning in production seems prudent.
6. Who creates the root folder in M-1, given the `drive.file` visibility constraint (Risk 5)?
