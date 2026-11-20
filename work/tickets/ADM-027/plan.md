Status: READY

# Plan — ADM-027: Google Drive attachment client for bug reports

READY on the proposed defaults of the six questions in `## Blocking questions`. Ming answers them in the
batch 2026-11-19-2 approval round. Any answer that differs from a default changes only the steps named
in that question.

## Scope summary
Source: `docs/spec-v1.md` §5.12, "Design facts and decisions carried into PLAN" → "Attachment storage —
Google Drive" (lines 179–184), plus "Implications of Drive" (lines 185–191) and OQ23/OQ24/OQ34/OQ35
(lines 175, 254–255, 265–266).

- l.180 — "**Client:** Drive v3 REST over plain HTTP, no Google SDK. `httpx` is already a runtime
  dependency of this API, so no new library is needed (stack rule holds). OAuth scope `drive.file` (the
  app sees only files it created)."
- l.181 — "**Config (R5):** four env keys — OAuth client id, client secret, long-lived refresh token, root
  folder id (all specific to this API, OQ34) — all-or-nothing: none set = attachments off; a partial set
  = startup error naming the variables only, never values. New keys in `app/core/config.py` and
  `.env.example`; production values are repository secrets of `mmlabs-online/deployment` written to the
  env file at deploy."
- l.183 — "files go under this API's **own root folder** (not TuDoCuaTui's), with **one subfolder per app
  key** (e.g. `<root>/ricotdin/…`) …; any further split per report … is PLAN's choice. Files tagged with
  Drive `appProperties`. The DB row is written after Drive accepts the file; if the DB write fails the
  Drive file is deleted; Drive creates are never retried. A sweep CLI (dry-run by default) deletes
  untracked Drive files older than a grace period."
- l.184 — "images are capped at **5 MB** … so **Drive multipart upload suffices** for v1."
- l.189 — "**Custody (OQ34):** the owner holds the Google account and rotates this API's refresh token;
  rotation is a documented runbook step for the owner."
- l.191 — "**Deletes are permanent**."
- l.175 (OQ35) — when Drive is off or fails, "the report is **still accepted as text**". In this ticket
  that means the adapter fails with a typed error and never fails startup when the keys are absent.
- l.182 — "Drive file ids never reach the browser". This is the basis for AC3.

`work/decisions.md` has nothing on Drive or attachments. No conflict between the ticket and the spec
(consult-software-architect Finding 1).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | With no Drive env keys the app starts and the client reports "not configured"; a partial or malformed set fails startup naming only the variables. | `tests/test_config.py` › `test_drive_keys_none_is_disabled`, `test_drive_keys_partial_names_missing_only` (parametrized over all 14 partial subsets), `test_drive_keys_malformed_names_variable_only`, `test_drive_errors_do_not_echo_values`. `tests/test_attachments_drive.py` › `test_build_store_disabled_when_not_configured` (every method raises `DriveError(not_configured)`), `test_create_app_logs_attachment_store_state`. |
| 2 | Create/read/delete/folder lookup work against a fake Drive in tests; the 5 MB cap is enforced on the stream. | `tests/test_attachments_drive.py` › `test_create_*`, `test_open_read_*`, `test_delete_*`, `test_ensure_app_folder_*` against `tests/fake_drive.py` (httpx `MockTransport`). Cap: `test_read_capped_rejects_over_5mb_without_draining`, `test_create_rejects_over_5mb_without_request`, `test_open_read_over_cap_raises_too_large`. |
| 3 | No Google response text, Drive id, token or URL appears in logs or error details (test). | `tests/test_attachments_drive.py` › `test_no_sentinel_leaks_on_any_error_path` (caplog at DEBUG on root, `httpx` and `httpcore`; checks `str`/`repr` of the error and the walked `__cause__`/`__context__` chain). `tests/test_cli_attachments.py` › `test_sweep_output_has_no_drive_ids` (capsys). |
| 4 | Sweep CLI: dry-run lists, `--apply` deletes only unreferenced files older than the grace period. | `tests/test_attachments_sweep.py` (service) and `tests/test_cli_attachments.py` › `test_sweep_dry_run_lists_and_deletes_nothing`, `test_sweep_apply_deletes_only_old_unreferenced`, `test_sweep_apply_refuses_without_reference_checker`, `test_sweep_grace_floor_and_usage`, `test_sweep_max_deletes_aborts`. |
| 5 | `docs/deploy.md` documents the four secrets (deployment-repo secrets, unprefixed in the env file, like TuDo D28) and the owner's refresh-token rotation step. | Read the new `docs/deploy.md` section "Attachments — Google Drive (ADM-027)". It must contain: the table of the four keys; the `ORG_ADMIN_GOOGLE_DRIVE_*` deployment secrets written unprefixed with `printf`; one-time setup (Q2); the rotation runbook; leak response; and the `drive_auth_failed` log meaning. |
| — | `make check` green. | `make check`. No migration in this ticket, so no `alembic check` is needed. |

