Status: READY

# Plan — ADM-002c: Set admin app origins from the CLI

## Spec summary
- spec §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`." Since ADM-002 that means a Bearer `aud=admin` token (ADR-0008). ADR-0008 §Decision 7 says "Admin tokens need a registered admin origin (ADM-002c)", and §Consequences says "Until ADM-002c sets admin origins in production, no admin token can be obtained there".
- spec §5.3: each app has "its allowed browser `origins`" … "app origins are limited to `mmlabs.online`, its subdomains and localhost (D19)". `admin` = `closed`.
- D19 (`docs/identity-architecture.md` §8): "only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback … Any other origin → 422."
- Ticket scope: "New CLI command `set-admin-origins` via a new `AppService.set_admin_origins`: follows D19, refuses loopback in staging/production, refuses origins held by another app, never changes `join_policy`, `org_mode`, status or roles of `admin`. Runbook in `docs/deploy.md`; the ops workflow input in `mmlabs-online/deployment` is a separate PR (owner). Non-credentialed admin CORS only if an admin UI exists by then." No admin UI exists, so admin CORS is out of scope (follow-up).
- R3 (no new endpoint), R4 (audit once ADM-004 lands; the workflow run is the audit trail until then), R5/R11 (fixed error lines, no submitted values echoed), R7 (stay in plan).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `set-admin-origins --origin …` replaces `apps[admin].origins` (replace semantics, D19-normalised, deduplicated, stored in input order) | `tests/test_cli_set_admin_origins.py › test_cli_sets_origins_exit_0`, `test_service_replaces_with_diff` |
| 2 | Only `origins` changes on `admin`. `key`, `name`, `status`, `join_policy`, `org_mode`, `default_role_id`, roles, permissions, role_permissions and assignments stay unchanged | `test_service_invariants_unchanged` |
| 3 | Fails closed, with no repair, when the admin row is missing or has drifted from its seed (status `active`, join_policy `closed`, org_mode `none`, default_role_id NULL): exit 1, nothing written | `test_service_refuses_drift[...]` (4 cases), `test_service_refuses_missing_admin`, `test_cli_drift_exit_1` |
| 4 | Loopback refused in staging/production (exit 64) and accepted in development/test | `test_service_loopback_refused[staging|production]`, `test_cli_loopback_production_exit_64`, `test_service_loopback_allowed_locally` |
| 5 | Origin held by another app (any status) refused (exit 1) | `test_service_origin_taken[active|disabled]`, `test_cli_origin_taken_exit_1` |
| 6 | Apex `https://mmlabs.online` refused (exit 64) | `test_service_apex_refused`, `test_cli_apex_exit_64` |
| 7 | At most 2 distinct origins, counted after normalisation (exit 64) | `test_service_third_origin_refused`, `test_service_duplicates_count_once`, `test_cli_third_origin_exit_64` |
| 8 | A set-equal re-run reports `unchanged` and writes nothing (`updated_at` unchanged, apps not marked changed) | `test_service_rerun_unchanged_no_write`, `test_cli_rerun_prints_unchanged` |
| 9 | `--clear` empties origins (break-glass). It is mutually exclusive with `--origin`. Clearing an already-empty set reports `unchanged` | `test_service_clear`, `test_service_clear_when_empty_unchanged`, `test_cli_clear_exit_0`, `test_cli_usage_errors_exit_64[clear+origin]` |
| 10 | `--dry-run` prints the result plus `dry-run: rolled back, nothing was saved`, and the DB is unchanged | `test_cli_dry_run_rolls_back`, `test_cli_clear_dry_run_rolls_back` |
| 11 | Masked fixed-line errors like `register-app`: stderr never echoes a submitted value, a traceback or the DSN. Exit codes are 0, 1 or 64 | `test_cli_refusals_never_echo_values[...]`, `test_cli_db_error_exit_1_no_leak`, `test_cli_db_echo_exit_1`, `test_cli_conflict_exit_1`, `test_subprocess_set_admin_origins_usage_exit_64` |
| 12 | The service takes the admin row lock (`lock_app("admin")`) before any other admin query | `test_service_locks_admin_first` |
| 13 | `register-app --key admin` keeps refusing (exit 64), and `register-app` refuses an origin set on `admin` by the new command (exit 1) | existing `test_cli_register_app.py › test_admin_refused_exit_64`, `test_origin_taken_by_admin_app` (unchanged), plus new `test_register_app_refuses_origin_set_by_set_admin_origins` |
| 14 | deploy.md runbook: deploy → sign up/verify → grant-super-admin → set-admin-origins → login with `X-MM-App: admin` → token | Reviewer reads `docs/deploy.md` "Set admin origins (ADM-002c)" and the rewritten "Admin access (ADM-002)" steps |
| 15 | `make check` green, `alembic check` no drift (no schema change) | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `AdminOriginsSet` (`extra="forbid"`, `origins: list[str] = Field(default_factory=list, max_length=MAX_ORIGINS)`, field validator `normalize_origins`) | Validated input for the service, reusing D19. An empty list is allowed (`--clear`) |
| `app/modules/rbac/service.py` | Add `MAX_ADMIN_ORIGINS = 2`, `APEX_ORIGIN = "https://mmlabs.online"`, `AdminOriginsOutcome` (`updated`/`unchanged`), frozen dataclass `AdminOriginsResult`, and `AppService.set_admin_origins(data, *, app_env)` | The business rules live in the service (R1). They are enforced there, not only in the CLI |
| `app/cli.py` | Add the `set-admin-origins` subcommand: dispatch in `amain`, `_parse_set_admin_origins`, `_set_admin_origins`, `_print_admin_origins`, `_AdminOriginsRollback`, `_ADMIN_ORIGIN_REASONS`, new fixed messages, usage line. Update the module docstring | Operator entry point (ticket AC 1) |
| `Makefile` | Add target `set-admin-origins` (`make set-admin-origins ARGS='--origin https://admin.mmlabs.online'`), same shape as `register-app`, and add it to `.PHONY` | Local parity with `register-app`. deploy.md cites it |
| `tests/test_cli_set_admin_origins.py` | New: the service and CLI tests below | AC 1–13 (R8) |
| `tests/test_cli_register_app.py` | Add one test: `register-app` refused after `set-admin-origins` claims the origin | AC 13. The existing tests stay unchanged |
| `docs/deploy.md` | New section "Set admin origins (ADM-002c)". Rewrite "Admin access (ADM-002)": drop the "Until ADM-002c ships" paragraph and update the runbook steps. Add one line in "Browser origins (CORS)" and in One-time setup step 6 (command list) | Ticket AC 3 |
| `docs/adr/0008-admin-api-authentication.md` | Append "## Amendment — admin origins (ADM-002c, 2026-11-15)": the admin origin policy (dedicated reserved host, apex refused, max 2, not shared with any app, CLI-only write path, `--clear` break-glass, fail closed on drift). Update the Consequences bullet | The admin origin policy outlives this ticket (planner rule 7). An amendment rather than a new ADR, because it completes ADR-0008 §7 |

