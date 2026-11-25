Status: READY

# Plan — ADM-045: Usage event retention purge

## Scope summary
- `docs/spec-v1.md` §5.14, OQ69 (line 289): "**Retention (OQ69).** Per-use events kept **90 days** (purge job); per-period totals per user + app + metric kept indefinitely in v1."
- `docs/spec-v1.md` §5.14 risks (line 312): "Usage reports are high-volume and are not audit entries … Usage history retention: OQ69 (90 days of events)."
- `docs/adr/0022-usage-key-scopes.md:71`: "Idempotency records are kept at least as long as events (90 days)." So 90 days is a floor, and a consume replayed with a key older than that is a new consume (accepted).
- Ticket scope: "Cleanup CLI: delete usage events older than 90 days (dry-run default, batched, counts only); keep per-period totals."
- Code already expects this: `app/modules/usage/models.py:160` says "Purged after 90 days (ADM-045)", and `:126` says counters are "kept indefinitely (OQ69)".

Out of scope: scheduling (cron or a scheduled workflow), the `ops-org-admin-api` choice in `mmlabs-online/deployment`, VACUUM, staff history reads (ADM-043).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Events older than the retention (default 90 d) are deleted | `tests/test_usage_events_retention.py › test_purges_events_older_than_retention` (91 d is gone) |
| 2 | Recent events are kept | same file › `test_keeps_recent_events` (89 d and fresh rows stay; the boundary uses `<` cutoff) |
| 3 | Per-period totals (`usage_counters`) are untouched | same file › `test_counters_untouched` (`used`, `updated_at` and the row count are equal before and after an apply run) |
| 4 | A dry run changes nothing | `test_dry_run_changes_nothing` (service) and `tests/test_cli_usage_events_purge.py › test_default_is_dry_run` (no flag: rows stay, no summary row, prints `would delete` and `dry-run: nothing was deleted`) |
| 5 | Batched, one commit per batch, one cutoff for the run | `test_runs_several_batches` (batch_size=2, 5 old rows → 5 deleted, 3 summary rows all with the same `cutoff`) and `test_delete_batch_respects_limit_when_subquery_rescanned` |
| 6 | Counts only (no keys, user ids or emails), with an audit summary per deleting batch (R4) | `test_summary_row_shape` (`changes` keys are exactly `cutoff`, `retention_days`, `deleted`; actor `system:cli`) and the CLI `test_output_counts_only` |
| 7 | Retention config: floor 90, at least the adjust window | `tests/test_config.py › test_usage_event_retention_floor_and_window` |
| 8 | Rows held by a live transaction are skipped, not waited on | `test_skips_locked_rows` (committed data, second connection holds `FOR UPDATE`) |
| 9 | A purged key replays as a new consume (ADR-0022) | `test_purged_key_consumes_again` |
| 10 | `make check` is green | `make check`; `uv run alembic check` reports no drift (no model change) |

