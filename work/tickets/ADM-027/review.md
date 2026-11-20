## Attempt 1 — 2026-11-19
Verdict: PASS

Reviewed product commit 2b3a1a6 (branch `claude/festive-volta-rikup7`). Work bookkeeping for batch
2026-11-19-2 and the ADM-028 consult/plan files in the branch diff are not part of this implementation.

### Checks (run by reviewer)
- `make typecheck` — Success: no issues found in 155 source files.
- `make test` (full), run 1 — 1 failed, 2832 passed: `tests/test_auth_app_context.py::test_missing_app_header_403[x]`
  with `UndefinedTableError: relation "apps" does not exist` while inserting fixtures. Not related to this
  diff (no new test touches the schema; the file passes alone, 20 passed). Looks like shared test-DB
  interference or a flake.
- `make test` (full), run 2 — 2833 passed, 1 warning in 365.86s, exit 0. This matches impl-log (2833 passed).
- `uv run ruff check . --exclude .github/scripts` — All checks passed.
- `uv run ruff format --check app tests migrations` — 302 files already formatted.
- `uv run alembic check` — No new upgrade operations detected. The ticket adds no migration.
- Plain `ruff check .` / `ruff format --check .` still fail only on the kit-managed `.github/scripts/risk_tier.py`.
  That failure is already on main and is not caused by this ticket (see Follow-ups).

### Acceptance criteria
- [x] AC1: no Drive keys means the app starts and reports "not configured"; a partial or malformed set fails startup naming only the variables.
  - Gate: `app/core/config.py` `_drive_gate` and `drive_enabled`.
  - Tests in `tests/test_config.py`: `test_drive_keys_none_is_disabled`, `test_drive_keys_partial_names_missing_only` (all 14 subsets of 1–3 keys), `test_drive_keys_malformed_names_variable_only`, `test_drive_errors_do_not_echo_values`, `test_drive_secret_fields_hidden_in_repr`.
  - Tests in `tests/test_attachments_drive.py`: `test_build_store_disabled_when_not_configured` and `test_create_app_logs_attachment_store_state`.
  - All green in the full run.
- [x] AC2: create, read, delete and folder lookup work against the fake Drive, and the 5 MB cap is enforced on the stream.
  - `tests/test_attachments_drive.py` covers `test_create_*`, `test_open_read_*`, `test_delete_404_is_success`, `test_ensure_app_folder_*`, `test_read_and_delete_retry_bounded`, `test_idempotent_call_refreshes_once_on_401`, `test_redirect_is_rejected` and `test_list_files_pages_and_filters`.
  - The cap is enforced in `drive.py:291-304` (`_capped_stream`) and `store.py` (`read_capped`). Tests: `test_open_read_over_cap_raises_too_large`, `test_read_capped_rejects_over_5mb_without_draining`, `test_create_rejects_over_5mb_without_request`.
- [x] AC3: no Google text, Drive id, token or URL appears in logs or error details.
  - `test_no_sentinel_leaks_on_any_error_path` captures logs at DEBUG on root, `httpx` and `httpcore`. It asserts on `str`, `repr` and `args`, and requires `__cause__` and `__context__` to be `None`.
  - `test_sweep_output_has_no_drive_ids` covers the CLI output.
  - In code, every `DriveError` is raised outside its `except` block (`drive.py:165-186, 220-224, 301-304`), and log lines carry only op, code, status and attempt (`drive.py:113-116`).
- [x] AC4: the sweep CLI lists in dry-run mode, and `--apply` deletes only unreferenced files older than the grace period.
  - `tests/test_attachments_sweep.py`: all 7 tests.
  - `tests/test_cli_attachments.py`: `test_sweep_dry_run_lists_and_deletes_nothing`, `test_sweep_apply_deletes_only_old_unreferenced`, `test_sweep_apply_refuses_without_reference_checker`, `test_sweep_grace_floor_and_usage`, `test_sweep_max_deletes_aborts`.
  - The sweep fails closed: `sweep.py:66-67`, `83-94`.