No other files. No migration, no endpoint, no config/env key.

## Tests to write
All are in `tests/test_cli_set_admin_origins.py` unless stated. Use the helpers and patterns from `tests/test_cli_register_app.py` (`_scope`, `_savepoint_scope`, `_never`, `_assert_no_leak`, `Settings(...)` with `app_env`, `make_app` from `tests/rbac_factories.py`). Constants: `ADMIN = "https://admin.mmlabs.online"`, `ADMIN2 = "https://admin2.mmlabs.online"`.

| Test (file › name) | Covers |
|---|---|
| `test_service_sets_origins` | Empty → `[ADMIN]`; outcome `updated`; `origins_added == (ADMIN,)`; `session.info[APPS_CHANGED_KEY]` is True |
| `test_service_replaces_with_diff` | `[ADMIN]` → `[ADMIN2]`: added/removed correct, stored `[ADMIN2]` |
| `test_service_invariants_unchanged` | Snapshot of key, name, status, join_policy, org_mode, default_role_id, the role/permission/role_permission rows of `admin` and the assignment count, taken before; all equal after a successful set |
| `test_service_rerun_unchanged_no_write` | Same set in another order (and with an uppercase/trailing-slash variant that normalises equal): `unchanged`, `updated_at` unchanged, apps not marked changed |
| `test_service_clear` | `[ADMIN]` → `[]`: `updated`, `origins_removed == (ADMIN,)` |
| `test_service_clear_when_empty_unchanged` | `[]` → `[]`: `unchanged`, no write |
| `test_service_loopback_refused[staging, production]` | `http://localhost:3000` → `UnprocessableError` reason `loopback_not_allowed`, nothing written |
| `test_service_loopback_allowed_locally` | `app_env="development"`: loopback accepted |
| `test_service_apex_refused` | `https://mmlabs.online` (and `https://MMLABS.online/`) → reason `apex_not_allowed` |
| `test_service_third_origin_refused` | 3 distinct origins → reason `too_many_origins` |
| `test_service_duplicates_count_once` | `[ADMIN, ADMIN + "/", ADMIN2]` → accepted, stored `[ADMIN, ADMIN2]` |
| `test_service_origin_taken[active, disabled]` | Another app (active or disabled) holds `ADMIN` → `ConflictError` reason `origin_taken`, admin unchanged |
| `test_service_loopback_not_checked_for_taken` | In development another app holds `http://localhost:3000`: the admin may hold it too (same as `register`) |
| `test_service_refuses_drift[status_disabled, join_open, org_required, default_role_set]` | Mutate the admin row in the test transaction → `ConflictError` reason `admin_not_seeded`; origins and the mutated field unchanged (no repair) |
| `test_service_refuses_missing_admin` | Monkeypatch `RbacRepository.lock_app` to return None for `admin` → reason `admin_not_seeded` |
| `test_service_locks_admin_first` | Same technique as `test_register_locks_app_first`: the first SQL statement on the app table is the `FOR NO KEY UPDATE` lock on `admin` (input refusals that need no DB may come earlier and issue no SQL) |
| `test_cli_sets_origins_exit_0` | Exact stdout: `updated app admin`, `join_policy: closed`, `status: active`, `origins: https://admin.mmlabs.online`, `+ origin https://admin.mmlabs.online`, `MSG_CORS_NOTE`; stderr empty |
| `test_cli_rerun_prints_unchanged` | `unchanged app admin`, no `+`/`-` lines, no CORS note, exit 0 |
| `test_cli_clear_exit_0` | `--clear`: `origins: (none)`, `- origin …`, `MSG_CORS_NOTE`, `MSG_ADMIN_CLOSED`; DB origins `[]` |
| `test_cli_dry_run_rolls_back` | `_savepoint_scope`; output plus `MSG_DRY_RUN`; DB origins and `updated_at` unchanged |
| `test_cli_clear_dry_run_rolls_back` | `--clear --dry-run`: origins kept |
| `test_cli_usage_errors_exit_64[no_args, clear+origin, key_admin, abbrev_orig, unknown_flag, origin_without_value]` | `SET_ADMIN_ORIGINS_USAGE` on stderr, exit 64, session scope never entered (`_never`) |
| `test_cli_invalid_origin_exit_64[http, api_host, foreign, port, ip, empty]` | `MSG_ADMIN_BAD_ORIGIN`, exit 64, `_never` scope, value not echoed |
| `test_cli_loopback_production_exit_64` | `MSG_LOOPBACK`, exit 64 |
| `test_cli_apex_exit_64` | `MSG_ADMIN_APEX`, exit 64 |
| `test_cli_third_origin_exit_64` | `MSG_ADMIN_TOO_MANY`, exit 64 |
| `test_cli_origin_taken_exit_1` | `MSG_ORIGIN_TAKEN`, exit 1 |
| `test_cli_drift_exit_1` | `MSG_ADMIN_NOT_SEEDED`, exit 1 |
| `test_cli_refusals_never_echo_values[...]` | For each refusal path above, `_assert_no_leak(out, err, <submitted origin>)` and the error goes to stderr only |
| `test_cli_db_echo_exit_1` | `Settings(db_echo=True)` → `MSG_DB_ECHO`, exit 1, `_never` |
| `test_cli_db_error_exit_1_no_leak` | Scope raises `DBAPIError` carrying the origin and DSN → `MSG_SET_ORIGINS_UNEXPECTED`, no leak |
| `test_cli_conflict_exit_1` | Service raises a `ConflictError` with no known reason → `MSG_CONFLICT`, exit 1 |
| `test_subprocess_set_admin_origins_usage_exit_64` | `python -m app.cli set-admin-origins` (no args) in a subprocess: exit 64, usage line, no traceback |
| `tests/test_cli_register_app.py › test_register_app_refuses_origin_set_by_set_admin_origins` | `amain(["set-admin-origins", "--origin", ADMIN])` then `register-app --key ricotdin … --origin ADMIN` → exit 1 `MSG_ORIGIN_TAKEN` |

