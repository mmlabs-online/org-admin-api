# Consult — software-architect — ADM-027 (2026-11-19)

ADM-027 architecture consult (read-only). The verdict is in Recommendations 1 and 5, and the biggest risks are R-A and R-B.

## Findings

1. **Scope reference.** The ticket cites "§5.12 Attachment storage — Google Drive". That is a bullet inside spec §5.12 "Bug reports from apps" (`docs/spec-v1.md` lines 179–192), not a heading of its own. There is no conflict: the ticket matches the spec on these points:
   - four all-or-nothing keys
   - `drive.file` scope
   - `<root>/<app_key>/…` layout
   - `appProperties` tags
   - creates never retried
   - dry-run sweep
   - 5 MB images, multipart only
   - plain httpx with no Google SDK
   - the report is still accepted when Drive is off or fails (OQ35)
2. **Existing patterns to copy:**
   - The outbound HTTP pattern already exists in `app/modules/auth/turnstile.py`. It has a Protocol plus a concrete class taking `transport: httpx.AsyncBaseTransport | None` (for MockTransport tests), it is put on `app.state` in `app/main.py:78`, and it is exposed through a `deps.py` getter. It logs only `type(exc).__name__` and status codes. The Drive client should follow the same shape.
   - The "optional feature, log on/off at startup" pattern exists for email (`build_email_sender`, `logger.info("email_sender=%s", ...)`, main.py:86–90). The lifespan already closes a long-lived resource (`dispatcher.aclose()`, main.py:40–44), so a shared `httpx.AsyncClient` can be closed there too.
   - Config: `Settings` has `hide_input_in_errors=True`, and gates are `model_validator(mode="after")` methods that raise messages naming variables only (`_email_gate`, `_jwt_keys_gate` in `app/core/config.py`). The all-or-nothing gate fits this style.
   - CLI: `app/cli.py` dispatches on `argv[:1]`, uses `_Parser` (swallows argparse echo), prints fixed messages only, and catches all exceptions without `str(exc)`. `cleanup` and `audit-purge` are the templates for a sweep command, including the `--dry-run` wording ("would delete"). Note one difference: they use `--dry-run` opt-in, while this ticket wants dry-run by default with `--apply`.
   - `httpx>=0.28.1` is already a runtime dependency (pyproject.toml), so there is no stack change.
3. **No catch-all exception handler.** `app/core/errors.py:234` registers only AppError, validation and HTTP handlers. Any exception that escapes is logged with its full traceback by Starlette/uvicorn. httpx exception messages (`HTTPStatusError`, `ConnectError`, …) contain the request URL, which includes Drive file ids and query strings. A chained `__cause__`/`__context__` also prints. This is the main leak path for AC3.
4. **httpx logs every request at INFO** (`httpx` logger: `HTTP Request: GET https://www.googleapis.com/drive/v3/files/<id>?alt=media ...`), and `httpcore` logs at DEBUG. These lines are invisible today only because nothing raises the root level. If someone turns on INFO logging in deploy, Drive ids appear in logs.
5. **Bug-report ticket chain.** The board has ADM-028 (model + text submit), ADM-029 (staff), and ADM-030 (attachments upload/download). No DB rows that reference Drive files exist until ADM-030. So in this ticket the sweep has no real "referenced" set.

## Recommendations

1. **Module shape (R1, R7).** Put a new infrastructure adapter in `app/modules/attachments/`. No router, models or migration in this ticket.
   - `store.py`: an `AttachmentStore` Protocol (`create`, `open_read`, `delete`, `ensure_app_folder`, `list_files` for the sweep) plus a `DisabledAttachmentStore` that always raises `not_configured`.
   - `drive.py`: `GoogleDriveStore(settings, transport=None)`.
   - `errors.py`: `DriveError(Exception)` with a closed `code` set, for example `not_configured`, `auth_failed` (invalid_grant / revoked), `unavailable` (5xx / network / timeout after retries), `rate_limited`, `not_found`, `too_large`, `rejected` (other 4xx).
   - `DriveError` should not be an `AppError`. Per OQ35, ADM-028/030 services decide whether a Drive failure becomes a per-attachment outcome or "attachment unavailable", so it is not an HTTP status here.
   - Justification for the port: the store has three implementations (Drive, disabled, test fake), and the services in ADM-030 then depend on the Protocol only. This is not ceremony.
   - Name it `attachments`, not `bug_reports`: the video ticket (§5.7) reuses it.
