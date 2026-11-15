# consult-database-optimizer — ADM-004d

**Findings**
- The new migration (`down_revision="d7e4b2a91c3f"`) only replaces `audit_log_immutable()`. Both existing triggers (`audit_log_no_update_delete`, `audit_log_no_truncate`) stay bound to it by OID, so nothing needs to be re-created. `action` is NOT NULL, so `<>` has no NULL gap.
- Tests that create backdated rows with an explicit `occurred_at`, all of which the new INSERT trigger would break:
  - `tests/test_cli_audit_purge.py::_aged`, including a 400-day `audit_log.purged` trail row at :154
  - `tests/test_audit_schema.py::AGED`, used by the floor and boundary tests
  - `tests/test_audit_migrations.py::_flagged_delete_refused`, which uses its own engine
  - `tests/test_audit_logs_api.py::_add`. It uses ORM `occurred_at=T0+…` with a fixed 2026-11-01 for the ordering, tie and `from`/`to` filter tests. With the trigger, the ORM object keeps T0 in Python while the DB stores `now()`, so the expected orders silently diverge.
- `_migrate_db` runs `downgrade base` against committed rows, from `auth.login_failed` and the concurrency tests. A new row guard in `c3f1a9d27b60` would refuse every test session after the first.
- `env.py` runs the whole `downgrade base` in one transaction. A refusal in `c3f1a9d27b60` therefore rolls back the earlier down-steps too, which is good.

**Recommendations**
1. **Purge-trail guard (AC: purge-trail DELETE refused even with the flag).**
   - Upgrade:
     ```
     CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN IF current_setting('audit.allow_purge', true) = 'on' AND OLD.occurred_at < now() - interval '90 days' AND OLD.action <> 'audit_log.purged' THEN RETURN OLD; END IF; END IF; RAISE EXCEPTION 'audit_log is append-only'; END $$
     ```
   - Downgrade: copy `d7e4b2a91c3f.PURGE_FUNCTION` exactly, as a string literal. Do not import it: migrations must stay self-contained.
   - Tests:
     - After down to d7e4, compare `prosrc` with the body of `_load("d7e4b2a91c3f").PURGE_FUNCTION`.
     - Update `test_purge_migration_up_down_up`'s trigger set.
     - Add: a 91-day-old `audit_log.purged` row with the flag on is refused.
   - Keep `_purgeable()` as it is. It still keeps the trail rows out of the count.
2. **INSERT stamp (AC: inserted `occurred_at` overridden).**
   - Use a separate function, `audit_log_stamp_occurred_at()` (`BEGIN NEW.occurred_at := now(); RETURN NEW; END`), with `BEFORE INSERT … FOR EACH ROW`. Do not reuse the immutable one: it would need TG_OP branching, and that would complicate the byte-exact downgrade chain.
   - Use `now()`, not `clock_timestamp()`. This keeps ADR-0009's "one transaction shares `occurred_at`" and the "summary row is never inside the cutoff" argument.
   - Keep `server_default=now()`: removing it causes `alembic check` drift, and it stays as documentation.
   - Downgrade: `DROP TRIGGER`, then `DROP FUNCTION`.
   - Test: insert with `occurred_at = now() - interval '400 days'`, then read it back and assert it equals `now()`.
3. **Test-only backdating.**
   - Add an opt-in to `db_session`, for example `@pytest.mark.backdate_audit`. It runs `ALTER TABLE audit_log DISABLE TRIGGER audit_log_stamp_occurred_at` on `conn` right after `trans = await conn.begin()`, before the `AsyncSession` exists.
   - It must not go inside the session: in `create_savepoint` mode, a `session.rollback()` would undo it (`test_purge_summary_failure_rolls_back_batch`). The outer rollback re-enables the trigger.
   - `_flagged_delete_refused` does the same on its own connection.
   - Why this does not weaken production: it needs table ownership, the same power as `DROP TRIGGER`, which already defeats everything (ADR-0009). So it adds no new bypass.
   - Reject a GUC such as `audit.allow_backdate`: any role can set it (security-review finding 3).
   - Reject `session_replication_role=replica`: it needs a superuser and also turns off the append-only trigger.
4. **`c3f1a9d27b60.downgrade()`.**
   - Add a module-level `_refuse_if_rows(bind)` (`SELECT EXISTS (SELECT 1 FROM audit_log)`).
   - Order in `downgrade()`: refuse offline first, then the guard unless the attribute is set, then the existing drops. Changing `downgrade()` of an applied revision is safe; `upgrade()` stays unchanged.
   - Use a **new** attribute, `allow_audit_log_loss`, rather than reusing `allow_outcome_loss`. The two guards protect different losses, and one name per guard keeps the tests honest. Set it next to the existing one in `_migrate_db`.
   - Tests:
     - The guard refuses with a row present (`db_session` + `run_sync`).
     - `_CleanBind` passes.
     - `command.downgrade(cfg, "c3f1a9d27b60:5e2a9c7d1f04", sql=True)` raises "offline".

**Risks**
- `DISABLE TRIGGER` takes ACCESS EXCLUSIVE until the test ends. A marked test that opens a second connection touching `audit_log` hangs. Today none of the backdating tests do; `test_dry_run_transaction_is_read_only` has its own engine and must not be marked.
- `test_audit_logs_api` needs the marker. Without it, it fails in confusing ways rather than loudly.
- The new migration's downgrade loosens the table (backdating, purgeable trail). `docs/deploy.md` should list it the way it lists d7e4: "may be downgraded, but it drops the DB guards", or forbid it in production.
- The trigger functions inherit the caller's `search_path`. A role that can CREATE in a schema listed before `pg_catalog` could shadow `now()`. This is low risk while the app role is the owner anyway. Optional cheap hardening on both functions: `SET search_path = pg_catalog, pg_temp`. Note that `CREATE OR REPLACE` resets `proconfig`, so the downgrade stays exact. If you take it, record it as a choice inside the ticket scope (R7).

**Open questions**
1. Should the new migration's downgrade also refuse offline or when rows exist, or is "loosening allowed, documented" enough?
2. Should the `search_path` pinning go into this ticket or become a follow-up?
3. Should `c3f1a9d27b60`'s guard also count rows that the trail keeps, or every row? I recommend any row.
