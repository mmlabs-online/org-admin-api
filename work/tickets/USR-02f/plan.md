Status: READY

# Plan — USR-02f: Bootstrap super-admin CLI (grant-super-admin) with masked output and runbook

## Spec summary
- spec §5.3: "Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap super-admin (`admin:*`) exists."
- identity-architecture.md D16: "no interim 'mark verified' path. Joining an app and getting an app token stay blocked until USR-04 ships."
- ADR-0003 §10 (as applied in USR-02c): "Every RBAC mutation … starts with `SELECT … FROM apps WHERE key = … FOR NO KEY UPDATE` and decides every guard after it … the order is always app → rows (no deadlocks)."
- CLAUDE.md R5 ("No secrets … in code, logs or error details"), R11 ("never log passwords, tokens or full emails in errors"), R3 (every HTTP endpoint has an auth class; this ticket adds **no** HTTP endpoint).
- USR-02c plan → Split proposal → "USR-02f" (goal, acceptance criteria and owner questions copied into this ticket).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `python -m app.cli grant-super-admin --email <email>` (stdlib `argparse`) grants `admin:super-admin` in one transaction: lock `admin` app → seeded role → user `FOR SHARE` by normalized email → status check → `AssignmentService._grant_locked`; app scope, `granted_by` NULL, `expires_at` NULL | `test_cli_grant_super_admin.py › test_grant_creates_app_scope_row` (one row, `org_id`/`granted_by`/`expires_at` NULL); `› test_grant_locks_admin_app_then_user` (SQL capture order); `› test_grant_mixed_case_email` |
| 2 | Idempotent: "granted" and "already held" both exit 0; an expired row is replaced in place; running twice leaves one row | `› test_grant_twice_already_held`, `› test_grant_replaces_expired_row` (same `id`, `expires_at` NULL afterwards) |
| 3 | Exit codes 0 / 2 unknown email / 3 disabled / 1 unexpected / 64 usage; each failure prints one fixed line, no traceback, no DSN | `› test_unknown_email_exit_2`, `› test_disabled_user_exit_3_no_write`, `› test_usage_errors_exit_64` (parametrized), `› test_db_error_exit_1_no_leak`, `› test_db_echo_refused_exit_1`, `› test_settings_error_exit_1`, `› test_not_seeded_exit_1`, `› test_subprocess_unreachable_db_exit_1` |
| 4 | Output: user id, masked email, `created_at`, `email_verified: yes/no`; the full email never appears on stdout/stderr, even on a DB error (`hide_parameters=True`, `DB_ECHO` refused) | `› test_grant_output_fields` (exact lines); every CLI test asserts the full email is absent from `capsys` (helper `_assert_no_leak`); `› test_db_error_exit_1_no_leak` raises a `DBAPIError` carrying the email in params and message |
| 5 | Not reachable over HTTP (never imported by `app.main` or any module under `app/`); no flag needed; no user creation, no password, no "mark verified" path (D16) | `› test_cli_not_imported_by_app` (AST scan of `app/**/*.py`); `› test_grant_with_rbac_flag_off` (default `Settings`); code review: `app/cli.py` has no `password`, no `User(` construction, no `email_verified_at` write |
| 6 | `make grant-super-admin email=...` and a `docs/deploy.md` runbook entry (migrate service, required env incl. `TRUSTED_PROXY_CIDRS`, sign up and log in first, check `created_at`, password reset after USR-04) | Reviewer reads `Makefile` (recipe prefixed with `@`, so make does not echo the email) and `docs/deploy.md` → "Bootstrap the first super-admin" |
| 7 | Masking helper in `app/core/text.py` with unit tests | `test_text.py › test_mask_email` (parametrized) |
| 8 | `make check` green, incl. `ruff format --check` on this plan | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/cli.py` | **new**: argparse parser, `amain()` (one catch-all), `main()` entry, CLI-only engine/session scope, fixed messages, output | AC1, AC3–AC5 |
| `app/modules/rbac/service.py` | add `BootstrapOutcome`, `BootstrapResult`, `AssignmentService.bootstrap_super_admin(email)` | business rules stay in the service (R1); calls the private `_grant_locked` from inside its own class, so no public wrapper is needed |
| `app/modules/users/repository.py` | add `get_by_email_for_share(email)` | exact-email lookup with `FOR SHARE` (SQLAlchemy only in the repository, R1) |
| `app/core/text.py` | add `mask_email(email: str) -> str` | AC4/AC7 |
| `Makefile` | add `grant-super-admin` target (+ `.PHONY`) | AC6 |
| `docs/deploy.md` | add section "Bootstrap the first super-admin" | AC6 |
| `tests/test_cli_grant_super_admin.py` | **new** | AC1–AC5 (R8) |
| `tests/test_text.py` | add `test_mask_email` | AC7 |

No migration, no new config key, no new dependency, no change to `app/main.py`, `app/db/session.py` or `.env.example`. The `Dockerfile` already copies `app/` (the image contains `app/cli.py`).

## Tests to write
All CLI tests except the subprocess ones call `await amain(argv, settings=..., session_scope=...)` with a scope that yields the per-test `db_session` (no `begin()`, the fixture owns the transaction), and capture output with `capsys`. Every CLI test ends with `_assert_no_leak(capsys_output, email)`: the full email (any case) is absent from stdout and stderr, and so are `Traceback` and `postgresql`.

| Test (file › name) | Covers |
|---|---|
| `test_cli_grant_super_admin.py › test_grant_creates_app_scope_row` | AC1: exit 0, stdout first line `granted admin:super-admin`; exactly one `role_assignments` row for (user, seeded role) with `org_id`, `granted_by`, `expires_at` NULL |
| `› test_grant_output_fields` | AC4: stdout lines `user_id: <uuid>`, `email: <mask_email(user.email)>`, `created_at: <isoformat>`, `email_verified: no`; then with `email_verified_at` set → `email_verified: yes` |
| `› test_grant_mixed_case_email` | AC1: user `alice.x@example.test`, argv `--email "  Alice.X@EXAMPLE.test "` → exit 0 (normalization = sign-up's `strip().lower()`) |
| `› test_grant_twice_already_held` | AC2: second run → exit 0, stdout first line `already held admin:super-admin`, still one row, same `id` |
| `› test_grant_replaces_expired_row` | AC2: factory `assign(..., expires_at=<past>)` on the seeded role → exit 0 `granted`; same row `id`, `expires_at` NULL, one row |
| `› test_grant_active_row_with_future_expiry_already_held` | AC2: a non-expired row with a future `expires_at` (inserted outside the API) → `already held`, row unchanged |
| `› test_unknown_email_exit_2` | AC3: exit 2, stderr exactly `MSG_UNKNOWN_EMAIL`, stdout empty, no row |
| `› test_disabled_user_exit_3_no_write` | AC3: user `status=disabled` → exit 3, stderr `MSG_DISABLED`, no row written |
| `› test_usage_errors_exit_64` | AC3, parametrized argv: `[]`, `["grant-super-admin"]`, `["grant-super-admin", "secret@example.test"]` (positional: argparse would echo it), `["grant-super-admin", "--email", "not-an-email"]`, `["other-cmd"]`, `["grant-super-admin", "--email", "a@b.test", "--extra"]` → 64, stderr exactly `USAGE`, never the argv values; the session scope is never entered |
| `› test_db_echo_refused_exit_1` | AC4: `settings=Settings(db_echo=True)` → exit 1, `MSG_DB_ECHO`; the scope is never entered (scope raises `AssertionError` if called) |
| `› test_settings_error_exit_1` | AC3: `settings=None` and `get_settings` monkeypatched to raise `ValueError("TRUSTED_PROXY_CIDRS …")` → exit 1, `MSG_CONFIG` |
| `› test_not_seeded_exit_1` | AC3: monkeypatch `RbacRepository.get_role_by_key` to return `None` → exit 1, `MSG_NOT_SEEDED` |
| `› test_db_error_exit_1_no_leak` | AC3/AC4: monkeypatch `UserRepository.get_by_email_for_share` to raise `DBAPIError("SELECT … WHERE email = $1", {"email": EMAIL}, Exception(f"Key (email)=({EMAIL})"))` → exit 1, `MSG_UNEXPECTED`, email absent |
| `› test_grant_with_rbac_flag_off` | AC5: default `Settings()` (both admin flags off) → exit 0 |
| `› test_grant_locks_admin_app_then_user` | AC1 order: SQL capture via `event.listen(sync_engine, "before_cursor_execute")` on `db_session.bind` (pattern of `test_rbac_locking.py`): the `FROM apps … FOR NO KEY UPDATE` statement precedes the `FROM users … FOR SHARE` statement, which precedes the `role_assignments` insert |
| `› test_cli_not_imported_by_app` | AC5: AST scan of every `app/**/*.py` except `app/cli.py`: no `import app.cli` / `from app import cli` / `from app.cli import …` |
| `› test_subprocess_usage_exit_64` | AC3 through the real entry: `subprocess.run([sys.executable, "-m", "app.cli", "grant-super-admin", "secret@example.test"], cwd=<repo root>, env={…, "APP_ENV": "test"})` → returncode 64, `secret@example.test` absent from both streams (`# noqa: S603`) |
| `› test_subprocess_unreachable_db_exit_1` | AC3/AC4 end to end: env `DATABASE_URL=postgresql+asyncpg://u:s3cr3t-pw@127.0.0.1:1/x`, `--email Secret.Person@example.test` → returncode 1, stderr exactly `MSG_UNEXPECTED` + newline; no `s3cr3t-pw`, no email, no `Traceback`, no `127.0.0.1` |
| `test_text.py › test_mask_email` | AC7, parametrized: `john.doe@example.com` → `j***@e***.com`; `a@b.co` → `a***@b***.co`; `x@localhost` → `x***@l***`; `john@mail.example.co.uk` → `j***@m***.uk`; `élodie@exämple.fr` → `é***@e***.fr`; `no-at-sign` → `***`; `@example.com` → `***@e***.com`; `user@` → `u***@***`; the mask is always `***` (length not leaked) |