## Approach
1. **Schema** (`schemas.py`): add `AdminOriginsSet`, as described above. The pydantic `max_length=MAX_ORIGINS` bounds the input. The admin cap of 2 is applied in the service after deduplication.
2. **Service** (`AppService.set_admin_origins(data: AdminOriginsSet, *, app_env: str) -> AdminOriginsResult`). The docstring says the caller owns the transaction, that it writes only `origins`, and "R4: ADM-004 audit entry (actor system:cli) in the same transaction". Every refusal uses `_register_refusal(..., field="origins" | "key", reason=...)` and happens before any write.
   1. Pure input checks (no SQL):
      - any origin `== APEX_ORIGIN` → `UnprocessableError` `apex_not_allowed`;
      - `len(origins) > MAX_ADMIN_ORIGINS` → `UnprocessableError` `too_many_origins`;
      - `app_env in {"staging","production"}` and any `is_loopback_origin` → `UnprocessableError` `loopback_not_allowed` (same check as `register`).
   2. `app = await self.repo.lock_app(ADMIN_APP_KEY)`. If it is None, or `status != active`, `join_policy != closed`, `org_mode != none` or `default_role_id is not None` → `ConflictError` `admin_not_seeded`, field `key` (fail closed, never repaired). The ConflictError message is fixed and holds no values.
   3. `claimed = [o for o in origins if not is_loopback_origin(o)]`. If `await self.repo.app_keys_holding_origins(claimed, ADMIN_APP_KEY)` returns anything → `ConflictError` `origin_taken`.
   4. If `set(origins) == set(app.origins)` → return `unchanged` with no write and no `mark_apps_changed`.
   5. `app.origins = list(origins)`; `await self.repo.save()` (`IntegrityError` → `ConflictError("App was changed concurrently")`); `self.repo.mark_apps_changed()`. Do **not** route through `AppService.update` (generic field loop, and its admin origin guard belongs to ADM-002b).
   6. Return `AdminOriginsResult(outcome, key, status, join_policy, org_mode, origins: tuple, origins_added: tuple (sorted), origins_removed: tuple (sorted))`. These are plain values, so the CLI never touches the ORM (R1).
