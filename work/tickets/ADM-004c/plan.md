Status: READY

# Plan — ADM-004c: Audit retention purge

## Spec summary
- spec §5.4: "Append-only record of every mutating admin action: actor, action, target type/id,
  before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors."
- spec §6: "100% of mutating endpoints write an audit entry (once §5.4 lands)."
- spec Q6 (open): "Compliance needs (audit retention period, PII handling)?" This ticket does not
  answer Q6. It ships the mechanism, and the defaults below are recorded as owner decisions pending
  confirmation (ticket Notes; ADM-004b plan A7).
- ADR-0009 §1: "Append-only twice ... only DDL defeats them." §8: "Retention is indefinite in v1."
  Amendment (ADM-004b): "Retention stays indefinite until ADM-004c (purge)."
- Ticket scope (split from ADM-004b): DELETE is allowed only with `SET LOCAL audit.allow_purge='on'`
  AND `OLD.occurred_at < now() - interval '90 days'`; UPDATE and TRUNCATE are always refused. CLI
  `audit-purge --older-than-days N [--dry-run]` writes one `audit_log.purged` summary row per batch
  transaction and is never an HTTP route. The bypass lives in one CLI-only module, enforced by a grep
  test. Email cleanup writes one summary row per batch transaction.
- Carried from the ADM-004b security review (ticket Notes): the `af3eb84d2d5e` downgrade guard and
  the deploy.md line; strip C1 from the user agent; `type(exc).__name__` in `audit_write_failed`.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | DELETE without the flag raises. DELETE with the flag on a row 90 days old or newer raises. UPDATE and TRUNCATE always raise, with or without the flag. DELETE with the flag on a row older than 90 days deletes it. | `tests/test_audit_schema.py › test_delete_old_row_without_flag_refused`, `test_purge_flag_deletes_old_row`, `test_purge_flag_refuses_recent_row`, `test_purge_flag_boundary_90_days_refused`, `test_purge_flag_other_values_refused`, `test_update_refused_with_flag`, `test_truncate_refused_with_flag` (the existing `test_update_refused`, `test_delete_refused` and `test_truncate_refused` stay green) |
