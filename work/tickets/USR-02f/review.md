## Attempt 1 — 2026-11-12
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest) via `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", format "196 files already formatted", mypy "Success: no issues found in 53 source files", pytest "531 passed in 20.99s". This matches impl-log.md. The format count is 196 here and 195 in the log; the extra file is the committed `impl-log.md`, which ruff also formats, so this is not a test mismatch.
- alembic check: not applicable. The diff has no schema change and no migration.

### Acceptance criteria
- [x] AC1 `python -m app.cli grant-super-admin --email <email>` (argparse) runs in one transaction: lock the admin app → seeded role → user `FOR SHARE` by normalized email → `_grant_locked`. App scope, `granted_by` NULL, `expires_at` NULL. Evidence: `app/cli.py:56-68` (parser, same normalization as `app/modules/auth/service.py:31`), `app/cli.py:71-85` (`session.begin()` = one transaction), `app/modules/rbac/service.py:542-573`, `app/modules/users/repository.py:21-31`. Tests: `test_grant_creates_app_scope_row`, `test_grant_mixed_case_email` and `test_grant_locks_admin_app_then_user` (order app lock < user `FOR SHARE` < INSERT) all pass.
- [x] AC2 Idempotency: "granted" and "already held" both exit 0, and an expired row is replaced in place. Evidence: `test_grant_twice_already_held` (same id), `test_grant_replaces_expired_row` (same id, `expires_at` NULL), `test_grant_active_row_with_future_expiry_already_held`.
- [x] AC3 Exit codes are 0/2/3/1/64, and each failure prints one fixed line with no traceback and no DSN. Evidence: `app/cli.py:23-38,130-140`. Tests: `test_unknown_email_exit_2`, `test_disabled_user_exit_3_no_write`, `test_usage_errors_exit_64` (6 params, argv values never echoed), `test_db_echo_refused_exit_1`, `test_settings_error_exit_1`, `test_not_seeded_exit_1`, `test_db_error_exit_1_no_leak`, `test_subprocess_usage_exit_64`, and `test_subprocess_unreachable_db_exit_1` (real entry point: no password, host, email or `Traceback` in the output).
- [x] AC4 Output shows the user id, masked email, `created_at` and `email_verified: yes/no`, and the full email is never printed. Evidence: `app/cli.py:122-129`; `test_grant_output_fields` checks the exact lines including `yes`/`no`; every CLI test calls `_assert_no_leak` (case-insensitive). The CLI builds its own engine with `hide_parameters=True, echo=False, NullPool` and never imports `app.db.session` (`app/cli.py:71-85`). `DB_ECHO` is refused before any connection (`app/cli.py:107-108`).
- [x] AC5 The CLI is not reachable over HTTP, needs no flag, creates no user, takes no password and has no verify path. Evidence: `test_cli_not_imported_by_app` (AST scan of `app/**`); `test_grant_with_rbac_flag_off`; `app/cli.py` and `bootstrap_super_admin` have no `User(` construction, no password handling and no `email_verified_at` write.
- [x] AC6 `make grant-super-admin email=...` exists (`Makefile:21-23`, `@`-prefixed, listed in `.PHONY`). The runbook is in `docs/deploy.md` → "Bootstrap the first super-admin" and covers `run --rm --no-deps migrate …`, the required env (`DATABASE_URL`, `APP_ENV`, `TRUSTED_PROXY_CIDRS`; never `DB_ECHO`), signing up and logging in first (pre-registration hijack), the `created_at` check and how to revoke, the password reset after USR-04, and the exit-code table. The implementer confirmed plan assumption A2 against the deployment compose: `command:` can be overridden and there is no `ENTRYPOINT`.
- [x] AC7 The masking helper is `app/core/text.py:24-36`. `test_mask_email` (9 params) covers a fixed `***` that does not reveal length, no `@`, an empty local part, an empty domain, unicode and multi-label domains.
- [x] AC8 `make check` is green (see above).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: CLI → `AssignmentService.bootstrap_super_admin` → repositories. The CLI receives plain `BootstrapResult` values and never touches the ORM. SQL stays in the repositories.
- R2: no schema change.
- R3: no HTTP endpoint added; nothing imports `app.cli`.
- R4: a `# R4` marker sits at the write point (`app/modules/rbac/service.py:565`), as planned until ADM-004 (FU2).
- R5: no new config key; the DSN is never printed.
- R6: not applicable (no HTTP).
- R7: every changed file is in the plan's file list; the other changes are the ticket and backlog bookkeeping.
- R8: every exit code has a test.
- R10/D16: respected.
- R11: output shows only the masked email; `hide_parameters` is on; no `str(exc)`; `sqlalchemy` and `asyncpg` loggers are silenced in `main()`; `tracebacklimit = 0`.

Correctness review:
- The lock order (app → user) follows ADR-0003 §10.
- `FOR SHARE` on the user blocks a concurrent disable or delete while still allowing FK `KEY SHARE`.
- The pre-check decides "already held" itself and does not infer it from a `ConflictError`.
- Exit 2 and exit 3 commit an empty transaction, which is harmless.
- Every lazy import sits inside the catch-all.
- `--help` exits 0 through argparse's `SystemExit`, which `except Exception` does not catch, so this is correct.

### Nits (do not block)
- `Makefile:22`: the usage line goes to stdout, not stderr. Also, `'$(email)'` breaks if the value contains a single quote. This is local dev only and the CLI validates the email anyway.
- `test_grant_output_fields` checks `email_verified: yes` only on the "already held" path. That is fine, but a separate test on a fresh grant would read more clearly.
- `bootstrap_super_admin` calls `find_assignment`, and `_grant_locked` then repeats the same lookup: one redundant SELECT in a one-shot CLI.

### Follow-ups (out of scope)
- FU1–FU5 from plan.md:
  - USR-04: decide the policy for unverified accounts.
  - ADM-004: write the audit entry at the `# R4` marker.
  - Deployment repo: `TRUSTED_PROXY_CIDRS` is missing from `x-app.environment`. The owner must add it before the next deploy, or the API, `migrate` and the CLI will all refuse to start in production.
  - Optional: read the email from stdin.
  - Lock order (admin app → user) for future disable/delete paths.
