## Attempt 1

## security-review — ADM-004c, attempt 1 (commit c3da104)

I read the code only. I did not run `make check`, pytest or psql.

### Findings

No 🔴 and no 🟡.

💭 **1. The purge trail is protected by app code only, not by the DB.** `migrations/versions/20261115_d7e4b2a91c3f_audit_log_purge_floor.py:29-37`, `app/modules/audit/purge.py:80-84`
- `audit_log.purged` rows are left out only by `_purgeable()` in the CLI.
- The trigger accepts a DELETE of any row older than 90 days once the latch is set. Any DB role can set that latch with `SET LOCAL audit.allow_purge='on'`.
- So someone with DB credentials and no DDL rights can erase the trail of earlier purges. `docs/deploy.md` and ADR-0009 say these rows are "kept forever".
- Fix (follow-up, a small migration): add `AND OLD.action <> 'audit_log.purged'` to the trigger's DELETE branch, so D-c is enforced by the DB like the floor.
- Not blocking: the plan (D-c) chose to filter in the app, and the floor still holds.

💭 **2. `occurred_at` can be set on insert.** `app/modules/audit/models.py` uses `server_default=now()` and nothing forces it.
- App code never sets `occurred_at`: `_build` leaves it out, and a grep found no `occurred_at=` in `app/`.
- A raw-SQL writer could insert backdated rows that can be purged at once. They could also forge history.
- This cannot reach real rows, because UPDATE is refused, so the floor is not bypassed.
- Follow-up: a `BEFORE INSERT` trigger that sets `NEW.occurred_at := now()`, or a CHECK `occurred_at <= now()` (only partly useful).

💭 **3. Wording in `docs/deploy.md:677`.** It says "anyone with the DB owner role can set it". In fact any role can set a custom GUC with `SET`, `SET LOCAL` or a DSN `options=-c audit.allow_purge=on`. The text should say "any DB role".
- Related: if an operator ever adds `-c audit.allow_purge=on` to `DATABASE_URL`, every API transaction has the latch on, and only the floor still applies. Worth one line in the runbook.

💭 **4. A down-migration can still destroy the whole log.** `migrations/versions/20261115_af3eb84d2d5e_add_audit_log_outcome.py:61-75`
- The guard itself is correct:
  - It refuses in offline `--sql` mode.
  - Its EXISTS check matches the CHECK (`failure`, `denied`).
  - `allow_outcome_loss` is read only from `config.attributes`. The `alembic` CLI cannot set it: `-x` goes to `get_x_argument`, and `migrations/env.py` reads only `url`.
  - It is set only in `tests/conftest.py:69`, and nothing in `app/` or the Docker entrypoint calls alembic from code.
- On a table with no failure or denied rows, `alembic downgrade base` still passes this guard and then reaches `c3f1a9d27b60`, which drops the table with no guard. That is not new, and deploy.md covers it as a rule.
- Follow-up: the same kind of refusal in `c3f1a9d27b60.downgrade()` whenever rows exist.

💭 **5. DST drift.** `interval '90 days'` on a `timestamptz` is calendar arithmetic in the session `TimeZone`, so a session can move the floor by about 1 hour around DST. Choosing a zone does not move it by more than a few hours. The plan already records this and accepted it.

### Checked and clean, by question
1. **Can HTTP or other app code set the latch or delete rows?**
   - No. `allow_purge` appears only in `purge.py` and the migration, and only `app/cli.py` imports `purge.py`. The static test enforces both.
   - There is no `set_config` or `session_replication_role` in `app/`, no route path containing "purge", and no session events.
   - The only other `text()` SQL is `SET LOCAL lock_timeout` in `auth/repository.py:258` and `SELECT 1` in the health check.
   - Only `SET LOCAL` is used, so the latch dies at commit or rollback and does not leak across the pool (the CLI also uses `NullPool`).
2. **Can the floor be bypassed?**
   - `now()` is the transaction start time, so an old open transaction only makes the floor stricter.
   - A NULL `occurred_at` is impossible (NOT NULL), and even then `NULL < x` would fall through to the RAISE.
   - The match is strictly `'on'`; NULL and empty fall through.
   - The run's cutoff is computed earlier than each batch's trigger `now()`, and N ≥ 90, so the cutoff is always at or below the trigger threshold.
3. **Can a batch delete without its summary row?**
   - No. `AuditLogger.record` flushes and raises on error, without swallowing it. The exception leaves `run()` before `commit()`, and the scope closes with a rollback, so the delete is undone with it.
   - The summary row's `occurred_at = now()` can never be inside the cutoff.
   - `audit_log.purged` rows are left out of both the count and the delete (see finding 1 for the DB side).
4. **CLI input**
   - `re.fullmatch(r"[0-9]{1,4}")` accepts ASCII digits only: no signs, spaces, Unicode digits or `_`. The range 90–3650 is then checked.
   - `_Parser.error` never echoes argv, and `add_help=False`.
   - Every `Exception` prints one fixed line, never `str(exc)`. Settings failures go through `_Fail(MSG_CONFIG)`.
   - The engine uses `hide_parameters=True`, and the output is counts and cutoff only.
5. **The downgrade guard and `allow_outcome_loss`.** Neither is reachable outside tests (finding 4).
6. **Trigger correctness**
   - For TRUNCATE (statement-level), `TG_OP='TRUNCATE'` skips the DELETE branch, so `OLD` is never read and the function raises.
   - UPDATE raises.
   - An allowed DELETE returns `RETURN OLD`, not NULL.
   - The downgrade restores the original body byte for byte.
7. **Email cleanup.** There is one `email_retention.purged` row per deleting batch, in that batch's transaction. Its key `{table: {from: None, to: n}}` passes `_check_keys` only under the existing allowlist, and the aggregate block was removed.
8. **Log content**
   - `[\x00-\x1f\x7f-\x9f]` strips C0, DEL and C1 characters.
   - `error=%s` logs `type(exc).__name__`, the class name only, with no message, values or DSN.
   - Unicode format characters such as U+202E and U+2028 are still kept in `user_agent`. That is outside this ticket and could be a follow-up.

### Follow-ups (out of scope)
- A DB-enforced guard for the purge trail (finding 1).
- `occurred_at` set only by the DB on insert (finding 2).
- A refusal in the `c3f1a9d27b60` downgrade while rows exist (finding 4).
- The runtime/migration DB role split: it is already listed, and it remains the real fix for bypass by DDL.
- Stripping Unicode bidi and line-separator characters from `user_agent`.

**Verdict: PASS (security). The 90-day floor is enforced by the DB. Only the CLI can set the latch, and each batch is atomic with its summary row. All findings are 💭 notes or follow-ups.**