| 2 | Migration up/down/up is clean. Downgrade restores the original function text byte for byte, and after a down step DELETE with the flag raises again. | `tests/test_audit_migrations.py › test_purge_migration_down_restores_original_function` (compares `pg_proc.prosrc`), `› test_purge_migration_up_down_up`; CI `alembic upgrade head && alembic check` |
| 3 | `af3eb84d2d5e.downgrade()` refuses with `RuntimeError` while `audit_log` has `outcome IN ('failure','denied')` rows, and always refuses in offline (`--sql`) mode. deploy.md names the revision in the "never downgrade production past" line. | `tests/test_audit_migrations.py › test_outcome_downgrade_refuses_with_failure_rows`, `› test_outcome_downgrade_passes_on_clean_table`, `› test_outcome_downgrade_refuses_offline`; review of `docs/deploy.md` |
| 4 | `audit-purge` refuses a missing, non-integer, < 90 or > 3650 N with exit 64 and a fixed usage line that never echoes argv. The repository re-checks N ≥ 90. | `tests/test_cli_audit_purge.py › test_usage_errors` (parametrized: missing, `89`, `0`, `-5`, `3651`, `90.5`, `abc`, `٩٠`, unknown flag, abbreviation `--older`), `› test_repository_rechecks_min_days` |
| 5 | The cutoff is computed once on the DB clock and reused for every batch, for the dry run and for every summary row. Rows older than the cutoff are deleted in batches; newer rows and `audit_log.purged` rows are kept. | `› test_purge_deletes_only_older_than_cutoff`, `› test_purge_keeps_purge_trail`, `› test_purge_batches_share_cutoff` (batch size 2, 5 old rows → 3 summary rows with the same `cutoff`, `deleted` 2/2/1) |
| 6 | Each batch transaction writes its summary row (`audit_log.purged`, `system:cli`, `{cutoff, older_than_days, deleted}`), only when that batch deleted > 0 rows. A batch whose summary row fails is rolled back with its delete. | `› test_purge_summary_row_shape`, `› test_purge_zero_rows_writes_nothing`, `› test_purge_summary_failure_rolls_back_batch` |
| 7 | The dry run changes nothing: it never sets the flag, runs in a READ ONLY transaction and only counts. | `› test_dry_run_changes_nothing` (row count unchanged, no summary row, flag not `on`), `› test_dry_run_transaction_is_read_only` (fresh connection: an INSERT after `start_read_only()` fails with "read-only transaction") |
| 8 | The flag is never left `on` after a batch. DB errors print one fixed line (no DSN, SQL or traceback). `DB_ECHO` is refused. | `› test_flag_reset_after_run`, `› test_db_failure_fixed_line`, `› test_refuses_db_echo` |
| 9 | Bypass confinement: `allow_purge` (any case) appears only in `app/modules/audit/purge.py` and the new migration. `app/` contains no `DISABLE TRIGGER`, `session_replication_role`, `ALTER TABLE audit_log` or `set_config(`. Only `app/cli.py` imports the purge module. No route path contains `purge`. | `tests/test_audit_purge_guard.py › test_allow_purge_only_in_purge_module_and_migration`, `› test_no_trigger_bypass_in_app`, `› test_purge_module_imported_only_by_cli`, `› test_no_purge_route`, `› test_min_days_matches_migration_floor` |
| 10 | Email cleanup writes one `email_retention.purged` row per batch transaction that deleted > 0 rows, in the same transaction as that batch's delete. A zero-delete run writes nothing. | `tests/test_cli_cleanup.py › test_cleanup_audit_summary` (updated: one row per table), `› test_cleanup_audit_row_per_batch` (batch size 1), `› test_cleanup_zero_rows_no_audit_row`, `› test_cleanup_summary_failure_rolls_back_batch`, `› test_cleanup_dry_run_no_audit_row` (unchanged) |
| 11 | The user agent has C0, DEL and C1 (`\x80-\x9f`) characters stripped. `audit_write_failed` logs the exception class name. | `tests/test_audit_logger.py › test_user_agent_control_chars_stripped` (new C1 cases), `› test_record_failure_swallows_and_logs` and the savepoint test assert `error=<ClassName>` |
| 12 | `make check` is green. | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261115_d7e4b2a91c3f_audit_log_purge_floor.py` (new; the implementer may pick another 12-hex id) | `CREATE OR REPLACE FUNCTION audit_log_immutable()` with the purge branch; downgrade restores `ORIGINAL_FUNCTION` byte for byte; triggers unchanged | AC1, AC2; R2 |
| `migrations/versions/20261115_af3eb84d2d5e_add_audit_log_outcome.py` | `downgrade()` refuses in offline mode, and refuses when failure/denied rows exist unless the `allow_outcome_loss` config attribute is set (tests only); guard in a module-level helper | AC3 (carried security finding) |
| `app/modules/audit/purge.py` (new) | `MIN_DAYS`, `MAX_DAYS`, `BATCH_SIZE`, `AuditPurgeRepository`, `AuditPurgeService`, `PurgeResult`. This is the only `app/` file with `allow_purge` | AC4–AC8 |
| `app/modules/audit/actions.py` | `AUDIT_LOG_PURGED = "audit_log.purged"` | AC6 |
| `app/cli.py` | `audit-purge` subcommand: strict parsing, lazy import of `app.modules.audit.purge`, fixed messages, per-batch scope (reuse `_cli_cleanup_scope`); module docstring entry | AC4, AC8 |
| `app/modules/auth/retention.py` | Record the summary inside the batch loop, before each commit, when `deleted > 0`; drop the final aggregate row | AC10 |
| `app/modules/audit/service.py` | `_CONTROL_CHARS = [\x00-\x1f\x7f-\x9f]`; add `error=%s` with `type(exc).__name__` to both `audit_write_failed` warnings | AC11 |
| `tests/conftest.py` | `_migrate_db`: set `cfg.attributes["allow_outcome_loss"] = True` before `downgrade base` | Committed `auth.login_failed` rows from the concurrency tests would otherwise block every later local test session (see Approach 2) |
| `tests/test_audit_schema.py` | Purge-trigger tests (AC1) | AC1 |
| `tests/test_audit_migrations.py` (new) | Migration up/down/up, `prosrc`, the `af3eb84d2d5e` guard | AC2, AC3 |
| `tests/test_cli_audit_purge.py` (new) | CLI and service tests | AC4–AC8 |
| `tests/test_audit_purge_guard.py` (new) | Static confinement tests | AC9 |
| `tests/test_cli_cleanup.py` | Per-batch summary expectations | AC10 |
| `tests/test_audit_logger.py` | C1 cases; `error=` in the log line | AC11 |
| `docs/adr/0009-audit-log.md` | `## Amendment — retention purge (ADM-004c)`: supersedes §8's "indefinite", covers the trigger, GUC latch vs. floor, CLI-only, summary rows, purge trail kept, zero-delete runs write nothing, backups | Decision outlives the ticket (planner rule 7) |
| `docs/deploy.md` | Audit section: retention text, "never downgrade production past `c3f1a9d27b60` or `af3eb84d2d5e`"; new `## Audit purge (ADM-004c)` runbook | AC3; ops truth |
| `docs/identity-architecture.md` | S8: replace "Retention is ADM-004c" with one line on the purge | Keep the design doc true |

