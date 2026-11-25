# Consult — database-optimizer — ADM-045 (2026-11-25)

**Findings**
- `usage_events` (app/modules/usage/models.py:159-215) has `ix_usage_events_created_at` (:190), so `WHERE created_at < cutoff` is an index range scan. The docstring (:160) already says "Purged after 90 days (ADM-045)".
- `usage_counters` (:125-156) has no FK to events. It is keyed by (user, metric, period, period_start) and "kept indefinitely (OQ69)". A DELETE on events cannot touch it. The only inbound FK on events goes the other way (events -> users/metrics).
- Settle and refund read an event by (app_id, idempotency_key) (meter_repository.py:130-134). They are gated by `usage_adjust_window_seconds`, default 86400, `ge=60` and no upper bound (config.py:141; meter_service.py:202-206). Once an event is past the window, settle and refund return 409 and never read the counter. Purging events older than 90 days is therefore safe for counters. The orphan concern from the ADM-042 security review does not apply as long as retention is far larger than the window.
- Replay of a purged key: the consume insert uses ON CONFLICT DO NOTHING on `uq_usage_events_app_id_idempotency_key` (meter_repository.py:116). After purge the key is new, so a late replay would consume again. ADR-0022 sets a minimum idempotency retention of 90 days, so this is accepted. A settle or refund with a purged key gets 404, or whatever the not-found path returns, instead of window-closed. Either way nothing is adjusted.
- Existing patterns to copy: `audit/purge.py` (BATCH_SIZE 1000 at :24, `.limit(batch)` at :67), `auth/retention.py` (one commit per batch, summary audit row, counts only) and `cli.py` (`audit-purge` at :15-17, :762-793, dry-run default via `--dry-run`).

**Recommendations**
1. Add `app/modules/usage/retention.py` with a service and a repo method. Count with `SELECT count(*) WHERE created_at < :cutoff`. Delete with `DELETE FROM usage_events WHERE id IN (SELECT id ... WHERE created_at < :cutoff ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED)`. Use `ORDER BY created_at` so the created_at index is used. `SKIP LOCKED` means a row held by a concurrent consume, settle or refund is skipped, not waited on. Use ctid batches only if the plan shows trouble.
2. Commit once per batch, as the existing purges do. Loop until a batch returns fewer than n rows, with a small sleep or a max-batches cap.
3. Compute the cutoff once, from `now() - retention`, before the loop. Rows that age past it during the run are caught on the next run.
4. Add `usage_event_retention_days: int = Field(default=90, ge=90)` in config.py. Add a validator that retention in seconds is at least `usage_adjust_window_seconds`, copying the join-retention validator at config.py:266. Add the key to `.env.example`.
5. CLI: `python -m app.cli usage-events-purge [--apply]`. Default is dry-run, per the ticket. The `--dry-run` flag on audit-purge is a different shape, so the ticket and plan must pick one. Output is counts and the cutoff only. Never print keys or emails (R11).
6. Hot consume path: the batch DELETE takes row locks only on events older than 90 days. New consumes insert fresh rows, so they do not conflict. Index maintenance is the only shared cost, and batches of about 1000 keep it small. No table lock.
7. Tests:
   - 91-day-old and 89-day-old events: only the old one is deleted.
   - Counters are unchanged after a purge.
   - Dry-run deletes nothing.
   - A run that spans several batches (batch_size=2).
   - A consume with a purged key succeeds as new.
   - A config below the floor is rejected.
   - A locked row is skipped.

**Risks**
- Without SKIP LOCKED and a short transaction, a long purge delays settle or refund on old rows. These are already outside the window, so impact is low.
- Table bloat after a big first purge. Autovacuum handles it. Do not run VACUUM FULL.
- The `(user_id, metric_id, created_at)` index and the created_at index both churn during the purge, so keep batches modest.

**Open questions for Ming**
1. Should the purge write an `audit_log` summary row per batch, like audit-purge and email retention (R4)? Default: yes, counts only.
2. Should the purge be scheduled, or run manually or from cron? Default: CLI only, scheduling is out of scope.
3. Is a late replay or consume with a purged key (past 90 days) acceptable? Default: yes, per ADR-0022.
4. Is the retention env-configurable with a 90-day floor? Default: yes.
