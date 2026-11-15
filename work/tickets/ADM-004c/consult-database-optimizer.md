# consult-database-optimizer — ADM-004c

**Findings**

1. The current function (`c3f1a9d27b60`) is `CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$`. It is shared by the row trigger (`BEFORE UPDATE OR DELETE`) and the statement trigger (`BEFORE TRUNCATE`). `CREATE OR REPLACE` keeps both trigger bindings, so no trigger DDL is needed.
2. `af3eb84d2d5e` is merged on main (PR #30) but is in no release tag yet (v0.1.2 does not contain it). Alembic runs `downgrade()` from the files on disk, not from anything stored in the DB, and keeps no checksum. Editing that migration's `downgrade()` therefore protects every database, including one already at `af3eb84d2d5e`, as soon as the new code is the one running migrations.
3. A guard in a new no-op migration does not work. Its `downgrade()` can't tell whether the target is `af3eb84d2d5e` (harmless) or below it. It also never runs for a DB that is still at `af3eb84d2d5e`.
4. `EmailRetentionService.run` commits per batch but writes one summary row after all batches. A crash mid-run leaves rows deleted with no audit row.
5. `_check_keys` does not reject `cutoff` or `deleted`, so a flat `{"cutoff": "<iso>", "deleted": n}` passes without a new exemption.

**Recommendations**

- **New migration (revises `af3eb84d2d5e`), `upgrade()`:**
  ```sql
  CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN
    IF TG_OP = 'DELETE' THEN
      IF current_setting('audit.allow_purge', true) = 'on'
         AND OLD.occurred_at < now() - interval '90 days' THEN
        RETURN OLD;
      END IF;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only';
  END $$
  ```
  - Use a nested `IF` on `TG_OP`. SQL does not guarantee that `AND` short-circuits, and `OLD` is NULL in the TRUNCATE statement trigger.
  - It must be `RETURN OLD`. `RETURN NULL` silently skips the row: rowcount 0, the purge loop stops, and nothing raises.
  - `current_setting(..., true)` returns NULL if the setting was never set, and `''` after a `SET LOCAL` has ended. Neither equals `'on'`.
- **`downgrade()`:** `CREATE OR REPLACE` with the original text copied byte for byte into a module constant (migrations must not import `app`). The test asserts `prosrc` equals `" BEGIN RAISE EXCEPTION 'audit_log is append-only'; END "` after a down step, and that DELETE with the flag raises again.
- **`now()`, not `clock_timestamp()`:** `now()` is the transaction start time, the same value the purge predicate sees. If the CLI predicate holds, so does the trigger check, and tests are deterministic. A long-open transaction only makes `now()` older, which is stricter. `clock_timestamp()` drifts within the batch and adds nothing.
- **Cutoff:** compute it once on the DB clock, as `SELECT now() - make_interval(days => :n)`. Pass that bound `timestamptz` to every batch, the dry run and every summary row. Later batches have a larger `now()`, so the floor still holds. The repository re-checks `n >= 90` (raising `ValueError`, like `EmailRetentionRepository._check`) in addition to the CLI check.
- **Per batch transaction, in this order:**
  1. `SET LOCAL lock_timeout = '2s'`
  2. `SET LOCAL audit.allow_purge = 'on'` (a literal; SET takes no bind parameters)
  3. The batch delete:
     ```sql
     DELETE FROM audit_log WHERE id IN (
       SELECT id FROM audit_log WHERE occurred_at < :cutoff
       ORDER BY occurred_at, id LIMIT :n FOR UPDATE SKIP LOCKED)
     ```
     This uses `ix_audit_log_occurred_at_id`; keep `synchronize_session=False`.
  4. `SET LOCAL audit.allow_purge = 'off'` (defence in depth)
  5. If deleted > 0, `AuditLogger.record(AUDIT_LOG_PURGED, actor=SYSTEM_CLI, ctx=NONE, changes={"cutoff": iso, "deleted": n})`
  6. COMMIT

  Stop when deleted < batch. The summary row has `occurred_at = now()`, so it is never inside the cutoff. A failed summary insert rolls back that batch's delete (R4).
- **Dry run:** `SET TRANSACTION READ ONLY`, compute the cutoff, `SELECT count(*) ... WHERE occurred_at < :cutoff` (an index-only scan), then ROLLBACK. Never set the flag. Optionally print min/max `occurred_at` of the affected rows.
- **Downgrade guard in `af3eb84d2d5e`:** edit that file's `downgrade()` directly (finding 2). Before dropping anything, run `op.get_bind().scalar(text("SELECT EXISTS (SELECT 1 FROM audit_log WHERE outcome IN ('failure','denied'))"))` and raise `RuntimeError` if it is true. Use `IN (...)`, not `<> 'success'`, so the query matches `ix_audit_log_outcome`'s predicate; the planner does not use the CHECK for implication. Add `af3eb84d2d5e` to deploy.md's "never downgrade production past" line, with a test that downgrade refuses when such a row exists. The new function migration itself can be downgraded safely, since that only makes the table stricter.
- **Email cleanup:** move `record` inside the loop. After each `purge`, if `deleted > 0`, record `EMAIL_RETENTION_PURGED` with `{table: change(None, deleted)}` and then commit. That keeps `_ALLOWED_KEYS`/`_is_count` unchanged. Tests that expect one row per run must change.

**Risks**

- The flag is not a privilege boundary. Any SQL running as the app role can set it, and the owner role can `DROP TRIGGER` anyway. The 90-day floor is the real protection until the runtime and migration DB roles are split.
- In tests, with `join_transaction_mode="create_savepoint"`, `SET LOCAL` lasts until the outer test transaction ends. A test that purges and then asserts "DELETE without flag raises" in the same test will wrongly pass the DELETE. Reset the flag with `SET LOCAL ... = 'off'`, or use separate tests. Expected trigger errors also need a nested savepoint per statement.
- The purge eventually deletes old `audit_log.purged` and `email_retention.purged` summary rows, which erases the purge history itself.
- Before calling, the batch loop must check that a row with a recent `occurred_at` cannot match. Rows are inserted with `server_default now()`, but the ORM column could still be given an explicit value. Only add rows through the service, which leaves `occurred_at` unset.
- `interval '90 days'` follows the session TimeZone's DST rules, which is within the margin of error. Use `interval '2160 hours'` if you want it exact.
- A large first purge leaves dead tuples and index bloat. Autovacuum handles it; a manual `VACUUM audit_log` is an ops note, not code.

**Open questions**

1. Should `action = 'audit_log.purged'` rows be excluded from the purge, or kept for longer?
2. Should a run that deletes 0 rows write nothing (ADR-0009 §3: no-ops record nothing)? That would leave no trace that the run happened.
3. Should Q6/Decree 13 allow a per-class retention (anonymous `auth.login_failed` rows shorter)? The hard-coded 90-day floor would then need revisiting in a later migration.
