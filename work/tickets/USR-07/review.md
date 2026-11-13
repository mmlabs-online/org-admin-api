## Attempt 1 — 2026-11-13
Verdict: PASS

Diff reviewed: `git diff fb7978f HEAD` (USR-07 plan commit → `cd3cafe`). Files: `Makefile`, `app/cli.py`, `app/modules/rbac/{repository,schemas,service}.py`, `docs/deploy.md`, `tests/test_cli_register_app.py`, `work/backlog.md`, `work/tickets/USR-07/{impl-log,ticket}.md`.

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` → exit 0. `ruff check`: All checks passed · `ruff format --check`: 308 files already formatted · `mypy app`: Success: no issues found in 77 source files · `pytest -q`: **896 passed** in 30.9 s. Matches impl-log on every gate (896 passed; impl-log reports 307 formatted files vs 308 here, a count difference with no failures either way and no effect on the verdict).
- alembic check (if migrations changed): n/a — no model or migration change in the diff.
- `make -n register-app ARGS="--key x --name X --origin https://x.mmlabs.online"` → prints `uv run python -m app.cli register-app --key x --name X --origin https://x.mmlabs.online`; `make register-app` without ARGS → recipe exits 64 (make exits 2), same as `grant-super-admin`.
- `tests/test_cli_grant_super_admin.py` is not in the diff and passes.

### Acceptance criteria
- [x] AC1 create with `K:member` default role — `test_create_app_and_member_role`, `test_create_two_separate_apps` (pass); `service.py` `register` → `self.create(AppCreate(...))` which builds the member role and sets `default_role_id`.
- [x] AC2 idempotent re-run, no write — `test_rerun_unchanged_no_write` asserts `unchanged app ricotdin` and `updated_at` unchanged; `AppService.update` returns early when nothing differs; origins only passed when the set differs.
- [x] AC3 update semantics — `test_origins_replaced_with_diff`, `test_policy_kept_when_flag_omitted`, `test_join_policy_changed_when_passed`, `test_org_mode_change_refused`, `test_same_org_mode_accepted`, `test_disabled_app_refused_exit_3`, `test_status_and_default_role_untouched` (pass). `AppUpdate` fields built from `name`, optional `origins`, optional `join_policy` only.
- [x] AC4 `admin` refused (CLI before DB, service too) — `test_admin_refused_exit_64` (`_never` scope), `test_service_refuses_admin`; `cli.py` `_parse_register` `args.key == "admin"`; `service.py` `register` first statement.
- [x] AC5 D19 origins via `normalize_origins`, not echoed; loopback refused in staging/production; origin held by another app refused — `AppRegister._origins` validator; `test_invalid_origin_exit_64` (4 params), `test_loopback_refused_in_production`, `test_loopback_allowed_locally`, `test_origin_taken_exit_1`, `test_origin_taken_by_admin_app`; repository `app_keys_holding_origins` (all statuses, `admin` included).
- [x] AC6 permissions additive, `is_admin=false`, wildcard/prefix/malformed refused, existing is_admin refused, land in member role — `test_permissions_added_to_member_role`, `test_permission_rejected_exit_64` (4 params), `test_existing_admin_permission_refused`.
- [x] AC7 member role rebuilt only on create or added permission (D9) — `test_rerun_without_new_permission_keeps_tightening`; `if added: _build_for_app(app)`.
- [x] AC8 `lock_app` first — `test_register_locks_app_first` (statement recorder: `FOR NO KEY UPDATE` on `apps` precedes every INSERT/UPDATE); every refusal is decided before the first write.
- [x] AC9 grant-super-admin discipline (fixed messages, exit codes 0/1/3/64, own engine with `hide_parameters`, no tracebacks/DSN/values) — `test_usage_errors_exit_64` (8 params), `test_db_echo_refused_exit_1`, `test_db_error_exit_1_no_leak`, `test_concurrent_create_conflict_exit_1`, `test_subprocess_register_usage_exit_64`; CLI never prints `str(exc)` or the pydantic error; reuses `_cli_session_scope` (`hide_parameters=True`, `NullPool`).
- [x] AC10 `--dry-run` rolls back — `test_dry_run_rolls_back` (savepoint scope; no row afterwards; last line `MSG_DRY_RUN`); production scope rolls back via `session.begin()` on `_Rollback`.
- [x] AC11 reuses RBAC service layer (R1) — `cli.py` imports only `AppRegister`, `AppService`, `AppError`/`ConflictError`; no ORM model or repository; `register` reuses `create`/`update`/`PermissionService.create`/`_build_for_app`.
- [x] AC12 `make register-app ARGS='…'` — see Checks.
- [x] AC13 runbook in `docs/deploy.md` — "Register an app (USR-07)" with ricotdin + ricotdin-staging worked example, output sample, semantics, exit table, compose `migrate` fallback, local make; bootstrap step 2 via the ops workflow; one-time setup step 6. Cross-checked against `mmlabs-online/deployment` `.github/workflows/ops-org-admin-api.yml` (branch `org-admin-api-ops`, commit a6f27e1): workflow name, inputs (`key`, `name`, `origins` comma/space, `permissions`, `join_policy`/`org_mode` with `keep`, `dry_run` default true, `email`), environment `org-admin-api-ops`, `main` only, run via `docker compose … run --rm --no-deps migrate /app/.venv/bin/python -m app.cli`, output in the run summary, email masked / payload caveat — all consistent with the doc.
- [x] AC14 grant-super-admin unchanged — `USAGE` and grant messages untouched; only the settings load was extracted into `_load_settings` with identical messages; grant tests pass unmodified.
- [x] AC15 `make check` green — see Checks.