3. **CLI** (`app/cli.py`).
   - `amain`: `if argv[:1] == ["set-admin-origins"]: return await _set_admin_origins(argv[1:], settings=..., session_scope=...)`.
   - `_parse_set_admin_origins`: `_Parser(prog="python -m app.cli set-admin-origins", allow_abbrev=False, add_help=False)`. A required mutually exclusive group holds `--origin` (`action="append"`) and `--clear` (`store_true`). `--dry-run` sits outside the group. There is no `--key`, so `--key admin` is an unknown argument → usage error. Build `AdminOriginsSet(origins=args.origin or [])` (empty when `--clear`). On `ValidationError` raise `_Refused(MSG_ADMIN_BAD_ORIGIN, EXIT_USAGE)` and never print the pydantic error.
   - `_set_admin_origins`: the same structure as `_register_app`. `_load_settings` (config / `DB_ECHO` → exit 1). Use the scope. Raise `_AdminOriginsRollback(result)` when `--dry-run`, then print plus `MSG_DRY_RUN`. Map `AppError` via `_ADMIN_ORIGIN_REASONS`; otherwise `ConflictError` → `MSG_CONFLICT`/1; otherwise `MSG_SET_ORIGINS_UNEXPECTED`/1. A catch-all `Exception` → `MSG_SET_ORIGINS_UNEXPECTED`/1. Never `str(exc)`. Print only after the scope has committed.
   - `_ADMIN_ORIGIN_REASONS` = `{"loopback_not_allowed": (MSG_LOOPBACK, 64), "apex_not_allowed": (MSG_ADMIN_APEX, 64), "too_many_origins": (MSG_ADMIN_TOO_MANY, 64), "origin_taken": (MSG_ORIGIN_TAKEN, 1), "admin_not_seeded": (MSG_ADMIN_NOT_SEEDED, 1)}`.
   - New constants (fixed text, never interpolated with input):
     - `SET_ADMIN_ORIGINS_USAGE = "usage: python -m app.cli set-admin-origins (--origin <origin> [--origin <origin>] | --clear) [--dry-run]"`
     - `MSG_ADMIN_BAD_ORIGIN = "error: each --origin must be https://<host>.mmlabs.online (not api.mmlabs.online, default port), or loopback"`
     - `MSG_ADMIN_APEX = "error: https://mmlabs.online cannot be an admin origin; use a dedicated host"`
     - `MSG_ADMIN_TOO_MANY = "error: at most 2 admin origins"`
     - `MSG_ADMIN_NOT_SEEDED = "error: admin app missing or not in its seeded state; nothing was changed"`
     - `MSG_SET_ORIGINS_UNEXPECTED = "error: unexpected failure; the admin origins change was not confirmed"`
     - `MSG_ADMIN_CLOSED = "note: no new admin tokens can be obtained once this applies; issued tokens last up to their TTL"`
   - Reused: `MSG_LOOPBACK`, `MSG_ORIGIN_TAKEN`, `MSG_CONFLICT`, `MSG_DRY_RUN`, `MSG_CORS_NOTE`, `MSG_CONFIG`, `MSG_DB_ECHO`.
   - `_print_admin_origins(result)` is a dedicated printer. It does not use `_print_result`, which would print a misleading `member role: missing`. Output lines: `"{outcome} app admin"`, `join_policy: …`, `status: …`, `origins: <space-joined or "(none)">`, then the `+ origin` and `- origin` lines. Add `MSG_CORS_NOTE` if anything was added or removed, and `MSG_ADMIN_CLOSED` if updated and the result has no origins. Origins are not secret and are printed on success only, never on refusal.
   - Exit codes: 0 for updated, unchanged or dry run. 1 for settings, `DB_ECHO`, admin missing or drifted, origin taken, conflict or database errors. 64 for usage, a bad origin, apex, too many, or loopback in staging/production. These are consistent with `register-app`. There is no exit 3 here: a disabled admin counts as drift, so it exits 1.
   - `register-app` is untouched. It keeps refusing `admin` in both the parser and the service.
