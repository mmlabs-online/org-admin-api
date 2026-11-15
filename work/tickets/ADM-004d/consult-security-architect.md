# consult-security-architect — ADM-004d

## security-architect consult: ADM-004d

**Findings**
1. **Purge trail.** `audit_log.purged` rows hold no PII: just the `system:cli` actor, the cutoff and counts. They grow by one row per 1000 deleted. `docs/deploy.md:672` and ADR-0009 already promise them "kept forever". Nothing argues for a longer floor instead.
2. **`occurred_at` trigger.** No `app/` path sets `occurred_at`, so the trigger costs the app nothing. Tests do insert backdated rows through raw SQL:
   - `tests/test_audit_logs_api.py:42` (`T0 + minutes`)
   - `tests/test_audit_schema.py:167`
   - `tests/test_audit_migrations.py:62`
   - `tests/test_cli_audit_purge.py:70`

   These tests break unless they get a bypass.
3. **Restores.** A full restore of a pg_dump into an empty database keeps the timestamps. pg_dump writes triggers in the post-data section, after COPY. A **data-only** restore into a migrated schema fires the trigger on every COPY row. That rewrites every `occurred_at` to the restore time, which destroys history and makes everything unpurgeable for 90 days.
4. **c3f1a9d27b60 downgrade.** `tests/conftest.py:63-71` runs `downgrade base` on a test DB that holds committed rows from the concurrency tests. A refusal while rows exist would break the session fixture unless it has a bypass.
5. **User agent.** User agents come from `request.headers.get("user-agent")` (`app/modules/audit/deps.py:17`, `auth/router.py:128`). Starlette decodes headers as **latin-1**, so a UTF-8 U+202E arrives as `â\x80®`, and the existing C1 strip already breaks it. Over HTTP, the only format character that can arrive intact is U+00AD (soft hyphen). The new strip is defence in depth for other callers, such as future services or the CLI, and for the separate session `user_agent` (`sessions/service.py:151`, out of scope).

**Recommendations**
1. **Make the purge-trail protection absolute, with no floor.** Use `IF TG_OP='DELETE' AND OLD.action <> 'audit_log.purged' AND current_setting(...)='on' AND OLD.occurred_at < now() - interval '90 days' THEN RETURN OLD`. Write it in a new migration. Its downgrade restores `d7e4b2a91c3f`'s `PURGE_FUNCTION` byte for byte, and a test checks `prosrc`. Test: with the flag set, deleting a 91-day-old `audit_log.purged` row raises.
2. **Insert trigger.** Use a separate function, `audit_log_stamp()`, plus `BEFORE INSERT ... FOR EACH ROW` that sets `NEW.occurred_at := now()`.
   - Use `now()`, not `clock_timestamp()`. That keeps "rows of one transaction share `occurred_at`" (ADR-0009:50) and matches the floor's clock.
   - Leave `audit_log_immutable()` unchanged for INSERT.
   - **Test bypass:** a fixture runs `ALTER TABLE audit_log DISABLE TRIGGER audit_log_stamp_occurred_at` inside the rolled-back test transaction. That needs owner rights, is transactional and leaves no trace. Do **not** add a GUC bypass, because any role can set a GUC (that is the 004c latch lesson). The existing static guard forbids `disable trigger` in `app/` only, which is fine.
   - Test: an explicit `occurred_at` of 2020 is stored as `now()`.
3. **c3f1a9d27b60 downgrade.** Refuse in offline mode. Refuse while `EXISTS (SELECT 1 FROM audit_log)` is true, unless `context.config.attributes["allow_audit_log_drop"]` is set. Use a new, distinct name; do not reuse `allow_outcome_loss`. Set it only in `tests/conftest.py`. Add tests for offline refusal, refusal with rows, and a stub-bind pass. Editing a shipped migration's `downgrade()` is acceptable: it is not schema, so `alembic check` is unaffected.
4. **Unicode strip.** Extend `_CONTROL_CHARS` with an explicit class: `­؜᠎​-‏ -‮⁠-⁤⁦-⁯﻿￹-￻` plus tags `\U000e0000-\U000e007f`.
   - This covers bidi controls (LRM/RLM/ALM, embeddings and overrides, isolates), zero-width characters, BOM, line and paragraph separators, and invisible operators.
   - Legitimate user agents are ASCII (RFC 9110 tokens and comments), so this costs nothing.
   - Do not strip by category `Cf` wholesale. That stays version-dependent, and an explicit list is reviewable.
   - Parametrized test: U+202E, U+2028, U+200B, U+FEFF and U+00AD are removed; a normal Chrome UA is unchanged.
5. **`docs/deploy.md`.**
   - Line 677: replace "anyone with the DB owner role can set it" with "any DB role can set it (`SET`, `SET LOCAL`, or `options=-c` in a DSN)".
   - Add: "Never put `-c audit.allow_purge=on` (or any `options`) in `DATABASE_URL`: every API transaction would carry the latch."
   - Line 672: add "enforced by the trigger since ADM-004d".
   - Never-downgrade bullet: add that `c3f1a9d27b60`'s downgrade now refuses offline and while rows exist.
   - New bullet **Restore**: "Restore audit data only as a full restore into an empty database (triggers are created after the data). A data-only restore into a migrated schema must be `pg_restore --data-only --disable-triggers` run as superuser; otherwise the insert trigger rewrites every `occurred_at`. Never set `session_replication_role` on the live database."
   - Add one line to the ADR-0009 amendment that lists both new DB guards.
6. Add the `SKIP LOCKED` comment at `purge.py:121`, as in scope item 6.

**Risks**
- `ops/backup/RESTORE.md` lives in the deployment repo. If it does a data-only restore, history is silently corrupted. Make that a follow-up issue there; it does not block this ticket.
- `DISABLE TRIGGER` in fixtures takes an ACCESS EXCLUSIVE lock on `audit_log` for the length of the test transaction. Keep it out of the concurrency tests that commit rows, and confirm they never need backdated rows.
- Anyone with owner or DDL rights can still bypass every guard, because the API runs as owner. The runtime/migration role split remains the real fix and is already a follow-up.
- A raw-SQL writer can insert fake `audit_log.purged` rows that are now undeletable. The only effect is a forged entry that cannot be removed; no data is lost. As an optional follow-up, a CHECK `action <> 'audit_log.purged' OR actor_kind = 'system'` would narrow it.

**Open questions**
- Owner: is "never deletable, even after legal retention ends" acceptable for the purge trail? It holds no personal data, so this is recommended, but it bears on spec Q6 / Decree 13.
- Does the deployment repo's restore procedure use full or data-only restores? Who owns updating RESTORE.md?
