# Review — ADM-002c

## Attempt 1 — 2026-11-15
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): `ruff check` All checks passed!; `ruff format --check` 371 files already formatted; `mypy app` Success: no issues found in 88 source files; `pytest -q` 1203 passed in 69.90s.
- alembic check: `No new upgrade operations detected.` (no schema change, none expected).
- Matches impl-log (1203 passed, mypy 88 files). Format count 371 vs 370 in impl-log; both green, see Nits.

### Acceptance criteria
- [x] 1. `set-admin-origins --origin …` replaces `apps[admin].origins` (D19-normalised, deduped, input order) — `app/modules/rbac/service.py:521-528`, `app/modules/rbac/schemas.py:126-137` (reuses `normalize_origins`, which dedupes via `dict.fromkeys`); tests `test_service_sets_origins`, `test_service_replaces_with_diff`, `test_cli_sets_origins_exit_0` pass.
- [x] 2. Only `origins` written on `admin` — the only assignment is `app.origins = list(origins)` (`service.py:523`); `AppService.update` not used. `test_service_invariants_unchanged` snapshots key/name/status/join_policy/org_mode/default_role_id/roles/permissions/role_permissions/assignment count before and after.
- [x] 3. Drift fails closed, no repair — `service.py:498-508` (missing row, status≠active, join_policy≠closed, org_mode≠none, default_role_id set → `ConflictError admin_not_seeded`); `test_service_refuses_drift[4 cases]` asserts origins and the drifted field unchanged; `test_service_refuses_missing_admin`; `test_cli_drift_exit_1` (exit 1).
- [x] 4. Loopback refused in staging/production (64), allowed locally — `service.py:490-496`; `test_service_loopback_refused[staging|production]`, `test_cli_loopback_production_exit_64`, `test_service_loopback_allowed_locally` (`app_env="local"`, justified deviation: `Settings.app_env` has no `development`).
- [x] 5. Origin held by another app (any status) refused (1) — `service.py:510-518` via `app_keys_holding_origins` (any status, excludes `admin`); `test_service_origin_taken[active|disabled]`, `test_cli_origin_taken_exit_1`.
- [x] 6. Apex refused (64) — `service.py:479` (post-normalisation, so `https://MMLABS.online/` is caught); `test_service_apex_refused[2 spellings]`, `test_cli_apex_exit_64`.
- [x] 7. ≤ 2 distinct origins after normalisation — `service.py:486`; `test_service_third_origin_refused`, `test_service_duplicates_count_once`, `test_cli_third_origin_exit_64`.
- [x] 8. Set-equal re-run writes nothing — `service.py:521` set comparison; `test_service_rerun_unchanged_no_write` (reordered + case/slash variant; `updated_at` and `APPS_CHANGED_KEY` unchanged), `test_cli_rerun_prints_unchanged`.
- [x] 9. `--clear` (mutually exclusive with `--origin`; empty→empty unchanged) — `app/cli.py:396-399` required mutually exclusive group; `test_service_clear`, `test_service_clear_when_empty_unchanged`, `test_cli_clear_exit_0`, `test_cli_usage_errors_exit_64[clear+origin]`.
- [x] 10. Dry-run rolls back — `cli.py:445` raises `_AdminOriginsRollback` inside the scope, so `session.begin()` (real scope, `cli.py` `_cli_session_scope`) / the savepoint (tests) rolls back; the `after_commit` origin-registry hook does not fire (`origin_registry.py:88-94`). `test_cli_dry_run_rolls_back` (origins and `updated_at` unchanged), `test_cli_clear_dry_run_rolls_back`.
- [x] 11. Masked fixed-line errors, exit codes 0/1/64 like `register-app` — every refusal maps to a constant (`cli.py:382-388`, `_ADMIN_ORIGIN_REASONS`, separate from `_REASONS` so `admin_app` cannot leak); pydantic error never printed (`cli.py:403-405`); catch-all never `str(exc)` (`cli.py:469-473`); `_Parser.error` drops argparse text. Codes match `_REASONS` (`loopback_not_allowed` 64, `origin_taken` 1). Tests `test_cli_refusals_never_echo_values[6]`, `test_cli_invalid_origin_exit_64[6]`, `test_cli_usage_errors_exit_64[6]` (incl. `secret-value` not echoed), `test_cli_db_error_exit_1_no_leak` (origin + DSN password not echoed), `test_cli_db_echo_exit_1`, `test_cli_conflict_exit_1`, `test_subprocess_set_admin_origins_usage_exit_64`.
- [x] 12. Admin row lock before any other admin query; every refusal before the first write — pure checks issue no SQL (`service.py:479-496`), then `lock_app` (`service.py:498`), drift and `origin_taken` checks, and only then the write (`service.py:523`). `test_service_locks_admin_first` asserts the first `apps` statement is the `FOR NO KEY UPDATE` select.
- [x] 13. `register-app --key admin` still refused; `register-app` refuses an origin set by the new command — existing tests unchanged; new `tests/test_cli_register_app.py::test_register_app_refuses_origin_set_by_set_admin_origins` passes.
- [x] 14. deploy.md runbook — `docs/deploy.md` "Admin access (ADM-002)" steps 1-7 (deploy → sign up/verify → grant-super-admin → set-admin-origins dry-run then real → wait 60 s → curl login → curl token → Bearer). Verified against the code:
  - Login body `{"email","password"}` matches `LoginRequest` (`app/modules/auth/schemas.py:26-31`, `extra="forbid"`, optional `turnstile_token` documented); response carries `account_id` (`LoginResponse`).
  - Token body `{"audience":"admin","account_id":…}` matches `TokenRequest` (`app/modules/tokens/schemas.py:9-14`); `audience` must equal `X-MM-App` (`tokens/service.py:55`) — both `admin`.
  - Both calls send `Origin: https://admin.mmlabs.online` and `X-MM-App: admin` (`deploy.md:362,367`); login writes the jar (`-c`), token reads and rewrites it (`-b -c`, handles rotation).
  - Secrets: `umask 077` + `mktemp` (0600) for jar and body, `read -r -s` for the password, body via builtin `printf` to a file and `-d @file` (not in argv/`ps`/history), `unset pw`, both files deleted (`deploy.md:357-370`).
  - Fallback compose command matches the existing register-app/grant-super-admin form; exit table, break-glass, host policy present. ADR-0008 amendment added.