No other files. `app/modules/audit/repository.py` stays `add`/`list` only ("append-only by shape"). The purge repository lives in `purge.py` on purpose.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_audit_schema.py › test_delete_old_row_without_flag_refused` | Row 91 d old, no flag → `audit_log is append-only` |
| `› test_purge_flag_deletes_old_row` | `SET LOCAL audit.allow_purge='on'`, row 91 d old → rowcount 1; resets the flag to `off` in `finally` |
| `› test_purge_flag_refuses_recent_row` | flag on, row 89 d old → raises |
| `› test_purge_flag_boundary_90_days_refused` | flag on, `occurred_at = now() - interval '90 days'` (same transaction `now()`) → raises (strict `<`) |
| `› test_purge_flag_other_values_refused` | parametrized `'off'`, `''`, `'true'`, `'ON'`, `'1'` → raises |
| `› test_update_refused_with_flag`, `› test_truncate_refused_with_flag` | UPDATE / TRUNCATE still refused with flag on (TRUNCATE path: nested `IF`, `OLD` not touched) |
| `test_audit_migrations.py › test_purge_migration_down_restores_original_function` | Sync test: `command.downgrade(cfg, "af3eb84d2d5e")`; `prosrc == " BEGIN RAISE EXCEPTION 'audit_log is append-only'; END "`; with flag on, DELETE of a 91 d row raises (rolled-back connection); `finally: command.upgrade(cfg, "head")` |
| `› test_purge_migration_up_down_up` | down one, up head; `prosrc` contains `allow_purge`; both triggers still bound (`pg_trigger` names) |
| `› test_outcome_downgrade_refuses_with_failure_rows` | Insert an `outcome='denied'` row in `db_session` (rolled back); call the migration's guard helper (loaded with `importlib` from its path) via `run_sync` → `RuntimeError` |
| `› test_outcome_downgrade_passes_on_clean_table` | Guard helper with a stub bind whose `scalar` returns `False` → no raise. A real empty table cannot be assumed: the concurrency tests commit `auth.login_failed` rows |
| `› test_outcome_downgrade_refuses_offline` | `command.downgrade(cfg, "af3eb84d2d5e:c3f1a9d27b60", sql=True)` → `RuntimeError` (no DB touched) |
| `test_cli_audit_purge.py › test_usage_errors` | AC4 parametrized; exit 64, `err == AUDIT_PURGE_USAGE + "\n"`, the bad value is not echoed, the scope is never entered |
| `› test_repository_rechecks_min_days` | `AuditPurgeRepository(db).cutoff(89)` → `ValueError`; also `True` (bool) → `ValueError` |
| `› test_purge_deletes_only_older_than_cutoff` | Rows at 400 d, 366 d, 364 d and now; `--older-than-days 365` → 2 deleted; output line counts only |
| `› test_purge_keeps_purge_trail` | An old `audit_log.purged` row (400 d) is not deleted; dry-run count excludes it |
| `› test_purge_batches_share_cutoff` | `AuditPurgeService(session, batch_size=2)`, 5 old rows → 3 summary rows, same `cutoff`, `deleted` 2, 2, 1 |
| `› test_purge_summary_row_shape` | actor `system`/`system:cli`, no target/ip/ua/request id, `outcome=success`, `changes == {"cutoff": <iso>, "older_than_days": 365, "deleted": n}` |
| `› test_purge_zero_rows_writes_nothing` | No old rows → exit 0, `deleted 0`, no summary row |
| `› test_purge_summary_failure_rolls_back_batch` | Monkeypatch `AuditLogger.record` to raise; the test scope rolls back on exception (like the real scope's close) → old rows still present, exit 1, `MSG_AUDIT_PURGE_FAILED` |
| `› test_dry_run_changes_nothing` | `start_read_only` monkeypatched to a no-op (savepoint harness, see Approach 7); count printed, rows unchanged, no summary row, `current_setting('audit.allow_purge', true)` is not `on` |
| `› test_dry_run_transaction_is_read_only` | Fresh engine/session (nothing committed): `start_read_only()`, then an INSERT raises `read-only transaction`; rollback |
| `› test_flag_reset_after_run` | After a real (non-dry) run in `db_session`, `current_setting('audit.allow_purge', true) == 'off'`; then a DELETE of a 91 d row raises |
| `› test_db_failure_fixed_line` | Failing scope (as in `test_cli_cleanup._failing`) → exit 1, fixed line, `_assert_no_leak` |
| `› test_refuses_db_echo` | `Settings(db_echo=True)` → exit 1, `MSG_DB_ECHO`, scope never entered |
| `test_audit_purge_guard.py › test_allow_purge_only_in_purge_module_and_migration` | Scan `app/**/*.py`, `migrations/**/*.py`, case-insensitive `allow_purge` → exactly {purge module, new migration} |
| `› test_no_trigger_bypass_in_app` | `app/**/*.py` has no `disable trigger`, `session_replication_role`, `alter table audit_log`, `set_config(` (case-insensitive, whitespace-normalized) |
| `› test_purge_module_imported_only_by_cli` | AST over `app/**/*.py`: `Import`/`ImportFrom` of `app.modules.audit.purge` or `from app.modules.audit import purge` only in `app/cli.py` |
| `› test_no_purge_route` | `create_app().routes`: no path contains `purge` (case-insensitive) |
| `› test_min_days_matches_migration_floor` | Regex `interval '(\d+) days'` in the new migration == `MIN_DAYS` (one match) |
| `test_cli_cleanup.py › test_cleanup_audit_summary` (updated) | Two rows: `{"email_send_log": {"from": None, "to": 1}}`, `{"email_tokens": {"from": None, "to": 2}}` |
| `› test_cleanup_audit_row_per_batch` | `batch_size=1` → one row per deleting batch, each `to: 1`; the final empty batch writes nothing |
| `› test_cleanup_zero_rows_no_audit_row` | Nothing to delete → no row |
| `› test_cleanup_summary_failure_rolls_back_batch` | `record` raises → that batch's delete is rolled back |
| `test_audit_logger.py › test_user_agent_control_chars_stripped` (extended) | `("a\x85b\x9bc\x80", "abc")`, `("\x9f", None)`; existing cases unchanged |
| `› test_record_failure_swallows_and_logs`, `› test_record_in_savepoint_*` (extended asserts) | `"error=<ExcName>"` in the log line; still no PII |

## Approach
1. **Trigger migration** (new, `down_revision = "af3eb84d2d5e"`, hand-written; `alembic check` cannot see functions):
   - `upgrade()`: `CREATE OR REPLACE FUNCTION audit_log_immutable()` with the DB consult's body (nested `IF TG_OP = 'DELETE'`, then `current_setting('audit.allow_purge', true) = 'on' AND OLD.occurred_at < now() - interval '90 days'` → `RETURN OLD`, otherwise `RAISE EXCEPTION 'audit_log is append-only'`). It must be `RETURN OLD`, never `RETURN NULL`. Triggers are untouched.
   - `downgrade()`: `CREATE OR REPLACE` with module constant `ORIGINAL_FUNCTION`, copied byte for byte from `c3f1a9d27b60` (`"CREATE FUNCTION"` becomes `"CREATE OR REPLACE FUNCTION"`; body string identical). Do not import `app`. The docstring says the floor is hard-coded (never env or settings: security consult finding 2) and that this downgrade is safe because it only makes the table stricter.
2. **`af3eb84d2d5e` guard** (editing the file is valid: it is in no release tag and Alembic runs `downgrade()` from disk; DB consult findings 2–3):
   - Add a module-level `_refuse_if_outcome_rows(bind)` that runs `SELECT EXISTS (SELECT 1 FROM audit_log WHERE outcome IN ('failure','denied'))` (it matches `ix_audit_log_outcome`'s predicate) and raises `RuntimeError` with a fixed message ("audit_log has failure/denied rows; dropping outcome would show them as successes; refusing").
   - `downgrade()`: if `context.is_offline_mode()`, raise `RuntimeError`. Otherwise, unless `context.config.attributes.get("allow_outcome_loss")`, call the guard. Only then drop.
   - The attribute can only be set programmatically, not from the `alembic` CLI. Only `tests/conftest.py` sets it. Without it, a local second test session would fail at `downgrade base`, because `test_login_concurrency` commits `auth.login_failed` rows that cannot be deleted.
3. **`app/modules/audit/purge.py`** (R1: repository = SQL only, service = rules; one module so the bypass is confined):
   - `MIN_DAYS = 90` (must equal the migration floor, tested), `MAX_DAYS = 3650`, `BATCH_SIZE = 1000`.
   - `AuditPurgeRepository(session)`:
     - `cutoff(days) -> datetime`: `ValueError` unless `type(days) is int` and `days >= MIN_DAYS`; then `SELECT now() - make_interval(days => :n)`.
     - `start_read_only()`: `SET TRANSACTION READ ONLY`.
     - `count(cutoff) -> int`: `occurred_at < :cutoff AND action <> 'audit_log.purged'`.
     - `open_batch()`: `SET LOCAL lock_timeout = '2s'`, then `SET LOCAL audit.allow_purge = 'on'` (literal, never plain `SET`).
     - `delete_batch(cutoff, batch) -> int`: `DELETE FROM audit_log WHERE id IN (SELECT id FROM audit_log WHERE occurred_at < :cutoff AND action <> 'audit_log.purged' ORDER BY occurred_at, id LIMIT :n FOR UPDATE SKIP LOCKED)`. It may use SQLAlchemy Core like `EmailRetentionRepository.purge_*`, with `synchronize_session=False`. Returns rowcount.
     - `close_batch()`: `SET LOCAL audit.allow_purge = 'off'`.
     - `commit()`.
   - `AuditPurgeService(session, batch_size=BATCH_SIZE).run(days, *, dry_run) -> PurgeResult(cutoff, older_than_days, deleted)`:
     - Dry run: `start_read_only()` first, then cutoff, then count; the CLI scope closes without commit, so the transaction is rolled back. It never calls `open_batch`.
     - Real run: compute the cutoff once, then commit (releases the snapshot; nothing written). Then loop: `open_batch` → `delete_batch` → `close_batch` → if `deleted > 0`, `AuditLogger.record(AUDIT_LOG_PURGED, actor=SYSTEM_CLI, ctx=AuditContext.NONE, changes={"cutoff": cutoff.isoformat(), "older_than_days": days, "deleted": deleted})` → `commit`. Stop when `deleted < batch_size`.
     - A failed summary insert raises before commit, so the batch's delete rolls back with it (R4). The summary row's `occurred_at = now()` is never inside the cutoff.
4. **CLI `audit-purge`** (`app/cli.py`, pattern of `_cleanup`):
   - `_Parser(allow_abbrev=False, add_help=False)`: `--older-than-days` is required and parsed as a string, then checked against `^[0-9]{1,4}$` (ASCII only; `int()` would accept non-ASCII digits and `+`/spaces) and `MIN_DAYS ≤ N ≤ MAX_DAYS`. Otherwise `_UsageError` → `AUDIT_PURGE_USAGE` on stderr, exit 64. `--dry-run` is a `store_true` flag.
   - Lazy `from app.modules.audit.purge import ...` inside the function; `_load_settings` (refuses `DB_ECHO`); scope `_cli_cleanup_scope` (no outer `begin`, commit per batch).
   - Output: `audit_log: deleted <n> (older than <N> d, cutoff <iso>)`, or `would delete` plus `dry-run: nothing was deleted`.
   - Any other exception prints `MSG_AUDIT_PURGE_FAILED = "error: audit purge failed; batches already committed stay deleted with their summary rows, run again"` with exit 1, never `str(exc)` (R5, R11).
   - Constants: `AUDIT_PURGE_USAGE = "usage: python -m app.cli audit-purge --older-than-days <90-3650> [--dry-run]"`. Add a docstring entry.
5. **Email cleanup** (`retention.py`): inside the `while` loop do `set_lock_timeout` → `purge` → `if deleted > 0: record(EMAIL_RETENTION_PURGED, ..., changes={table: change(None, deleted)})` → `commit`. Remove the trailing aggregate block. `_ALLOWED_KEYS`/`_is_count` are unchanged (DB consult).
6. **Carried items** (`service.py`): `_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")`. Both warnings become `"audit_write_failed action=%s outcome=%s error=%s"` with `type(exc).__name__`, using `except Exception as exc`. The class name carries no PII (R11).
7. **Test-harness rules** (DB consult risk):
   - `SET LOCAL` survives a released savepoint in the `create_savepoint` fixture. Every test that sets the flag resets it to `off` in `finally`, and the service itself resets it per batch.
   - Wrap each expected trigger error in `begin_nested()`, as `_append_only` does.
   - `SET TRANSACTION READ ONLY` cannot run inside the fixture's savepoint after earlier queries. CLI dry-run tests monkeypatch `start_read_only` to a no-op, and one separate test proves read-only on a fresh connection that commits nothing.
   - Migration command tests are plain `def` tests, because `env.py` calls `asyncio.run`, and they always end at `head` in `finally`.
   - Rows with an old `occurred_at` are inserted with explicit SQL (`now() - interval 'N days'`).
8. **Docs:**
   - ADR-0009 amendment. deploy.md: change the audit section's "Retention is indefinite until ADM-004c" text and the never-downgrade line (`c3f1a9d27b60` and `af3eb84d2d5e`; the new purge migration may be downgraded).
   - New `## Audit purge (ADM-004c)` runbook:
     - Policy default is 365 d; the CLI accepts 90–3650.
     - Run the dry run first, then the real run, through the `ops-org-admin-api` workflow's separate `audit-purge` command (once the deployment PR lands). Never schedule it.
     - Output and exit codes (0 / 1 / 64).
     - Keep the run summary as the operator trail, because zero-delete runs write no row.
     - Purged rows still exist in backups until backup retention expires.
     - Run `VACUUM (ANALYZE) audit_log` after a large first purge (autovacuum handles the rest).
     - The GUC is a safety latch, not access control; the runtime/migration role split remains a follow-up.
     - Future audit DDL on a large table uses `NOT VALID` + `VALIDATE` and `CONCURRENTLY`.
   - identity-architecture S8: one line on the purge.
9. **Rules:**
   - R1 and R2: covered above.
   - R3: no endpoint, CLI only; `test_no_purge_route`.
   - R4: the summary row is written in the batch's transaction.
   - R5: the floor is not configurable; there are no new env keys.
   - R6: N/A.
   - R7: the request-id work goes to ADM-002d (follow-up); the deployment workflow is a separate PR.
   - R8: tests above.
   - R10: N/A.
   - R11: fixed lines, no PII, `hide_parameters`.

**CLI contract for the `mmlabs-online/deployment` PR (orchestrator, not in this file list):**
- Command: `python -m app.cli audit-purge --older-than-days <N> [--dry-run]`, run like `cleanup` (`run --rm --no-deps migrate /app/.venv/bin/python -m app.cli ...`).
- `N` must be ASCII digits with 90 ≤ N ≤ 3650. The workflow offers `choice` 365/180/90, `dry_run` defaults to `true`, and inputs are passed via `env:` (never `${{ inputs.* }}` in `run:`). It uses the `org-admin-api-ops` environment and has no schedule.
- Exit codes: 0 done, 1 failure (one fixed line on stderr), 64 usage.
- Stdout (stable for the run summary): `audit_log: deleted <n> (older than <N> d, cutoff <iso>)`, or `audit_log: would delete <n> (...)` plus `dry-run: nothing was deleted`.

## Specialist input used
- **database-optimizer:**
  - **Adopted:** nested `IF TG_OP`; `RETURN OLD`; `current_setting(..., true) = 'on'`; `now()` instead of `clock_timestamp()`; the cutoff computed once with `make_interval`; batch order (lock_timeout → flag on → delete → flag off → summary → commit); the dry run as a READ ONLY count; editing `af3eb84d2d5e` instead of adding a no-op guard migration; `IN ('failure','denied')` to match the partial index; the per-batch email summary keeping `_ALLOWED_KEYS`; the harness pitfalls.
  - **Not adopted:** `interval '2160 hours'`. `'90 days'` is the ticket's text and the DST drift is within margin. Also not adopted: min/max `occurred_at` in the dry run (optional, not in the ticket).
- **security-architect:**
  - **Adopted:** the floor is hard-coded, not from env; 90 ≤ N ≤ 3650 with exit 64 and a fixed message; a `MIN_DAYS` test; `SET LOCAL` only; `audit_log.purged` rows excluded; the summary keys `{cutoff, older_than_days, deleted}`; fixed-line errors; the four static tests; offline-mode refusal plus tests for the downgrade guard; C1 stripping and `type(exc).__name__` in this ticket; `CF-Ray` → ADM-002d; the large-table DDL pattern as a deploy.md note only; the admin UI showing `outcome` as an admin-UI follow-up; the ops-workflow constraints (documented as the contract).
  - **Adjusted:** the guard uses `IN ('failure','denied')` (DB consult) instead of `<> 'success'`; they are equivalent under the CHECK. I added `set_config(` to the forbidden list, because it is the other way to set the GUC.

## Assumptions
Owner defaults are recorded as decisions, not blockers (ticket Notes, ADM-004b A7), pending Q6/Decree 13:
- **D-a.** Retention policy is 365 days (runbook default). The CLI accepts ≥ 90 (the DB floor), and the workflow `choice` limits operators to 365/180/90.
- **D-b.** IP addresses are stored in full; read-time masking comes before `admin:audit:read` is widened (not this ticket).
- **D-c.** `audit_log.purged` rows are kept forever (excluded from the purge). `email_retention.purged` and all other rows are purged normally.
- **D-d.** Backups still contain purged rows until backup retention expires. Whether Decree 13 requires backup erasure is an owner question, recorded in ADR-0009 and the runbook.
- **D-e.** A run that deletes 0 rows writes no audit row (ADR-0009 §3: no-ops record nothing). The ops run summary is the trace.
- **D-f.** No per-class retention (for example, shorter for `auth.login_failed`). That would need a later migration of the floor.

Technical assumptions:
- The DB session TimeZone is the server default. `make_interval(days)` and `interval '90 days'` use the same calendar arithmetic, so `row < cutoff` at batch time implies `row < now() - 90 days`.
- `audit.allow_purge` works as an unregistered dotted placeholder GUC on PG17 (no `postgresql.conf` change).
- The GUC is a latch, not a privilege boundary (both consults). The floor is the real protection until the runtime and migration DB roles are split (existing deploy.md follow-up).

## Blocking questions
None.

## Split proposal
None.
- Estimated production lines: ~260 (migration ~45, `af3eb84d2d5e` edit ~20, `purge.py` ~110, `cli.py` ~60, `retention.py` ~10 net, `service.py`/`actions.py` ~6).
- Docs: ~80. Tests: ~380.
- One new migration. The edit to `af3eb84d2d5e` is a guard, not a schema change.

**Follow-ups (not in this diff):**
- ADM-002d: use `CF-Ray`, or always generate the stored request id (middleware trust semantics, echoed header, docs, ADR-0009 note). The orchestrator adds this note to ADM-002d; this plan does not edit that ticket.
- Admin UI must show `outcome`.
- Run the API as a DB role with INSERT/SELECT only on `audit_log` (existing).
- `mmlabs-online/deployment`: `audit-purge` workflow choice (orchestrator; contract above).
- Owner: Q6 / Decree 13 (retention period, per-class retention, backup erasure).

## Data model / migration
No table or column change.
- **New migration `d7e4b2a91c3f`** (revises `af3eb84d2d5e`):
  - Upgrade: `CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN IF current_setting('audit.allow_purge', true) = 'on' AND OLD.occurred_at < now() - interval '90 days' THEN RETURN OLD; END IF; END IF; RAISE EXCEPTION 'audit_log is append-only'; END $$`.
  - Triggers `audit_log_no_update_delete` (row, UPDATE OR DELETE) and `audit_log_no_truncate` (statement) keep their binding.
  - Downgrade: `CREATE OR REPLACE` with the original body `" BEGIN RAISE EXCEPTION 'audit_log is append-only'; END "`, which is safe (stricter).
- **`af3eb84d2d5e.downgrade()`:** refuses in offline mode, and refuses when failure/denied rows exist (unless the test-only `allow_outcome_loss` config attribute is set). Production must never downgrade past it (deploy.md).
- **Index use:** `ix_audit_log_occurred_at_id` serves the ordered batch select and the dry-run count; `action <> 'audit_log.purged'` is a filter on that scan.

## API contract
None. No endpoint; `audit-purge` is CLI-only and `test_no_purge_route` enforces it.
