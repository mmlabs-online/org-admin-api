Status: READY

# Plan — USR-07: Register an app in production from the CLI (first consumer: ricotdin)

## Spec summary
- §5.3: "Membership (2026-11-11, D8–D10 …): each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin`, an `org_mode` (`none` | `required`; …) and its allowed browser `origins`. … Policy and role contents are data. … app origins are limited to `mmlabs.online`, its subdomains and localhost (D19)."
- §5.8 abuse protection: "CORS only for registered app origins; cookie-authenticated POSTs check Origin and `X-MM-App` (D12)."
- D9 (identity-architecture §8): "Default role `<app>:member` = all app permissions except `is_admin` ones … Policy and role content are data."
- D19: "only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback … Any other origin → 422."
- Ticket goal: "An operator can register a consuming app (key, name, origins, join policy, org mode, default member role) on the production server without the flag-gated RBAC admin API, so that ricotdin can start using mmlabs Identity before ADM-002 ships."
- Owner 2026-11-12: separate apps `ricotdin` → `https://ricotdin.mmlabs.online` and `ricotdin-staging` → `https://ricotdin-staging.mmlabs.online`, both `join_policy=open`, `org_mode=none`, registered with the CLI.
- Owner constraint (orchestrator, this run): production is reachable only through GitHub Actions in `mmlabs-online/deployment`; the CLI runs from a `workflow_dispatch` ops workflow there (separate deployment PR, see "Deploy-side follow-up").

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `python -m app.cli register-app --key K --name N --origin O [--origin …] [--join-policy …] [--org-mode …] [--permission …] [--dry-run]` creates the app with `K:member` as default role | `test_cli_register_app.py › test_create_app_and_member_role`, `test_create_two_separate_apps` |
| 2 | Re-run is idempotent: same input → `unchanged`, no write (`updated_at` unchanged) | `test_rerun_unchanged_no_write` |
| 3 | Update semantics: origins replace (≥1 `--origin` required), `--join-policy`/`--org-mode` applied only when passed, status and `default_role_id` never touched, disabled app refused (exit 3), `org_mode` change on an existing app refused (exit 1) | `test_origins_replaced_with_diff`, `test_policy_kept_when_flag_omitted`, `test_join_policy_changed_when_passed`, `test_org_mode_change_refused`, `test_same_org_mode_accepted`, `test_disabled_app_refused_exit_3`, `test_status_and_default_role_untouched` |
| 4 | `admin` app refused (exit 64, DB never opened; service refuses too) | `test_admin_refused_exit_64`, `test_service_refuses_admin` |
| 5 | Origins validated by D19 (`normalize_origin`), value never echoed; loopback refused when `APP_ENV` is staging/production; an origin held by another app (any status, `admin` included) refused | `test_invalid_origin_exit_64`, `test_loopback_refused_in_production`, `test_loopback_allowed_locally`, `test_origin_taken_exit_1`, `test_origin_taken_by_admin_app` |
| 6 | Permissions additive, created `is_admin=false`; `K:*`, wrong prefix and malformed keys refused (exit 64); an existing `is_admin` permission named refused (exit 1); added permissions land in the member role | `test_permissions_added_to_member_role`, `test_permission_rejected_exit_64`, `test_existing_admin_permission_refused` |
| 7 | Member role is rebuilt only on create or when a permission is added (manual tightening kept, D9) | `test_rerun_without_new_permission_keeps_tightening` |
| 8 | `lock_app(K)` is the first locking statement, before any write | `test_register_locks_app_first` |
| 9 | Same output/error discipline as grant-super-admin: fixed messages, exit codes 0/1/3/64, CLI engine with `hide_parameters`, no tracebacks, no DSN, no submitted values in errors | `test_usage_errors_exit_64`, `test_db_echo_refused_exit_1`, `test_db_error_exit_1_no_leak`, `test_concurrent_create_conflict_exit_1`, `test_subprocess_register_usage_exit_64` |
| 10 | `--dry-run` runs the whole registration and rolls back; output ends with the dry-run line | `test_dry_run_rolls_back` |
| 11 | Reuses the RBAC service layer (R1): business rules in `AppService.register`, CLI only parses and prints | Code review: `app/cli.py` imports no ORM model or repository; `register` calls `create`/`update`/`PermissionService.create`/`_build_for_app` |
| 12 | `make register-app ARGS='…'` | `make -n register-app ARGS="--key x --name X --origin https://x.mmlabs.online"` prints the `uv run python -m app.cli register-app …` line; `make register-app` without ARGS exits 64 |
| 13 | Runbook in `docs/deploy.md`: register-app (ricotdin + ricotdin-staging worked example) and grant-super-admin, both run via the ops workflow (which runs the `migrate` service) | Doc review |
| 14 | grant-super-admin unchanged | `tests/test_cli_grant_super_admin.py` passes unmodified |
| 15 | `make check` green | Command |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `AppRegister` input model | Validation of CLI input reuses `AppName`, `normalize_origins`, `PermissionKey` (AC 5, 6) |
| `app/modules/rbac/repository.py` | Add `app_keys_holding_origins(origins, exclude_key)` | Origin uniqueness lookup (SQLAlchemy only, R1) |
| `app/modules/rbac/service.py` | Add `RegisterOutcome`, `RegisterResult`, `AppService.register(...)` | Business rules in the service (R1, AC 11) |
| `app/cli.py` | Add `register-app` subcommand, its messages, exit mapping, dry-run; small shared settings helper | AC 1–10 |
| `Makefile` | Add `register-app` target and `.PHONY` entry | AC 12 |
| `tests/test_cli_register_app.py` | New test module | R8 |
| `docs/deploy.md` | New "Register an app (USR-07)" runbook; "Bootstrap the first super-admin" step 2 runs via the ops workflow (manual compose command kept as fallback); one-time setup adds the `org-admin-api-ops` environment; "Browser origins" points to the runbook | AC 13 |

