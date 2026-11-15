# consult-database-optimizer — ADM-004b

**Findings**

1. `audit_log` has a `BEFORE UPDATE OR DELETE` row trigger and a TRUNCATE trigger (`c3f1a9d27b60`). A data backfill (`UPDATE audit_log SET outcome=…`) would raise `audit_log is append-only`. DDL (`ADD/DROP COLUMN`, `ADD CONSTRAINT`, `CREATE INDEX`) does not fire the trigger.
2. On PG17, `ADD COLUMN outcome text NOT NULL DEFAULT 'success'` changes metadata only. Existing rows read `'success'` without being rewritten, so no backfill is needed.
3. `migrations/env.py` sets `compare_type=True` but not `compare_server_default`. `alembic check` will not catch a model/migration mismatch in the default, and it never compares CHECKs.
4. Request sessions use `SessionLocal(expire_on_commit=False, autoflush=False)`. `rollback()` always expires every loaded instance, whatever `expire_on_commit` says. A later attribute access triggers a lazy load, which raises `MissingGreenlet` under async. `tests/test_session_scope.py:59` already works around this.
5. Test harness: `join_transaction_mode="create_savepoint"`. In tests, `session.commit()` is `RELEASE SAVEPOINT` and `session.rollback()` is `ROLLBACK TO SAVEPOINT`, and both end the session's current savepoint. Factories (`tests/auth_factories.py`, `rbac_factories.py`) only flush. So in `record_failure`, the `rollback()` would wipe uncommitted test setup (users, roles, the admin principal's rows) along with any partial work.
6. Login: `failures.commit()` runs in phase 2, before the password check, so the outcome is not known at that commit. The ticket's rule "add the row before that commit" cannot apply to login. The failure happens after it, with no later commit.
7. `resolve()` reuse path: `revoke_device` → `commit` → raise. In `start_for_login` (`commit_on_reuse=False`) the revoke is part of the successful login transaction.

**Recommendations**

- **Migration (R2):**
  - `op.add_column("audit_log", sa.Column("outcome", sa.Text(), server_default=sa.text("'success'"), nullable=False))`.
  - `CHECK (outcome IN ('success','failure','denied'))` named `ck_audit_log_outcome_valid`, with the same text in `models.py`. The table is small, so a plain `ADD CONSTRAINT` is fine; `NOT VALID`+`VALIDATE` is not needed.
  - No UPDATE anywhere.
  - Model: `outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'success'"))`. Check it by hand, since autogenerate won't (finding 3).
- **Index:** add a partial `ix_audit_log_outcome (outcome, occurred_at, id) WHERE outcome IN ('failure','denied')`, declared identically in the model. Success rows are most of the table and are already served by `ix_audit_log_occurred_at_id`. Add a test or EXPLAIN check that `outcome='denied'` uses it; the planner proves `= const` implies `IN (...)`. `action=auth.login_failed` queries already use `ix_audit_log_action`.
- **Downgrade:** `drop_index` then `drop_column("audit_log","outcome")`. The CHECK goes with the column, and no other rows or data are touched.
- **`record_failure` (ADR-0009 §7):**
  - Take plain values only: ids, `AuditContext`, an outcome enum. Never ORM objects, because the rollback expires them.
  - Sequence: `try: await session.rollback(); session.add(row); await session.flush(); await session.commit()`, then `except Exception: logger.warning("audit_write_failed action=%s", action); try: await session.rollback() except Exception: pass`.
  - Never catch `BaseException`, so cancellation still propagates.
  - The caller raises afterwards. `get_session`'s own rollback is then a harmless no-op.
- **Tests:** denied-path and login tests must `await db_session.commit()` their setup first (the `make_strict_client` convention), or the rollback erases it. Add a fault-injection test that monkeypatches the repository `add` to raise and asserts the intended 401/403/409 comes back with no 500.
- **Login:** call `record_failure` after the failed check, once the reservation commit is done.
  - Its rollback then only discards the `FOR SHARE` read and releases that lock, which is fine.
  - Unknown email → `actor_kind='anonymous'`, no target, no email/HMAC.
  - Success → plain `record()` in the login transaction.
- **Reuse (`commit_on_reuse=True`):** add the row inside `async with session.begin_nested():` before `repo.commit()`, and catch/log around that block. A failed audit insert then rolls back only its savepoint, and the revocation still commits. Do not use `record_failure` here: its rollback would undo `revoke_device`. When reuse is detected inside login (`commit_on_reuse=False`), write a normal `record()` in the login transaction.
- **Retention:** use the GUC bypass for v1, not partitioning.
  - Replace `audit_log_immutable()` with a version that refuses UPDATE and TRUNCATE always. It allows DELETE only when `current_setting('audit.purge', true) = 'on'` and `OLD.occurred_at < now() - interval '<floor>'`. The floor is a DB-enforced minimum from Q6, so even a misused bypass cannot erase recent evidence.
  - The purge runs only from `app/cli.py`. It does `SET LOCAL audit.purge = 'on'` in each batch transaction (like `email_retention`: `lock_timeout`, `FOR UPDATE SKIP LOCKED`, batch on `ix_audit_log_occurred_at_id`), then writes one `audit_log.purged` summary row with integer counts.
  - Keep the bypass statement in a CLI-only repository class that no `app/modules/*/router.py` or service imports.
  - Add a test that greps `app/` for `audit.purge` outside that file and the migration, and a test that a DELETE without the GUC (and with the GUC but a recent row) still raises.
  - The migration that replaces the function gets a downgrade that restores the original function text.
- **Partitioning:** reject for v1. The PK would have to become `(id, occurred_at)`, the table would need copying, `alembic check` sees partitions as drift, and monthly partitions need a creation job on the shared Postgres.

**Risks**

- The GUC is not a privilege boundary: any SQL on the app role can set it. The real boundary is the role split noted in ADR-0009 §1 (runtime role without DELETE). Until then, the DB-enforced age floor is the main protection.
- Unknown-email `login_failed` rows are attacker-driven volume, bounded only by the ~20/min per-IP limit. They suggest a shorter retention for `actor_kind='anonymous'`.
- Some `record_failure` call sites (guards, `last_super_admin`) may run after writes the caller still expects, such as reservation-style counters. The rollback discards those writes. Each call site needs auditing.
- After a production downgrade, failure rows would read as successes; downgrade is already forbidden there.
- The existing `test_denied_guard_writes_no_row` must be inverted.

**Open questions**

1. Q6: what is the retention period, and is there one period or one per class (admin mutations vs. anonymous login failures)? This sets the trigger floor and the purge predicate.
2. Is the runtime/migration DB role split scheduled? It decides whether the GUC bypass is acceptable long-term.
3. Should a failure row go on the request session if the guard ran after a `FOR UPDATE` lock? (Rollback releases it first, which is the intended behaviour; please confirm.)