4. **Makefile**: add a `set-admin-origins` target that requires `ARGS`.
5. **Docs**:
   - `docs/deploy.md`, new section "Set admin origins (ADM-002c)":
     - Purpose. Prerequisites: a release containing ADM-002c, plus the ops environment.
     - Ops workflow command `set-admin-origins` (input added by the separate deployment PR), with `dry_run` on first.
     - Fallback docker compose command. Locally: `make set-admin-origins ARGS=…`.
     - Expected output. Semantics: replace, max 2, apex refused, loopback refused in staging/production, no sharing with any app, only `origins` changes, fail closed on drift, 60 s delay.
     - Exit table (0/1/64).
     - **Break-glass** `--clear`: no new admin tokens after at most 60 s. Tokens already issued live up to `ACCESS_TOKEN_TTL_SECONDS` (15 min). To cut them sooner, revoke the role or disable the user (per-request reload, ADR-0008 §3).
     - **Host policy:** `https://admin.mmlabs.online` is reserved for the future admin UI. No DNS record or tunnel route is created for it until an admin UI exists. It must never be routed to another service, and it must be audited when DNS/tunnel routes change (subdomain takeover = an admin origin).
     - Until ADM-004, the workflow run is the audit trail.
   - `docs/deploy.md`, rewrite "Admin access (ADM-002)" runbook:
     1. Deploy.
     2. Sign up and verify your email through a registered app.
     3. `grant-super-admin`.
     4. `set-admin-origins --origin https://admin.mmlabs.online` (dry run, then real).
     5. Wait 60 s.
     6. curl login, then curl token. Both calls send an explicit `Origin: https://admin.mmlabs.online` and `X-MM-App: admin`. Create the cookie jar with `umask 077` (mode 0600) and delete it afterwards. Read the password with `read -s` into a request body file at mode 0600 (`-d @file`), never on the command line or in history. After 3 failed logins, Turnstile is required and curl cannot pass it, so get the password right (or wait for the backoff).
     7. Call the admin endpoints with `Authorization: Bearer`.
   - State that the Origin check is CSRF protection, not authentication, and that the browser console is not a supported path until an admin UI exists.
   - Keep the request bodies exactly matching the existing login/token schemas: check `app/modules/auth` and `app/modules/tokens` schemas for field names before writing them.
   - "Browser origins (CORS)": one line saying that the `admin` origins are set only with `set-admin-origins`.
   - One-time setup step 6: add `set-admin-origins` to the workflow command list, "once the deployment PR adds it".
   - ADR-0008 amendment: as listed in the files table.
6. **Rules**:
   - R1: router-free; the CLI calls the service only. The service raises `AppError` subclasses. The repository is unchanged, reusing `lock_app`, `app_keys_holding_origins`, `save` and `mark_apps_changed`.
   - R2: no schema change.
   - R3: no endpoint, so `test_route_auth_classes` is unaffected.
   - R4: comment in the service. The workflow run is the audit trail until ADM-004.
   - R5/R11: fixed lines, no values in errors, CLI engine with `hide_parameters=True` (existing `_cli_session_scope`).
   - R6: n/a over HTTP. Service errors carry `details=[{"field","reason"}]` only.
   - R7: follow-ups listed below. R8: tests above.
   - R10: no hosted login UI.