## Ming verification after merge
After M-1, Ming checks these against the real Google account:
- `attachments-init-root` (Q2) creates the root folder and prints its id. A deploy with the four secrets
  logs `attachment_store=drive`.
- `python -m app.cli attachments-sweep` (dry run) on the Mac exits 0 against the real Drive, which proves
  the token refresh and the listing work. Until ADM-030 it reports "references not checked".
- The OAuth consent screen of the project that holds the client is published ("In production", not
  "Testing"). Otherwise the refresh token expires after 7 days (consult R-B).
- The client sits in its own Google Cloud project, apart from TuDo's (Q4).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/core/config.py` | Add 4 keys, a `drive_enabled` property and a `_drive_gate` validator (after `_email_gate`, l.249–262). | AC1, R5. | **High** (`app/core/**`) |
| `.env.example` | New "Attachments — Google Drive (ADM-027)" block with the 4 keys, empty values. | AC1, R5. | **High** |
| `app/modules/attachments/__init__.py` | New, empty. | Module. | no |
| `app/modules/attachments/errors.py` | New: `DriveErrorCode` StrEnum and `DriveError(Exception)`, code only. | AC3. | no |
| `app/modules/attachments/store.py` | New: `AttachmentStore` Protocol, `ImageType` enum, `StoredFile` dataclass, `MAX_ATTACHMENT_BYTES`, `read_capped()`, `DisabledAttachmentStore`, `build_attachment_store(settings)`. | AC1, AC2; the port that ADM-030 depends on. | no |
| `app/modules/attachments/drive.py` | New: `DriveApi` (token + request wrapper) and `GoogleDriveStore` (create, open_read, delete, ensure_app_folder, list_files, aclose), plus `create_root_folder()` for Q2. | AC2, AC3. | no |
| `app/modules/attachments/sweep.py` | New: `ReferenceChecker` Protocol, `SweepService`, `SweepResult`, grace constants. | AC4. | no |
| `app/main.py` | Build the store and put it on `app.state.attachment_store`. Log `attachment_store=drive\|disabled`. Close it in `_lifespan` (l.40–44). | AC1. | no |
| `app/cli.py` | Add `attachments-sweep` and `attachments-init-root` (Q2) subcommands, docstring lines and `amain` kwargs. Add `httpx`/`httpcore` to the logger silencing in `main()` (l.795). | AC4, Q2. | no |
| `docs/adr/0018-attachment-storage-google-drive.md` | New ADR. Covers Drive as an external store plus a new secret, the flat layout, the `appProperties` contract, create-no-retry with the sweep as the consistency mechanism, the fail-closed sweep and the error codes. | The decision outlives the ticket (loop.config Domain focus; consult-SA Rec 9). | no |
| `docs/deploy.md` | New section "Attachments — Google Drive (ADM-027)" after "Retention cleanup". Add one bullet under "Required runtime config" pointing to it. | AC5. | no |
| `work/ming-todo.md` | Reword M-1 with the secret names, the init-root step and the published consent screen. | M-1 says "names set by ADM-027's plan". | no |
| `tests/fake_drive.py` | New: an in-memory fake Drive for `httpx.MockTransport`, with failure and sentinel injection. | AC2–AC4. | no |
| `tests/test_config.py` | Add the Drive config matrix tests. | AC1. | no |
| `tests/test_attachments_drive.py` | New. | AC1–AC3. | no |
| `tests/test_attachments_sweep.py` | New. | AC4. | no |
| `tests/test_cli_attachments.py` | New. | AC3, AC4, Q2. | no |

High-path files: `app/core/config.py` and `.env.example`. This triggers the plan-approval gate and the
`appsec-engineer` review (`security-review.md`). The PR is High, so Ming merges it.

## Data model / migration
None. The ticket creates no table. The sweep's reference set comes from a `ReferenceChecker` that ADM-030
implements over its attachment table.