## Approach
1. **`mask_email` (`app/core/text.py`).** Pure function, no pydantic. `local, sep, domain = email.rpartition("@")`; no `@` → `"***"`. Local → first character + `***` (empty → `***`). Domain → split on `.`: first character of the first label + `***`, plus `.` + last label when there are ≥ 2 labels (empty domain → `***`). Fixed `***` so the length is not leaked. Docstring: "for operator output only (R11)".
2. **Repository (`users/repository.py`).** `get_by_email_for_share(email: str) -> User | None`: `select(User).where(User.email == email).with_for_update(read=True).execution_options(populate_existing=True)`. `email` must already be normalized; CITEXT makes the comparison case-insensitive anyway. `FOR SHARE` blocks a concurrent `UPDATE users SET status` (disable, `UsersService.set_status`) and `DELETE` until commit, while FK checks (`KEY SHARE`) from the insert are not blocked.
3. **Service (`rbac/service.py`).**
   - `class BootstrapOutcome(StrEnum)`: `GRANTED`, `ALREADY_HELD`, `UNKNOWN_EMAIL`, `USER_DISABLED`, `NOT_SEEDED`.
   - `@dataclass(frozen=True) class BootstrapResult`: `outcome`, `user_id: uuid.UUID | None`, `email: str | None`, `created_at: datetime | None`, `email_verified: bool | None` (plain values, so the CLI never touches the ORM).
   - `AssignmentService.bootstrap_super_admin(self, email: str) -> BootstrapResult`, docstring "CLI only (USR-02f). The caller owns the transaction; `email` is already normalized." Steps, in this order (ADR-0003 §10, app → rows):
     1. `app = await self.repo.lock_app(ADMIN_APP_KEY)`; `role = await self.repo.get_role_by_key(app.id, SUPER_ADMIN_ROLE)` when `app` is not None; either missing → `NOT_SEEDED`.
     2. `user = await UserRepository(self.repo.session).get_by_email_for_share(email)`; None → `UNKNOWN_EMAIL`.
     3. `user.status != UserStatus.ACTIVE.value` → `USER_DISABLED` (nothing written).
     4. Pre-check: `existing = await self.repo.find_assignment(user.id, role.id, None)`; if `existing` and (`existing.expires_at is None` or `> await self.repo.db_now()`) → `ALREADY_HELD`. The outcome is decided here, never inferred from a `ConflictError`.
     5. Otherwise `await self._grant_locked(app, role, user.id, None, None, None)` → `GRANTED` (a new row, or an expired row replaced in place: `created=False` also counts as granted). Any `AppError` it raises (impossible while the app lock and the user `FOR SHARE` lock are held) propagates → CLI exit 1.
     6. `# R4: ADM-004 audit entry (actor system:cli) in the same transaction.` above step 5.
   - **No public `grant_locked` wrapper:** the bootstrap logic lives inside `AssignmentService`, so the private method is only called by its own class and the CLI calls one public method.
   - Lock order note in the docstring: admin app → user row. A future disable/delete path that takes `lock_app("admin")` (USR-02c FU1) must lock in the same order.
