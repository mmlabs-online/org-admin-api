# Implementation log — USR-02f

## Attempt 1 — 2026-11-12

### Files touched
| File | Required because |
|---|---|
| `app/cli.py` (new) | AC1, AC3–AC5: argparse parser (`error()` → exit 64, message dropped), `amain()` with one catch-all and lazy imports, fixed messages, CLI-only engine (`hide_parameters=True`, `echo=False`, `NullPool`), `DB_ECHO` refusal, result block, `main()` (`tracebacklimit = 0`, `NullHandler` + `propagate = False` on `sqlalchemy`/`asyncpg`) |
| `app/modules/rbac/service.py` | `BootstrapOutcome`, `BootstrapResult`, `AssignmentService.bootstrap_super_admin()` (lock admin app → seeded role → user `FOR SHARE` → status → pre-check → `_grant_locked`; `# R4` marker) |
| `app/modules/users/repository.py` | `get_by_email_for_share()` (`FOR SHARE`, `populate_existing`) |
| `app/core/text.py` | `mask_email()` |
| `Makefile` | `grant-super-admin` target (`@`-prefixed, quoted value) + `.PHONY` |
| `docs/deploy.md` | new section "Bootstrap the first super-admin" (appended after "Operations"/rollback, at end of file) |
| `tests/test_cli_grant_super_admin.py` (new) | AC1–AC5 |
| `tests/test_text.py` | `test_mask_email` |

### Tests added / changed
- `tests/test_cli_grant_super_admin.py` › `test_grant_creates_app_scope_row`, `test_grant_output_fields`, `test_grant_mixed_case_email`, `test_grant_twice_already_held`, `test_grant_replaces_expired_row`, `test_grant_active_row_with_future_expiry_already_held`, `test_unknown_email_exit_2`, `test_disabled_user_exit_3_no_write`, `test_usage_errors_exit_64` (6 params), `test_db_echo_refused_exit_1`, `test_settings_error_exit_1`, `test_not_seeded_exit_1`, `test_db_error_exit_1_no_leak`, `test_grant_with_rbac_flag_off`, `test_grant_locks_admin_app_then_user`, `test_cli_not_imported_by_app`, `test_subprocess_usage_exit_64`, `test_subprocess_unreachable_db_exit_1`
- `tests/test_text.py` › `test_mask_email` (9 params: the plan's 8 plus a long local/domain case showing the length is not leaked)

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", format "195 files already formatted", mypy "Success: no issues found in 53 source files", pytest "531 passed in 19.20s".
- alembic check: not applicable (no schema change, no migration).
- Local Postgres: already running (`pg_isready` → accepting connections); nothing started.
- `make grant-super-admin` without `email=` → prints the usage line, recipe exits 64.

### Deviations from plan
- Test emails use `example.com`, not `example.test`: pydantic `EmailStr` (the same validator sign-up uses) rejects the special-use `.test` TLD, so `alice.x@example.test` would be a usage error (64). `["grant-super-admin", "--email", "a@b.test", "--extra"]` is kept as in the plan (exit 64 either way).
- `BootstrapResult` fields after `outcome` default to `None` (failure outcomes carry no user data).
- In `amain`, a GRANTED/ALREADY_HELD result missing `email`/`created_at` raises `_Fail(MSG_UNEXPECTED)` instead of an `assert` (ruff S101 forbids `assert` in `app/`); unreachable in practice.

### A2 (deployment `migrate` service)
Read-only check of `/home/user/deployment/apps/org-admin-api/compose.yml`: `migrate` sets `command: ["/app/.venv/bin/alembic", "upgrade", "head"]` and the image has `CMD` only (no `ENTRYPOINT`), so `run --rm --no-deps migrate /app/.venv/bin/python -m app.cli …` overrides the command as written; no `--entrypoint` needed. Its `x-app.environment` has no `TRUSTED_PROXY_CIDRS` yet (FU3).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- FU1–FU5 as listed in plan.md (USR-04 unverified policy, ADM-004 audit at `# R4`, deployment `TRUSTED_PROXY_CIDRS`, optional stdin email, lock order for future disable/delete).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +706 / -1 lines (production: cli 154, service 54, repository 12, text 15, Makefile 6; docs 54; tests 412).
