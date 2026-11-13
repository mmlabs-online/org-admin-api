## Attempt 1 — 2026-11-13

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/schemas.py` | `AppRegister` input model (reuses `AppName`, `normalize_origins`, `PermissionKey`); placed after `PermissionKey` to avoid a forward reference |
| `app/modules/rbac/repository.py` | `app_keys_holding_origins(origins, exclude_key)` (origin uniqueness, any status, `admin` included) |
| `app/modules/rbac/service.py` | `RegisterOutcome`, `RegisterResult`, `_register_refusal`, `AppService.register` (all business rules; reuses `create`/`update`/`PermissionService.create`/`_build_for_app`; R4 marker before the first write) |
| `app/cli.py` | `register-app` subcommand (parser, fixed messages, reason → exit mapping, dry-run via `_Rollback`, print after commit); `_load_settings` shared with grant-super-admin (same messages); module docstring covers both commands |
| `Makefile` | `register-app` target + `.PHONY` |
| `tests/test_cli_register_app.py` | New: every test listed in the plan |
| `docs/deploy.md` | "Register an app (USR-07)" runbook (ricotdin + ricotdin-staging worked example, output, semantics, exit table, compose fallback, local make); bootstrap step 2 via the ops workflow (manual command kept as fallback); one-time setup step 6 (`org-admin-api-ops` environment); "Browser origins" points to the runbook |
| `work/tickets/USR-07/impl-log.md` | This log |

### Tests added / changed
- `tests/test_cli_register_app.py` › `test_create_app_and_member_role`, `test_create_two_separate_apps`, `test_rerun_unchanged_no_write`, `test_origins_replaced_with_diff`, `test_policy_kept_when_flag_omitted`, `test_join_policy_changed_when_passed`, `test_org_mode_change_refused`, `test_same_org_mode_accepted`, `test_disabled_app_refused_exit_3`, `test_status_and_default_role_untouched`, `test_admin_refused_exit_64`, `test_service_refuses_admin`, `test_invalid_origin_exit_64` (4 params), `test_loopback_refused_in_production`, `test_loopback_allowed_locally`, `test_origin_taken_exit_1`, `test_origin_taken_by_admin_app`, `test_permissions_added_to_member_role`, `test_permission_rejected_exit_64` (4 params), `test_existing_admin_permission_refused`, `test_rerun_without_new_permission_keeps_tightening`, `test_register_locks_app_first`, `test_dry_run_rolls_back`, `test_usage_errors_exit_64` (8 params: the plan's 7 + empty argv), `test_db_echo_refused_exit_1`, `test_db_error_exit_1_no_leak`, `test_concurrent_create_conflict_exit_1`, `test_subprocess_register_usage_exit_64`
- `tests/test_cli_grant_super_admin.py` not modified (passes).

### Checks (actual output summary)
- Postgres: local cluster already accepting connections (`pg_isready`); nothing started or created.
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run twice, both exit 0:
  `ruff check`: All checks passed! · `ruff format --check`: 307 files already formatted · `mypy app`: Success: no issues found in 77 source files · `pytest -q`: 896 passed (29.7 s / 29.5 s).
- `make -n register-app ARGS="--key x --name X --origin https://x.mmlabs.online"` prints `uv run python -m app.cli register-app --key x --name X --origin https://x.mmlabs.online`; `make register-app` without ARGS prints the usage line and the recipe exits 64 (make itself reports `Error 64` and exits 2, same as `grant-super-admin`).
- alembic check: n/a (no schema change).

### Out-of-plan files
None.

### Deviations from plan (minor)
- `register-app` parser uses `add_help=False`, so `-h` is a usage error (fixed `REGISTER_USAGE`, exit 64) instead of argparse's help output and exit 0.
- `test_invalid_origin_exit_64` checks that the full submitted origin is not echoed (the fixed `MSG_BAD_ORIGIN` itself names `api.mmlabs.online`).
- In the CLI, a service `AppError` with an unlisted reason that is not a `ConflictError` (e.g. `NotFoundError` if the app vanished mid-run) maps to `MSG_REGISTER_UNEXPECTED` / 1, as the plan's "anything else".

### Follow-ups (noticed, not done)
- As in the plan: admin API one-origin-one-app rule (D19 amendment); owner to choose ricotdin permission keys; deployment PR for `ops-org-admin-api.yml` (orchestrator, not this diff); replace the R4 marker after ADM-004.

### Scope self-check
- Could the diff be smaller? No — every addition maps to an acceptance criterion or plan test.
- Diff size: +499 / -17 lines in tracked files, plus the new test module (597 lines) and this log.