No migration, no new env var, no endpoint. `tests/test_cli_grant_super_admin.py` is not modified.

## Tests to write
All in `tests/test_cli_register_app.py`, in-process via `amain(argv, settings=…, session_scope=…)` with the `_scope(db_session)` / `_never` helpers copied from the grant tests, plus `_savepoint_scope(db_session)` (`async with db_session.begin_nested(): yield db_session`) for the dry-run test. Every test asserts no `Traceback`, no `postgresql`, and (where an invalid value was submitted) that the value is not in stdout/stderr.

| Test (file › name) | Covers |
|---|---|
| `test_create_app_and_member_role` | ricotdin created: exact stdout lines; row has name, `open`, `none`, origins; `default_role_id` = `ricotdin:member`; member role empty; stderr empty; exit 0 |
| `test_create_two_separate_apps` | `ricotdin` and `ricotdin-staging` each with its own single origin and own member role; no origin swapped |
| `test_rerun_unchanged_no_write` | Second identical run → `unchanged app ricotdin`, no change lines, `updated_at` unchanged |
| `test_origins_replaced_with_diff` | Re-run with a different origin set → `+ origin`/`- origin` lines, stored origins = new set; same set in another order → `unchanged`, stored order kept |
| `test_policy_kept_when_flag_omitted` | App with `approval`; re-run without `--join-policy` keeps `approval` |
| `test_join_policy_changed_when_passed` | `--join-policy open` on an `approval` app → `~ join_policy: approval -> open`, `updated` |
| `test_org_mode_change_refused` | Existing `none` app, `--org-mode required` → exit 1 `MSG_ORG_MODE`, nothing changed (name change in the same run not applied) |
| `test_same_org_mode_accepted` | `--org-mode none` on a `none` app → exit 0 |
| `test_disabled_app_refused_exit_3` | Disabled app → exit 3 `MSG_APP_DISABLED`, still disabled, origins unchanged |
| `test_status_and_default_role_untouched` | App whose default role is a custom role: re-run with a new name → `default_role_id` and `status` unchanged |
| `test_admin_refused_exit_64` | `--key admin` with `_never` scope → exit 64 `MSG_ADMIN_APP` |
| `test_service_refuses_admin` | `AppService.register(AppRegister(key="admin", …), app_env="test")` raises `ConflictError` with reason `admin_app`; admin row unchanged |
| `test_invalid_origin_exit_64` | Parametrized: `http://evil.example`, `https://api.mmlabs.online`, `https://x.mmlabs.online:8443`, `https://evil.com` → exit 64 `MSG_BAD_ORIGIN`, `_never` scope, value not echoed |
| `test_loopback_refused_in_production` | `settings=Settings().model_copy(update={"app_env": "production"})`, `--origin http://localhost:5173` → exit 64 `MSG_LOOPBACK`, nothing written |
| `test_loopback_allowed_locally` | Same origin with `APP_ENV=test` → exit 0; two apps may share a loopback origin |
| `test_origin_taken_exit_1` | `stoktok` holds `https://ricotdin.mmlabs.online` → register ricotdin with it → exit 1 `MSG_ORIGIN_TAKEN`, no ricotdin row |
| `test_origin_taken_by_admin_app` | `admin` app holds an origin → claiming it refused (exit 1) |
| `test_permissions_added_to_member_role` | `--permission ricotdin:app:use --permission ricotdin:notes:write` → `+ permission`, `+ member` lines; both `is_admin=false`; member role holds both; re-run adding a third → only the third reported |
| `test_permission_rejected_exit_64` | Parametrized: `ricotdin:*`, `stoktok:app:use`, `ricotdin:x`, `RICOTDIN:a:b` → exit 64 `MSG_BAD_PERMISSION`, no app/permission written |
| `test_existing_admin_permission_refused` | Existing `ricotdin:roles:manage` with `is_admin=true`, named via `--permission` → exit 1 `MSG_ADMIN_PERMISSION`, nothing changed |
| `test_rerun_without_new_permission_keeps_tightening` | Register with 2 permissions, remove one from the member role directly, re-run with the same 2 → member role still holds 1, `unchanged` |
| `test_register_locks_app_first` | Statement recorder (as in the grant test): first `FROM apps … FOR NO KEY UPDATE` precedes every `INSERT`/`UPDATE` |
| `test_dry_run_rolls_back` | `--dry-run` with `_savepoint_scope`: output = create output + last line `MSG_DRY_RUN`; afterwards no `ricotdin` row (query with `populate_existing`) |
| `test_usage_errors_exit_64` | Parametrized: no `--origin`, no `--name`, no `--key`, `--join-policy maybe`, `--org-mode x`, unknown `--extra secret-value`, positional extra → exit 64, stderr == `REGISTER_USAGE` + newline, submitted values not echoed |
| `test_db_echo_refused_exit_1` | `Settings(db_echo=True)` → exit 1 `MSG_DB_ECHO`, `_never` scope |
| `test_db_error_exit_1_no_leak` | `RbacRepository.lock_app` monkeypatched to raise `DBAPIError` whose text contains the origin and a DSN → exit 1 `MSG_REGISTER_UNEXPECTED`, neither appears |
| `test_concurrent_create_conflict_exit_1` | `RbacRepository.add_app` monkeypatched to raise `IntegrityError` → exit 1 `MSG_CONFLICT` |
| `test_subprocess_register_usage_exit_64` | `python -m app.cli register-app --key x` in a subprocess → exit 64, stderr == `REGISTER_USAGE` |