## API contract
None. No HTTP endpoint is added. The store sits on `app.state` for ADM-030. A `deps.py` getter is added by
ADM-030 together with its first route, which keeps R1 and avoids dead code here.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_config.py` › `test_drive_keys_none_is_disabled` | No key set: `drive_enabled is False`. Empty strings count as unset (`env_ignore_empty`). |
| `tests/test_config.py` › `test_drive_keys_all_valid_enabled` | All 4 valid: `drive_enabled is True`. |
| `tests/test_config.py` › `test_drive_keys_partial_names_missing_only` (parametrized, the 14 non-empty proper subsets) | Error names exactly the missing keys, in fixed order. No value appears in `str(exc)`. |
| `tests/test_config.py` › `test_drive_keys_malformed_names_variable_only` (parametrized per key: whitespace, control char, too long, bad client-id suffix, bad folder-id charset/length) | Fixed message `"<KEY> is malformed"`. No value in the text. |
| `tests/test_config.py` › `test_drive_secret_fields_hidden_in_repr` | `repr(settings)` has none of the 4 sentinel values. |
| `tests/test_attachments_drive.py` › `test_build_store_disabled_when_not_configured` | `DisabledAttachmentStore`. Every method raises `DriveError` with code `not_configured`. `configured is False`. |
| `tests/test_attachments_drive.py` › `test_create_app_logs_attachment_store_state` | `create_app` with and without keys logs `attachment_store=disabled` / `drive` (WARNING in staging/production when disabled). Sets `app.state.attachment_store`. |
| › `test_token_refreshed_once_and_cached` | Concurrent calls trigger 1 token POST (lock). The refresh token goes only in the form body. The cached token is reused until 60 s before expiry. |
| › `test_token_invalid_grant_is_auth_failed` | 400 `invalid_grant` → `auth_failed`. Fixed log event `drive_auth_failed`. |
| › `test_ensure_app_folder_finds_existing` / `_creates_when_missing` / `_picks_oldest_duplicate` / `_cached` | Folder lookup by `appProperties` under the root. Create when missing, re-list after creating, pick the oldest `createdTime`. The per-process cache means a second call makes no HTTP request. |
| › `test_ensure_app_folder_rejects_bad_app_key` | `app_key` must match `^[a-z][a-z0-9-]{1,31}$`, else `invalid_input` with no request sent. The q-escaping unit test covers `'` and `\`. |
| › `test_create_multipart_body_and_properties` | Single POST to the upload URL. `multipart/related` with a random boundary. Metadata: name = attachment UUID, parent = app folder, `mimeType` from `ImageType`, appProperties `mm_service, kind=attachment, app_key, report_id, attachment_id`. Returns the file id. |
| › `test_create_never_retried` (parametrized: 500, 503, timeout, 401, 429) | Exactly 1 upload request, then the mapped code (`unavailable`/`auth_failed`/`rate_limited`). On 401 the cached token is invalidated. |
| › `test_create_rejects_over_5mb_without_request` | `len(data) > 5 MiB` → `too_large`, 0 requests. |
| › `test_create_quota_exceeded` | 403 `storageQuotaExceeded` → `quota_exceeded`. 403 `userRateLimitExceeded`/`rateLimitExceeded` → `rate_limited`. Other 403/400 → `rejected`. |
| › `test_open_read_streams_bytes` | `async with store.open_read(fid) as chunks` yields the content. The response is closed on exit, including on early exit (fake records the close). |
| › `test_open_read_over_cap_raises_too_large` | Fake serves 5 MiB + 1 → `too_large` while iterating. |
| › `test_open_read_not_found` | 404 → `not_found`. |
| › `test_read_and_delete_retry_bounded` | 503, 503, 200 → success after 3 attempts. 4× 503 → `unavailable` after 3. 400 is not retried. A 401 on an idempotent call refreshes once and retries once. |
| › `test_delete_404_is_success` | Idempotent delete. |
| › `test_redirect_is_rejected` | 302 from the fake → `rejected` (`follow_redirects=False`). |
| › `test_invalid_file_id_rejected_before_request` | `"../x"`, `"a/b"`, `"a?b"`, `""`, 129 chars → `invalid_input`, 0 requests. |
| › `test_read_capped_rejects_over_5mb_without_draining` | `read_capped(gen, MAX)` raises `too_large` once the total exceeds the cap. The generator's remaining chunks are not consumed. Exactly 5 MiB passes. |
| › `test_list_files_pages_and_filters` | Paged listing (`nextPageToken`) of files in each app folder carrying `mm_service=org-admin-api`, `kind=attachment`. Folders are excluded. Unparsable appProperties come back as `StoredFile` with `attachment_id=None`. |
| › `test_no_sentinel_leaks_on_any_error_path` | Sentinels: refresh token, client secret, access token, file id, root id, a `googleapis.com` URL, Google error text in bodies/headers. Every error path (token 400/401/500, create 4xx/5xx/timeout, read 404/5xx, delete 5xx, list 5xx, invalid JSON) is run with caplog DEBUG on root/`httpx`/`httpcore`. No sentinel may appear in `caplog.text`, `str(e)`, `repr(e)`, `e.args`, or the `__cause__`/`__context__` chain (both `None`). |
| `tests/test_attachments_sweep.py` › `test_dry_run_lists_candidates_deletes_nothing` | Candidates = files older than the grace period and not referenced. 0 deletes. |
| › `test_apply_deletes_only_old_unreferenced` | A young file, a referenced file and an unparsable-properties file are all kept. Only old unreferenced files are deleted. |
| › `test_apply_refuses_without_checker` | `checker=None` + apply → `SweepRefused("no_reference_checker")`, 0 deletes. A dry run without a checker lists files older than the grace period, flagged `references_checked=False`. |
| › `test_checker_error_aborts_without_deletes` | Checker raises → nothing deleted, the error propagates as `SweepRefused("reference_check_failed")`. |
| › `test_max_deletes_aborts_before_deleting` | Candidates > `max_deletes` with apply → refused, 0 deletes. |
| › `test_grace_floor` | `grace < 24 h` → `ValueError` at construction. |
| › `test_delete_error_stops_and_reports_counts` | A Drive error mid-run stops the run. The result carries the deleted count and the error code. |
| `tests/test_cli_attachments.py` › `test_sweep_dry_run_lists_and_deletes_nothing` | Default is a dry run. Prints `would delete …` lines (attachment UUID, app key, age in hours) and `dry-run: nothing was deleted`. Exit 0. |
| › `test_sweep_apply_deletes_only_old_unreferenced` | `--apply` with an injected store and checker. Prints `deleted <n>`. |
| › `test_sweep_apply_refuses_without_reference_checker` | Production wiring (checker `None`) + `--apply` → fixed message, exit 1, 0 deletes. |
| › `test_sweep_not_configured` | Keys unset → fixed message `error: attachments are not configured (GOOGLE_DRIVE_* keys)`, exit 1. |
| › `test_sweep_grace_floor_and_usage` | `--grace-hours 23`, `abc`, `٣٠`, `-1` → usage, exit 64. Default is 48. |
| › `test_sweep_max_deletes_aborts` | Over the cap → fixed message, exit 1. |
| › `test_sweep_output_has_no_drive_ids` | capsys contains no fake file id or folder id, including on Drive error paths. |
| › `test_init_root_creates_folder_and_prints_id` | Q2: TTY prompts (id via `read_line`, secret and token via `read_secret`). Exactly 1 folder create with no parent and appProperties `mm_service, kind=root`. Prints the id once. A non-TTY run → `MSG_NO_TTY`. A Drive error → fixed message with no Google text. |

