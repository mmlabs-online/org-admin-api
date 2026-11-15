Status: READY

# Plan — ADM-004d: Audit log DB hardening

## Spec summary
- Spec §5.4: "Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors."
- Spec §2: "A complete, queryable audit log of admin actions."
- Spec Q6 (open): "Compliance needs (audit retention period, PII handling)?" This ticket does not change retention. It makes the existing ADR-0009 promises ("`audit_log.purged` rows are never purged") hold in the database.
- Ticket scope items 1–6 (from the ADM-004c reviews) are the whole scope. Nothing else is added.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A purge-trail DELETE is refused even with the flag (scope 1) | `tests/test_audit_schema.py::test_purge_flag_refuses_purge_trail_row`: a 400-day-old `audit_log.purged` row, `SET LOCAL audit.allow_purge='on'`, then DELETE raises "audit_log is append-only" |
| 2 | An inserted `occurred_at` is overridden with `now()` (scope 2) | `tests/test_audit_schema.py::test_insert_occurred_at_stamped`: raw INSERT with `occurred_at = '2020-01-01'` reads back equal to `SELECT now()` (same transaction). Not marked. |
| 3 | `c3f1a9d27b60.downgrade()` refuses while rows exist and in offline mode (scope 3) | `tests/test_audit_migrations.py::test_audit_log_downgrade_refuses_with_rows`, `::test_audit_log_downgrade_passes_on_clean_table`, `::test_audit_log_downgrade_refuses_offline` |
| 4 | The new migration's downgrade restores `d7e4b2a91c3f`'s function byte for byte and drops the stamp trigger and function | `tests/test_audit_migrations.py::test_guards_migration_down_restores_purge_function` (`prosrc` equals the body of `_load("d7e4b2a91c3f").PURGE_FUNCTION`; trigger set is the two originals; no `audit_log_stamp_occurred_at` in `pg_proc`), plus `::test_guards_migration_previous_function_matches_d7e4` (static string equality) |
| 5 | Migration up/down/up is clean | `test_purge_migration_up_down_up` (updated: three triggers at head, `audit_log.purged` in `prosrc`); session fixture `_migrate_db` does `downgrade base` + `upgrade head`; `alembic check` in `make check`/CI shows no drift |
| 6 | `docs/deploy.md` says any DB role can set the flag and never to put `-c audit.allow_purge=on` / `options` in `DATABASE_URL` (scope 4) | Reviewer reads the Audit log and Audit purge sections of `docs/deploy.md` |
| 7 | Unicode bidi / line-separator / invisible format characters are stripped from `user_agent` (scope 5) | `tests/test_audit_logger.py::test_user_agent_control_chars_stripped` (extended parametrization) and `::test_user_agent_normal_chrome_unchanged` |
| 8 | `SKIP LOCKED` comment in the purge batch loop (scope 6) | Reviewer reads `app/modules/audit/purge.py` at the `if deleted < self.batch_size` exit |
| 9 | `make check` green | `make check` (ruff, format, mypy --strict, pytest), plus `alembic check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261115_e91b6c3d58a2_audit_log_db_guards.py` | **New** migration, `revision="e91b6c3d58a2"`, `down_revision="d7e4b2a91c3f"`. Upgrade: replace `audit_log_immutable()` with the purge-trail exception, create `audit_log_stamp_occurred_at()` and its `BEFORE INSERT` trigger. Downgrade: drop the trigger and function, restore d7e4's function from a literal | Scope 1, 2 (R2: schema change ships with a hand-reviewed migration and a working `downgrade()`) |
| `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py` | `downgrade()`: refuse offline, then refuse while any row exists unless `context.config.attributes["allow_audit_log_loss"]`. Add module-level `_refuse_if_rows(bind)` and the message constant. `upgrade()` unchanged. Docstring line updated | Scope 3 |
| `app/modules/audit/service.py` | Extend `_CONTROL_CHARS` with the explicit format-character class, plus a one-line comment | Scope 5 |
| `app/modules/audit/purge.py` | Comment only, at the `if deleted < self.batch_size:` exit (about line 121) | Scope 6 |
| `tests/conftest.py` | `_migrate_db`: also set `cfg.attributes["allow_audit_log_loss"] = True`. `db_session`: take `request`, and when the test has the `backdate_audit` marker, run `ALTER TABLE audit_log DISABLE TRIGGER audit_log_stamp_occurred_at` on `conn` right after `trans = await conn.begin()`, before the `AsyncSession`. Add `pytest_configure` to register the marker | Scope 2 and 3 (test-only bypass and test-session bypass) |
| `tests/test_audit_schema.py` | Mark the 7 `_aged` tests `@pytest.mark.backdate_audit`. Add `test_purge_flag_refuses_purge_trail_row` (marked) and `test_insert_occurred_at_stamped` (not marked) | AC 1, 2 |
| `tests/test_audit_logs_api.py` | Module-level `pytestmark = pytest.mark.backdate_audit` (`_add` sets `occurred_at=T0+…`) | Stamp trigger would otherwise silently break the ordering and filter tests |
| `tests/test_cli_audit_purge.py` | Module-level `pytestmark = pytest.mark.backdate_audit` (`_aged` inserts backdated rows) | Stamp trigger would otherwise make every row unpurgeable |
| `tests/test_audit_migrations.py` | Update `test_purge_migration_up_down_up`. Add the new-migration down test, the static literal test, and the three `c3f1a9d27b60` guard tests | AC 3, 4, 5 |
| `tests/test_audit_purge_guard.py` | `test_allow_purge_only_in_purge_module_and_migration`: expected set gains the new migration. `test_min_days_matches_migration_floor`: check every `interval '(\d+) days'` in both migrations equals `MIN_DAYS` | The new migration contains `allow_purge` and the floor, so the existing static guards would fail or go stale |
| `tests/test_audit_logger.py` | Extend `test_user_agent_control_chars_stripped` parametrization. Add `test_user_agent_normal_chrome_unchanged` | AC 7 |
| `docs/deploy.md` | Audit log and Audit purge sections: the wording below | Scope 4 and the restore guidance from the security consult |
| `docs/adr/0009-*.md` | Add a short amendment "DB guards (ADM-004d, 2026-11-15)" | Decision record: both new DB guards and the latch-wording correction |

