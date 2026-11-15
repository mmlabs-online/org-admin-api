# Review — ADM-004d

## Attempt 1 — 2026-11-15
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff `All checks passed!`; format `447 files already formatted`; mypy `Success: no issues found in 100 source files`; pytest `1587 passed, 1 warning in 108.55s` (pytest count matches impl-log; format count 447 vs logged 446 is a file-count difference only, no file needed formatting).
- alembic check (local org_admin): `No new upgrade operations detected.`
- Migration cycle on local org_admin: `upgrade head` (already at e91b6c3d58a2) → `downgrade -1` (e91b6c3d58a2 → d7e4b2a91c3f) → `upgrade head` (→ e91b6c3d58a2), all clean; `alembic current` = `e91b6c3d58a2 (head)`.
- psql on scratch DB `rev_adm004d_scratch` (created, migrated to head, dropped; no trigger touched on org_admin):
  - INSERT with `occurred_at = TIMESTAMPTZ '2020-01-01'` → `occurred_at = now()` is `t`.
  - Seeded (trigger disabled in a committed txn, scratch only) a 91-day-old `audit_log.purged` row and a 91-day-old `auth.login_failed` row. With `SET LOCAL audit.allow_purge='on'`: trail DELETE → `ERROR: audit_log is append-only`; control row DELETE → `DELETE 1` (rolled back). So the refusal is due to the new action clause only.
  - `alembic downgrade -1`: `prosrc` of `audit_log_immutable` equals `d7e4b2a91c3f.PURGE_FUNCTION.split("$$")[1]` (Python equality `True`); 0 `audit_log_stamp_occurred_at` functions; triggers = `audit_log_no_update_delete, audit_log_no_truncate`.
  - CLI `alembic downgrade base` with rows → `RuntimeError: downgrade of c3f1a9d27b60 is refused: audit_log has rows ...`; `alembic -x allow_audit_log_loss=1 downgrade base` → same refusal; DB stayed at `e91b6c3d58a2` (whole downgrade rolled back). `alembic downgrade c3f1a9d27b60:5e2a9c7d1f04 --sql` → `RuntimeError: offline downgrade of c3f1a9d27b60 is refused`.

### Acceptance criteria
- [x] Purge-trail DELETE refused even with the flag — evidence: `migrations/versions/20261115_e91b6c3d58a2_audit_log_db_guards.py:32`; `tests/test_audit_schema.py:241-251` `test_purge_flag_refuses_purge_trail_row` (400-day row, via `_refused_with_flag`) passes; psql check above on a 91-day row.
- [x] Inserted `occurred_at` overridden with `now()` — evidence: `e91b6c3d58a2...py:39-46` (`now()`, BEFORE INSERT FOR EACH ROW); `tests/test_audit_schema.py:254-266` `test_insert_occurred_at_stamped` (unmarked, so it also proves the default fixture keeps the trigger on) passes; psql check above.
- [x] `c3f1a9d27b60.downgrade()` refuses while rows exist and offline — evidence: `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py:140-156`; `tests/test_audit_migrations.py:148` `test_audit_log_downgrade_refuses_with_rows`, `:161` `..._passes_on_clean_table`, `:165` `..._refuses_offline` pass; CLI refusal confirmed on scratch DB.
- [x] New migration's downgrade restores d7e4's function byte for byte, drops stamp trigger + function — evidence: `e91b6c3d58a2...py:50-72`; `tests/test_audit_migrations.py` `test_guards_migration_down_restores_purge_function`, `:116` `test_guards_migration_previous_function_matches_d7e4` pass; psql prosrc equality above.
- [x] Migration up/down/up clean; `make check` green — evidence: cycle and `make check` output above; `test_purge_migration_up_down_up` updated (three triggers, `audit_log.purged` in prosrc).
- [x] deploy.md wording (scope 4) — evidence: `docs/deploy.md:687-691` ("any DB role can set it", never put `-c audit.allow_purge=on` / `options` in `DATABASE_URL`); never-downgrade list incl. `e91b6c3d58a2` at `:645-650`; Restore bullet follows.
- [x] Unicode bidi / line-separator / format chars stripped from `user_agent` — evidence: `app/modules/audit/service.py:28-34`; `tests/test_audit_logger.py:321` (11 new cases) and `:330` `test_user_agent_normal_chrome_unchanged` pass. Reviewer enumerated the class over all code points: 227 chars, outside C0/C1 + tags block exactly U+00AD, U+061C, U+180E, U+200B–200F, U+2028–202E, U+2060–2064, U+2066–206F, U+FEFF, U+FFF9–FFFB. Safari, curl, and a UA with `café ñ ü —` pass through unchanged.
- [x] `SKIP LOCKED` comment — evidence: `app/modules/audit/purge.py:121-122`, comment only.

### Specific checks requested by the orchestrator
- **`backdate_audit` only inside a test transaction**: `tests/conftest.py:95-112` — `DISABLE TRIGGER` runs on `conn` only after `trans = await conn.begin()` and before the `AsyncSession` (`join_transaction_mode="create_savepoint"`, so `session.commit()`/`rollback()` act on savepoints, never the outer txn); `finally` always runs `trans.rollback()`, which reverts the DDL. Marked modules (`test_audit_logs_api.py:17`, `test_cli_audit_purge.py:24`) never call `.commit()` on the connection; the one own-engine test there (`test_dry_run_transaction_is_read_only`) does not request `db_session`, so the marker has no effect on it. The marker is read nowhere in `app/` or `migrations/`; `test_no_trigger_bypass_in_app` still forbids `disable trigger` in `app/`.
- **`allow_audit_log_loss` test-only, unreachable from the CLI**: only set at `tests/conftest.py:72`; read only at `c3f1a9d27b60...py:155`. `migrations/env.py` copies nothing from `-x` into `config.attributes` (only `url` is read from `-x`), and the CLI builds a fresh `Config` with empty `attributes`. Confirmed empirically: `alembic -x allow_audit_log_loss=1 downgrade base` is still refused.
- **Normal UA unchanged**: `tests/test_audit_logger.py:330` plus the reviewer's enumeration above.

### Issues
None.

### Rules R1–R11
No violations found. R1: no layer change. R2: hand-written migration with working downgrade (verified by cycle and psql), no model change, no drift. R3/R6/R9: no endpoint change. R4: purge summary row still written in the batch transaction; it now gets `now()` from the trigger (same value as before). R5/R11: no secrets; deploy.md forbids the latch in `DATABASE_URL`. R7: changed files match the plan's list (plus orchestrator-owned `work/` files). R8: every AC has a passing test.

### Nits (do not block)
- `test_audit_log_downgrade_refuses_with_rows` tests `_refuse_if_rows` directly, not the `allow_audit_log_loss` wiring in `downgrade()` (same pattern as the existing `af3eb84d2d5e` tests). The reviewer confirmed the wiring by hand through the CLI.
- impl-log's ruff format count (446) differs from this run's (447). This is cosmetic.
- The class also strips ZWJ/ZWNJ (U+200C/U+200D). That is fine for user agents, but note it if the regex is ever reused for display names.

### Follow-ups (out of scope)
- Pin `search_path` on the audit trigger functions with the runtime/migration role split (from plan).
- CHECK so that `audit_log.purged` rows require `actor_kind = 'system'` (from plan).
- Apply the same Unicode strip to the device-session `user_agent` (`app/modules/sessions/service.py`) (from plan).
- Consider adding `alembic check` to the local `make check` target (impl-log).