2. **Leak-proofing (AC3, R5, R11).**
   - Catch `httpx.HTTPError`, `ValueError` (JSON) and `KeyError` around every call, and re-raise `DriveError(code)` with `from None`, so neither `__cause__` nor `__context__` carries a URL.
   - `DriveError.__str__`/`args` hold only the code.
   - Log only the event, code, HTTP status, attempt number and our own UUIDs. Never log the response body, `error_description`, file id, folder id or URL.
   - In `create_app` (and the CLI `main()`), set the `httpx` and `httpcore` loggers to WARNING, or attach a NullHandler with `propagate=False` as the CLI already does for sqlalchemy.
   - AC3 test: the fake Drive returns bodies, headers and Location values containing sentinel strings (a fake access token, file id, refresh token, a `googleapis.com` URL). Use `caplog.set_level(DEBUG)` on the root logger plus `httpx`/`httpcore`, run every error path, then assert no sentinel appears in `caplog.text`, `str(exc)`, `repr(exc)`, or the walked `__cause__`/`__context__` chain.
3. **Token handling (R11).**
   - Refresh the access token with `POST https://oauth2.googleapis.com/token` (`grant_type=refresh_token`).
   - Cache it in memory until about 60 s before `expires_in`.
   - Refresh under an `asyncio.Lock` so concurrent uploads do not stampede.
   - On one 401 from Drive, invalidate and refresh once.
   - `invalid_grant` maps to `auth_failed` and logs a fixed `drive_auth_failed` event. This is the signal for the owner's rotation runbook.
   - Keep endpoint URLs as module constants, not config, so the bearer cannot be sent elsewhere.
   - Set `follow_redirects=False` and treat any 3xx as `rejected`.
   - Store all four settings as `SecretStr` or `str | None`. The client secret and refresh token must be `SecretStr`.
4. **Config gate (AC1, R5).**
   - The planner fixes the names. A suggestion: `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`. See the open question on TuDo name parity.
   - One `_drive_gate` validator:
     - all unset means off, and an `attachments_enabled` property is False;
     - a partial set raises `"<NAMES> must be set together (Google Drive attachments)"`, listing only the missing names;
     - malformed values raise a message naming the variable only.
   - Keep the "malformed" checks loose but cheap: no whitespace or control characters, a length cap, and a folder id matching `[A-Za-z0-9_-]{10,128}`. Being too strict about Google token formats will break on Google-side changes.
   - Off is allowed in production (spec: none set = off). Log `attachment_store=drive|disabled` at startup, like email.
   - Test matrix: none / each single key / each three-of-four / malformed / all valid. Also assert that no value appears in the `ValidationError` text.