### Issues
None blocking or should-fix.

### Rules R1–R11
No violations found.
- R1: business rules in `AppService.register`; query in `RbacRepository.app_keys_holding_origins`; CLI parses/prints only.
- R2: no schema change.
- R3: no endpoint; CLI not imported by `app.main` (existing `test_cli_not_imported_by_app` passes).
- R4: marker `# R4: ADM-004 audit entry (actor system:cli)` before the first write; workflow run is the interim audit trail (documented).
- R5/R11: no new config; no secrets; errors are fixed lines; DB errors never stringified; `hide_parameters=True`.
- R6: service refusals carry `{"field", "reason"}` details.
- R7: every changed file is in the plan's file list (ticket/backlog/impl-log are workflow files).
- R8: every plan test exists and passes.

### Nits (do not block)
- `docs/deploy.md` "Browser origins" paragraph: the edited line now exceeds the file's ~100-column wrap; rewrap.
- `Makefile` `register-app`: `$(ARGS)` is word-split by the shell, so a `--name` with spaces needs nested quotes (`ARGS="--name 'Ricotdin Staging' …"`); a one-line hint in the runbook's "Locally" line would help.
- On the create path `lock_app` locks nothing (no row yet); concurrent creates of the same key are handled by the unique key → `MSG_CONFLICT` (tested). As the plan states, two concurrent registrations of *different* keys claiming the same origin are not race-proof; only the workflow's shared concurrency group prevents it (the manual compose fallback and the admin API do not).
- `-h` is a usage error (exit 64) rather than argparse help — documented deviation, acceptable.

### Follow-ups (out of scope)
- Apply the one-origin-one-app rule to `POST/PATCH /api/v1/apps` and record it as a D19 amendment (ideally with a DB-level guard, which would also close the cross-key race above).
- Owner: choose ricotdin permission keys (e.g. `ricotdin:app:use`) and register them with `permissions`.
- Merge deployment PR #56 (`ops-org-admin-api.yml`) and create the `org-admin-api-ops` GitHub Environment before the first run.
- After ADM-004: replace the R4 marker with a real audit entry (actor `system:cli`).