## Approach
1. **Config (`app/core/config.py`, High; R5).** Add the keys after the email block (l.102–120), commented
   `# Bug-report attachments in Google Drive (ADM-027, ADR-0018): all four or none`:
   - `google_drive_client_id: str | None = None`
   - `google_drive_client_secret: SecretStr | None = None`
   - `google_drive_refresh_token: SecretStr | None = None`
   - `google_drive_root_folder_id: SecretStr | None = None`. It is a `SecretStr` because AC3 bans Drive
     ids from logs and `repr`.

   Add the property `drive_enabled -> bool` (all four set).

   Add `_drive_gate` (`model_validator(mode="after")`, same style as `_email_gate` l.249–262):
   - count the keys that are set;
   - 0 → return;
   - 1–3 → `ValueError(f"{', '.join(missing)} must be set together with the other GOOGLE_DRIVE_* keys (or none of them)")`;
   - 4 → format checks, each `ValueError(f"{NAME} is malformed")`:
     - client id: ≤ 255 chars, `^[A-Za-z0-9.-]+\.apps\.googleusercontent\.com$`;
     - secret and token: 1–2048 chars, no whitespace or control characters (`^[\x21-\x7e]+$`);
     - folder id: `^[A-Za-z0-9_-]{10,128}$`.

   No network call at startup. `hide_input_in_errors=True` already keeps values out of the
   `ValidationError`. Because `get_settings()` is shared, the API, `migrate` and the CLI all refuse a
   partial set.

   Keys stay optional in staging/production (spec l.181: "none set = attachments off").
2. **`.env.example` (High).** Add a commented block naming the 4 keys with empty values. Note that the
   secret, the token and the folder id are secrets and that it is all four or none.
3. **`attachments/errors.py`.** `class DriveErrorCode(StrEnum)` with `not_configured, auth_failed,
   quota_exceeded, rate_limited, not_found, too_large, rejected, unavailable, invalid_input`.
   `class DriveError(Exception)`: `__init__(self, code)` sets `self.code` and calls
   `super().__init__(code.value)`. `__str__` returns `code.value`. It is **not** an `AppError`: ADM-030's
   service decides the per-attachment outcome (OQ35; consult-SA Rec 1).
