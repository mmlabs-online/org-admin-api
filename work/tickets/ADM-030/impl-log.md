## Attempt 1 — 2026-11-20

Slice 1 per plan.md (approved by Ming 2026-11-20, all defaults). Alembic head confirmed
`8c2f6a1d4e93` (`uv run alembic heads`); the new migration `d7a3f5b2e801` chains on it. Local
Postgres via `service postgresql start`; fake Drive only, no network.

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261120_d7a3f5b2e801_adm030_bug_report_attachments.py` | New table, hand-edited, working `downgrade()` (R2) |
| `app/db/models.py`, `app/modules/bug_reports/models.py` | `BugReportAttachment` model + registry (R2) |
| `app/core/errors.py` | Core 411/413/415/503 classes, `_HTTP_CODES` 411 + 503, `ErrorBody.code` text (R6). 413/415 were already in `_HTTP_CODES` |
| `app/core/config.py`, `app/core/rate_limit.py`, `.env.example` | 5 settings, per-IP scope `bug_report_attachment`, keys without values (R5) |
| `app/modules/attachments/drive.py` | ADM-027 finding 1 and the `expires_in` clamp: call 45 s / token 20 s / stream 60 s deadlines |
| `app/modules/bug_reports/image_types.py`, `upload.py` | Pure type checks and bounded body read |
| `app/modules/bug_reports/errors.py` | Module errors |
| `app/modules/bug_reports/repository.py` | `BugReportAttachmentRepository` (brief, lock, count, add, get, list, commit) |
| `app/modules/bug_reports/service.py` | `resolve_reporter` extracted (no behaviour change); `BugReportAttachmentService` |
| `app/modules/bug_reports/staff_service.py` | `list_attachments`, `open_download` |
| `app/modules/bug_reports/schemas.py` | `ImageType` on `content_type`, docstring/description |
| `app/modules/bug_reports/auth_router.py`, `router.py` | Upload route (cookie class), download route (Bearer), detail fills `attachments` |
| `app/modules/audit/actions.py` | `bug_report.attachment_added` |
| `app/main.py` | Upload slot semaphore |
| `docs/adr/0023-bug-report-attachments.md`, `docs/identity-architecture.md` (D32), `docs/spec-v1.md` l.194 (`PUT` -> `POST`), `CLAUDE.md` (R6 only) | Plan docs + Q1/Q2 |
| tests (below) | R8 |

### Tests added / changed
- `tests/test_image_types.py` (new), `tests/test_bug_report_upload_read.py` (new)
- `tests/test_bug_report_attachments_upload.py` (new, 47): roundtrip x4 types, 415 x8, 413 before body read (ASGI body counter), lying length, 422 incomplete, 411 chunked, 409 6th, real-commit race (`BarrierStore` forces both uploads to reach Drive, no sleeps), 403 denied (one identical body) / closed x2 / window env, 403 unverified, origin/app header x4, bad ids, rate limits (IP, user counts rejected), busy 429, slot release, Drive disabled/failure x4, DB failure deletes Drive file, report closed mid-upload, rotated cookie survives a post-commit 503, no cookie on pre-commit error, no Drive id in responses, no content/ids/filename in logs and audit, CORS preflight
- `tests/test_bug_report_attachments_download.py` (new): headers, one 404 body x3, unavailable x3, transient 503, truncated stream aborts, Drive stream closed (full and early), 401/403, not audited
- `tests/test_bug_report_attachments_migration.py` (new): roundtrip, CHECKs, unique file id, cascade
- `tests/attachment_world.py` (new shared harness; fixtures registered at the bottom of `tests/conftest.py`)
- `tests/test_attachments_drive.py`: call/token/stream deadlines, `expires_in` clamp and non-finite
- `tests/test_bug_reports_staff_api.py`: `test_detail_lists_attachments`
- `tests/test_route_auth_classes.py`, `tests/test_openapi_contract.py`, `tests/test_audit_coverage.py`: registries extended (recounted, nothing copied from the plan). `test_no_5xx_documented` now allows 503 on the two attachment routes only; the upload is in `IDENTITY_MULTI_ROW_AUDITED` (raw body + Drive, covered by the upload tests)
- Plan correction: unverified email is 403 `email_unverified` in this codebase (the plan said 401); the test asserts 403. Tests are deterministic: no random-collision ids, deadline tests patch constants and use barriers/long sleeps cut by `asyncio.timeout`, not timing races.

### Checks (actual output summary)
- lint: `uv run ruff check . --exclude .github/scripts` -> All checks passed!
- format:check: `uv run ruff format --check app tests migrations` -> 373 files already formatted
- typecheck: `make typecheck` -> Success: no issues found in 191 source files
- alembic: `uv run alembic check` -> No new upgrade operations detected.
- test: `make test` (full) -> 3438 passed, 1 warning in 514.51s (0:08:34)

### Out-of-plan files
- `tests/attachment_world.py` and a 2-line import at the bottom of `tests/conftest.py`: shared test harness for the two API test files (fixtures must be registered from conftest).
- `app/modules/attachments/drive.py` is outside the ticket's file list but in scope by Ming's decision.

### Follow-ups (noticed, not done)
- CLAUDE.md R3 class 2 still reads "bug reports — D28": add "and their attachment upload, D32" (instructed to touch R6 only).
- ADM-030b: sweep wiring (`ReferenceChecker` pairs, DB checker, `cli.py`, deploy.md).
- `list_files` `app_key` check (ADM-027 4), process-wide httpx logger (ADM-027 5).
- Admin-pan: "attachment unavailable" on 404 `attachment_unavailable`, render via `blob:` in `<img>` only; per-file 503 in the app guide (ADM-032).

### Scope self-check
- Could the diff be smaller? No: every file is in the plan or listed above.
- Diff size: see `git diff --stat` of the commit.

## Attempt 2 (REWORK, security-review attempt 1, two yellow findings) — 2026-11-20

### Files touched
- `app/modules/sessions/service.py` — `resolve(allow_rotate=True)`; `False` validates and touches but never rotates (reuse check unchanged). Required by finding 1.
- `app/modules/auth/deps.py` — `_resolve_device` shared helper, `get_device` unchanged in behaviour, new `DeviceNoRotateDep`. Required by finding 1.
- `app/modules/bug_reports/auth_router.py` — upload uses `DeviceNoRotateDep`; Set-Cookie-on-error plumbing removed.
- `app/modules/bug_reports/service.py` — `STORE_DEADLINE_SECONDS = 60.0` around `store.create` (TimeoutError -> `AttachmentStorageUnavailableError(retryable=True)`, logged `code=unavailable`); `committed` removed; comment documenting 8 admitted vs 4 Drive-concurrent uploads (gap documented, constant not changed).
- `docs/adr/0023-*.md` — §4, §11, residual-risk bullet removed.
- `tests/test_bug_report_attachments_upload.py` — replaced the two rotation tests with: no rotation on upload (ok and 503, DB hashes unchanged), other cookie route still rotates, revoked session -> 401, store deadline -> 503 within patched 0.2 s and slot freed.

### Checks
- ruff check: All checks passed!
- ruff format --check: 373 files already formatted
- make typecheck: Success: no issues found in 191 source files
- alembic check: No new upgrade operations detected.
- make test: 3440 passed, 1 warning in 519.98s

### Out-of-plan files
- sessions service, auth deps (High paths): approved by Ming for this rework.

### Follow-ups
- none

### Scope self-check
- Only the two findings and the ADR wording were changed.
