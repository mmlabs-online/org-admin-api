# Implementation log — ADM-004c

## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261115_d7e4b2a91c3f_audit_log_purge_floor.py` (new) | AC1, AC2: `CREATE OR REPLACE FUNCTION audit_log_immutable()` with the purge branch (nested `IF TG_OP = 'DELETE'`, latch `= 'on'` AND `OLD.occurred_at < now() - interval '90 days'` → `RETURN OLD`); downgrade restores `ORIGINAL_FUNCTION` (body byte-identical to `c3f1a9d27b60`); triggers untouched; no `app` import |
| `migrations/versions/20261115_af3eb84d2d5e_add_audit_log_outcome.py` | AC3: module-level `_refuse_if_outcome_rows(bind)`; `downgrade()` refuses offline, and refuses failure/denied rows unless `config.attributes["allow_outcome_loss"]` |
| `app/modules/audit/purge.py` (new) | AC4–AC8: `MIN_DAYS=90`, `MAX_DAYS=3650`, `BATCH_SIZE=1000`, `PurgeResult`, `AuditPurgeRepository` (`cutoff`, `start_read_only`, `count`, `open_batch`, `delete_batch`, `close_batch`, `commit`), `AuditPurgeService.run` |
| `app/modules/audit/actions.py` | AC6: `AUDIT_LOG_PURGED = "audit_log.purged"` |
| `app/cli.py` | AC4, AC8: `audit-purge` subcommand (lazy import, strict ASCII parsing, `_load_settings`, `_cli_cleanup_scope`, fixed lines), `AUDIT_PURGE_USAGE`, `MSG_AUDIT_PURGE_FAILED`, docstring entry |
| `app/modules/auth/retention.py` | AC10: summary row per deleting batch before its commit; trailing aggregate removed |
| `app/modules/audit/service.py` | AC11: `_CONTROL_CHARS` covers `\x7f-\x9f`; both `audit_write_failed` warnings log `error=<ExceptionClass>` |
| `tests/conftest.py` | `allow_outcome_loss = True` before `downgrade base` (committed `auth.login_failed` rows) |
| `tests/test_audit_schema.py` | AC1 |
| `tests/test_audit_migrations.py` (new) | AC2, AC3 |
| `tests/test_cli_audit_purge.py` (new) | AC4–AC8 |
| `tests/test_audit_purge_guard.py` (new) | AC9 |
| `tests/test_cli_cleanup.py` | AC10 |
| `tests/test_audit_logger.py` | AC11 |
| `docs/adr/0009-audit-log.md` | `## Amendment — retention purge (ADM-004c)`; the ADM-004b retention line points to it |
| `docs/deploy.md` | Audit section: purge exception, never-downgrade line now `c3f1a9d27b60` or `af3eb84d2d5e`, `error=` in the log line, large-table DDL note; new `## Audit purge (ADM-004c)` runbook |
| `docs/identity-architecture.md` | S8 retention line |

