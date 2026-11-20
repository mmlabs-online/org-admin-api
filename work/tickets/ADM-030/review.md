## Attempt 1 — 2026-11-20
Verdict: PASS

Reviewed: product commit `2f2cb34` (slice 1; sweep wiring split to ADM-030b by Ming's approval, plan Q7),
`git diff origin/main...HEAD`. Local Postgres (`service postgresql` already accepting connections).

### Checks (run by reviewer)
- `make typecheck` — exit 0, "Success: no issues found in 191 source files".
- `uv run ruff check . --exclude .github/scripts` — "All checks passed!" (unexcluded `ruff check .` fails only on
  kit-managed `.github/scripts/risk_tier.py`, pre-existing, not part of this diff).
- `uv run ruff format --check app tests migrations` — "373 files already formatted".
- `make test` (full) — exit 0, "3438 passed, 1 warning in 533.59s" (warning: pre-existing pty DeprecationWarning in
  `test_cli_create_account.py`). Matches impl-log (3438 passed).
- `uv run alembic check` — "No new upgrade operations detected."; `alembic heads` = `d7a3f5b2e801` (single head,
  `down_revision = 8c2f6a1d4e93`).
- Migration roundtrip on the dev DB: `alembic downgrade -1 && alembic current && alembic upgrade head && alembic
  current && alembic check` — all succeeded, check clean after re-upgrade. Also covered on a scratch DB by
  `test_bug_report_attachments_migration.py::test_upgrade_downgrade_roundtrip`.
- Determinism: `pytest tests/test_bug_report_attachments_upload.py tests/test_bug_report_attachments_download.py
  tests/test_attachments_drive.py tests/test_bug_report_attachments_migration.py tests/test_image_types.py
  tests/test_bug_report_upload_read.py tests/test_bug_reports_staff_api.py` x4 — "200 passed" every run
  (27.07s / 26.92s / 26.83s / 27.98s). The race test uses an `asyncio.Barrier` store, no sleeps.

### Acceptance criteria
- [x] AC1 each type uploads and downloads byte-identical — `test_upload_each_type_roundtrip[png,jpeg,webp,gif]`
  (sha256 equal, 201 shape exactly 4 fields, stored type = sniffed, one audit row with `{attachment_id,
  attachment_count}`).
- [x] AC2 disallowed/mismatched → 415 — `test_image_types.py` (parse/sniff/polyglot), `test_upload_rejects_type`
  (svg, html, pdf, jpg_alias, missing, mismatch_png_as_jpeg, html_as_png, too_short); no Drive create, no row.
- [x] AC3 > 5 MB → 413 before body read — `test_upload_413_before_body_read` (ASGI counter: 0 body messages),
  `test_upload_413_lying_length`, `test_upload_411_chunked`, `test_upload_incomplete_422`; unit
  `test_bug_report_upload_read.py`.
- [x] AC4 6th file → 409 `attachment_limit_reached` — `test_upload_sixth_is_409`,
  `test_concurrent_uploads_respect_cap` (real commits, one 201 + one 409, loser's Drive file deleted, 5 rows).
- [x] AC5 only reporter / open / window (403 otherwise) — `test_upload_denied_403_is_one_answer` (identical bodies),
  `test_upload_closed_403[report_done,window_expired]`, `test_upload_window_is_configurable`,
  `test_upload_report_closed_between_check_and_insert`.
- [x] AC6 Drive off/failing: report stays, per-file failure — `test_upload_drive_disabled_503`,
  `test_upload_drive_failure_503[...]` (503 `attachment_storage_unavailable`, `retryable` + `Retry-After` only when
  retryable), `test_upload_db_failure_deletes_drive_file`.
- [x] AC7 missing file → stable error — `test_download_unavailable_404[drive_not_found,deleted_marker,
  drive_disabled]` (exact same envelope), `test_download_drive_transient_503`, `test_download_truncated_stream_aborts`.
- [x] AC8 download headers asserted — `test_download_headers` (Content-Type from DB, Content-Length, Content-
  Disposition `attachment; filename="<aid>.png"`, nosniff, CSP `default-src 'none'; sandbox`, `no-store`, CORP
  `same-origin`).
- [x] AC9 Drive ids absent from responses — `test_drive_ids_never_exposed` (upload 201/503/403, detail, download
  headers); `BugReportAttachmentRead` has no id field for Drive (`schemas.py`).
- [x] AC10 no content/filenames/Drive ids in logs or audit — `test_no_content_or_ids_in_logs_and_audit` (caplog with
  loggers forced live + every audit row; sentinel file id, embedded sentinel bytes, client filename, content type).
- [x] Plan AC11 ADM-027 deadline fix — `test_call_deadline_bounds_retries`, `test_call_deadline_bounds_a_hanging_create`,
  `test_token_fetch_deadline_releases_lock`, `test_stream_deadline`, `test_expires_in_clamped`,
  `test_expires_in_non_finite_is_unavailable`.
- [x] Plan AC12 staff detail lists attachments — `test_bug_reports_staff_api.py::test_detail_lists_attachments`.
- [x] Plan AC13 registries — `test_route_auth_classes.py` (COOKIE upload; BEARER download → `admin:bug-reports:read`),
  `test_openapi_contract.py` (503 allowed on exactly the two routes), `test_audit_coverage.py`; all green in the
  full run.
- Not mine (Ming verification after merge, carry into the PR): real-Drive upload from a browser app right after
  creating a report; admin-pan view via Bearer fetch → `blob:`; file under `<root>/<app_key>/` named by the
  attachment id; Drive keys unset on staging → 503 `attachment_storage_unavailable` and the report still listed;
  a 5 MB image through Cloudflare Tunnel from a real device.

### Deviations in impl-log (judged)
- **Unverified email 403 instead of the plan's 401** — accepted. `EmailUnverifiedError` is a
  `PermissionDeniedError` (`app/modules/tokens/errors.py:10`) and the existing submit route already asserts 403
  (`tests/test_bug_reports_submit.py:301`); `resolve_reporter` is the unchanged, extracted submit logic. The plan was
  wrong, not the code.
- **Upload classified in `IDENTITY_MULTI_ROW_AUDITED`** instead of a new `IDENTITY_AUDITED_ELSEWHERE` set — accepted.
  Same effect on the completeness assertion (`tests/test_audit_coverage.py:895-899`), with a comment explaining the
  route is there because the JSON harness cannot send a raw body; the audit itself is asserted in the upload tests.
  The set name is now slightly off (one row, not multi-row); nit below.
- **`tests/attachment_world.py` + 2-line import at the end of `tests/conftest.py`** — accepted. Test-only shared
  harness used by both API test files, justified under `Out-of-plan files`; no product effect.
- **`app/modules/attachments/drive.py`** — in the plan's file list (Scope summary + Files table), not a deviation.
- **CLAUDE.md R3 class 2 not edited** (plan listed "R3/R6"); impl-log says it was instructed to touch R6 only and
  lists it as a follow-up. Not a blocker: R3 class 2 already covers "bug reports — D28" and D32 records the upload's
  class. Follow-up below.
- Work-board files in the diff (`work/backlog.md`, `work/batches/2026-11-20-1.md`, `work/tickets/ADM-039/ticket.md`)
  come from the orchestrator's chore commit `fdb13f7`, not the product commit.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: router does HTTP only (headers, stream adapter, `StreamingResponse`, cookie on error); `BugReportAttachmentService`
  and `open_download` are HTTP-free and raise `AppError` subclasses; SQL only in `BugReportAttachmentRepository`.
- R2: hand-written migration with working `downgrade()` (verified), model registered in `app/db/models.py`, CHECK
  texts match the model, `alembic check` clean.
- R3: upload is cookie class via `ReportAppContextDep` + `DeviceDep`; download is Bearer with
  `require_permission(READ)`; both in the route registry.
- R4: `bug_report.attachment_added` written in tx 2 with the insert, committed together (`service.py` `_record`).
- R5: 5 settings in `config.py`, keys without values in `.env.example`.
- R6: 411/413/415/503 approved by Ming at the plan gate (Q2), added to `errors.py` and CLAUDE.md R6.
- R8/R9/R11: tests per endpoint (happy, 404, 409/415/422, 401/403); Origin + `X-MM-App` + verified email enforced;
  no Drive ids/content in logs.

### Blockers from review agents
None — appsec-engineer attempt 1: no 🔴; two 🟡 (cookie rotation on a long upload can outlive the 60 s grace and revoke the device session; no overall deadline on store.create) — listed for Ming in the PR.

### Nits (do not block)
- `app/modules/bug_reports/service.py` (`upload`, `int(content_length)`): an ASCII-digit string longer than Python's
  4300-digit int limit would raise `ValueError` → 500. Unreachable behind uvicorn/h11 in practice; a `len(...) > 8`
  guard mapping to 413 would make it total.
- `tests/test_audit_coverage.py:885` `IDENTITY_MULTI_ROW_AUDITED` now also holds a single-row route; a rename (e.g.
  `IDENTITY_AUDITED_ELSEWHERE`, as the plan proposed) would read truer.
- `drive.py` `_run`: when the 45 s call deadline cancels an in-flight `create`, Drive may still have stored the file;
  it becomes an orphan only the sweep can remove, and `attachments-sweep --apply` stays refused until ADM-030b.
  Acceptable (documented fail-closed), but worth naming in ADM-030b.

### Follow-ups (out of scope)
- CLAUDE.md R3 class 2: add "and their attachment upload — D32" (owner-approved per plan; not done in this diff).
- ADM-030b: sweep wiring (`ReferenceChecker` pairs, DB checker, `cli.py`, `docs/deploy.md`); include orphans left by
  deadline-cancelled creates.
- ADM-027 💭4 (`list_files` app_key check) and 💭5 (process-wide httpx logger).
- Admin-pan: "attachment unavailable" on 404 `attachment_unavailable`, render via `blob:` in `<img>` only; app guide
  (ADM-032): per-file 503 with `details.retryable`.

## Attempt 2 — 2026-11-20
Verdict: PASS

Reviewed: rework commit `8dbc491` (Ming's request in ticket.md Notes, 2026-11-20: appsec 🟡 1 and 🟡 2 in PR #87),
`git diff 750e697..HEAD` and `git diff origin/main...HEAD`. Product files changed by the rework: `app/modules/sessions/service.py`,
`app/modules/auth/deps.py` (High paths, approved by Ming for this rework), `app/modules/bug_reports/auth_router.py`,
`app/modules/bug_reports/service.py`, `docs/adr/0023-bug-report-attachments.md`, `tests/test_bug_report_attachments_upload.py`.
The rest are work files (impl-log, ticket, backlog, batch). No other test file changed, so every existing session /
rotation test runs unmodified.

### Checks (run by reviewer)
- `make typecheck` — exit 0, "Success: no issues found in 191 source files".
- `uv run ruff check . --exclude .github/scripts` — exit 0, "All checks passed!".
- `uv run ruff format --check app tests migrations` — exit 0, "373 files already formatted".
- `uv run alembic check` — exit 0, "No new upgrade operations detected." (the rework has no schema change).
- `make test` (full) — exit 0, "3440 passed, 1 warning in 548.21s" (the warning is the existing pty DeprecationWarning in
  `test_cli_create_account.py`). Matches impl-log (3440 passed; attempt 1 had 3438, so net +2 tests = 4 new, 2 replaced).
- Determinism: `pytest tests/test_bug_report_attachments_upload.py tests/test_auth_accounts.py tests/test_auth_join.py
  tests/test_auth_logout.py` x4 — "109 passed" every run (25.37s / 24.35s / 25.39s / 25.48s). The deadline test patches
  the deadline to 0.2 s against a 30 s hang, wrapped in a 5 s `wait_for`, which gives a wide margin.

### Rework findings verified
- [x] **🟡 1: the upload must not rotate `mm_device`.** `DeviceSessionService.resolve(..., allow_rotate=True)`
  (`app/modules/sessions/service.py:68`); the only behaviour change is the guard at `:93`
  (`allow_rotate and (force_rotate or …)`). When it is False, the current value still goes through `_usable`
  (revoked / idle / max-age) and `touch`. Previous-value grace and reuse revocation are untouched. The default `True`
  keeps every existing caller as before, including `start_for_login` (`:141`). In `app/modules/auth/deps.py:142-166`,
  `_resolve_device` keeps the 401 clear-cookie path for both dependencies, `get_device` passes `allow_rotate=True`
  (unchanged), and `DeviceNoRotateDep` is new. Only the upload route uses it (`app/modules/bug_reports/auth_router.py:125`).
  Every other cookie route (auth router, the submit route at `bug_reports/auth_router.py:50`, organizations,
  invitations) still uses `DeviceDep`, per grep. The Set-Cookie-on-error code and `service.committed` were removed;
  nothing can set a cookie on the upload route now. Evidence: `test_upload_never_rotates_cookie` (`:550`; device row
  25 h stale: 201 and 503 both have no `set-cookie`, `token_hash`/`prev_token_hash` unchanged, the same value works
  again), `test_other_cookie_route_still_rotates` (`:566`; `GET /api/v1/auth/accounts` still rotates),
  `test_upload_still_validates_device_session` (`:578`; revoked session → 401, no Drive call). The existing rotation
  tests pass unmodified in the full run (`test_auth_accounts.py::test_accounts_no_store_and_rotation`,
  `test_auth_join.py::test_join_no_store_and_rotation` / `test_join_error_after_rotation_rolls_back`,
  `test_auth_logout.py` grace and previous-value cases, and the sessions tests).
- [x] **🟡 2: overall deadline around `store.create` → 503.** `asyncio.timeout(STORE_DEADLINE_SECONDS=60.0)` wraps
  the whole `store.create`, including the wait for the Drive slot and the folder lookup
  (`app/modules/bug_reports/service.py:170,275`). A `TimeoutError` maps to `DriveErrorCode.UNAVAILABLE` (`:285`), which
  is logged with ids only and raised as `AttachmentStorageUnavailableError(retryable=True)`. The upload slot is released
  in the existing `finally` (`:260`). Evidence: `test_upload_store_deadline_503_and_frees_slot` (`:590`; hanging
  `ensure_app_folder`, 503 `attachment_storage_unavailable`, semaphore value restored, no row).
- [x] **ADR wording only**: ADR-0023 §4 (no rotation on this route, no `Set-Cookie`), §11 (60 s overall deadline, the
  8 admitted vs 4 concurrent Drive uploads gap), and the obsolete residual-risk bullet removed. ADR-0004 does not
  promise rotation on every route (rotation happens "when `rotated_at` is ≥ 24 h old"), so documenting the exception in
  ADR-0023 is enough. D32 needs no change.
- [x] Attempt 1 acceptance criteria still hold: the full suite is green, including every attachment upload/download,
  registry, OpenAPI and audit-coverage test.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the router stays HTTP-only and is simpler now; the timeout handling lives in the service and raises an `AppError` subclass.
- R3/R11: the upload remains cookie class (Origin + `X-MM-App` via `ReportAppContextDep`, device validated). Not rotating on one
  route is within R11 ("at most every ~24 h") and documented in ADR-0023 §4. Reuse detection is unchanged.
- R5/R11: the deadline log line has `report_id`, `attachment_id` and `code` only.
- R7: the rework diff touches only the two findings, the ADR and the work files. The High-path edits (sessions service, auth deps)
  were approved by Ming in ticket.md Notes.

### Blockers from review agents
None — appsec-engineer attempt 2: PASS, no 🔴 or 🟡 (both attempt-1 🟡 fixed).

### Nits (do not block)
- A timeout that fires after Drive has stored the file leaves an orphan, the same as the existing 45 s per-call deadline
  (attempt 1 nit). The ADM-030b sweep is the net; already listed there.
- The browser's cookie `Max-Age` is refreshed only on rotation (ADR-0004 §1), so an upload does not extend the cookie
  lifetime, while `touch` still extends the server-side idle window. This is consistent with other non-rotating
  requests; mentioned only for completeness.
- The attempt 1 nits still apply (`int(content_length)` digit-limit guard, `IDENTITY_MULTI_ROW_AUDITED` name).

### Follow-ups (out of scope)
- Unchanged from attempt 1 (CLAUDE.md R3 class 2 wording; ADM-030b sweep including deadline orphans; ADM-027 💭4/💭5;
  admin-pan / app guide notes).
