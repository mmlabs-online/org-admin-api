## Attempt 1

# Security review: ADM-004d, attempt 1 (appsec-engineer)

Commit reviewed: `c2d18f1` (`git diff origin/main...HEAD`). I only read code. I did not run `make check`, pytest, alembic or psql, so nothing below was executed.

## Findings

No 🔴. No 🟡.

### Breaking the trigger body (purge trail): could not break it
- `migrations/versions/20261115_e91b6c3d58a2_audit_log_db_guards.py:27-37`
  - **NULL cases.** `action` is `NOT NULL` (`app/modules/audit/models.py:85`), so `OLD.action <> 'audit_log.purged'` never evaluates to NULL. An unset latch gives `current_setting(..., true)` = NULL, and `NULL = 'on'` is NULL, so the IF is false and the trigger raises. Both fail closed.
  - **Case variants.** The `action_format` CHECK (`^[a-z_]+(\.[a-z_]+)+$`, models.py:34) forces lowercase. A `Audit_Log.Purged` row cannot exist, so there is no case variant to slip past `<>`. The column uses the default deterministic collation.
  - **Other delete paths.** The function covers all DELETEs: `DELETE … USING`, `DELETE` inside `MERGE`, and `ON CONFLICT DO UPDATE` (which goes through UPDATE and is always refused). `audit_log` has no inbound FK cascade. TRUNCATE has its own trigger. The only remaining paths are owner DDL (`DROP`, `DISABLE TRIGGER`), as already documented.
- 💭 `e91b6c3d58a2_audit_log_db_guards.py:33`: anyone with DB write access can now insert a row with action `audit_log.purged`, and that row can never be deleted. The app writes only enum actions, and DB write access already outranks this, so it is a note only.

### BEFORE INSERT stamp: could not break it
- `e91b6c3d58a2_audit_log_db_guards.py:39-47`
  - It is a `FOR EACH ROW` trigger, so it fires for multi-row `VALUES`, `INSERT … SELECT`, `COPY FROM` (COPY does fire BEFORE ROW triggers), the INSERT action of `MERGE`, and `ON CONFLICT`. The stamp runs before conflict detection. The DO UPDATE branch then hits `audit_log_immutable` and is refused. DO NOTHING is harmless.
  - **Trigger order.** The immutable triggers are UPDATE/DELETE/TRUNCATE only, so they never run alongside this one. It is the only BEFORE INSERT trigger.
  - **Can a caller still backdate?** `now()` is the server's transaction start time; a session cannot set it. The only way to get an earlier stamp is to hold a transaction open, which is bounded by the server clock and cannot reach the 90-day floor in practice.
  - The remaining bypasses are owner DDL (`ALTER TABLE … DISABLE TRIGGER`) and `session_replication_role` (needs superuser or an explicit grant). The ADR amendment and deploy.md state this, and the runtime/migration role split is still a follow-up.
- App side: nothing writes `occurred_at` (grep shows reads only). The ORM column relies on its `server_default`, so app behaviour does not change.
- The rollback path is fine: deploy.md:223 says a rollback restarts the previous image on the new schema, and that image never sets `occurred_at`.
- 💭 If a future BEFORE INSERT trigger on `audit_log` is named alphabetically after `audit_log_stamp_occurred_at`, it runs later and could overwrite `occurred_at`. Any such trigger needs the same review.

### Downgrade guards: not reachable outside tests
- `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py:153-156`
  - The offline check runs first, then `SELECT EXISTS`.
  - `allow_audit_log_loss` is read only from `context.config.attributes`. `migrations/env.py` maps only `-x url`, and nothing in `app/` calls alembic, so the `alembic` CLI cannot set it.
  - `env.py:39-41` runs the whole migration run in one `begin_transaction()`, so a refusal at `c3f1` also rolls back the earlier `e91b`/`d7e4`/`af3e` down-steps. Postgres DDL is transactional.
- `backdate_audit` exists only in `tests/conftest.py`. `.dockerignore` excludes `tests`, so the image does not ship the hook.
  - Its `DISABLE TRIGGER` runs inside the per-test transaction, before the savepoint session exists. A crash or rollback re-enables the trigger.
  - The module-wide marks in `test_cli_audit_purge.py` and `test_audit_logs_api.py` are safe. `make_client` reuses the same `db_session`. The own-engine tests (`test_dry_run_transaction_is_read_only`, `test_db_failure_fixed_line`, `test_refuses_db_echo`) do not request `db_session`, so the marker has no effect on them.
- 💭 `e91b6c3d58a2.downgrade()` (`:68-72`) has no guard of its own. A stray `alembic downgrade -1` in production would silently drop both guards. This was an accepted plan decision (plan.md decision 2). Rollbacks never migrate down (deploy.md:223) and the deploy.md never-downgrade list covers it. It could be hardened later with the same `allow_*` attribute pattern.

### User-agent character class
- `app/modules/audit/service.py:31-34`
  - The regex is correct. In a raw `str` pattern, `re` itself handles `\uXXXX` and `\UXXXXXXXX`, and all ranges are ordered correctly.
  - No ASCII is over-stripped: only `\x00-\x1f` and `\x7f` are removed, as before.
  - It is a single character class with no quantifiers or alternation, so `sub()` runs in linear time with no ReDoS risk. Stripping happens before truncation to 512.
- 💭 The comment at `service.py:30` is accurate but misleading. Over HTTP, `\xad` almost always arrives as a UTF-8 continuation byte read as latin-1, not as a soft hyphen. For example, `í` = `C3 AD` is stored as `Ã`. This makes the existing C1 mojibake loss slightly worse, but has no security impact.
- 💭 The list misses some invisible characters, which matter only to non-HTTP callers:
  - Cf: U+0600–0605, U+06DD, U+070F, U+08E2, U+110BD, U+1D173–1D17A.
  - Invisible fillers that are not Cf: U+115F, U+1160, U+3164, U+FFA0, and the variation selectors U+FE00–FE0F.

### Docs
- `docs/deploy.md:651-656`, restore guidance: correct.
  - `pg_dump`/`pg_restore` put triggers in the post-data section, so a full restore loads the rows before the stamp trigger exists.
  - `--data-only --disable-triggers` needs superuser because it emits `DISABLE TRIGGER ALL`, which also disables the system FK triggers.
- 💭 If the backups are plain-SQL dumps restored with `psql` (deploy.md:21 only says "pg_dump"), `--disable-triggers` has to be passed to `pg_dump` at dump time, not to `pg_restore`. One clause would cover it.
- 💭 I cannot verify the claim about `RESTORE.md` in the deployment repo from here.
- `docs/deploy.md:645-650`, never-downgrade list: correct and matches the code, including the note that reaching `d7e4b2a91c3f` passes through `e91b6c3d58a2`.
- `docs/adr/0009-audit-log.md:121-129`, latch wording: correct. Any role can `SET` a custom dotted GUC.
- `docs/deploy.md:690-691`, the `DATABASE_URL` warning: sound.

## Follow-ups (out of scope)
1. Split the runtime and migration roles. This is the existing follow-up, and it is the only thing that removes the owner-DDL bypass for both guards.
2. Optionally add an `allow_guard_loss`-style refusal to `e91b6c3d58a2.downgrade()`.
3. Optionally extend `_CONTROL_CHARS` with the remaining Cf characters listed above, plus the Hangul fillers.

**Verdict: PASS (security).** I found no exploitable path to delete purge-trail rows, backdate inserts, reach the test hooks in production, or cause ReDoS; there are only 💭 notes.