## Ming verification after merge
- Add the `usage-events-purge` choice to the `ops-org-admin-api` workflow in `mmlabs-online/deployment`, then run it in production with dry run first, read the count, and run again with `--apply`. Agents never run it against production (loop.config Guardrails).
- Set `USAGE_EVENT_RETENTION_DAYS` only if a value other than 90 is wanted. Unset means 90.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/usage/retention.py` | **new**: `UsageEventRetentionRepository` (cutoff, start_read_only, count, set_lock_timeout, delete_batch, commit) and `UsageEventRetentionService.run(*, apply) -> PurgeResult`; `MIN_DAYS = 90`, `BATCH_SIZE = 1000` | the purge logic, shaped like `app/modules/audit/purge.py` | no |
| `app/modules/audit/actions.py` | add `USAGE_EVENTS_PURGED = "usage_events.purged"` after `JOIN_REQUEST_RETENTION_PURGED` (:80) | summary audit row (R4) | no |
| `app/core/config.py` | add `usage_event_retention_days: int = Field(default=90, ge=90)` after `:141`, update the comment at `:129-131`, add a validator `_usage_retention_covers_adjust_window` next to `_join_retention_covers_cooldown` (`:265-271`) | retention from env (R5), safe against the adjust window | **yes** (`app/core/**`) |
| `.env.example` | `# Days to keep usage events …` comment and `USAGE_EVENT_RETENTION_DAYS=` after `:106` | R5: every key listed without a value | **yes** |
| `app/cli.py` | docstring bullet (`:15-17` area); `USAGE_EVENTS_PURGE_USAGE` and `MSG_USAGE_EVENTS_PURGE_FAILED` constants (near `:129-133`); dispatch in `amain` (after `:297-298`); `_usage_events_purge()` after `_audit_purge` (`:762-805`) | operator entry point | no |
| `Makefile` | `usage-events-purge` target and `.PHONY` entry, like `cleanup` (`:1`, `:33-34`) | local run parity (`make usage-events-purge ARGS=--apply`) | no |
| `docs/deploy.md` | new section `## Usage event purge (ADM-045)` after "Retention cleanup" (`:937-986`): command, flag, output, exit codes, fallback compose command, note that scheduling is an open owner decision, VACUUM note | ops runbook | no |
| `tests/test_usage_events_retention.py` | **new**: service and repository tests | AC 1-3, 5, 6, 8, 9 | no |
| `tests/test_cli_usage_events_purge.py` | **new**: CLI tests (scope injection as `tests/test_cli_audit_purge.py:31-103`) | AC 4, 6, usage errors, no leaks | no |
| `tests/test_config.py` | add `test_usage_event_retention_floor_and_window` next to `:415-421` | AC 7 | no |

Tier: **High** (`app/core/config.py`, `.env.example`). Plan approval is needed, and an agent never merges it. appsec-engineer review is triggered by `.env.example`.

## Data model / migration
None. No model or schema change; head stays `c91e4b7a3d58`. It uses the existing `ix_usage_events_created_at` (`models.py:190`). `usage_counters` has no FK to `usage_events` (`models.py:125-156`), so the DELETE cannot cascade into counters.

## API contract
None (CLI only, never an HTTP route).

CLI contract:
```
python -m app.cli usage-events-purge [--apply]
```
- No flag means a dry run: count only, in a READ ONLY transaction, nothing written.
- `--apply` deletes.
- Output is one line: `usage_events: deleted|would delete <n> (older than <N> d, cutoff <iso>)`. A dry run adds `dry-run: nothing was deleted` (`MSG_CLEANUP_DRY_RUN`).
- Exit codes: 0 means done; 1 is one fixed line for settings, `DB_ECHO` or database errors (batches already committed stay deleted; running again is safe); 64 is a usage error (any other argument, abbreviations off, `add_help=False`).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_usage_events_retention.py › test_purges_events_older_than_retention` | 91-day-old consumed, settled and refunded events are deleted |
| › `test_keeps_recent_events` | 89-day-old and fresh events stay |
| › `test_counters_untouched` | counter rows for the same user and metric are unchanged (count, `used`, `updated_at`) |
| › `test_dry_run_changes_nothing` | `run(apply=False)` returns the count, deletes nothing and writes no audit row |
| › `test_dry_run_transaction_is_read_only` | the dry run issues `SET TRANSACTION READ ONLY` first (as `test_cli_audit_purge.py:239`) |
| › `test_runs_several_batches` | batch_size=2 with 5 old rows: 5 deleted, 3 summary rows with the same cutoff |
| › `test_zero_rows_writes_no_summary` | nothing old: result 0, no `usage_events.purged` row |
| › `test_summary_row_shape` | action `usage_events.purged`, actor `system:cli`, `changes` = {`cutoff`, `retention_days`, `deleted`} only |
| › `test_repository_refuses_below_floor` | `cutoff(89)` raises `ValueError` (defence in depth beyond config) |
| › `test_delete_batch_respects_limit_when_subquery_rescanned` | uses `tests/purge_plan.py › force_rescanned_semijoin`; the MATERIALIZED CTE keeps the LIMIT (BUG-001 pattern) |
| › `test_skips_locked_rows` | committed data cleaned up by id (pattern `tests/test_usage_concurrency.py`): a second connection holds `SELECT … FOR UPDATE` on one old event, the purge deletes the others and returns without waiting, and the locked row survives |
| › `test_purged_key_consumes_again` | after a purge, `POST …/usage/consume` with the purged key returns 200 `replayed=false` and the counter grows (ADR-0022:71) |
| `tests/test_cli_usage_events_purge.py › test_usage_errors` (parametrized) | `--dry-run`, `--bogus`, `--app`, `--help`, `--apply x` → 64 and the usage line on stderr |
| › `test_default_is_dry_run` | no flag: rows stay, prints `would delete` and the dry-run line |
| › `test_apply_deletes` | `--apply`: old rows gone, output line matches the regex |
| › `test_output_counts_only` | output has no idempotency key, user id or `@` (`_assert_no_leak` pattern) |
| › `test_db_failure_fixed_line` | failing scope → exit 1, `MSG_USAGE_EVENTS_PURGE_FAILED`, no DSN or traceback |
| › `test_refuses_db_echo` | `DB_ECHO` on → exit 1 |
| `tests/test_config.py › test_usage_event_retention_floor_and_window` | 89 → error; 90 is accepted; `usage_adjust_window_seconds=91*86400` with the default 90 d → error |

## Approach
1. **Config** (`app/core/config.py`): add `usage_event_retention_days: int = Field(default=90, ge=90)`. Add the validator below, modelled on the join-retention validator:
   ```python
   @model_validator(mode="after")
   def _usage_retention_covers_adjust_window(self) -> Self:
       if self.usage_event_retention_days * 86400 < self.usage_adjust_window_seconds:
           raise ValueError("USAGE_EVENT_RETENTION_DAYS must cover USAGE_ADJUST_WINDOW_SECONDS")
       return self
   ```
   The message names keys, never values (R5). Add the key to `.env.example`.
2. **Retention module** (`app/modules/usage/retention.py`): copy `app/modules/audit/purge.py:22-127` without the latch or trigger parts.
   - `cutoff(days)`: `SELECT now() - make_interval(days => :n)` on the DB clock. It refuses `days < MIN_DAYS` or a non-int.
   - `count(cutoff)`: `SELECT count(*) FROM usage_events WHERE created_at < :cutoff`.
   - `delete_batch(cutoff, n)`: a MATERIALIZED CTE `SELECT id … WHERE created_at < cutoff ORDER BY created_at, id LIMIT n FOR UPDATE SKIP LOCKED`, then `DELETE … WHERE id IN (SELECT id FROM batch)` with `synchronize_session=False`. Return the rowcount.
   - Each batch runs `SET LOCAL lock_timeout = '2s'` first.
   - The service computes the cutoff once, commits (which ends the snapshot), then loops: delete, write the audit summary if `deleted > 0`, commit. It stops when `deleted < batch_size`. No sleep and no batch cap, the same as the existing purges.
   - A dry run starts read-only, then counts.
   - The service never touches `usage_counters`. R1 holds: SQL stays in the repository class and the service holds the rules.
3. **Audit** (R4): add the `AuditAction.USAGE_EVENTS_PURGED` row through `AuditLogger.record(actor=SYSTEM_CLI, ctx=AuditContext.NONE, changes={"cutoff", "retention_days", "deleted"})` in the same transaction as the batch. Counts only (R11).
4. **CLI** (`app/cli.py`): `_usage_events_purge` follows `_audit_purge` (`:762-805`). Use `_Parser(allow_abbrev=False, add_help=False)` with `--apply` `store_true`. Call `_load_settings` (refuses `DB_ECHO`) and use `_cli_cleanup_scope`. The retention is `settings.usage_event_retention_days`. Error handling is the same `_UsageError` / `_Fail` / `Exception` ladder, never `str(exc)`.
5. **Makefile** target and **docs/deploy.md** section, mirroring the "Audit purge" layout (`:887-935`). The section says:
   - the workflow choice must be added in `mmlabs-online/deployment` (Ming);
   - scheduling is an open owner decision;
   - after a large first purge, run `VACUUM (ANALYZE) usage_events`, never VACUUM FULL;
   - a consume replayed with a purged key counts again (ADR-0022), and settle or refund on a purged key returns 404 `usage_event_not_found` (`meter_service.py:196`).
6. Run `make check` and `uv run alembic check`. Never run the CLI against production.

**Flag style inconsistency (noted for Ming):** `cleanup` and `audit-purge` delete by default and take an opt-in `--dry-run` (`cli.py:726`, `:773`). `attachments-sweep` defaults to a dry run and takes `--apply` (`cli.py:839`). The ticket requires a dry-run default, so this plan uses `--apply`, the same as `attachments-sweep`. Changing the older commands is a follow-up, not part of this diff.

## Specialist input used
`consult-database-optimizer.md`:
- **Adopted:**
  - Recommendation 1: own module, count plus a batched DELETE with `ORDER BY created_at FOR UPDATE SKIP LOCKED`. Wrapped as a MATERIALIZED CTE because of BUG-001.
  - Recommendations 2 and 3: commit per batch; one cutoff for the run.
  - Recommendation 4: config with floor 90 and an adjust-window validator.
  - Recommendation 5: `usage-events-purge` with `--apply`; counts only.
  - Recommendation 6: no table lock; batches of 1000.
  - All seven tests.
  - Open question 1: a summary row per batch, like the existing purges.
- **Rejected:** "small sleep or max-batches cap". The existing purges have neither, and SKIP LOCKED plus 1000-row batches keep the impact low. It can be added later if production volume shows a need.

## Assumptions
- Retention comes from config only, with no `--older-than-days` flag (consult recommendation 4; join retention works the same way). The 90-day floor is also re-checked in the repository.
- `usage_adjust_window_seconds` keeps its upper bound open. The new validator is the only coupling.
- `docs/identity-architecture.md`'s audit-action list is not updated here. The usage actions from ADM-039/042 are not listed there either, so this goes to follow-ups.
- Hotspots: ADM-043 (PLANNING) may touch `app/modules/usage/` and ADM-046 (BACKLOG) may touch `config.py` and `meter_*`. Neither is IN_PROGRESS, REVIEW or DONE, so no rebase is expected. `app/cli.py`, `.env.example` and `docs/deploy.md` are shared by many tickets, so expect a trivial rebase if another lands first.

## Blocking questions
None that block the plan: each owner choice below has a default that is already planned. Ming confirms or overrides them at the plan-approval gate (High path).

**Questions for Ming**
1. CLI name and flag: `usage-events-purge`, dry run by default, `--apply` to delete (the `attachments-sweep` style; it differs from the `--dry-run` style of `cleanup` and `audit-purge`). **Default: yes.**
2. Audit trail: one `usage_events.purged` summary row (counts and cutoff only) **per deleting batch**, the same as audit-purge, email and join retention, rather than one per run. **Default: per batch.**
3. Retention configurable through `USAGE_EVENT_RETENTION_DAYS`, default 90, floor 90, and it must cover `USAGE_ADJUST_WINDOW_SECONDS`. **Default: yes.**
4. A consume replayed more than 90 days later with a purged idempotency key counts as a new consume, and settle or refund on it returns 404 (ADR-0022:71). **Default: accepted.**
5. Scheduling: CLI and ops workflow only for now; a cron or scheduled workflow is a separate owner decision, documented in deploy.md. **Default: out of scope.**
6. Counters (`usage_counters`) are never purged in v1 (OQ69). **Default: confirmed by the spec; no change.**

## Split proposal
None (about 300 changed lines including tests).