No other file. `_purgeable()` in `purge.py` stays unchanged: it still keeps trail rows out of the count (database-optimizer, recommendation 1).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_audit_schema.py › test_purge_flag_refuses_purge_trail_row` (marked) | A 400-day-old row with `action='audit_log.purged'`, `actor_kind='system'`, `actor_label='system:cli'`. DELETE with the flag on raises "audit_log is append-only" (via `_refused_with_flag`) |
| `test_audit_schema.py › test_insert_occurred_at_stamped` (**not** marked) | Raw INSERT with `occurred_at = TIMESTAMPTZ '2020-01-01 00:00:00+00'`; the stored value `== await db_session.scalar(text("SELECT now()"))`. Also proves the unmarked fixture keeps the trigger enabled |
| `test_audit_schema.py` the 7 existing `_aged` tests (marked) | Floor, boundary, flag values and UPDATE/TRUNCATE-with-flag tests still pass with backdated rows |
| `test_audit_migrations.py › test_guards_migration_down_restores_purge_function` | `command.downgrade(cfg, "d7e4b2a91c3f")`, then: `prosrc == _load("d7e4b2a91c3f").PURGE_FUNCTION.split("$$")[1]`; triggers `== {"audit_log_no_update_delete", "audit_log_no_truncate"}`; no `pg_proc` row named `audit_log_stamp_occurred_at`. `finally: command.upgrade(cfg, "head")` |
| `test_audit_migrations.py › test_guards_migration_previous_function_matches_d7e4` | `_load("e91b6c3d58a2").PREVIOUS_FUNCTION == _load("d7e4b2a91c3f").PURGE_FUNCTION` (catches drift of the copied literal) |
| `test_audit_migrations.py › test_purge_migration_up_down_up` (update) | After `downgrade -1` and `upgrade head`: `"audit_log.purged" in prosrc` and the trigger set includes `audit_log_stamp_occurred_at` (three triggers) |
| `test_audit_migrations.py › test_audit_log_downgrade_refuses_with_rows` | `db_session` inserts a row; `conn.run_sync(_load("c3f1a9d27b60")._refuse_if_rows)` raises `RuntimeError` matching "audit_log has rows" |
| `test_audit_migrations.py › test_audit_log_downgrade_passes_on_clean_table` | `_refuse_if_rows(_CleanBind())` does not raise (reuse the existing `_CleanBind`) |
| `test_audit_migrations.py › test_audit_log_downgrade_refuses_offline` | `command.downgrade(_cfg(), "c3f1a9d27b60:5e2a9c7d1f04", sql=True)` raises `RuntimeError` matching "offline" |
| `test_audit_purge_guard.py` (2 updated) | `allow_purge` confinement includes the new migration; the floor in both migrations equals `MIN_DAYS` |
| `test_audit_logger.py › test_user_agent_control_chars_stripped` (extended) | New cases: `"a‮b"→"ab"`, `"a b c"→"abc"`, `"x​y"→"xy"`, `"﻿UA"→"UA"`, `"so­ft"→"soft"`, `"a؜b᠎c"→"abc"`, `"⁦x⁩"→"x"`, `"a⁠⁤b"→"ab"`, `"a￹b"→"ab"`, `"a\U000e0041b"→"ab"`, `"‮"→None` |
| `test_audit_logger.py › test_user_agent_normal_chrome_unchanged` | `"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"` is stored unchanged |

## Approach
1. **New migration `e91b6c3d58a2`** (hand-written; `alembic check` cannot see functions or triggers). Module constants:
   - `GUARD_FUNCTION`: `CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN IF current_setting('audit.allow_purge', true) = 'on' AND OLD.occurred_at < now() - interval '90 days' AND OLD.action <> 'audit_log.purged' THEN RETURN OLD; END IF; END IF; RAISE EXCEPTION 'audit_log is append-only'; END $$`. Write it in the same string-concatenation style as d7e4.
   - `STAMP_FUNCTION`: `CREATE FUNCTION audit_log_stamp_occurred_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.occurred_at := now(); RETURN NEW; END $$`.
   - `STAMP_TRIGGER`: `CREATE TRIGGER audit_log_stamp_occurred_at BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_stamp_occurred_at()`.
   - `PREVIOUS_FUNCTION`: a verbatim **copy** of `d7e4b2a91c3f.PURGE_FUNCTION`. Do not import it: migrations stay self-contained.
   - `upgrade()`: `GUARD_FUNCTION`, `STAMP_FUNCTION`, `STAMP_TRIGGER`. The existing triggers keep their OID binding.
   - `downgrade()`: `DROP TRIGGER audit_log_stamp_occurred_at ON audit_log`, `DROP FUNCTION audit_log_stamp_occurred_at()`, then `PREVIOUS_FUNCTION`.
   - Use `now()`, not `clock_timestamp()`. This keeps ADR-0009's "rows of one transaction share `occurred_at`" and the floor's clock.
   - `action` is NOT NULL, so `<>` has no NULL gap.
   - Keep the `occurred_at` `server_default` (removing it would cause `alembic check` drift).
   - The docstring states the downgrade loosens the table, and that production does not downgrade past it (see step 6).
2. **`c3f1a9d27b60.downgrade()`**. Mirror `af3eb84d2d5e`:
   - Add `from alembic import context, op`.
   - Add `AUDIT_LOG_LOSS = "downgrade of c3f1a9d27b60 is refused: audit_log has rows (it would destroy the audit log)"`.
   - Add `_refuse_if_rows(bind)`, which runs `SELECT EXISTS (SELECT 1 FROM audit_log)` and raises `RuntimeError(AUDIT_LOG_LOSS)`.
   - In `downgrade()`: first, if `context.is_offline_mode()`, raise `RuntimeError("offline downgrade of c3f1a9d27b60 is refused: it cannot check rows")`. Then, if `allow_audit_log_loss` is not set, call `_refuse_if_rows(op.get_bind())`. Then run the existing drops unchanged.
   - The guard counts **any** row, including the trail rows.
   - Changing `downgrade()` of an applied revision is safe: `upgrade()` and the schema are unchanged.
   - `env.py` runs `downgrade base` in one transaction, so a refusal also rolls back the earlier down-steps.
3. **Test-only backdating.** In `db_session`, the marker runs `ALTER TABLE audit_log DISABLE TRIGGER audit_log_stamp_occurred_at` on `conn` right after `conn.begin()`, before the `AsyncSession` exists.
   - It must not run inside the session: `create_savepoint` mode would undo it on `session.rollback()` (`test_purge_summary_failure_rolls_back_batch`).
   - The outer `trans.rollback()` re-enables the trigger, so nothing is committed.
   - Rejected alternatives: no GUC bypass, because any role can set one (the ADM-004c latch lesson); no `session_replication_role`, because it needs a superuser and disables the append-only trigger too.
   - This adds no production bypass: `DISABLE TRIGGER` needs table ownership, the same power as `DROP TRIGGER` (ADR-0009). The static guard `test_no_trigger_bypass_in_app` still forbids it in `app/`, and tests are outside that scan, so no change is needed there.
   - Tests that must use it:
     - `test_audit_logs_api.py` (whole module)
     - `test_cli_audit_purge.py` (whole module; `_aged`, including the 400-day trail row)
     - `test_audit_schema.py`: the 7 `AGED` tests and the new trail test
   - **`test_audit_migrations.py::_flagged_delete_refused` needs no change, contrary to the brief.** It runs only after `downgrade af3eb84d2d5e`, where the stamp trigger no longer exists, so `DISABLE TRIGGER` there would fail with "trigger does not exist". Leave it as is; no other own-engine test backdates rows.
   - ⚠ **ACCESS EXCLUSIVE.** `DISABLE TRIGGER` holds an ACCESS EXCLUSIVE lock on `audit_log` until the test's rollback. A marked test that opens a second connection touching `audit_log` hangs.
     - None of the marked tests do this today. `test_dry_run_transaction_is_read_only` uses its own engine but does not request `db_session`, so the module mark does nothing for it. It runs after the previous test's rollback (pytest is serial here).
     - Never mark the concurrency tests (`test_login_concurrency`, `test_email_lock_concurrency`, `test_rbac_locking`) or any test that commits on a second connection.
     - Write this warning in the fixture's docstring.
4. **User-agent strip** (`service.py`). Extend the regex to `[\x00-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁤⁦-⁯﻿￹-￻\U000e0000-\U000e007f]`. It covers, in order: C0/C1/DEL, soft hyphen, ALM, Mongolian vowel separator, zero-width characters and LRM/RLM, LS/PS and the bidi embeddings and overrides, word joiner and invisible operators, bidi isolates and deprecated format characters, BOM, interlinear annotation, and the tags block.
   - Use an explicit list, not category `Cf`, so the result is reviewable and does not depend on the Unicode version.
   - Add a comment noting that Starlette decodes headers as latin-1, so over HTTP this mostly matters for U+00AD and for non-HTTP callers.
   - Truncation stays after the strip.
   - `sessions/service.py` `user_agent` is out of scope and becomes a follow-up (R7).
5. **`purge.py` comment**, at the short-batch exit: `# A short batch can also mean FOR UPDATE SKIP LOCKED skipped rows another transaction holds: the run then ends early. Re-running is safe (fresh cutoff, same floor).`
6. **`docs/deploy.md`.**
   - Audit log section, first bullet: add that since ADM-004d the DB also stamps `occurred_at := now()` on every INSERT (no backdated rows), and refuses to delete `audit_log.purged` rows even under the purge latch.
   - Never-downgrade bullet:
     - Add `e91b6c3d58a2`. It may be downgraded in dev/test, but **not in production**: it drops the purge-trail and insert-stamp guards.
     - Note that `c3f1a9d27b60`'s `downgrade()` now also refuses offline and while any row exists.
     - Reword the d7e4 sentence so it no longer implies that d7e4 can be reached safely in production. d7e4's own downgrade is still stricter, but getting there passes through `e91b6c3d58a2`.
   - New bullet **Restore**: "Restore audit data only as a full restore into an empty database. pg_dump creates triggers after the data, so timestamps survive, and the deployment repo's `RESTORE.md` already does `DROP DATABASE` + full `pg_restore`. A data-only restore into a migrated schema must be `pg_restore --data-only --disable-triggers` run as superuser; otherwise the insert trigger rewrites every `occurred_at` to the restore time. Never set `session_replication_role` on the live database."
   - Audit purge section:
     - Line 673: "(the purge trail is kept forever; enforced by the trigger since ADM-004d)".
     - Line 677: replace "anyone with the DB owner role can set it" with "any DB role can set it (`SET`, `SET LOCAL`, or `options=-c` in a DSN)".
     - Add: "Never put `-c audit.allow_purge=on` (or any `options`) in `DATABASE_URL`: every API transaction would carry the latch."