5. **Layout: flat `<root>/<app_key>/` instead of per-report folders.**
   - Option A (recommended): flat per app. Each file carries `appProperties` such as `{mm_service: "org-admin-api", app_key, report_id, attachment_id}`.
     - Gains: one Drive create per upload; no folder-create races per report; no empty folders left after the 90-day attachment deletes (spec: attachment rows stay with a deleted marker, so deletes are per file anyway); simpler sweep.
     - Gives up: browsing by report in the Drive UI, and deleting a whole report in one call.
   - Option B: `<root>/<app_key>/<report_id>/`. One `files.delete` on the folder removes its children at the 365-day report purge, but it doubles the non-idempotent creates per report, and duplicate folders appear under concurrency.
   - App folders under A: find-or-create by `appProperties` (`mm_service`, `app_key`, `kind=app_folder`) with parent = root. Cache the id per process. On duplicates, deterministically pick the oldest by `createdTime`. App keys are already grammar-checked (`a-z0-9-`), but still escape `'` and `\` in every `q` string.
6. **Retries (spec: creates never retried).**
   - `create` makes one attempt. An uncertain outcome (timeout after the body was sent) becomes `unavailable`, and any orphan file is left to the sweep.
   - `read` (before streaming starts), `delete`, `list` and folder lookup use bounded retries: for example 3 attempts with jittered backoff on connection errors, 429 and 5xx, and a short cap on `Retry-After`.
   - A folder create is also a create, so do not retry it. Re-run find-then-create instead.
   - `delete` treats 404 as success, so it is idempotent for retention re-runs.
   - Use explicit timeouts (connect plus overall) so a request handler cannot hang on Drive.
7. **Size cap and streaming (AC2).**
   - Multipart needs the whole body, so `create` takes `bytes` of at most 5 MB.
   - Provide `read_capped(stream: AsyncIterator[bytes], max_bytes) -> bytes`, which raises `too_large` as soon as the cumulative size exceeds the cap, without reading the rest. ADM-030 uses it on `request.stream()`.
   - `create` re-checks `len(data)`.
   - Also cap concurrent uploads with a semaphore, because the server holds up to 5 MB in memory per upload. Make the cap a setting with a small default, similar to `argon2_max_concurrency`.
   - `open_read` should be an async context manager that yields a byte iterator and enforces the same cap. A bare iterator leaks pooled connections when the client disconnects. ADM-030 wraps it in a `StreamingResponse` with a background close.
   - Magic-byte type checks belong to ADM-030 (upload), not here. Tag the type in `appProperties` only if the plan wants it.
8. **Sweep: fail closed.**
   - The sweep lives in `attachments/sweep.py`. It takes a `ReferenceChecker` Protocol (`async referenced(attachment_ids) -> set[UUID]`) and a grace period, with a hard floor (for example ≥ 1 h, default 24 h) longer than the time between a Drive accept and the DB commit.
   - It lists only files (never folders) under the root that carry `mm_service=org-admin-api`, and deletes a file only if `createdTime < now − grace` and its `attachment_id` is not referenced.
   - Files with missing or unparsable `appProperties` are reported but never deleted.
   - A checker error aborts the run with nothing deleted.
   - Until ADM-030 provides a real checker, the production CLI wiring has none, so `--apply` must refuse with a fixed message. Otherwise an empty reference set deletes every file. Tests inject a checker to cover AC4.
   - CLI: `python -m app.cli attachments-sweep [--apply] [--grace-hours N]`, dry-run by default, digits-only argument parsing as in `audit-purge`. Output is counts plus our UUIDs, app key and age, never Drive ids (AC3).
   - Exit with a fixed message on `not_configured`.
9. **Record the decision.** Add a short ADR (next number is 0018) or a D-entry in `docs/identity-architecture.md` §8. It should cover:
   - Drive as an external store and a new third-party secret
   - the flat layout
   - the `appProperties` schema (it becomes a contract the sweep and retention rely on)
   - create-no-retry and the sweep as the consistency mechanism
   - the fail-closed sweep
   - the fixed error codes

   `app/core/config.py` is a High path, so the existing plan-approval gate applies.
10. **`docs/deploy.md` (AC5).** Document the following:
    - the four secrets and their env names
    - the rotation runbook: issue a new refresh token, update the deployment secret, redeploy, revoke the old token, then verify with the smoke script or the startup log
    - what `drive_auth_failed` in the logs means
    - the shared quota with TuDoCuaTui

## Risks

- **R-A (high): `drive.file` cannot see a folder created by hand in the Drive web UI.** With scope `drive.file`, the app can only access files it created itself or that were opened through the Picker. If M-1 has the owner create the root folder manually, every create under it fails (404 or permission error). Mitigation: let this API's own OAuth client create the root folder. For example, add a one-off CLI `attachments-init-root` (or a step in the smoke script) that creates the folder and prints its id once for the owner to store as a secret. Then reword M-1 accordingly. Check how TuDo did it (its D27/D28).
- **R-B (high): refresh tokens can expire after 7 days.** If the Google Cloud project's OAuth consent screen is "External" with publishing status "Testing", refresh tokens expire after 7 days, and attachments would silently go off a week after M-1. The publishing status is per project, so if the new client sits in TuDo's project it shares TuDo's status. Tokens also expire after about 6 months unused, and the owner changing the account password can revoke them. The runbook should state the required publishing status, and `auth_failed` must be visible in the logs.
- **R-C: leak through tracebacks and httpx logging** (Findings 3–4). Recommendation 2 handles it, but only if every code path wraps errors. A regression test is mandatory.
- **R-D: the sweep could delete live files.** Mitigated by the fail-closed checker, the grace floor and the `mm_service` marker. Restoring the DB from a backup makes the sweep delete files whose rows came back missing, which is correct. Rows pointing at files that no longer exist are the opposite case, and ADM-030 shows them as "attachment unavailable".
- **R-E: memory and Drive rate limits under load.** Mitigated by 5 MB buffers with a semaphore. The quota is shared with TuDo and the whole Google account (Gmail/Photos), so a full quota surfaces as Drive 403 `storageQuotaExceeded`. Consider mapping that to its own code (`quota_exceeded`) for operator clarity.
- **R-F: scope creep into ADM-030.** Magic-byte checks, download headers, the per-attachment outcome response and the retention purge all belong to ADM-028/030 (R7). Keep this ticket to the adapter, config, sweep service with CLI, and docs.

## Open questions

1. **Env names.** "Unprefixed in the env file, like TuDo D28": should this API use the same env names as TuDo (for example `GOOGLE_CLIENT_ID`, …), or `GOOGLE_DRIVE_*`? The deployment-repo secret names must differ from TuDo's either way, for example `ORG_ADMIN_GOOGLE_DRIVE_*` mapped to unprefixed keys. This needs the TuDo source or the owner.
2. **Root folder creation (R-B above).** Will the owner create the root folder through this API's client (a CLI or script in this ticket), or did TuDo use another approach, such as a token with broader scope at setup time?
3. **Layout.** Confirm flat `<root>/<app_key>/` (Recommendation 5) over per-report folders.
4. **Sweep CLI before ADM-030.** Is it acceptable that `--apply` refuses until ADM-030 registers the reference checker, while AC4 is proven with an injected checker in tests?
5. **Dry-run output.** Is our attachment UUID, app key and age enough for the dry-run list, given AC3 forbids Drive ids?
6. **Real smoke script.** Should it be part of this ticket's diff (for example `scripts/drive_smoke.py`, manual and excluded from CI), or only documented?
