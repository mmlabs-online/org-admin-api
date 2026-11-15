## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `app/modules/membership/service.py` | Extracted `lock_joinable_app` / `join_default_role` from `JoinService.join` (behaviour and order unchanged), shared with the CLI |
| `app/modules/auth/provisioning.py` (new) | `vet_and_hash_password`, `AccountProvisioningService.create_verified` (CLI only) |
| `app/cli.py` | `create-account` subcommand, `_Tty`/`_SystemTty`, `_normalize_email`, `tty` kwarg on `amain`, docstring |
| `tests/test_cli_create_account.py` (new) | All tests in the plan |
| `docs/adr/0009-operator-vouched-verified-email.md` (new) | ADR |
| `docs/identity-architecture.md` | D16 amended |
| `docs/adr/0007-email-verification-and-delivery.md` | Amendment line under Context |
| `docs/deploy.md` | New "Create an account from the CLI" section; line 268, step 1/3 and setup step 6 amended |

### Tests added / changed
- `tests/test_cli_create_account.py`: every test in the plan (51 cases incl. parametrization):
  test_create_verified_user_no_join, test_email_verified_at_from_db_clock,
  test_create_with_join_then_login_and_token, test_create_mixed_case_email_normalized,
  test_create_no_email_tokens_or_send_log, test_existing_email_exit_1_unchanged,
  test_weak_password_refused, test_password_max_length_128_accepted,
  test_password_mismatch_refused, test_email_retype_mismatch_refused,
  test_non_tty_refused_before_prompt_and_db, test_getpass_warning_refused,
  test_prompt_eof_or_interrupt_refused, test_password_option_is_usage_error,
  test_db_echo_refused_before_prompts, test_join_refusals_create_nothing,
  test_failure_after_insert_rolls_back_user, test_locks_app_before_user_insert,
  test_hash_before_session_scope, test_secret_not_in_output_or_logs, test_db_error_no_leak,
  test_audit_log_line_ids_only, test_allowed_in_every_app_env (local, test; production settings
  not tried, they need extra keys), test_subprocess_non_tty_exit_1,
  test_pty_end_to_end_no_echo_no_argv.
- `tests/test_auth_join.py` unchanged and passing.

### Checks (actual output summary)
- make check (ruff, format, mypy app, pytest): ruff "All checks passed!", format "381 files already formatted", mypy "Success: no issues found in 89 source files", pytest "1254 passed, 1 warning in 72.14s" (the warning is the stdlib forkpty multi-thread DeprecationWarning from the pty test). Local Postgres on localhost:5432, DB `org_admin_test`; nothing had to be started.
- alembic check: not applicable (no migration).

### Out-of-plan files
None. Small deviations: two `# noqa: S105` on message constants in `app/cli.py`, `# noqa: S603/S606` in the new test file (ruff). The `grant-super-admin` docstring bullet was left as is (already scoped to that command).

### Follow-ups (noticed, not done)
- Those in plan.md (ADM-004 audit with `system:cli`, deployment-repo note, optional make target, early read-only check, RLIMIT_CORE).
- `uv run mypy .` (whole repo) reports errors in tests; `make check` only checks `app`, so pre-existing and untouched.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: product code ~+240 (cli ~+160, provisioning +78, membership ~+/-30), tests +647, docs ~+170.

## Attempt 2 — 2026-11-15 (REWORK: adapt to ADM-004 after merging `main`)

### Files touched
| File | Required because |
|---|---|
| `app/modules/auth/provisioning.py` | `_grant_locked` now takes `actor=` (`SYSTEM_CLI`); writes real audit rows (R4) via `AuditLogger`: `user.created` (target user, changes `status`, `verified`) and, with `--join`, `app.joined` (target role_assignment, `role_key`), actor `system:cli`, `AuditContext.NONE`, same transaction. The ids-only log line is kept. |
| `app/modules/audit/actions.py` | new `AuditAction.USER_CREATED = "user.created"` (the DB only has a format check on `action`, so no migration) |
| `tests/test_cli_create_account.py` | audit rows asserted (actor system:cli, no email/password/hash, targets, changes); none after every refusal (join refusals, existing email, failure after insert); `_grant_locked` stub takes `**kwargs` |
| `docs/adr/0010-operator-vouched-verified-email.md` | accepted risk b) now says the run is audited; operator still not named |
| `docs/deploy.md` | create-account runbook: audit rows exist; keep a short note to record the operator |

### Tests added / changed
- test_create_verified_user_no_join, test_create_with_join_then_login_and_token: audit rows.
- test_join_refusals_create_nothing, test_failure_after_insert_rolls_back_user, test_existing_email_exit_1_unchanged: no audit rows.
- `tests/test_audit_coverage.py` unchanged (covers Bearer routes only) and passing.

### Checks (actual output summary)
- make check: ruff "All checks passed!", format "418 files already formatted", mypy "Success: no issues found in 99 source files", pytest "1445 passed, 1 warning in 102.66s" (warning: forkpty DeprecationWarning from the pty test).
- Postgres was stopped; started with `service postgresql start`. alembic check: n/a (no migration).

### Out-of-plan files
`app/modules/audit/actions.py` (enum entry, reason above).

### Follow-ups (noticed, not done)
- Audit rows carry no operator identity (CLI has none); a future `--operator` or env-based label could add it.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: provisioning ~+25/-8, actions +1, tests ~+45, docs ~+8/-6.