## Approach
1. **Schema** (`schemas.py`): `AppRegister` (`extra="forbid"`): `key: str = Field(pattern=APP_KEY_RE, max_length=32)`, `name: AppName`, `origins: list[str] = Field(min_length=1, max_length=MAX_ORIGINS)` with a `field_validator` calling `normalize_origins`, `join_policy: JoinPolicy | None = None`, `org_mode: OrgMode | None = None`, `permissions: list[PermissionKey] = Field(default_factory=list, max_length=50)`.
2. **Repository**: `async def app_keys_holding_origins(self, origins: list[str], exclude_key: str) -> list[str]` → `select(App.key).where(App.origins.overlap(origins), App.key != exclude_key).order_by(App.key)`. All statuses, `admin` included.
3. **Service** (`AppService.register(data: AppRegister, *, app_env: str) -> RegisterResult`), one transaction owned by the caller, order app → rows (ADR-0003 §10):
   1. `data.key == ADMIN_APP_KEY` → `ConflictError("The admin app cannot be registered", details=[{"field": "key", "reason": "admin_app"}])`.
   2. Pure input checks, before any lock: loopback origin (`is_loopback_origin`) when `app_env in {"staging", "production"}` → `UnprocessableError` reason `loopback_not_allowed` (field `origins`); permissions deduplicated; any key whose prefix is not `data.key` → reason `prefix_mismatch`; `f"{key}:*"` → reason `wildcard_not_allowed` (field `permissions`).
   3. `app = await self.repo.lock_app(data.key)`.
   4. Existing app: `status == disabled` → `ConflictError` reason `app_disabled`; `data.org_mode is not None and != app.org_mode` → `ConflictError` reason `org_mode_change`. Existing permissions named that are `is_admin` (`repo.permissions_by_keys`) → `ConflictError` reason `is_admin_permission`.
   5. Origin uniqueness: `repo.app_keys_holding_origins([o for o in data.origins if not is_loopback_origin(o)], data.key)` non-empty → `ConflictError` reason `origin_taken` (field `origins`; the conflicting app key is not put in the message). Loopback is exempt (dev convenience; ignored in staging/production anyway).
   6. Create path (`app is None`): `await self.create(AppCreate(key, name, join_policy or OPEN, org_mode or NONE, origins))` (builds `K:member`, sets `default_role_id`, marks apps changed). Change lines: every origin `+`.
   7. Update path: snapshot `name`, `join_policy`, `origins`; build `AppUpdate` kwargs with `name` always, `origins` only if `set(data.origins) != set(app.origins)`, `join_policy` only if passed; never `status`, `org_mode` (equal or refused) or `default_role_id`; `await self.update(data.key, AppUpdate(**kwargs))` (no write when nothing differs).
   8. Permissions: for each requested key not already present, `await PermissionService(self.repo.session).create(data.key, PermissionCreate(key=k, is_admin=False))`.
   9. Member role rebuild only if a permission was added: `await self.catalog._build_for_app(app)` (lock held; on create the role already exists from step 6, so it is built once more only when needed). Never on a plain re-run (D9: keeps manual tightening).
   10. Read the member role's keys (`get_role_by_key(app.id, f"{K}:member")`, `role_permission_keys`); `None` if the role is missing (not rebuilt).
   11. `# R4: ADM-004 audit entry (actor system:cli) in the same transaction.` marker before the first write.
   12. Return `RegisterResult` (frozen dataclass, plain values): `outcome` (`created` | `updated` | `unchanged`; `updated` if any app field changed, a permission was added or the member role changed), `key`, `name`, `join_policy`, `org_mode`, `origins: tuple[str, ...]`, `origins_added`, `origins_removed`, `changes: tuple[tuple[str, str, str], ...]` (field, old, new for `name`/`join_policy`), `permissions_added`, `member_role: str | None`, `member_permissions`, `member_added`, `member_removed`.