- [x] AC5: the "Attachments — Google Drive (ADM-027)" section in `docs/deploy.md` has everything the plan requires:
  - the key table;
  - the `ORG_ADMIN_GOOGLE_DRIVE_*` deployment secrets, written unprefixed with `printf` after a charset check;
  - one-time setup, including init-root and publishing the consent screen;
  - the rotation runbook;
  - the leak response;
  - the meaning of `drive_auth_failed`;
  - sweep usage and exit codes.
- [x] `make check` is green: typecheck, full tests (run 2), ruff (excluding the kit script) and the format check.

Ming verification after merge (not checked by the reviewer; carry these into the PR):
- `attachments-init-root` creates the root folder and prints its id.
- A deploy with the four secrets logs `attachment_store=drive`.
- `python -m app.cli attachments-sweep` (dry run) exits 0 against the real Drive. Until ADM-030 it prints "references not checked".
- The OAuth consent screen is published ("In production").
- The OAuth client lives in its own Google Cloud project, separate from TuDoCuaTui's.

### Scope
Every product file changed in 2b3a1a6 is in the plan's file list:
- `.env.example`
- `app/core/config.py`
- `app/main.py`
- `app/cli.py`
- `app/modules/attachments/*`
- ADR-0018
- `docs/deploy.md`
- `work/ming-todo.md`
- `tests/fake_drive.py`
- the four test files

The High-path files (`app/core/config.py`, `.env.example`) are named in the plan, and Ming approved the plan. There are no out-of-plan files.

### Issues
None blocking.

### Rules (CLAUDE.md)
No violations found.
- R1: no router. The adapter sits behind the `AttachmentStore` Protocol.
- R5: config comes from env only. Secrets are `SecretStr`, gate errors use fixed text, and `.env.example` has empty values.
- R6/R11: `DriveError` carries a fixed code only. The refresh token travels only in the form body. Logs carry no URL, id or token.
- R7: no tables or routes.
- R8: green. No endpoint is added.
- R2, R3, R4 and R9 do not apply.

### Blockers from review agents
None. `security-review.md` has no 🔴. Its 🟡 1 (no overall deadline per Drive call, and token retries run while the lock is held) is under Follow-ups below.

### Nits (do not block)
- `drive.py:447,456`: `ImageType(content_type)` raises a plain `ValueError` for a non-enum string instead of `DriveError(invalid_input)`. Callers are typed, so this only matters if ADM-030 passes raw strings.
- `drive.py:180,199`: `expires_in` is not bounded. A value such as `"inf"` would cache the token forever (security-review 💭 2). Clamp it to 0–3600 and require a finite value.
- `drive.py:506-509`: `app_key` from `appProperties` is printed by the sweep without being checked against `_APP_KEY_RE` (security-review 💭 4).
- `sweep.py:70-89`: `run()` collects the whole listing in memory. That is acceptable for an owner-run CLI.

### Follow-ups (out of scope)
- **Before ADM-030 puts Drive calls on the request path** (security-review 🟡 1): add an overall deadline per call. Today the bound is only per attempt, so one call can take 3 × 60 s plus a refresh, and `_access_token` retries 3 × 60 s while holding the lock. Bound the token fetch too, and give `_capped_stream` a wall-clock deadline. Do this in ADM-030 or a small hardening ticket.
- ADM-030: wire a DB-backed `ReferenceChecker` into `attachments-sweep`, and add the `deps.py` getter. Consider matching `(attachment_id, file_id)` pairs so that duplicate Drive files with the same attachment id are found (security-review 💭 3).
- The full suite flaked once with `relation "apps" does not exist` in `tests/test_auth_app_context.py`. This is probably test-DB interference from migration tests or a concurrent session. Investigate if it recurs.
- `ruff check .` / `ruff format --check .` fail on the kit-managed `.github/scripts/risk_tier.py`. This is already on main; fix it in spaceM project-kit.
- Alembic `fileConfig` in the test setup disables existing loggers, so `caplog` sees no `app.*` logs unless a test re-enables them (noted in impl-log).
