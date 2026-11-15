# Review — ADM-004c

## Attempt 1 — 2026-11-15
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, HEAD `c3da104`): ruff `All checks passed!`; format `439 files already formatted`; mypy `Success: no issues found in 100 source files`; pytest `1568 passed, 1 warning in 115.49s` (pre-existing forkpty DeprecationWarning). Exit 0. Matches impl-log (1568 passed).
- alembic check: `No new upgrade operations detected.`
- Migration cycle on local `org_admin`: `upgrade head` (already `d7e4b2a91c3f`) → `downgrade -1` (`d7e4b2a91c3f -> af3eb84d2d5e`, current `af3eb84d2d5e`) → `upgrade head` (`d7e4b2a91c3f (head)`). After the down step `prosrc` was ` BEGIN RAISE EXCEPTION 'audit_log is append-only'; END ` (leading and trailing space, checked with `cat -A`), byte-identical to the body created by `c3f1a9d27b60` (`migrations/versions/*c3f1a9d27b60*:113-114`).
- psql on `org_admin`, in one transaction that was rolled back (rows before 0, after 0; both triggers still `tgenabled = O`):
  - DELETE of a 91 d row with no flag → `audit_log is append-only`.
  - Flag `'ON'` (wrong case), 91 d row → raises.
  - Flag `'on'`: 89 d row → raises. Row at exactly `now() - interval '90 days'` → raises (strict `<`).
  - Flag `'on'`: UPDATE of the 91 d row → raises. `TRUNCATE audit_log` → raises.
  - Flag `'on'`, 91 d row → `DELETE 1` (rolled back).
- Offline guard via the real CLI: `alembic -x allow_outcome_loss=1 downgrade d7e4b2a91c3f:c3f1a9d27b60 --sql` and `ALLOW_OUTCOME_LOSS=1 alembic downgrade af3eb84d2d5e:c3f1a9d27b60 --sql` → both `RuntimeError: offline downgrade of af3eb84d2d5e is refused`. The DB stayed at `d7e4b2a91c3f (head)`.

### Acceptance criteria
- [x] AC1: DELETE without the flag, or with the flag on a row 90 days old or newer, raises. UPDATE and TRUNCATE always raise. With the flag, a row older than 90 days is deleted. Evidence: `migrations/versions/20261115_d7e4b2a91c3f_audit_log_purge_floor.py:29-38` (nested `IF TG_OP = 'DELETE'`, exact `= 'on'` with `current_setting(..., true)`, `RETURN OLD`, otherwise `RAISE`; the TRUNCATE path never touches `OLD`). Tests `tests/test_audit_schema.py` `test_delete_old_row_without_flag_refused`, `test_purge_flag_deletes_old_row`, `test_purge_flag_refuses_recent_row`, `test_purge_flag_boundary_90_days_refused`, `test_purge_flag_other_values_refused[off,'',true,ON,1]`, `test_update_refused_with_flag`, `test_truncate_refused_with_flag` all pass. Reviewer psql run above confirms the same.
- [x] AC2: up/down/up is clean, and the downgrade restores the original function. Evidence: `d7e4b2a91c3f…py:41-44,52-53`; `tests/test_audit_migrations.py::test_purge_migration_down_restores_original_function` (`prosrc ==` original, and a flagged DELETE of a 91 d row raises after the down step); `::test_purge_migration_up_down_up` (both triggers still bound). The reviewer cycle above agrees.
- [x] AC3: the `af3eb84d2d5e` guard. Evidence: `migrations/versions/20261115_af3eb84d2d5e_add_audit_log_outcome.py:54-60` (`_refuse_if_outcome_rows`, `IN ('failure','denied')`), `:70-73` (refuses offline first, then runs the guard unless `config.attributes["allow_outcome_loss"]`). Tests `test_outcome_downgrade_refuses_with_failure_rows`, `test_outcome_downgrade_passes_on_clean_table`, `test_outcome_downgrade_refuses_offline`. `docs/deploy.md` never-downgrade line names `c3f1a9d27b60` and `af3eb84d2d5e`.
  - **Bypass confinement verified.** `config.attributes` is an in-process dict that is empty for every `alembic` CLI run. `-x` values go to `get_x_argument`, not `attributes`, and `migrations/env.py:18-24` reads only `url` from either. No env var is read. The only writer is `tests/conftest.py:69` (test DB URL only). `grep` finds no `attributes` writes in `app/`, and `app/` never runs migrations.