4. **CLI (`app/cli.py`).** Module-level imports: stdlib only (`argparse`, `asyncio`, `logging`, `sys`, `collections.abc`, `contextlib`, `typing.TYPE_CHECKING`); `Settings`/`AsyncSession` under `TYPE_CHECKING` only. Module docstring: "Operator CLI (USR-02f). Never imported by `app.main`; not reachable over HTTP. Creates no users, takes no password, never marks an email verified (D16)."
   - **Fixed messages** (module constants, tests assert them exactly; stderr unless noted):
     ```
     USAGE             = "usage: python -m app.cli grant-super-admin --email <email>"
     MSG_UNKNOWN_EMAIL = "error: no account with this email; sign up first"
     MSG_DISABLED      = "error: the account is disabled; nothing was granted"
     MSG_NOT_SEEDED    = "error: admin app or admin:super-admin role missing; run migrations"
     MSG_CONFIG        = "error: settings could not be loaded; see docs/deploy.md (Required runtime config)"
     MSG_DB_ECHO       = "error: refusing to run with DB_ECHO enabled"
     MSG_UNEXPECTED    = "error: unexpected failure; the grant was not confirmed"
     ```
     Never print `str(exc)`, `repr(exc)` or any argv value (asyncpg `DETAIL` lines are part of the server message, so `hide_parameters` alone is not enough).
   - **Parser:** `class _Parser(argparse.ArgumentParser)` overriding `error(self, message)` → `raise _UsageError` (the message is dropped, because argparse echoes unrecognized arguments). `add_subparsers(dest="command", required=True)` inherits the class, so subcommand errors are covered too. Subcommand `grant-super-admin` with `--email` (required). `--help` keeps argparse's default (exit 0, no value printed).
   - **Email:** inside the catch-all, `str(TypeAdapter(EmailStr).validate_python(raw)).strip().lower()` — the same expression as sign-up (`auth/service.py`, comment points there). Any validation error → `_UsageError` (exit 64), without touching the DB. Email comes from argv only (see A3).
   - **`async def amain(argv: Sequence[str], *, settings: Settings | None = None, session_scope: SessionScope | None = None) -> int`**, `SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]`. One `try` covers everything, including the lazy imports (`pydantic`, `app.core.config`, `app.core.text`, `app.modules.rbac.service`, `sqlalchemy`):
     1. parse argv → email (`_UsageError` → print `USAGE`, return 64);
     2. `settings = settings or get_settings()` in an inner `try` that re-raises `_Fail(MSG_CONFIG) from None`;
     3. `settings.db_echo` → `_Fail(MSG_DB_ECHO)` before any connection;
     4. open the scope (default `_cli_session_scope(settings)`), `result = await AssignmentService(session).bootstrap_super_admin(email)`;
     5. map the outcome: `GRANTED` / `ALREADY_HELD` → print the result block to stdout, return 0; `UNKNOWN_EMAIL` → 2; `USER_DISABLED` → 3; `NOT_SEEDED` → `_Fail(MSG_NOT_SEEDED)` → 1;
     6. `except _UsageError` → 64; `except _Fail as f` → print `f.message`, 1; `except Exception` → print `MSG_UNEXPECTED`, 1.
     For exit 2/3 the transaction commits nothing (no write) and releases the locks.
   - **CLI-only engine** (`_cli_session_scope`, an `asynccontextmanager`): never imports `app.db.session` (that module builds the API engine at import time with `echo=settings.db_echo` and no `hide_parameters`). `create_async_engine(str(settings.database_url), hide_parameters=True, echo=False, poolclass=NullPool)`; `async_sessionmaker(engine, expire_on_commit=False, autoflush=False)`; `async with factory() as s, s.begin(): yield s` (commit on success, rollback on error); `await engine.dispose()` in `finally`.
   - **Result block** (stdout, one `key: value` per line):
     ```
     granted admin:super-admin            (or: already held admin:super-admin)
     user_id: 0b6f…
     email: j***@e***.com
     created_at: 2026-11-12T08:01:02.345678+00:00
     email_verified: no
     check: created_at must match your own sign-up; if not, revoke this grant (docs/deploy.md)
     ```
   - **`main() -> NoReturn`**: `sys.tracebacklimit = 0` (backstop for `BaseException`s such as `KeyboardInterrupt`); attach a `logging.NullHandler()` to the `sqlalchemy` and `asyncpg` loggers and set `propagate = False`, so Python's last-resort handler never prints their records to stderr; `sys.exit(asyncio.run(amain(sys.argv[1:])))`. `if __name__ == "__main__": main()`.
