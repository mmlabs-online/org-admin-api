# Implementation log — ADM-004d

## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261115_e91b6c3d58a2_audit_log_db_guards.py` (new) | Scope 1, 2: `audit_log_immutable()` refuses DELETE of `audit_log.purged` rows; `BEFORE INSERT` trigger `audit_log_stamp_occurred_at` sets `occurred_at := now()`. Downgrade drops trigger + function and restores d7e4's function from the copied literal `PREVIOUS_FUNCTION` |
| `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py` | Scope 3: `downgrade()` refuses offline, then refuses while any row exists (`_refuse_if_rows`, `AUDIT_LOG_LOSS`) unless `allow_audit_log_loss`; `upgrade()` unchanged |
| `app/modules/audit/service.py` | Scope 5: `_CONTROL_CHARS` extended with the explicit format-character list + comment |
| `app/modules/audit/purge.py` | Scope 6: `SKIP LOCKED` comment at the short-batch exit (comment only) |
| `tests/conftest.py` | `_migrate_db` sets `allow_audit_log_loss`; `db_session` honours the `backdate_audit` marker (`DISABLE TRIGGER` on `conn` right after `begin()`, ACCESS EXCLUSIVE warning in docstring); `pytest_configure` registers the marker |
| `tests/test_audit_schema.py` | 7 `_aged` tests marked; 2 new tests |
| `tests/test_audit_logs_api.py` | Module `pytestmark = backdate_audit` |
| `tests/test_cli_audit_purge.py` | Module `pytestmark = backdate_audit` |
| `tests/test_audit_migrations.py` | Updated up/down/up; 2 new-migration tests; 3 `c3f1a9d27b60` guard tests |
| `tests/test_audit_purge_guard.py` | `allow_purge` confinement includes the new migration; floor check covers both migrations |
| `tests/test_audit_logger.py` | Extended UA strip parametrization; Chrome UA unchanged test |
| `docs/deploy.md` | Scope 4: Audit log section (new guards, never-downgrade list incl. `e91b6c3d58a2`, Restore bullet); Audit purge section (trail enforced by trigger, "any DB role can set it", never put the latch in `DATABASE_URL`) |
| `docs/adr/0009-audit-log.md` | Amendment "DB guards (ADM-004d, 2026-11-15)" |

### Tests added / changed
- `tests/test_audit_schema.py › test_purge_flag_refuses_purge_trail_row` (marked)
- `tests/test_audit_schema.py › test_insert_occurred_at_stamped` (not marked)
- `tests/test_audit_schema.py` › 7 existing `_aged` tests marked `backdate_audit`
- `tests/test_audit_migrations.py › test_purge_migration_up_down_up` (updated: `audit_log.purged` in prosrc, three triggers)
- `tests/test_audit_migrations.py › test_guards_migration_down_restores_purge_function`
- `tests/test_audit_migrations.py › test_guards_migration_previous_function_matches_d7e4`
- `tests/test_audit_migrations.py › test_audit_log_downgrade_refuses_with_rows`
- `tests/test_audit_migrations.py › test_audit_log_downgrade_passes_on_clean_table`
- `tests/test_audit_migrations.py › test_audit_log_downgrade_refuses_offline`
- `tests/test_audit_purge_guard.py › test_allow_purge_only_in_purge_module_and_migration`, `test_min_days_matches_migration_floor` (updated)
- `tests/test_audit_logger.py › test_user_agent_control_chars_stripped` (+11 cases)
- `tests/test_audit_logger.py › test_user_agent_normal_chrome_unchanged`

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff `All checks passed!`; format `446 files already formatted`; mypy `Success: no issues found in 100 source files`; pytest `1587 passed, 1 warning in 97.28s` (warning: pre-existing forkpty DeprecationWarning in `test_cli_create_account.py`).
- Migration cycle on local `org_admin`: `upgrade head` (d7e4b2a91c3f → e91b6c3d58a2) → `downgrade -1` (→ d7e4b2a91c3f) → `upgrade head` (→ e91b6c3d58a2), all clean.
- `alembic check`: `No new upgrade operations detected.`
- Scratch DB `adm004d_scratch` (created and dropped; org_admin untouched): upgrade head; raw INSERT with `occurred_at='2020-01-01'` stored as today (stamp works); `alembic downgrade base` raised `RuntimeError: downgrade of c3f1a9d27b60 is refused: audit_log has rows ...` and the DB stayed at `e91b6c3d58a2` (whole downgrade rolled back).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Pin `search_path` on the audit trigger functions, together with the runtime/migration role split (from plan).
- CHECK: `audit_log.purged` rows require `actor_kind = 'system'` (from plan).
- Apply the same Unicode strip to the device-session `user_agent` (`app/modules/sessions/service.py`) (from plan).
- `make check` does not run `alembic check` locally (CI does); consider adding it to the Makefile target.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +277 / -21 lines (production app+migrations +106/-4, tests +140/-8, docs +31/-9).