### Tests added / changed
- `test_audit_schema.py` › `test_delete_old_row_without_flag_refused`, `test_purge_flag_deletes_old_row`, `test_purge_flag_refuses_recent_row`, `test_purge_flag_boundary_90_days_refused`, `test_purge_flag_other_values_refused` (off, '', true, ON, 1), `test_update_refused_with_flag`, `test_truncate_refused_with_flag`
- `test_audit_migrations.py` › `test_purge_migration_down_restores_original_function`, `test_purge_migration_up_down_up`, `test_outcome_downgrade_refuses_with_failure_rows`, `test_outcome_downgrade_passes_on_clean_table`, `test_outcome_downgrade_refuses_offline`
- `test_cli_audit_purge.py` › `test_usage_errors` (13 cases: missing, `--dry-run` only, 89, 0, -5, 3651, 90.5, abc, `٩٠`, ` 365`, unknown flag, `--older`, `--help`), `test_repository_rechecks_min_days`, `test_purge_deletes_only_older_than_cutoff`, `test_purge_keeps_purge_trail`, `test_purge_batches_share_cutoff`, `test_purge_summary_row_shape`, `test_purge_zero_rows_writes_nothing`, `test_purge_summary_failure_rolls_back_batch`, `test_dry_run_changes_nothing`, `test_dry_run_transaction_is_read_only`, `test_flag_reset_after_run`, `test_db_failure_fixed_line`, `test_refuses_db_echo`
- `test_audit_purge_guard.py` › `test_allow_purge_only_in_purge_module_and_migration`, `test_no_trigger_bypass_in_app`, `test_purge_module_imported_only_by_cli`, `test_no_purge_route`, `test_min_days_matches_migration_floor`
- `test_cli_cleanup.py` › `test_cleanup_audit_summary` (updated: one row per table), `test_cleanup_audit_row_per_batch`, `test_cleanup_zero_rows_no_audit_row`, `test_cleanup_summary_failure_rolls_back_batch`
- `test_audit_logger.py` › `test_user_agent_control_chars_stripped` (+2 C1 cases), `test_record_failure_swallows_and_logs` and `test_record_in_savepoint_keeps_outer_work` assert `error=RuntimeError`

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff `All checks passed!`, format `438 files already formatted`, mypy `Success: no issues found in 100 source files`, pytest `1568 passed, 1 warning` (pre-existing forkpty DeprecationWarning).
- `uv run alembic check`: `No new upgrade operations detected.`
- Migration cycle on local `org_admin`: upgrade head (`af3eb84d2d5e -> d7e4b2a91c3f`) → downgrade -1 (current `af3eb84d2d5e`) → upgrade head (`d7e4b2a91c3f (head)`).
- `af3eb84d2d5e` guard via the `alembic` CLI: `downgrade d7e4b2a91c3f:c3f1a9d27b60 --sql` → `RuntimeError: offline downgrade of af3eb84d2d5e is refused`; with one `denied` row, `downgrade c3f1a9d27b60` → `RuntimeError: audit_log has failure/denied rows; ...`, and transactional DDL left the DB at `d7e4b2a91c3f (head)` with `outcome` intact; on a fresh scratch DB (no rows) the downgrade to `c3f1a9d27b60` passes and upgrade head works again (scratch DB dropped).
- CLI smoke against local `org_admin`: dry run → `audit_log: would delete 0 (older than 365 d, cutoff 2025-11-15T10:17:29.330984+00:00)` + `dry-run: nothing was deleted`, exit 0 (real READ ONLY scope); real run → `deleted 0`, exit 0; `--older-than-days 89` → usage line, exit 64.
- Local Postgres was already running; nothing started. To undo my guard check on local `org_admin` (whose `audit_log` was empty before), I removed the one test `denied` row in a single transaction (trigger disabled, row deleted, trigger re-enabled; both triggers confirmed enabled `O`).

### Out-of-plan files
None.

### Deviations from the plan (small)
- `test_usage_errors` adds ` 365` (leading space) and `--help` cases. It does not assert per value that the value is absent from stderr, because some values (`0`, `365`) are substrings of the usage line; `err == AUDIT_PURGE_USAGE + "\n"` proves nothing is echoed.
- Test helper `_summaries` filters `audit_log.purged` rows to the last day, so the seeded 400-day trail row in `test_purge_keeps_purge_trail` is not counted as a summary.
- The dry-run line reuses `MSG_CLEANUP_DRY_RUN` (`dry-run: nothing was deleted`), the same text the plan specifies.

### Follow-ups (noticed, not done)
- ADM-002d: `CF-Ray` or always generate the stored request id (from the plan).
- Admin UI must show `outcome`.
- Run the API as a DB role with INSERT/SELECT only on `audit_log` (existing).
- `mmlabs-online/deployment`: `audit-purge` workflow choice (orchestrator; contract in plan and deploy.md).
- Owner: Q6 / Decree 13 (retention period, per-class retention, backup erasure).
- `tests/test_audit_identity.py` / `test_audit_denied.py` still match the `audit_write_failed` prefix only; they could assert `error=` too (not needed for AC11).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +965 / -34 in total. Production (app + migrations) +286 / -16; tests +594 / -10; docs +85 / -8.