5. **Makefile.**
   ```
   grant-super-admin:  ## Grant admin:super-admin: make grant-super-admin email=you@example.com
   	@test -n "$(email)" || { echo "usage: make grant-super-admin email=you@example.com"; exit 64; }
   	@uv run python -m app.cli grant-super-admin --email '$(email)'
   ```
   `@` so make does not echo the email; single quotes around the value. Add to `.PHONY`. Local/dev use; production uses the runbook.
6. **Runbook (`docs/deploy.md` → new section "Bootstrap the first super-admin", after "Operations").** Content, in order:
   - **Prerequisite:** `TRUSTED_PROXY_CIDRS` is set in `apps/org-admin-api/compose.yml` → `x-app.environment` (see "Required runtime config"). Without it `Settings()` refuses to load in production and the CLI exits 1 with `MSG_CONFIG` (the same failure blocks `migrate` and the API). Migrations are at head (the seeded `admin` app and role exist).
   - **Step 1 — sign up yourself** through the public sign-up, then **log in once** (after USR-03) to prove you control the password. Never grant to an account someone else could have registered in your name (pre-registration hijack, security consult).
   - **Step 2 — run on the Mac**, from the deployment repo checkout the deploy uses, with a leading space so zsh skips history (`setopt HIST_IGNORE_SPACE`), or after `fc -p`:
     ```
      docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env \
        run --rm --no-deps migrate /app/.venv/bin/python -m app.cli grant-super-admin --email you@example.com
     ```
     The `migrate` service (on the `db` network only, unlike the API container which also joins the tunnel network); `--rm` removes the container (the email is visible in `ps`/`docker inspect` only while it runs); `--no-deps` leaves Postgres untouched. Required env is the same as `migrate`: `DATABASE_URL` (env file written by the deploy), `APP_ENV=production`, `TRUSTED_PROXY_CIDRS`. **Never set `DB_ECHO`** (the CLI refuses it). `ADMIN_RBAC_API_ENABLED` is not needed.
   - **Step 3 — check the output:** `created_at` must match the time of your own sign-up and `email_verified` is `no` until USR-04. If `created_at` is earlier than your sign-up, the account is not yours: revoke the grant (one-off `psql` from "Operations": `DELETE FROM role_assignments WHERE id = …`) and investigate.
   - Exit codes table: 0 granted / already held, 2 unknown email (sign up first), 3 account disabled, 1 any other failure (config, missing seed, DB), 64 usage.
   - **After USR-04 ships:** verify the email, then reset the password (revokes every device session).
   - Re-running is safe (idempotent).