- [x] AC4: CLI validation. Evidence: `app/cli.py:691-700`. `_Parser` (`allow_abbrev=False`, `add_help=False`; `error()` raises `_UsageError` with no message, `app/cli.py:145-148`), `re.fullmatch(r"[0-9]{1,4}")` (ASCII only), `MIN_DAYS <= days <= MAX_DAYS` (90–3650). Fixed `AUDIT_PURGE_USAGE`, exit 64, no argv echo. `tests/test_cli_audit_purge.py::test_usage_errors` covers 13 cases, including `٩٠`, ` 365`, `--older`, `--help` and `-5`, and asserts `err == AUDIT_PURGE_USAGE + "\n"` and that the scope is never entered. `test_repository_rechecks_min_days` covers 89 and `True`.
- [x] AC5: the cutoff is computed once on the DB clock. `purge.py:38-45` (`SELECT now() - make_interval(days => :n)`) is called once in `run` (`:95-102`) and reused for every batch, every summary row and the dry run. Rows are ordered by `occurred_at, id`, `FOR UPDATE SKIP LOCKED` (`:59-71`). `audit_log.purged` rows are excluded (`_purgeable`, `:80-84`). Tests `test_purge_deletes_only_older_than_cutoff`, `test_purge_keeps_purge_trail`, `test_purge_batches_share_cutoff` (deleted 2/2/1, one cutoff).
- [x] AC6: per-batch transaction order. `open_batch` (`lock_timeout`, then `SET LOCAL ... 'on'`) → `delete_batch` → `close_batch` (`'off'`) → `record` only if `deleted > 0` → `commit` (`purge.py:104-119`). `AuditLogger.record` flushes and raises with no savepoint (`service.py:121-144`), so a failed summary rolls back the batch. Tests `test_purge_summary_row_shape`, `test_purge_zero_rows_writes_nothing`, `test_purge_summary_failure_rolls_back_batch`.
- [x] AC7: the dry run is read-only and never sets the flag. `SET TRANSACTION READ ONLY` is the first statement, then the cutoff and a count. The dry run never calls `open_batch`, and the scope closes without commit (`purge.py:96-99`; `_cli_cleanup_scope` `app/cli.py:201-214`). Tests `test_dry_run_changes_nothing` and `test_dry_run_transaction_is_read_only` (fresh connection; INSERT → `read-only transaction`).
- [x] AC8: the flag is reset after a run, DB errors print one fixed line, and `DB_ECHO` is refused. Tests `test_flag_reset_after_run`, `test_db_failure_fixed_line` (no DSN/traceback/`@`), `test_refuses_db_echo`. `app/cli.py:722-725` never prints `str(exc)`.
- [x] AC9: static confinement. `tests/test_audit_purge_guard.py` has 5 tests: `allow_purge` only in `purge.py` and the migration, no `disable trigger`/`session_replication_role`/`alter table audit_log`/`set_config(` in `app/`, AST check that only `app/cli.py` imports the purge module (lazy import at `app/cli.py:689`), no route with `purge`, and `MIN_DAYS` == the migration floor.
- [x] AC10: email cleanup writes one summary row per deleting batch, before that batch's commit (`app/modules/auth/retention.py:77-84`). The trailing aggregate was removed. Tests `test_cleanup_audit_summary`, `test_cleanup_audit_row_per_batch`, `test_cleanup_zero_rows_no_audit_row`, `test_cleanup_summary_failure_rolls_back_batch`, `test_cleanup_dry_run_no_audit_row`.
- [x] AC11: C1 stripping and `error=<ExcName>`. `app/modules/audit/service.py:28` (`[\x00-\x1f\x7f-\x9f]`), `:180-185` and `:242-247` (`type(exc).__name__`). Tests `test_user_agent_control_chars_stripped[a\x85b\x9bc\x80, \x9f]`, plus `test_record_failure_swallows_and_logs` and `test_record_in_savepoint_keeps_outer_work` assert `error=RuntimeError`.
- [x] AC12: `make check` green (above).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the purge repository is SQL only and the service holds the rules. The CLI is the only entry point.
- R2: hand-written migration with a working downgrade. `alembic check` is clean.
- R3: no endpoint (`test_no_purge_route`).
- R4: the summary row is written in each batch's transaction.
- R5: no new env keys, and the floor is not configurable.
- R11: fixed lines only and `hide_parameters=True`. The class name in `audit_write_failed` carries no PII.

Scope: every code, test and doc file is in the plan's file list. `work/backlog.md`, `work/tickets/ADM-004c/{ticket,consult-*,plan,impl-log}.md` and the ADM-002d item 10 note are orchestrator bookkeeping. The plan assigns the ADM-002d note to the orchestrator.

### Nits (do not block)
- 💭 `purge.py:121`: with `SKIP LOCKED`, a batch that skips locked rows can return fewer than `batch_size` rows and end the run early. A re-run is safe and the runbook already says to run again. A one-line comment would make this explicit.
- 💭 impl-log reports `438 files already formatted`; the reviewer run shows 439. Cosmetic; the pytest totals match.
- 💭 impl-log says the implementer used `DISABLE TRIGGER` on the local dev `org_admin` to remove a manual guard-test row. That was local only, and the reviewer confirmed both triggers are enabled (`O`). Prefer a scratch DB for such checks next time.
- 💭 `test_outcome_downgrade_passes_on_clean_table` uses a stub bind. A real-DB proof is impractical because committed failure rows cannot be deleted. Acceptable.

### Follow-ups (out of scope)
- `mmlabs-online/deployment`: `audit-purge` ops workflow choice (contract in plan and `docs/deploy.md`).
- ADM-002d item 10: `CF-Ray` or always generate the stored request id (already noted).
- Run the API as a DB role with INSERT/SELECT only on `audit_log` (the latch is not access control until then).
- Admin UI must show `outcome`.
- Owner: spec Q6 / Decree 13/2023 (retention period, per-class retention, backup erasure).