7. **ADR-0009**: append "## Amendment — DB guards (ADM-004d, 2026-11-15)" with 3 bullets:
   - The trigger refuses DELETE of `audit_log.purged` rows unconditionally.
   - A `BEFORE INSERT` trigger stamps `occurred_at := now()`. It is bypassed only by owner DDL; tests disable it inside a rolled-back transaction.
   - The latch can be set by any DB role, not only the owner (corrects the ADM-004c amendment's wording).
8. Run `make check` and `alembic check`.

Rules:
- **R1**: no layer change.
- **R2**: migration with a working downgrade; no new model; no drift.
- **R4**: unchanged; the summary row is still written in the batch transaction, and its `occurred_at` is now `now()` of that transaction, which is the same as before.
- **R5/R11**: no secrets; the deploy wording forbids putting the latch in `DATABASE_URL`.
- **R6**: no API change.
- **R7**: `search_path` pinning, the trail CHECK and the session user-agent strip go to follow-ups.
- **R8**: tests listed above.

**Estimate.** About 85 production lines (migrations about 65, `service.py` 3, `purge.py` 2, plus docstrings), about 110 test lines and about 20 doc lines. Total about 215 changed lines, well under the split threshold.

## Specialist input used
- **database-optimizer.**
  - Adopted:
    - Recommendation 1 (function text, literal copy for the downgrade, `prosrc` test, `_purgeable()` unchanged).
    - Recommendation 2 (a separate stamp function, `now()`, keep `server_default`).
    - Recommendation 3 (marker on `conn` before the session; no GUC; no `session_replication_role`).
    - Recommendation 4 (`_refuse_if_rows`, offline first, `allow_audit_log_loss`, three tests).
    - The ACCESS EXCLUSIVE risk.
  - Corrected: `_flagged_delete_refused` runs at `af3eb84d2d5e`, where the stamp trigger does not exist, so it must not disable it (see step 3).
  - Added: the static guard tests in `test_audit_purge_guard.py` must include the new migration, because it contains `allow_purge` and the floor.
  - Deferred: `search_path` pinning (see Assumptions).
- **security-architect.**
  - Adopted:
    - Recommendation 1 (absolute trail protection, no floor).
    - Recommendation 2 (stamp trigger, `DISABLE TRIGGER` test bypass, no GUC).
    - Recommendation 3 (offline refusal and row refusal). Attribute name `allow_audit_log_loss` per the orchestrator, instead of the consult's `allow_audit_log_drop`; the purpose is the same.
    - Recommendation 4 (explicit Unicode class, no `Cf`; parametrized test plus a Chrome UA).
    - Recommendation 5 (deploy.md wording, restore bullet, ADR line).
    - Recommendation 6 (`SKIP LOCKED` comment).
  - Restore risk: resolved, since `RESTORE.md` already does a full restore into a dropped and recreated DB (orchestrator), so there is no follow-up in the deployment repo.
  - Trail-row CHECK `action <> 'audit_log.purged' OR actor_kind = 'system'`: follow-up (not in ticket scope).

## Assumptions
Owner decisions recorded as defaults; none blocks the ticket:
1. **The purge trail is never deletable**, with no age floor, even after legal retention ends. It holds no PII (only `system:cli`, cutoff and counts), and deploy.md and ADR-0009 already promise "kept forever". If spec Q6 / Decree 13 later requires otherwise, it takes a new migration.
2. **`e91b6c3d58a2`'s downgrade is allowed** (schema only, no data loss) **but listed in deploy.md as never run in production**, because it loosens the table. It does not refuse offline: it touches no rows.
3. **`c3f1a9d27b60`'s guard counts any row**, trail rows included.
4. **`SET search_path = pg_catalog, pg_temp` on both functions: follow-up, not in this ticket.**
   - It is not in the ticket's scope list (R7, planner rule 1).
   - It buys nothing while the API runs as the table owner: an owner can already drop the trigger.
   - It would make the byte-exact downgrade chain depend on `proconfig` resetting under `CREATE OR REPLACE`, which needs its own test.
   - It belongs with the runtime/migration role split follow-up, where it starts to matter.
5. Test runs are serial (no xdist), so a marked test's ACCESS EXCLUSIVE lock never meets another test's connection.
6. The new revision id `e91b6c3d58a2` is unused (checked). The implementer may regenerate it, but must then keep the filename, the tests and the docs consistent.

Follow-ups (not in the diff):
- Pin `search_path` on the audit trigger functions, together with the runtime/migration role split.
- Add a CHECK so that `audit_log.purged` rows require `actor_kind = 'system'`, which narrows undeletable forged trail rows.
- Apply the same Unicode strip to the device-session `user_agent` (`app/modules/sessions/service.py:151`).
- Existing follow-up: run the API as an INSERT/SELECT-only role on `audit_log`.

## Blocking questions
None.

## Split proposal
None. This is one ticket of about 215 lines with one migration plus a `downgrade()` edit of a shipped one.

## Data model / migration
- **No table or column change.** `occurred_at timestamptz NOT NULL DEFAULT now()` stays as it is.
- **Migration `e91b6c3d58a2`** (revises `d7e4b2a91c3f`).
  - Upgrade:
    - `CREATE OR REPLACE FUNCTION audit_log_immutable()`: the DELETE passes only when the flag is `'on'` AND `OLD.occurred_at < now() - interval '90 days'` AND `OLD.action <> 'audit_log.purged'`; everything else raises "audit_log is append-only". The existing triggers `audit_log_no_update_delete` and `audit_log_no_truncate` stay bound.
    - New function `audit_log_stamp_occurred_at()` (plpgsql: `NEW.occurred_at := now(); RETURN NEW;`).
    - New trigger `audit_log_stamp_occurred_at BEFORE INSERT ON audit_log FOR EACH ROW`.
  - Downgrade:
    - Drop the trigger, then the function.
    - `CREATE OR REPLACE` with `d7e4b2a91c3f.PURGE_FUNCTION`'s exact text, as a copied literal.
    - The downgrade loosens the table; production never runs it (deploy.md).
- **`c3f1a9d27b60.downgrade()`**: refuses offline, and refuses while `EXISTS (SELECT 1 FROM audit_log)` unless `config.attributes["allow_audit_log_loss"]`. Only `tests/conftest.py::_migrate_db` sets that attribute. `upgrade()` is unchanged.

## API contract
None.