4. **CLI** (`app/cli.py`): in `amain`, `if argv[:1] == ["register-app"]: return await _register_app(argv[1:], settings=…, session_scope=…)`; the grant path, `USAGE` and its messages stay byte-identical. Extract the settings load + `DB_ECHO` check into `_load_settings(settings)` used by both (same messages). `_register_app`:
   - `_Parser(prog="python -m app.cli register-app", allow_abbrev=False)`: `--key`, `--name`, `--origin` (`action="append"`, required), `--join-policy` (choices, default `None`), `--org-mode` (choices, default `None`), `--permission` (`action="append"`, default `[]`), `--dry-run` (`store_true`). Parse error → `REGISTER_USAGE`, exit 64.
   - `args.key == "admin"` → `MSG_ADMIN_APP`, exit 64, before settings/DB.
   - Build `AppRegister`; on `ValidationError` map the first error's `loc[0]` to a fixed message (`key` → `MSG_BAD_KEY`, `name` → `MSG_BAD_NAME`, `origins` → `MSG_BAD_ORIGIN`, `permissions` → `MSG_BAD_PERMISSION`, else `REGISTER_USAGE`), exit 64. Never print the pydantic error (it contains `input_value`).
   - Load settings (`_load_settings`), then `async with scope() as session: result = await AppService(session).register(data, app_env=settings.app_env); if dry_run: raise _Rollback(result)`; catch `_Rollback` outside the `with` (the CLI scope's `session.begin()` rolls back). Print only after the scope exits (commit done).
   - `AppError` mapping by `details[0]["reason"]` (fallback by class): `admin_app` → `MSG_ADMIN_APP`/64; `loopback_not_allowed` → `MSG_LOOPBACK`/64; `prefix_mismatch`, `wildcard_not_allowed` → `MSG_BAD_PERMISSION`/64; `is_admin_permission` → `MSG_ADMIN_PERMISSION`/1; `origin_taken` → `MSG_ORIGIN_TAKEN`/1; `org_mode_change` → `MSG_ORG_MODE`/1; `app_disabled` → `MSG_APP_DISABLED`/3; any other `ConflictError` → `MSG_CONFLICT`/1; anything else → `MSG_REGISTER_UNEXPECTED`/1. Never `str(exc)`.
   - Messages (module constants, exact text):

```
REGISTER_USAGE = "usage: python -m app.cli register-app --key <key> --name <name> --origin <origin> [--origin <origin> ...] [--join-policy open|approval|closed] [--org-mode none|required] [--permission <key>:<resource>:<action> ...] [--dry-run]"
MSG_ADMIN_APP = "error: the admin app cannot be registered or changed from the CLI"
MSG_BAD_KEY = "error: --key must be 2-32 characters: a-z first, then a-z, 0-9 or -"
MSG_BAD_NAME = "error: --name must be 1-255 characters of printable text"
MSG_BAD_ORIGIN = "error: each --origin must be https://mmlabs.online or a subdomain (not api.mmlabs.online, default port), or loopback; at most 20"
MSG_LOOPBACK = "error: loopback origins are not allowed in staging/production"
MSG_BAD_PERMISSION = "error: each --permission must be <key>:<resource>:<action> with this app's key (no <key>:*)"
MSG_ADMIN_PERMISSION = "error: a --permission is an is_admin permission; the CLI never grants those to the member role"
MSG_ORIGIN_TAKEN = "error: an origin is already registered to another app; nothing was saved"
MSG_ORG_MODE = "error: org_mode of an existing app cannot be changed from the CLI; nothing was saved"
MSG_APP_DISABLED = "error: the app is disabled; nothing was changed"
MSG_CONFLICT = "error: the app was changed concurrently; nothing was saved, run again"
MSG_REGISTER_UNEXPECTED = "error: unexpected failure; the registration was not confirmed"
MSG_DRY_RUN = "dry-run: rolled back, nothing was saved"
MSG_CORS_NOTE = "note: the API applies origin changes within 60 s"
```

   - Exit codes: 0 created/updated/unchanged (also dry-run); 1 config, `DB_ECHO`, origin taken, org_mode change, is_admin permission, conflict, unexpected; 3 app disabled; 64 usage/validation (incl. `admin`, loopback in staging/production, bad permission).
   - Output (stdout, in this order; change lines sorted within each kind):

```
<created|updated|unchanged> app <key>
name: <name>
join_policy: <policy>
org_mode: <mode>
origins: <o1> <o2> ...                       (stored order, space-separated)
member role: <key>:member = <p1>, <p2>       ("= (none)" when empty; "member role: missing" when absent)
~ name: <old> -> <new>
~ join_policy: <old> -> <new>
+ origin <o>
- origin <o>
+ permission <p>
+ member <p>
- member <p>
note: the API applies origin changes within 60 s     (only when origins were added or removed)
dry-run: rolled back, nothing was saved              (only with --dry-run)
```

   - Update the module docstring to cover both commands.
5. **Makefile**: `register-app:  ## Register/update an app: make register-app ARGS='--key x --name X --origin https://x.mmlabs.online'` with `@test -n "$(ARGS)" || { echo "usage: make register-app ARGS='--key <key> --name <name> --origin <origin> ...'"; exit 64; }` and `@uv run python -m app.cli register-app $(ARGS)`; add to `.PHONY`.
6. **docs/deploy.md** (content in "Deploy-side follow-up" → runbook text): register-app runbook (prerequisites: a release containing USR-07 is deployed; dry run first, then apply; ricotdin and ricotdin-staging worked example; output sample; exit table; semantics list; local `make register-app`; manual compose fallback); grant-super-admin step 2 via the workflow; one-time setup item for the `org-admin-api-ops` environment.

Rules: R1 (rules in `AppService.register`, CLI parses/prints, repository holds the query); R2 n/a (no schema change); R3 (CLI is not an endpoint, not imported by `app.main`; existing `test_cli_not_imported_by_app` covers it); R4 marker with actor `system:cli`, the workflow run is the audit trail until ADM-004; R5/R11 (CLI engine `hide_parameters=True`, no DSN, no tracebacks, no submitted values in errors); R6 (service errors use `{"field","reason"}` details); R7 (admin-API origin uniqueness deferred, see follow-ups); R8 (tests above); R9 n/a; R10 respected.

## Specialist input used
- api-platform-engineer: adopted `AppService.register` returning a frozen result; `AppCreate`/`AppUpdate` reuse; origins replace with ≥1 required; policy/org flags default `None`; permissions additive; fixed messages per field; `make register-app ARGS=…`; test list. Exit code for `admin`: 64 (input refusal, not a separate code 4). Avoided double member build (rebuild only when a permission is added). Disabled app: refused (security view), not warned.
- identity-access-engineer: adopted CLI-side `admin` refusal before DB plus service defense; order lock → permissions → build; rebuild only on create/permission added; refuse `org_mode` change; refuse `<app>:*`/is_admin; separate-audience test for ricotdin vs ricotdin-staging. Open question on a starter permission (`ricotdin:app:use`) left to the owner (assumption below).
- security-architect: adopted refusals (admin, loopback in staging/production, wildcard/prefix/is_admin), never touching status/default role, disabled → exit ≠ 0, origin uniqueness in the service for the CLI path, `+`/`-` output, `--dry-run` (rolls back) with the workflow defaulting to dry run, print after commit, R4 marker, ops workflow design (env-only inputs, regexes, environment gating, shared concurrency, email masking). Rejected `--replace-origins` (the `-` lines plus dry-run default cover accidental removal). Admin API uniqueness: deferred (follow-up).

## Assumptions
- `ricotdin` and `ricotdin-staging` are registered with no permissions (member role empty, tokens carry `roles: ["ricotdin:member"]`); ricotdin's backend permission keys are added later by re-running with `--permission`. The runbook says so.
- Origin uniqueness is enforced for non-loopback origins across all apps (any status, `admin` included) on the CLI path only; the check is not race-proof across two concurrent registrations of different apps, which the workflow's shared concurrency group prevents in production.
- An existing app whose member role was deleted by hand is not repaired by a plain re-run (output says `member role: missing`); adding a permission rebuilds it.
- The ticket's "run via the `migrate` service" holds: the ops workflow runs the CLI through `docker compose run --rm --no-deps migrate …`.
- The ops workflow is usable for register-app only after a release containing USR-07 is deployed; grant-super-admin works with the current release.

## Blocking questions
None

## Split proposal
None. Estimate: ~300 production lines (service ~120, CLI ~150, schema/repository ~30, Makefile 4), ~400 test lines, docs ~100. Deployment workflow is a separate PR in another repo.

## Data model / migration
None

## API contract
None (no HTTP endpoint; admin `POST/PATCH /api/v1/apps` behaviour unchanged).

## Deploy-side follow-up (separate PR in `mmlabs-online/deployment`, implemented by the orchestrator, not the implementer)
New file `.github/workflows/ops-org-admin-api.yml`; a normal branch name (not `release/…`, so `pr-scope` does not restrict it). Required behaviour:

- **Trigger:** `workflow_dispatch` only. `run-name: ops-org-admin-api ${{ inputs.command }}` (no other input in the name).
- **Inputs:**
  - `command`: choice `register-app` | `grant-super-admin`, required.
  - `key`, `name`, `origins` (comma- or space-separated), `permissions` (comma- or space-separated): string, default empty.
  - `join_policy`: choice `keep` | `open` | `approval` | `closed`, default `keep`; `org_mode`: choice `keep` | `none` | `required`, default `keep` (`keep` = flag not passed).
  - `dry_run`: boolean, default `true` (register-app only).
  - `email`: string, default empty (grant-super-admin only).
- **Permissions:** `contents: read` only.
- **Concurrency:** `group: deploy-org-admin-api`, `cancel-in-progress: false` (the same group as the deploy, so it never races a migration or a deploy).
- **Job gating:** `if: github.ref == 'refs/heads/main'`; `runs-on: [self-hosted, macbook-runner]`; `environment: org-admin-api-ops` (new GitHub Environment: required reviewer = owner, deployment branches = `main` only; separate from `org-admin-api` so deploys are not gated); `timeout-minutes: 5`. No secrets used.
- **Step 1 `mask` (first step, no `env:` holding the email, since step env values appear in the log header before masking takes effect):** read the email from the event payload, e.g. `email="$(python3 -I -c 'import json,os;print((json.load(open(os.environ["GITHUB_EVENT_PATH"])).get("inputs") or {}).get("email") or "")')"`, trim whitespace, then `echo "::add-mask::$email"` and `echo "::add-mask::<lowercased email>"` when non-empty. Runs for both commands.
- **Step 2:** `actions/checkout` (main).
- **Step 3 `validate`:** every input passed only via `env:` (`KEY: ${{ inputs.key }}` …); never `${{ inputs.* }}` inside `run:`. No `set -x`. Fixed `::error::` messages that never echo a value. Rules:
  - register-app: `KEY` matches `^[a-z][a-z0-9-]{1,31}$` and is not `admin`; `NAME` matches `^[A-Za-z0-9 ._-]{1,64}$`; at least one origin, at most 20, each `^https://([a-z0-9-]+\.)*mmlabs\.online$` and not `api.mmlabs.online` or `*.api.mmlabs.online`; each permission `^[a-z][a-z0-9-]{1,31}:[a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}$` with prefix equal to `KEY`; `email` must be empty.
  - grant-super-admin: `EMAIL` matches `^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$`; `key`/`name`/`origins`/`permissions` must be empty.
  - Build a bash array: `args=(--key "$KEY" --name "$NAME")`, `--origin "$o"` per origin, `--permission "$p"` per permission, `--join-policy "$JOIN_POLICY"` / `--org-mode "$ORG_MODE"` only when not `keep`, `--dry-run` when `DRY_RUN == true`; or `args=(--email "$EMAIL")`.
- **Step 4 `run`:** check `apps/org-admin-api/release.env` and `/Users/mingming/.config/org-admin-api/org-admin-api.env` exist (else `::error::` "deploy org-admin-api first"); then `compose=(docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env)` and `"${compose[@]}" run --rm --no-deps migrate /app/.venv/bin/python -m app.cli "$COMMAND" "${args[@]}"`, capturing stdout+stderr and the exit code (`set +e` around it); print the output, append it to `$GITHUB_STEP_SUMMARY` in a fenced block with the triggering actor, command and (register-app) key and dry-run flag; exit with the CLI's code. The CLI output never contains the full email; the mask is a backstop.
- **Audit:** until ADM-004 the run (actor, time, inputs, summary) is the audit trail; note in the workflow header that `workflow_dispatch` inputs (the email included) stay readable in the run's event payload by anyone with read access to the deployment repo, which must stay private.
- **Header comment** like the other workflows: purpose, the two commands, link to org-admin-api `docs/deploy.md`.

Runbook text for `docs/deploy.md` (implementer writes it in this ticket):
- **Register an app (USR-07):** Actions → `ops-org-admin-api` → Run workflow (main) → command `register-app`, inputs; approve the `org-admin-api-ops` environment; read the output in the run summary; run with `dry_run` on first, check the `+`/`-` lines, then again with `dry_run` off. Worked example: `ricotdin` (name `Ricotdin`, origins `https://ricotdin.mmlabs.online`, join_policy `open`, org_mode `none`) and `ricotdin-staging` (name `Ricotdin Staging`, origins `https://ricotdin-staging.mmlabs.online`, `open`, `none`), with their expected output. Semantics: origins replace the stored set; join_policy/org_mode change only when passed; org_mode of an existing app is never changed; permissions are only added (`is_admin=false`) and then rebuild the member role (which drops permissions added to it by hand, D9); status and default role never change; disabled apps are refused; an origin belongs to one app; the API applies origin changes within 60 s; separate app keys mean separate token audiences. Exit-code table (0/1/3/64). Fallback with a shell on the Mac: the same `docker compose … run --rm --no-deps migrate /app/.venv/bin/python -m app.cli register-app …` line. Locally: `make register-app ARGS='…'`.
- **Bootstrap the first super-admin, step 2:** run via the same workflow with command `grant-super-admin` and `email`; the email is masked in logs, but the input remains in the run's event payload (deployment repo must stay private). Keep the existing manual command as the fallback.
- **One-time setup:** create the GitHub Environment `org-admin-api-ops` in `mmlabs-online/deployment` (required reviewer: owner; deployment branches: `main`).

## Follow-ups (not in this diff)
- Apply the one-origin-one-app rule to the admin API (`POST/PATCH /api/v1/apps`) and record it as a D19 amendment in `docs/identity-architecture.md` (owner decision; new business rule on an existing endpoint).
- Owner: choose ricotdin's permission keys (e.g. `ricotdin:app:use`) so its backend checks permissions rather than role names; register them with `--permission`.
- Deployment PR for `ops-org-admin-api.yml` (above); also closes USR-02f N3 (grant-super-admin needed a shell on the Mac).
- After ADM-004: replace the R4 marker with an audit entry (actor `system:cli`).