7. **Rules.** R1: CLI (entry point, like a router) → `AssignmentService` → repositories. R2: no schema change. R3: no HTTP endpoint added; the CLI is not mounted anywhere. R4: marker left (FU2). R5: no new config; settings from env only; DSN never printed. R6: not applicable (no HTTP), the CLI has its own fixed messages. R7: changes limited to the files above. R8: tests per exit code. R10/D16: no user creation, no password, no verification shortcut. R11: masked email only, `hide_parameters=True`, no `str(exc)`.

## Specialist input used
- identity-access-engineer 1 (separate engine, `hide_parameters`, `echo=False`, `NullPool`, refuse `db_echo`, testable core taking a session, thin `main`): **adopted**; the testable core is `AssignmentService.bootstrap_super_admin` plus `amain(..., session_scope=...)`.
- identity-access-engineer 2 (order app lock → role → user `FOR SHARE` → status → `_grant_locked`): **adopted**.
- identity-access-engineer 3 (normalize like sign-up, invalid → 64 before the DB): **adopted**; no shared normalization helper (would change sign-up, R7), the expression is duplicated with a pointer and pinned by `test_grant_mixed_case_email`.
- identity-access-engineer 4 (pre-check with `find_assignment`, don't interpret `ConflictError`; expired replaced = granted): **adopted**.
- identity-access-engineer 5/6 (fixed messages, `parser.error` → 64, no SQLAlchemy logging, forced DB-error test; no user creation / password / verify; `# R4`): **adopted**.
- identity-access-engineer risk (private `_grant_locked` coupling) / open question 4: **resolved** by putting the bootstrap method inside `AssignmentService`; no public wrapper.
- identity-access-engineer open question 3 (separate exit code for a disable race): **rejected**; `FOR SHARE` makes the race impossible, exit 3 is enough.
- security-architect "one engine just for the CLI … `pool_size=1`": **adapted** to `NullPool` (one-shot process, nothing to pool).
- security-architect "Exit 64 if `db_echo` is true": **rejected in favour of exit 1** with `MSG_DB_ECHO`; 64 is reserved for command-line usage (`EX_USAGE`), `DB_ECHO` is environment config.
- security-architect lazy imports under one catch-all, logger silencing, `sys.tracebacklimit = 0`: **adopted** (logger silencing via `NullHandler` + `propagate = False`, which also covers ERROR records; `tracebacklimit` only in `main()` so tests are unaffected).
- security-architect runbook (migrate service, `TRUSTED_PROXY_CIDRS` prerequisite, sign up and log in first, check `created_at`/`email_verified`, revoke if mismatched, after USR-04 verify + reset, never `DB_ECHO`, shell-history mitigation): **adopted**.
- security-architect finding 4 (deployment compose lacks `TRUSTED_PROXY_CIDRS`): **out of this diff** (other repository) → FU3.
- security-architect open question 3 / identity open question 2 (email from stdin): **not adopted** → A3.

## Assumptions
- **A1 — Unverified accounts are granted** (with `email_verified: no` and the `check:` line). Before USR-04 nobody can be verified (D16); refusing would make the bootstrap impossible. FU1 revisits after USR-04.
- **A2 — The deployment `migrate` service runs the API image with a command that can be overridden** and has the same env (`DATABASE_URL`, `APP_ENV`, `TRUSTED_PROXY_CIDRS` via `x-app`) as described in `docs/deploy.md` and the security consult. If its entrypoint is fixed to `alembic`, the runbook adds `--entrypoint /app/.venv/bin/python` and drops the interpreter from the command; the implementer notes which in `impl-log.md` (cannot be checked from this repository).
- **A3 — Email from argv only.** `--email -` (stdin) is not added: the email is PII, not a secret; the runbook's leading-space and `--rm` mitigations are enough for v1 (security consult rates it low). FU4 if the owner wants it.
- **A4 — `DB_ECHO=true` → exit 1** (config refusal), not 64.
- **A5 — Settings validation failures** (e.g. missing `TRUSTED_PROXY_CIDRS`) → exit 1 with `MSG_CONFIG`; the pydantic error text is never printed.
- **A6 — Disabled users are refused (exit 3)** although USR-02c lets staff assign roles to disabled users (USR-02c A2): a disabled bootstrap super-admin would not count for the last-super-admin guard and could not log in, so granting would be silently useless.
- **A7 — The stored `user.email` is masked** in the output (not the argv value); both are equal modulo case.
- **A8 — No connect timeout override**: asyncpg's default applies; an unreachable DB fails into `MSG_UNEXPECTED`.

## Blocking questions
None.

## Split proposal
None. Estimated ~200 production lines (CLI ~120, service ~40, repository ~8, helper ~15, Makefile 3) plus the runbook section and ~250 test lines.

## Data model / migration
None. Uses the seeded `admin` app and `admin:super-admin` role (migration `20261111_b638a7c84c31_create_rbac`) and `role_assignments` with `uq_role_assignments_user_role_org` (NULLS NOT DISTINCT).

## API contract
None (no HTTP endpoint; the CLI is never mounted).

CLI contract:

| Command | Exit | Stream | Output |
|---|---|---|---|
| `python -m app.cli grant-super-admin --email <email>` | 0 | stdout | `granted admin:super-admin` or `already held admin:super-admin`, then `user_id`, `email` (masked), `created_at`, `email_verified: yes/no`, `check:` line |
| same, no account | 2 | stderr | `MSG_UNKNOWN_EMAIL` |
| same, account disabled | 3 | stderr | `MSG_DISABLED` |
| same, settings / `DB_ECHO` / seed missing / DB or other error | 1 | stderr | `MSG_CONFIG` / `MSG_DB_ECHO` / `MSG_NOT_SEEDED` / `MSG_UNEXPECTED` |
| bad arguments or invalid email | 64 | stderr | `USAGE` |

## Follow-ups (not in this diff)
- **FU1 — USR-04:** decide whether the CLI refuses unverified accounts unless `--allow-unverified` is passed (both consults' open question 1); update the runbook's post-USR-04 step.
- **FU2 — ADM-004:** at the `# R4` marker in `bootstrap_super_admin`, write the audit entry with actor `system:cli` in the same transaction.
- **FU3 — Deployment repo (owner):** add `TRUSTED_PROXY_CIDRS` to `apps/org-admin-api/compose.yml` → `x-app.environment` before the next deploy; until then `migrate`, the API and this CLI refuse to start in production.
- **FU4 — Owner (optional):** read the email from stdin (`--email -`) to keep it out of argv and shell history.
- **FU5 — ADM-002 (USR-02c FU1):** when the disable and delete paths take `lock_app("admin")`, keep the order admin app → user row so they cannot deadlock with the CLI.