## Specialist input used
- **identity-access-engineer.** Adopted: the service design (lock, refuse rather than repair, loopback, `origin_taken`, write only origins, unchanged with no write); the invariants test; parser style; reuse of the reasons and messages; exit codes; the dedicated printer and its format; the dedicated host `admin.mmlabs.online`; the curl runbook (cookie jar 0600, password out of history, Turnstile after 3 failures, no browser-console path); no admin CORS now; the test list; the 60 s note.
  - Open Q1 (`--clear`): resolved per orchestrator guidance; included.
  - Open Q2 (reserving the host, ops workflow input): the host is an owner assumption below. The workflow input is a separate deployment PR handled by the orchestrator.
  - Open Q3 (log `admin_origins_changed`): rejected for now, as not in the ticket. The workflow run summary already records the diff, and ADM-004 brings the audit entry. Listed as a follow-up.
- **security-architect.** Adopted:
  - R1: write only origins, fail closed on drift across 4 fields, a test per case.
  - R2: apex refused, other apps' origins refused, loopback, max 2, dedicated host, no DNS until a UI exists, all in the service.
  - R3: `--clear`, replace semantics, `+`/`-` lines and the 60 s note.
  - R5: no production-only gating.
  - R6: fixed lines. Instead of extending the shared `_REASONS`, use a separate `_ADMIN_ORIGIN_REASONS` dict, so that register-only reasons like `admin_app` cannot leak into this command.
  - R7: `register-app --key admin` keeps refusing.
  - The extra tests.
  - R4 (ops workflow, `dry_run` default, shared `concurrency` group) belongs to the separate deployment PR; it is noted in deploy.md.
  - Finding 4 (no DB uniqueness on origins): the `admin` row lock serialises admin writers, but not a concurrent `register-app` on another app. Accepted with the workflow concurrency group as the mitigation; a DB-level fix is a follow-up.
  - The CSP/SRI gating for the admin UI is a follow-up.
  - Open Q3 (staging admin origin): staging stays admin-closed unless the owner asks. The command works there too, since staging just refuses loopback.
  - Open Q4 (cap): 2, per orchestrator guidance.

## Assumptions
- **Owner decision (non-blocking):** the production admin origin is `https://admin.mmlabs.online`. The host is reserved for a future admin UI, no DNS record or tunnel route is created until that UI exists, and it is never routed to another service. The CLI accepts any D19 origin other than the apex, so a different host is a runbook change only.
- **Owner decision (non-blocking):** the admin origin cap is 2 (to allow a rename or migration overlap), and `--clear` is in scope as break-glass.
- **Owner decision (non-blocking):** staging stays admin-closed (no admin origins) unless the owner runs the command there.
- The `set-admin-origins` input in the `ops-org-admin-api` workflow is a separate PR in `mmlabs-online/deployment`, handled by the orchestrator/owner and not part of this diff. Until it merges, the docker compose fallback is the path.
- The admin row's `name` is not part of the drift check: it is display data that the seed sets and nothing reads for security. The guidance lists exactly the 4 drift fields.
- Origins are not secrets: printing them on success matches `register-app`.
- Loopback origins are excluded from the `origin_taken` check, the same as `register`, because they are local-dev only and ignored in staging/production.

## Blocking questions
None

## Split proposal
None. This is one feature with no migration. Expect roughly 80 lines of service and schema, about 130 lines of CLI, about 350 lines of tests and about 100 lines of docs. Tests and docs make up most of it, which is acceptable for one cycle.

**Follow-ups (not in this diff, R7):**
1. ADM-002b: enforce `admin:*` on admin origin changes in `AppService.update` (the comment at `service.py` ~line 272).
2. Admin-UI ticket: non-credentialed, Bearer-only CORS on `/api/v1/*` with a fixed allow-list of headers. A strict CSP, no third-party scripts and SRI are gating requirements before any admin UI is served at the admin origin.
3. ADM-004: audit entry for `set-admin-origins` (actor `system:cli`, added/removed).
4. Optional: a DB-level guard against two apps holding the same origin (security Finding 4).
5. Optional: structured log `admin_origins_changed` with counts.

## Data model / migration
None. The existing `apps.origins` column is written; no schema change, and `alembic check` must stay clean.

## API contract
None. This is a CLI-only change with no HTTP endpoint added or changed.