- [x] 15. `make check` green, `alembic check` clean — see Checks.

### Issues
None.

### Rules R1–R11
No violations found. R1: CLI → `AppService.set_admin_origins` → existing repository methods; service raises `AppError` subclasses; CLI receives a frozen dataclass of plain values. R2: no schema change. R3: no endpoint. R4: comment at `service.py:522`; workflow run is the audit trail until ADM-004 (documented). R5/R11: fixed messages, CLI engine with `hide_parameters=True`, no submitted values in stderr. R7: all changed files are in the plan's list; ticket/backlog state edits are workflow bookkeeping.

### Nits (do not block)
- `docs/deploy.md:357`: `umask 077` persists in the operator's interactive shell afterwards; wrapping the block in `( … )` would scope it.
- `docs/deploy.md:360`: the `printf` JSON body needs hand-escaping for `"`/`\` in the password (documented); a `python3 -c 'import json,sys…'` or `jq -n --arg` builder would remove that footgun. The access token response is printed to the terminal (scrollback) — acceptable for a runbook.
- `tests/test_cli_set_admin_origins.py` ~line 110-112: extra blank lines after the service section header.
- impl-log reports 370 formatted files; reviewer run reports 371. Both green; cosmetic.

### Follow-ups (out of scope)
- ADM-002b: `admin:*` guard on admin origin changes through `AppService.update`.
- Admin-UI ticket: non-credentialed Bearer-only CORS on `/api/v1/*`; strict CSP/no third-party scripts/SRI before serving anything at the admin origin.
- ADM-004: audit entry for `set-admin-origins` (actor `system:cli`, added/removed).
- DB-level guard against two apps holding the same origin (concurrent `register-app` vs `set-admin-origins` is not serialised by the admin row lock; mitigated by the ops workflow concurrency group).
- Deployment repo PR: `set-admin-origins` input (`dry_run` default on, shared concurrency group) in `ops-org-admin-api`.
