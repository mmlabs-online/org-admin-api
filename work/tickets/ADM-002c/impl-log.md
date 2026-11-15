## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/schemas.py` | `AdminOriginsSet` (`extra="forbid"`, `origins` default `[]`, `max_length=MAX_ORIGINS`, D19 `normalize_origins`) |
| `app/modules/rbac/service.py` | `MAX_ADMIN_ORIGINS = 2`, `APEX_ORIGIN`, `AdminOriginsOutcome`, `AdminOriginsResult`, `AppService.set_admin_origins` (pure input checks → `lock_app("admin")` → drift check → `origin_taken` → unchanged/no-write or write `origins` only + `mark_apps_changed`) |
| `app/cli.py` | `set-admin-origins` subcommand: dispatch, `_parse_set_admin_origins` (required mutually exclusive `--origin`/`--clear`, `--dry-run`), `_set_admin_origins`, `_print_admin_origins`, `_AdminOriginsRollback`, `_ADMIN_ORIGIN_REASONS`, new fixed messages/usage, module docstring |
| `Makefile` | `set-admin-origins` target (requires `ARGS`), added to `.PHONY` |
| `tests/test_cli_set_admin_origins.py` | New: service + CLI tests (54 cases) |
| `tests/test_cli_register_app.py` | `test_register_app_refuses_origin_set_by_set_admin_origins` |
| `docs/deploy.md` | New "Set admin origins (ADM-002c)" section; "Admin access (ADM-002)" rewritten (runbook with curl, cookie jar/body 0600, `read -s`, Turnstile note, Origin = CSRF not auth); one line in "Browser origins (CORS)"; One-time setup step 6 command list |
| `docs/adr/0008-admin-api-authentication.md` | Consequences bullet updated; "Amendment — admin origins (ADM-002c, 2026-11-15)" appended |

### Tests added / changed
- `tests/test_cli_set_admin_origins.py` › `test_service_sets_origins`, `test_service_replaces_with_diff`, `test_service_invariants_unchanged`, `test_service_rerun_unchanged_no_write`, `test_service_clear`, `test_service_clear_when_empty_unchanged`, `test_service_loopback_refused[staging|production]`, `test_service_loopback_allowed_locally`, `test_service_apex_refused[2 spellings]`, `test_service_third_origin_refused`, `test_service_duplicates_count_once`, `test_service_origin_taken[active|disabled]`, `test_service_loopback_not_checked_for_taken`, `test_service_refuses_drift[status_disabled|join_open|org_required|default_role_set]`, `test_service_refuses_missing_admin`, `test_service_locks_admin_first`
- same file › `test_cli_sets_origins_exit_0`, `test_cli_rerun_prints_unchanged`, `test_cli_clear_exit_0`, `test_cli_dry_run_rolls_back`, `test_cli_clear_dry_run_rolls_back`, `test_cli_usage_errors_exit_64[no_args|clear+origin|key_admin|abbrev_orig|unknown_flag|origin_without_value]`, `test_cli_invalid_origin_exit_64[http|api_host|foreign|port|ip|empty]`, `test_cli_loopback_production_exit_64`, `test_cli_apex_exit_64`, `test_cli_third_origin_exit_64`, `test_cli_origin_taken_exit_1`, `test_cli_drift_exit_1`, `test_cli_refusals_never_echo_values[loopback|apex|too_many|taken|drift|invalid]`, `test_cli_db_echo_exit_1`, `test_cli_db_error_exit_1_no_leak`, `test_cli_conflict_exit_1`, `test_subprocess_set_admin_origins_usage_exit_64`
- `tests/test_cli_register_app.py` › `test_register_app_refuses_origin_set_by_set_admin_origins` (existing tests unchanged)

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`:
  - `ruff check .` → All checks passed!
  - `ruff format --check .` → 370 files already formatted
  - `mypy app` → Success: no issues found in 88 source files
  - `pytest -q` → 1203 passed in 65.55s
- `uv run alembic check` → No new upgrade operations detected. (no schema change)
- Local Postgres was already running; nothing set up.

### Out-of-plan files
None.

### Deviations (minor)
- Plan names `app_env="development"` for the local loopback tests; `Settings.app_env` has no such value (`local|test|staging|production`), so tests use `"local"`. The service rule is unchanged (only `staging`/`production` refuse loopback).
- `test_cli_refusals_never_echo_values[apex]` submits `https://MMLABS.online/` and checks that the raw submitted spelling is not echoed: the fixed `MSG_ADMIN_APEX` line itself names `https://mmlabs.online` by design.
- `test_service_locks_admin_first`: the first statement touching `apps` is asserted to be the `FOR NO KEY UPDATE` select (input refusals issue no SQL).

### Follow-ups (noticed, not done)
- ADM-002b: enforce `admin:*` on admin origin changes in `AppService.update` (comment at the field loop).
- Admin-UI ticket: non-credentialed, Bearer-only CORS on `/api/v1/*`; strict CSP, no third-party scripts, SRI before any admin UI is served at the admin origin.
- ADM-004: audit entry for `set-admin-origins` (actor `system:cli`, added/removed).
- Optional: DB-level guard against two apps holding the same origin (security Finding 4).
- Optional: structured log `admin_origins_changed` with counts.
- Deployment repo PR (owner): `set-admin-origins` command input in `ops-org-admin-api` (`dry_run` default on, shared `concurrency` group).
- deploy.md curl runbook builds the login body with `printf`; a password containing `"` or `\` must be JSON-escaped by hand (documented). A safer body builder could be added if the owner wants.

### Scope self-check
- Could the diff be smaller? No — service/schema ~95 lines, CLI ~130 lines, Makefile 4, tests ~635, docs ~150 (no git repo here, counts approximate).
- Diff size: approx. +1015 / -15 lines