4. **`attachments/store.py`.**
   - `MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024`.
   - `ImageType(StrEnum)`: `image/png, image/jpeg, image/webp, image/gif` (the allow-list. The magic-byte
     check is ADM-030's).
   - `@dataclass(frozen=True) StoredFile(file_id: str, app_key: str | None, attachment_id: UUID | None,
     report_id: UUID | None, created_at: datetime)`.
   - `async def read_capped(chunks: AsyncIterator[bytes], max_bytes: int = MAX_ATTACHMENT_BYTES) -> bytes`
     raises `too_large` as soon as the running total exceeds the cap. It never consults `Content-Length`.
   - `class AttachmentStore(Protocol)` with:
     - `configured: bool`
     - `ensure_app_folder(app_key) -> str`
     - `create(*, app_key, report_id: UUID, attachment_id: UUID, content_type: ImageType, data: bytes) -> str`
       (returns the Drive file id for ADM-030's row, never logged)
     - `open_read(file_id) -> AbstractAsyncContextManager[AsyncIterator[bytes]]`
     - `delete(file_id) -> None`
     - `list_files() -> AsyncIterator[StoredFile]`
     - `aclose()`
   - `DisabledAttachmentStore`: every operation raises `DriveError(not_configured)`; `aclose` is a no-op.
   - `build_attachment_store(settings, transport=None) -> AttachmentStore`.
5. **`attachments/drive.py`.** Pattern: `app/modules/auth/turnstile.py`, with an injectable `transport`.
   - **Constants** (not config, so the bearer cannot be sent elsewhere):
     - `TOKEN_URL = "https://oauth2.googleapis.com/token"`
     - `FILES_URL = "https://www.googleapis.com/drive/v3/files"`
     - `UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"`
     - `SERVICE_MARKER = "org-admin-api"`
     - `_FOLDER_MIME = "application/vnd.google-apps.folder"`
   - **`DriveApi(client_id, client_secret, refresh_token, transport=None)`**:
     - one shared `httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0),
       limits=httpx.Limits(max_connections=10), follow_redirects=False, transport=transport)`;
     - in `__init__`, `logging.getLogger("httpx")` and `"httpcore"` get `setLevel(logging.WARNING)`
       (consult-SA Rec 2, consult-SecA "Error and log hygiene").
   - **Token.** `_access_token()` caches in memory until `expires_in − 60 s`, under an `asyncio.Lock`
     (single-flight). It POSTs the form `grant_type=refresh_token` with id, secret and token in the body.
     Error mapping:
     - 400/401 → `auth_failed`, plus `logger.error("drive_auth_failed status=%s", status)`;
     - 5xx/network → `unavailable` (the refresh is idempotent, so bounded retry);
     - missing `access_token` → `unavailable`.
   - **`_send(method, url, *, idempotent, **kw)`** is the single request wrapper:
     - each attempt runs under `asyncio.timeout(60)`;
     - catch `httpx.HTTPError`, `TimeoutError`, `ValueError` and `KeyError`, then
       `raise DriveError(...) from None`, assigned **outside** the `except` block so `__context__` is also
       `None` (set a local code inside `except`, raise after it);
     - never call `raise_for_status()`.
     - Status mapping:
       - 2xx → ok;
       - 3xx → `rejected`;
       - 401 → invalidate the token. For idempotent calls refresh and retry once, otherwise `auth_failed`;
       - 403 with reason in `{storageQuotaExceeded}` → `quota_exceeded`;
       - 403 with reason in `{userRateLimitExceeded, rateLimitExceeded}` → `rate_limited`, and so does 429;
       - 404 → `not_found`;
       - other 4xx → `rejected`;
       - 5xx → `unavailable`.
     - Reasons are read from `error.errors[].reason` through that allow-list only. Google's text is never
       copied.
     - Retries only when `idempotent=True`: 3 attempts on network/429/5xx, jittered backoff of 0.5 s × 2ⁿ
       (`Retry-After` capped at 5 s).
     - Logs: `logger.warning("drive_request_failed op=%s code=%s status=%s attempt=%s", ...)` with a fixed
       op name (`create|read|delete|list|folder|token`). No URL, id or body.
   - **Input checks**, before anything goes into a URL or `q`:
     - `_check_file_id` with `^[A-Za-z0-9_-]{1,128}$`;
     - `_check_app_key` with `^[a-z][a-z0-9-]{1,31}$`;
     - `_q_literal(s)` escapes `\` then `'`;
     - a failure raises `invalid_input`.
   - **`GoogleDriveStore(settings, transport=None)`** wraps a `DriveApi` plus the root id. `configured` is
     True.
     - **`ensure_app_folder(app_key)`**:
       - per-key `asyncio.Lock` and an in-memory `dict` cache;
       - list query `'<root>' in parents and mimeType='<folder>' and trashed=false and appProperties has {
         key='mm_service' and value='org-admin-api' } and appProperties has { key='app_key' and
         value='<k>' }` with `orderBy=createdTime`;
       - pick the first match;
       - if none, create a folder (non-idempotent, one attempt) with name = app key and appProperties
         `{mm_service, kind: "app_folder", app_key}`;
       - then re-list and take the oldest (absorbs races between processes).
     - **`create(...)`**:
       - check `len(data) <= MAX_ATTACHMENT_BYTES` (else `too_large`, no request);
       - take a module-level `asyncio.Semaphore(4)` (`_MAX_CONCURRENT_UPLOADS`; constant, no new setting);
       - get the folder id;
       - build `multipart/related; boundary=<secrets.token_hex(16)>`: a JSON part
         `{name: str(attachment_id), parents: [folder], mimeType: content_type, appProperties: {mm_service,
         kind: "attachment", app_key, report_id, attachment_id}}`, then the media part;
       - POST `UPLOAD_URL?uploadType=multipart&fields=id` with `idempotent=False`;
       - return `id`.
     - **`open_read(file_id)`** is an `@asynccontextmanager`:
       - `client.stream("GET", f"{FILES_URL}/{id}", params={"alt": "media"})`;
       - status mapped before yielding (retry allowed only before the first byte);
       - yields an async iterator over `aiter_bytes()` that enforces the 5 MiB cap;
       - the response is always closed in `finally`;
       - Google's headers are never exposed.
     - **`delete(file_id)`**: DELETE with `idempotent=True`; a 404 counts as success.
     - **`list_files()`**:
       - list the app folders under the root (paged);
       - for each folder, list the files with `'<folder>' in parents and trashed=false and
         mimeType!='<folder>' and appProperties has { key='mm_service' and value='org-admin-api' }`,
         fields `nextPageToken,files(id,createdTime,appProperties)`, `pageSize=100`;
       - parse `appProperties` leniently: an invalid UUID becomes `None`.
     - **`aclose()`** closes the client.
   - **`async def create_root_folder(api: DriveApi) -> str`** (Q2): one non-idempotent folder create with
     no `parents`, name `org-admin-api attachments`, appProperties `{mm_service, kind: "root"}`.
6. **`attachments/sweep.py`.**
   - `class ReferenceChecker(Protocol): async def referenced(self, attachment_ids: Collection[UUID]) ->
     set[UUID]`.
   - Constants: `MIN_GRACE = timedelta(hours=24)`, `DEFAULT_GRACE_HOURS = 48`,
     `DEFAULT_MAX_DELETES = 100`.
   - `SweepService(store, checker: ReferenceChecker | None, *, grace, now: Callable[[], datetime])`.
     Construction raises `ValueError` below the floor.
   - `run(*, apply: bool, max_deletes: int) -> SweepResult`:
     1. Collect `list_files()`.
     2. Keep files older than the grace period that have a parsed `attachment_id`; count the unparsable
        ones as `skipped`.
     3. With a checker, drop the referenced ones (any checker exception → `SweepRefused(
        "reference_check_failed")`).
     4. With `apply` and no checker → `SweepRefused("no_reference_checker")` before any delete.
     5. With `apply` and `len(candidates) > max_deletes` → `SweepRefused("too_many")`.
     6. Otherwise delete one by one. A `DriveError` stops the run and is recorded in the result.
   - `SweepResult` holds `scanned`, `skipped`, `candidates: list[SweepItem(attachment_id, app_key,
     age_hours)]`, `deleted`, `references_checked`, `error_code`. It never holds file ids.
7. **`app/main.py`.**
   - After the email sender (l.86–90): `store = build_attachment_store(settings)`;
     `app.state.attachment_store = store`.
   - Log `attachment_store=drive|disabled`: `logger.warning` when disabled in staging/production,
     otherwise `info`.
   - In `_lifespan` after `dispatcher.aclose()`: `await app.state.attachment_store.aclose()`.
   - `Settings.model_construct` (used by the `openapi` CLI) leaves the keys `None`, so the store is
     disabled and nothing is network-bound.
8. **`app/cli.py`.** Follow the `cleanup` / `audit-purge` patterns: fixed messages, never `str(exc)`, a
   catch-all branch.
   - `amain` gains `attachment_store: "AttachmentStore | None" = None`,
     `reference_checker: "ReferenceChecker | None" = None` and
     `drive_transport: "httpx.AsyncBaseTransport | None" = None` for tests. The production checker is
     `None` until ADM-030.
   - **`attachments-sweep [--apply] [--grace-hours N] [--max-deletes N]`**:
     - parse digits only (`[0-9]{1,5}`, as in `audit-purge` l.733); `--grace-hours` must be 24–8760;
       `--max-deletes` must be 1–10000;
     - load settings; if `not settings.drive_enabled` → `MSG_ATTACHMENTS_NOT_CONFIGURED`, exit 1;
     - print one line per candidate: `would delete|deleted attachment <uuid> app <key> age <h> h`;
     - then the summary: `attachments: scanned <n>, skipped <n> (no ids), <verb> <n> (older than <g> h)`;
     - without a checker, add `note: references not checked (no attachment table yet, ADM-030)`;
     - a dry run ends with `MSG_CLEANUP_DRY_RUN`.
     - Refusals map to fixed messages: `no_reference_checker`, `reference_check_failed`, `too_many`, and
       a Drive error by code only (`error: Drive <code>; files already deleted stay deleted, run again`).
     - The store is always `aclose`d.
   - **`attachments-init-root`** (Q2):
     - TTY only (`MSG_NO_TTY`);
     - prompts for the client id (`read_line`), the client secret and the refresh token (`read_secret`);
     - **does not load `Settings`**, because a partial key set would refuse;
     - calls `create_root_folder`, then prints `root_folder_id: <id>` and
       `next: store it as ORG_ADMIN_GOOGLE_DRIVE_ROOT_FOLDER_ID (docs/deploy.md)`.
     - This is the only place a Drive id is printed. It is TTY output for the owner, not a log (recorded
       in ADR-0018). Values are deleted after use.
   - Update the module docstring. In `main()` add `"httpx", "httpcore"` to the silenced loggers (l.795).
9. **ADR-0018** `docs/adr/0018-attachment-storage-google-drive.md` (Status: Accepted on merge; context
   §5.12 OQ23/OQ34). It records:
   - the decisions in steps 1–8: env names, flat layout, the `appProperties` contract
     `mm_service/kind/app_key/report_id/attachment_id`, the error codes, the retry policy, the sweep floor
     and fail-closed rule, and the init-root exception to "no Drive ids in output";
   - the risks: shared quota with TuDo, refresh-token expiry under "Testing", and the Cloud-project
     boundary of `drive.file` (Q4).
10. **`docs/deploy.md`.** New section "Attachments — Google Drive (ADM-027)", modelled on "Email (USR-04)"
    l.110–148:
    - a key table;
    - "optional: none = off, partial = API/`migrate`/CLI refuse to start";
    - deployment prerequisites: GitHub secrets `ORG_ADMIN_GOOGLE_DRIVE_CLIENT_ID|CLIENT_SECRET|
      REFRESH_TOKEN|ROOT_FOLDER_ID`, written **unprefixed** in the `envfile` step with
      `printf 'GOOGLE_DRIVE_CLIENT_ID=%s\n'`, after a newline/charset check with the same regexes as the
      gate;
    - one-time setup:
      1. separate Cloud project and OAuth client (Desktop or Web type);
      2. consent screen **published**;
      3. mint the refresh token via OAuth Playground with own credentials, scope
         `https://www.googleapis.com/auth/drive.file`, from the TuDo Google account;
      4. run `uv run python -m app.cli attachments-init-root` locally;
      5. store the 4 secrets and redeploy;
      6. check the `attachment_store=drive` log;
    - the rotation runbook: mint a new token → update the secret → redeploy → confirm with the log and an
      `attachments-sweep` dry run → revoke the old token (Google Account → Security → Third-party access,
      or `https://oauth2.googleapis.com/revoke`);
    - leak response: revoke first. Attachments go off and reports still flow (OQ35);
    - `drive_auth_failed` meaning;
    - the shared quota with TuDo and Gmail/Photos;
    - an `attachments-sweep` usage block with exit codes.

    Add a pointer bullet in "Required runtime config".
11. **`work/ming-todo.md`.** Reword M-1 with the secret names, init-root, the separate project and the
    published consent screen. No values.
12. **Checks.** Run `make check`.

Rules:
- **R1**: no router. The adapter lives in its module; ADM-030's service depends on the Protocol.
- **R5**: env only, `SecretStr`, no values in errors.
- **R6/R11**: fixed codes, no tokens, ids or URLs in logs.
- **R7**: no tables or routes.
- **R8**: `make check`.
- **R2, R3, R4, R9**: not applicable (no schema, endpoint or admin mutation).

## Specialist input used
- consult-software-architect: adopted Rec 1 (module shape and Protocol; `DriveError` not an `AppError`).
- consult-software-architect: adopted Rec 2 (`from None`, logger pinning, sentinel test).
- consult-software-architect: adopted Rec 3, with one change. A 401 on a create does **not** refresh and
  retry, because spec l.183 says "creates are never retried", read literally. Idempotent calls do refresh
  and retry once.
- consult-software-architect: adopted Rec 4 (gate and matrix), Rec 5 (flat layout, Q3), Rec 6 (retries)
  and Rec 7 (`read_capped`, context-manager read, semaphore).
- consult-software-architect: changed the upload-concurrency cap from a setting to a constant, to keep
  config surface minimal (R7).
- consult-software-architect: adopted Rec 8 (fail-closed sweep), with a 24 h floor taken from the
  security consult; Rec 9 (ADR-0018); and Rec 10 (deploy docs).
- consult-software-architect: R-A is answered by Q2, `attachments-init-root`.
- consult-security-architect: adopted the `SecretStr` for the folder id, the error-code set including
  `quota_exceeded`, constant hosts, `follow_redirects=False`, the input hardening and q escaping.
- consult-security-architect: adopted the byte cap on read, closing the response, not passing Google's
  headers through, the semaphore, the root-scoped and owner-marked sweep, `--max-deletes`, and the
  rotation and leak runbook.
- consult-security-architect: did not adopt "trash instead of delete" (Q5 default: permanent, per spec
  l.183/l.191).
- consult-security-architect: did not adopt a startup-time network check. The log line plus the sweep dry
  run are enough.
- consult-security-architect: open questions 1, 2 and 6 became Q4, the M-1 docs and Q2.

## Assumptions
- **Credentials are not rejected by the startup gate when they look odd.** Only cheap format checks run
  (consult-SA Rec 4: being too strict about Google formats breaks later).
- **Off is accepted in staging/production.** A WARNING is logged (spec l.181 "none set = attachments off").
- **The dry-run output may print our attachment UUIDs, the app key and the age.** These are our ids, not
  Drive ids, so AC3 holds.
- **The 401-on-create policy.** It invalidates the cached token and returns `auth_failed` without retrying.
  ADM-030 treats that as a failed attachment (OQ35).
- **Upload memory.** At most 4 concurrent uploads × 5 MiB is buffered per process.
- **Hotspots.** ADM-028 (same batch, PLANNING) may also touch `app/main.py`, `docs/deploy.md`,
  `.env.example`/`app/core/config.py` (rate-limit keys) and `app/cli.py` (retention). It also records its
  auth-class choice as an ADR or decision-log entry, so **ADR number 0018 may collide**. Whichever merges
  second takes the next free number and rebases. ADM-007 (IN_PROGRESS, unclaimed) touches deploy docs and
  may cause a rebase in `docs/deploy.md`.
- **Unverified Google behaviour, to confirm in Ming's verification.** The `drive.file` scope can only see
  folders this client created (so the root folder is created by `attachments-init-root`). Google documents
  multipart uploads as fine up to 5 MB. The `appProperties` limit is 124 bytes per key+value (our longest
  pair, `attachment_id` + UUID, is about 49).
- **The `deps.py` getter and the DB-backed `ReferenceChecker` come with ADM-030.** Its plan must wire the
  checker into `attachments-sweep`.

## Blocking questions
Answer in the batch round. Each has a default the plan already uses.

1. **Env names (TuDo parity).** TuDo's source is not available here. Should this API use
   `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN` and
   `GOOGLE_DRIVE_ROOT_FOLDER_ID` in the env file, with deployment-repo secrets `ORG_ADMIN_GOOGLE_DRIVE_*`?
   Or should it reuse TuDo's exact env names (e.g. `GOOGLE_CLIENT_ID` …)?
   **Default:** `GOOGLE_DRIVE_*`. The env file is per service, so parity buys nothing, and the prefix says
   what the keys are for. If you want TuDo's names, tell us the four names; only the names in steps 1, 2
   and 10 change.
2. **Root-folder creation.** With scope `drive.file`, a folder made by hand in the Drive web UI is
   invisible to this client, so uploads would fail. How did TuDo do it?
   **Default:** this ticket adds `python -m app.cli attachments-init-root`, run once by you locally: it
   asks for the client id, secret and token, creates the root folder through this client and prints its
   id. **Alternative:** only document an OAuth Playground request. That drops the subcommand, about 60
   lines plus a test.
3. **Layout.** Should files be stored flat, as `<root>/<app_key>/<attachment_id>` with `report_id` in
   `appProperties`? The alternative is one folder per report, `<root>/<app_key>/<report_id>/`.
   **Default:** flat. It needs one Drive create per upload, avoids folder races and empty folders, and the
   retention deletes are per file anyway (spec l.165–167).
4. **Separate Google Cloud project.** `drive.file` access is most likely scoped to the Cloud project, not
   the OAuth client. If this API's client sits in TuDo's project, a leaked token or a bug here could
   reach TuDo's files. This does not change code.
   **Default:** the docs and M-1 say "create the client in a **new** Cloud project" (same Google account,
   as OQ34 decided).
5. **The sweep deletes permanently or moves to trash?**
   **Default:** permanent delete, as spec l.183/l.191 say. The safety comes from the 24 h floor (default
   48 h), the `mm_service` marker, `--max-deletes` and refusing `--apply` without a reference checker.
6. **Single PR or split** (see Split proposal).
   **Default:** one PR for the whole ticket, because you asked for adapter + config + sweep CLI + docs
   together.

## Split proposal
The estimate is about 750 lines of production code and docs (drive.py ≈ 330, store/errors ≈ 120,
sweep ≈ 110, cli ≈ 150, config ≈ 50, docs and ADR ≈ 200) plus about 600 lines of tests. That is over the
~400-line guide.

Default (Q6): one PR. The implementer commits in this order so a split stays cheap:
1. config + `.env.example` + errors/store/drive + `main.py` + tests;
2. sweep + CLI + tests;
3. ADR + docs + ming-todo.

If Ming chooses to split:
- **ADM-027 (this plan)** = commits 1 and 3 (without the sweep and CLI sections of the docs).
- **ADM-027b** = sweep service + `attachments-sweep` + `attachments-init-root`, with the docs for both.
- AC4 moves to ADM-027b. Root creation (Q2) then waits for 027b, so M-1 does too.
