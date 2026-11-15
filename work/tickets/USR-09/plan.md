Status: READY

# Plan — USR-09: Create a staff-vouched account from the CLI (bootstrap for a new app's first user)

## Spec summary
`docs/spec-v1.md` §5.8:
> - **Users:** email (unique, case-insensitive), password, status (`active` / `disabled`), email verified flag, timestamps. Staff are users too; ...
> - **Sign-up:** open self-registration, protected by Cloudflare Turnstile and a per-IP rate limit (D2). Unverified users may log in but must verify their email before joining any app or getting an app token.
> - **Passwords:** hashed with Argon2id; never logged or returned; minimum length and a breached/common-password check per the plan.

`docs/identity-architecture.md` D16 (to be amended by ADR-0009):
> **Email verification ordering (U1):** no interim "mark verified" path. ...

`docs/deploy.md` "Bootstrap the first super-admin" (line 268):
> The CLI creates no users, takes no password and never marks an email verified.

Ticket (owner, 2026-11-15, "chọn B"): a new subcommand `python -m app.cli create-account --email <e> [--join <app_key>]`. The password is read twice from the TTY and never from argv, env or a workflow input. The account is created with a verified email (operator-vouched, ADR). An existing email exits 1 and is not touched. `--join` assigns the default member role, as the join endpoint does, in the same transaction. The output is `created account` / `user_id` / `joined`, with the email masked.

This adds no endpoint, so R3, R6 and R9 do not apply. D2/S5 is not weakened: the verified flag is set, and the verified-email check before join or token still runs.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Creates a verified user that can log in at once and, after `--join`, obtain an access token for that app. | `tests/test_cli_create_account.py › test_create_verified_user_no_join` (row exists, `email_verified_at` not NULL, no assignment, `joined: -`). `› test_create_with_join_then_login_and_token`: real `POST /api/v1/auth/login`, then `POST /api/v1/auth/token` → 200 with `roles == ["stoktok:member"]` and `email_verified` true. `› test_create_no_email_tokens_or_send_log`. |
| 2 | An existing email gives exit 1 and leaves the existing user unchanged. | `› test_existing_email_exit_1_unchanged`, parametrized over unverified, verified and disabled existing users and a mixed-case email. It checks that `password_hash`, `email_verified_at`, `status` and `updated_at` are identical afterwards, and that no role assignment was added even with `--join`. |
| 3 | A weak password is refused with the same rules as sign-up. Non-TTY stdin and a mismatched confirmation are refused. | `› test_weak_password_refused` (too_short, too_common, too_long > 128; same `PasswordPolicy` and `PASSWORD_MAX_LENGTH` as sign-up). `› test_non_tty_refused_before_prompt_and_db`, `› test_getpass_warning_refused`, `› test_password_mismatch_refused`, `› test_email_retype_mismatch_refused`, `› test_subprocess_non_tty_exit_1`. |
| 4 | The password never appears in stdout, stderr, log records, audit or the process list (tests check all of them). | `› test_secret_not_in_output_or_logs`: capsys plus caplog at DEBUG on root, `sqlalchemy` and `asyncpg`; checks the plain password, its NFKC form and the Argon2 hash. `› test_db_error_no_leak`. `› test_password_option_is_usage_error`. `› test_pty_end_to_end_no_echo_no_argv` reads `/proc/<pid>/cmdline` and `/environ` while the CLI waits at the repeat prompt. Audit: today audit is the ids-only log line, covered by the caplog check (`› test_audit_log_line_ids_only`). |
| 5 | `--join` with an unknown, disabled or `admin` app exits 1 and creates nothing (one transaction). | `› test_join_refusals_create_nothing`, parametrized over unknown, disabled, `admin`, `closed`, `approval`, NULL default role and admin-permission default role: exit 1, the fixed message, 0 `users` rows and 0 `role_assignments`, using a savepoint scope so a commit would be visible. `› test_failure_after_insert_rolls_back_user`: `_grant_locked` is forced to raise and the user is gone. `› test_locks_app_before_user_insert`. |
| 6 | An ADR records the verified-flag exception, and `docs/deploy.md` is updated. `make check` passes. | `docs/adr/0009-operator-vouched-verified-email.md` exists with the content listed under Approach §8. The new `docs/deploy.md` section and the amended wording are present. `make check` is green against local Postgres (`org_admin_test`). The existing `tests/test_auth_join.py` passes unchanged after the helper extraction. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/membership/service.py` | Add module functions `lock_joinable_app(rbac, key) -> App` and `join_default_role(rbac, app) -> Role`, holding the current guard code moved out of `JoinService.join` (lines 50–55 and 73–87). `JoinService.join` calls them. Order and behaviour stay the same. | One shared guard sequence for the HTTP join and the CLI join, so they cannot drift (all three consults). |
| `app/modules/auth/provisioning.py` (new) | `vet_and_hash_password(...)` (no DB) and `AccountProvisioningService.create_verified(...)`, marked CLI-only in its docstring. | R1: business rules belong in the service layer; the CLI only does I/O. |
| `app/cli.py` | `create-account` subcommand: parser, TTY abstraction, prompts, fixed messages, reason→message mapping, output. Factor `_normalize_email` out of `_parse_email`. Add `tty` kwarg on `amain`. Update the docstring. | The ticket's command. |
| `tests/test_cli_create_account.py` (new) | All tests below. | R8, AC1–AC5. |
| `docs/adr/0009-operator-vouched-verified-email.md` (new) | The ADR (Approach §8). | AC6; a decision that outlives the ticket. |
| `docs/identity-architecture.md` | D16 row: append "*Amended by ADR-0009 (USR-09, 2026-11-15)*" plus a one-sentence exception. | D16 states the policy being amended. |
| `docs/adr/0007-email-verification-and-delivery.md` | One line under Context: "*Amended by ADR-0009:* operator-vouched CLI path." | ADR-0007 line 7–8 repeats D16. |
| `docs/deploy.md` | New section "Create an account from the CLI (bootstrap only)". Amend line 268 and step 1/3 of "Bootstrap the first super-admin" (see Approach §9). Add one sentence to "One-time setup" step 6 that `create-account` is deliberately not an ops-workflow command. | AC6, ticket "Docs". |

No other files. In particular: no migration, no `Makefile` target, no change to `app/modules/audit/recorder.py`, no change to `SignupService`.

## Tests to write
All in `tests/test_cli_create_account.py`. Reuse the helpers pattern of `tests/test_cli_register_app.py`: `_scope`, `_savepoint_scope`, `_never` and `_assert_no_leak`. Add a `FakeTty(isatty=True, lines=[...], secrets=[...])` that records the prompts and raises `AssertionError` if it is read more than its queue holds. Use `PW = "Orbit-lumen-58-quartz"` (passes the policy) and an NFKC-sensitive variant `"Ｏrbit-lumen-58-quartz"` (fullwidth O).

| Test (file › name) | Covers |
|---|---|
| `test_create_verified_user_no_join` | Exit 0. Stdout is exactly `created account` / `user_id: <id>` / `email: <mask_email>` / `joined: -`, and stderr is empty. The row has `email_verified_at` not NULL, `status=active` and a valid `password_hash` (`PasswordHasher.verify` is true). There are 0 assignments. AC1 |
| `test_email_verified_at_from_db_clock` | `email_verified_at` equals `created_at`, both set by the DB `now()` in the same transaction. AC1, consult DB rec 1 |
| `test_create_with_join_then_login_and_token` | `make_login_app` + `make_member_role` (`stoktok`, open), then the CLI with `--join stoktok`, then `client.post(LOGIN)` with `auth_headers()` and the cookie and `account_id` from the response, then `TOKEN` → 200 with `roles == ["stoktok:member"]` and `email_verified` true. The assignment has `org_id`, `expires_at` and `granted_by` all NULL. Output shows `joined: stoktok`. AC1 |
| `test_create_mixed_case_email_normalized` | `--email "  Bob.X@EXAMPLE.com "` with a retype of `bob.x@example.com` is stored as `bob.x@example.com`. |
| `test_create_no_email_tokens_or_send_log` | `email_tokens` and `email_send_log` have 0 new rows. Neither `SignupHook` nor Turnstile is used. |
| `test_existing_email_exit_1_unchanged` [unverified, verified, disabled, mixed-case] | Exit 1. Stderr is `MSG_EMAIL_TAKEN`. The existing row's `password_hash`, `email_verified_at`, `status` and `updated_at` are byte-identical, and `login_failures` is untouched. With `--join stoktok` there are 0 assignments for that user. AC2 |
| `test_weak_password_refused` [too_short, too_common, too_long(129 chars), short+common] | Exit 1. Stderr is the fixed weak-password line with the reason codes only. The session scope is never entered (`_never`), and the hasher is not called. AC3 |
| `test_password_max_length_128_accepted` | A strong 128-character password is accepted (same boundary as `SignupRequest`). AC3 |
| `test_password_mismatch_refused` | The two entries differ: exit 1, `MSG_PASSWORD_MISMATCH`, scope `_never`, no retry prompt. AC3 |
| `test_email_retype_mismatch_refused` [different, invalid, empty] | Exit 1, `MSG_EMAIL_MISMATCH`. The retype comes before the password prompts (FakeTty `secrets` is empty). Scope `_never`. AC3, identity consult risk 1 |
| `test_non_tty_refused_before_prompt_and_db` | `FakeTty(isatty=False)`: exit 1, `MSG_NO_TTY`, no prompt recorded, scope `_never`. AC3 |
| `test_getpass_warning_refused` | `monkeypatch.setattr(getpass, "getpass", ...)` makes it emit `getpass.GetPassWarning` and return PW, through the real `_SystemTty` with `isatty` patched true. Exit 1, `MSG_NO_TTY`, scope `_never`, PW not in the output. AC3, AC4 |
| `test_prompt_eof_or_interrupt_refused` [EOFError, KeyboardInterrupt] | The real `_SystemTty.read_secret` (patched `getpass.getpass` raises): exit 1, `MSG_ABORTED`, no traceback. The same applies to an empty `readline()` (EOF) on the email retype. AC3 |
| `test_password_option_is_usage_error` [`--password x`, `--password=x`, positional extra, `--join a --join b`, missing `--email`, invalid email] | Exit 64, stderr == `CREATE_USAGE`, the submitted values are not in stderr, scope `_never`. AC4 (no argv path) |
| `test_db_echo_refused_before_prompts` | `Settings(db_echo=True)`: exit 1, `MSG_DB_ECHO`, no prompt recorded. |
| `test_join_refusals_create_nothing` [unknown, disabled app, `admin`, closed, approval, NULL default role, default role with an `is_admin` permission] | Uses `_savepoint_scope`. Exit 1 with the matching fixed message, and the count of `users` with the email is 0 and of `role_assignments` is 0. `admin` is refused with scope `_never`. AC5 |
| `test_failure_after_insert_rolls_back_user` | Monkeypatch `AssignmentService._grant_locked` to raise `ConflictError`, under `_savepoint_scope`. Exit 1 with `MSG_CREATE_UNEXPECTED`, and 0 users with the email. Proves one transaction. AC5 |
| `test_locks_app_before_user_insert` | A `before_cursor_execute` recorder, as in `test_grant_locks_admin_app_then_user`. The statements come in this order: `FROM apps … FOR NO KEY UPDATE`, then `INSERT INTO users`, then `INSERT INTO role_assignments`. ADR-0003 §10 |
| `test_hash_before_session_scope` | Wraps `PasswordHasher.hash` and the scope to append events, and asserts the hash finishes before the scope is entered. Consult DB risk "Medium" |
| `test_secret_not_in_output_or_logs` [PW, NFKC variant; with and without `--join`] | `caplog.set_level(DEBUG)` for root, `sqlalchemy` and `asyncpg`. The plain password and its NFKC form are in no stdout, stderr or `record.getMessage()`/`args`. The stored Argon2 hash is in neither stdout nor stderr, and not in any record of an `app.*` logger. The `sqlalchemy.engine` records come from the test fixture's engine, which is not `hide_parameters`; the CLI engine's `hide_parameters=True` and the DB_ECHO refusal are covered by existing tests. The full email is not in stdout or stderr. AC4 |
| `test_db_error_no_leak` [`DBAPIError`, `IntegrityError` with DETAIL] | Patch `UserRepository.add` to raise one carrying the email and hash in params and DETAIL. `DBAPIError` → `MSG_CREATE_UNEXPECTED`; `IntegrityError` → `MSG_EMAIL_TAKEN`. Neither the email nor the hash appears in the output, and there is no traceback. AC4 |
| `test_audit_log_line_ids_only` | caplog holds exactly one `account_created actor=system:cli user_id=<id> app_id=<id or -> role_id=<id or -> assignment_id=<id or ->` record from `app.modules.auth.provisioning`, with no email, password or hash. R4 placeholder |
| `test_allowed_in_every_app_env` [local, test, staging-like `Settings(app_env="production", ...)` if constructible with test env] | The command does not refuse based on `APP_ENV`. A local and a test run are enough if production settings need extra keys; if so, record that in the impl-log. Assumption 6 |
| `test_subprocess_non_tty_exit_1` | `subprocess.run([... "create-account", "--email", "secret.person@example.com"], stdin=DEVNULL)`: exit 1, stderr == `MSG_NO_TTY`, stdout empty, the email is not in the output. AC3 |
| `test_pty_end_to_end_no_echo_no_argv` (`skipif sys.platform != "linux"`, 30 s deadline) | `pty.fork()` + `os.execve(sys.executable, [-m app.cli create-account --email …], env)` with an unreachable `DATABASE_URL` (`127.0.0.1:1`). The parent writes the email retype, PW and PW to the master side. When the repeat prompt appears, it reads `/proc/<pid>/cmdline` and `/proc/<pid>/environ` and asserts PW is in neither. At the end: exit 1, `MSG_CREATE_UNEXPECTED` in the output, PW not in the whole pty transcript (so echo was off), no "Traceback" and no DSN. AC4 "process list" |
| `tests/test_auth_join.py` (existing, unchanged) | The helper extraction changes nothing in the HTTP join. |

## Approach
1. **Shared join guards (`membership/service.py`).** Move the code verbatim into two functions:
   - `lock_joinable_app(rbac, key)`: if `key == ADMIN_APP_KEY`, raise `JoinClosedError`. Then `lock_app(key)`; if the app is None or not ACTIVE, raise `OriginNotAllowedError`. Return the app.
   - `join_default_role(rbac, app)`: `closed` → `JoinClosedError`; `approval` → `JoinApprovalUnavailableError`; NULL or missing role → `JoinUnavailableError`; `role_has_admin_permission` → the same warning log, then `JoinUnavailableError`. Return the role.

   `JoinService.join` keeps the app-mismatch check, calls `lock_joinable_app`, then runs the account/user/verified checks and the "already a member" short-circuit as today, then calls `join_default_role`. The HTTP join order does not change. `org_mode` is not checked, which mirrors the endpoint exactly.

2. **`app/modules/auth/provisioning.py`.**
   - `async def vet_and_hash_password(password: str, *, policy: PasswordPolicy, hasher: PasswordHasher) -> str`:
     - if `len(password) > PASSWORD_MAX_LENGTH` (from `auth/schemas.py`), raise `WeakPasswordError("Password does not meet the policy", details={"reasons": ["too_long"]})`;
     - otherwise run `policy.check(password)`, then `return await hasher.hash(password)`.
     - It does no DB work. The password is a local variable only.
   - `@dataclass(frozen=True) class ProvisionResult: user_id: UUID; email: str; joined: str | None`.
   - `class AccountProvisioningService(session)`, method `create_verified(email, password_hash, join_key) -> ProvisionResult`. Its docstring says: "CLI only (USR-09, ADR-0009); never call from a router." The `email` argument is already normalized. Steps:
     - a. If `join_key` is set: `app = await lock_joinable_app(rbac, join_key)`, then `role = await join_default_role(rbac, app)`. Both happen **before** the user insert (lock order app → users).
     - b. `user = await UserRepository(session).add(User(email=email, password_hash=password_hash, email_verified_at=func.now()))`. On `IntegrityError`, `raise EmailTakenError("Email is already registered") from None`. That is a CITEXT unique race-safe check with no pre-check, and it never touches the existing row.
     - c. If joining: `ra, _ = await AssignmentService(session)._grant_locked(app, role, user.id, None, None, None)`.
     - d. Write the comment `# R4: ADM-004 audit entry (actor system:cli) in the same transaction: user.create_verified (+ app.join).` and then `logger.info("account_created actor=system:cli user_id=%s app_id=%s role_id=%s assignment_id=%s", ...)`, with `-` when there is no join. It logs ids only.
     - e. Return the result.

     Every refusal is an exception raised inside the caller's scope, so nothing commits (consult DB finding 3).

3. **CLI (`app/cli.py`).**
   - Add `_normalize_email(value) -> str`, the current `TypeAdapter(EmailStr)` + strip + lower; it raises `_UsageError`. `_parse_email` uses it.
   - Add `class _Tty(Protocol)` with `isatty() -> bool`, `read_line(prompt) -> str` and `read_secret(prompt) -> str`, and `_SystemTty`:
     - `isatty` = `sys.stdin.isatty()`.
     - `read_line` writes the prompt to **stderr**, then `sys.stdin.readline()`. An empty result (EOF) → `_Refused(MSG_ABORTED, 1)`. It strips the trailing newline.
     - `read_secret` runs `warnings.catch_warnings()` + `simplefilter("error", getpass.GetPassWarning)` and calls `getpass.getpass(prompt)`, looked up on the module so tests can patch it. `GetPassWarning` → `_Refused(MSG_NO_TTY, 1)`; `EOFError` or `KeyboardInterrupt` → `_Refused(MSG_ABORTED, 1)`.
   - `amain(..., tty: "_Tty | None" = None)`. Add the dispatch `if argv[:1] == ["create-account"]` **before** the grant-super-admin fall-through.
   - `_create_account` runs, in order:
     1. `_Parser(allow_abbrev=False, add_help=False)` with `--email` (required) and `--join` (`action="append"`); more than one `--join` → `_UsageError`. There is no `--password`.
     2. Normalize the email (`_UsageError` → exit 64).
     3. If `join == "admin"` → `MSG_JOIN_ADMIN`, exit 1.
     4. `_load_settings` (config error and DB_ECHO).
     5. `tty.isatty()` false → `MSG_NO_TTY`, exit 1.
     6. `read_line("retype email: ")`, normalized with the same function (invalid → mismatch). If it differs → `MSG_EMAIL_MISMATCH`.
     7. `read_secret("password: ")` and `read_secret("repeat password: ")`. If they differ → `MSG_PASSWORD_MISMATCH`.
     8. `vet_and_hash_password(pw, policy=PasswordPolicy(settings.password_min_length), hasher=PasswordHasher(settings))`. `WeakPasswordError` → `MSG_WEAK_PASSWORD.format(reasons=", ".join(r for r in reasons if r in {"too_short", "too_common", "too_long"}))`.
     9. `async with scope() as session: result = await AccountProvisioningService(session).create_verified(email, password_hash, join)`.
     10. Catch outside the scope:
         - `EmailTakenError` → `MSG_EMAIL_TAKEN`
         - `OriginNotAllowedError` → `MSG_JOIN_UNKNOWN`
         - `JoinClosedError` → `MSG_JOIN_CLOSED`
         - `JoinApprovalUnavailableError` → `MSG_JOIN_APPROVAL`
         - `JoinUnavailableError` → `MSG_JOIN_UNAVAILABLE`
         - any other exception → `MSG_CREATE_UNEXPECTED`

         All exit 1. Never `str(exc)`.
     11. Print, only after commit: `created account`, `user_id: …`, `email: {mask_email(result.email)}`, `joined: {key or "-"}`.
   - Exit codes: 0 ok, 64 usage, 1 every refusal and failure.
   - Fixed strings, all module constants:
     - `CREATE_USAGE = "usage: python -m app.cli create-account --email <email> [--join <app_key>]"`
     - `MSG_NO_TTY = "error: run from an interactive terminal (no -T, ssh -t); nothing was created"`
     - `MSG_ABORTED = "error: input aborted; nothing was created"`
     - `MSG_EMAIL_MISMATCH = "error: the retyped email does not match; nothing was created"`
     - `MSG_PASSWORD_MISMATCH = "error: the passwords do not match; nothing was created"`
     - `MSG_WEAK_PASSWORD = "error: password refused ({reasons}); nothing was created"`
     - `MSG_EMAIL_TAKEN = "error: an account with this email already exists; nothing was changed"`
     - `MSG_JOIN_ADMIN = "error: --join admin is not allowed; nothing was created"`
     - `MSG_JOIN_UNKNOWN = "error: unknown or disabled app; nothing was created"`
     - `MSG_JOIN_CLOSED = "error: the app does not accept joins; nothing was created"`
     - `MSG_JOIN_APPROVAL = "error: the app requires approval to join; nothing was created"`
     - `MSG_JOIN_UNAVAILABLE = "error: the app has no usable default role; nothing was created"`
     - `MSG_CREATE_UNEXPECTED = "error: unexpected failure; the account was not confirmed"`
   - Docstring bullet: "`create-account --email <email> [--join <app_key>]` (USR-09, ADR-0009): the only CLI path that creates a user and marks its email verified; TTY-only password, never touches an existing account." Reword the `grant-super-admin` bullet so "never marks an email verified" stays true for that command.

4. **Rules.**
   - R1: the CLI is I/O only; the logic is in the service; repositories are unchanged.
   - R2: no schema change.
   - R4: there is no ADM-004 yet, so the ids-only log line and the comment stand in for the audit entry, plus a follow-up.
   - R5 and R11: the password lives only in locals. It is never in argv or env. Output is fixed lines and a masked email. The engine uses `hide_parameters`. Argon2id comes from the shared hasher.
   - R7: the guard extraction is the only refactor, and the consults required it.
   - R8: tests are listed above.
   - R10: no HTTP endpoint and no hosted UI.

5. **Lock and hash order.** The hash runs before the scope opens, so the `apps` row lock is never held during Argon2. Inside the scope: `lock_app`, then the role reads, then the user insert (savepoint), then the assignment insert (savepoint), then commit.

6. **Commit semantics.** `_cli_session_scope` commits on a clean exit. No path returns after a write without having succeeded, and every refusal raises.

7. **Make check** runs against local Postgres (`postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin_test`, `conftest.py` default). The pty test uses an unreachable DB URL, so it never commits to the test DB.

8. **ADR-0009 "Operator-vouched verified email (CLI create-account)"**, Status Accepted (USR-09 plan, 2026-11-15).
   - **Context:** the bootstrap deadlock. The verify link needs an app origin that serves `/verify-email`, and TuDo needs `TUDO_OWNER_SUB` before its release. Also D2, D16, ADR-0007, and the options the owner declined (localhost origin with a localhost Turnstile hostname; a TuDo "setup mode").
   - **Decision:**
     - Only `python -m app.cli create-account` may set `email_verified_at` without a token, and only on the row it inserts in the same transaction.
     - It never touches an existing account, never runs over HTTP, never from the ops workflow and never on a non-TTY path.
     - The operator chooses the password at a TTY and types it twice. The email is retyped on the TTY to catch typos.
     - The optional `--join` gives only the app's default non-admin role, under exactly the join-endpoint guards (shared helper). There is no `--role`, no `--org` and no repeated `--join`; the boundary with `grant-super-admin` is stated.
     - The trust level is root-equivalent on the DB host, the same as `grant-super-admin`.
     - The command is allowed in every `APP_ENV`.
   - **Amends:** D16, ADR-0007 Context, and the "never marks an email verified" wording in `docs/deploy.md` and `app/cli.py`, which is now scoped to `grant-super-admin`.
   - **Accepted risks:**
     - a) A typo verifies an address the operator does not control. The owner of that address can use forgot/reset and take over the account, including TuDo ownership. Mitigated by the retype, the runbook check and an immediate login.
     - b) There is no durable audit before ADM-004 (log line only, and `--rm` drops the logs). The runbook requires a manual record.
     - c) The email is visible in `ps`/`docker inspect` while the prompt waits.
     - d) Turnstile and the rate limits do not apply.
     - e) `create-account` + `grant-super-admin` yields a verified super-admin without email proof, accepted at DB-host trust.
     - f) The password stays in process memory until exit.
   - **Revisit:** if anyone proposes this for routine onboarding (bulk import and staff HTTP endpoints stay out of scope).

9. **`docs/deploy.md`.**
   - New section "Create an account from the CLI (bootstrap only)" after "Register an app (USR-07)":
     - Purpose and the ADR-0009 link.
     - Prerequisites: same as grant-super-admin, plus the app registered with `register-app` (join `open`) if `--join` is used.
     - The command line with a leading space or `fc -p` first:
       ` docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env run --rm --no-deps migrate /app/.venv/bin/python -m app.cli create-account --email you@example.com --join tudocuatui`
     - **No `-T`**; use `ssh -t` if remote. **Never `DB_ECHO`.**
     - The prompts in order: retype email, password, repeat password. Check the address before Enter.
     - The output block and the exit table: 0 / 1 / 64.
     - After the run:
       - log in once at the target app with the new password;
       - check `user_id` before copying it into `TUDO_OWNER_SUB`;
       - record the run (date, `user_id`, app key, operator) in the deployment repo, since the container logs are gone.
     - `create-account` is **not** an `ops-org-admin-api` command and must never be added there.
     - Locally: `uv run python -m app.cli create-account --email … [--join …]`.
   - Line 268: "`grant-super-admin` creates no users, takes no password and never marks an email verified; `create-account` (below, ADR-0009) is the only CLI path that does."
   - Step 1: add "or create it with `create-account`"; in that case step 3's `created_at` must match that run and `email_verified: yes` is expected.
   - "One-time setup" step 6: add one sentence that `create-account` is deliberately not in the workflow.

## Specialist input used
- identity-access-engineer:
  - **Adopted:** Rec 1 (service method, no `SignupService` reuse), 2 (hash before the transaction; order lock → insert → grant), 3 (mirror `JoinService` exactly, shared helper), 4 (injected TTY, `GetPassWarning` → error, no `--password`, EOF and Ctrl-C refusal), 5 (retype the email; the "yes" alternative is rejected because it would print the full email, which conflicts with R11 masking), 6 (secrecy tests incl. forced DB error), 7 (log line plus the ADM-004 follow-up, no recorder call with a fake actor), 8 (ADR-0009 content and doc updates; line 268 stays true for grant-super-admin), 9 (no ops-workflow command).
  - **Rejected:** none.
- security-architect:
  - **Adopted:** R-1 (order; the non-TTY exit is 1, not 64, per the ticket and the orchestrator), R-2 (tests; "process list" covered structurally by the no-argv/env design and at runtime by the pty test's `/proc` check), R-3, R-4 (detection through the unique constraint, byte-identical check), R-5 (shared helper; lock before insert; refusal with zero rows), R-6 (no `--role`, `--org` or repeated `--join`), R-7 (ids-only log line, manual run record in the runbook, follow-up with one actor name), R-8 (ADR content), R-9 (runbook without `-T`; ops-workflow follow-up), R-10 (CLI engine, fixed messages).
  - **Rejected:**
    - The distinct exit code 2 for an existing email: the ticket says 1.
    - A `make create-account` target: not in the ticket; listed as a follow-up.
    - `RLIMIT_CORE`: a follow-up, not in the ticket.
- database-optimizer:
  - **Adopted:** no migration; `email_verified_at=func.now()` at insert; raise inside the scope (never return after a write); hash before the scope; `IntegrityError` → raise, not a pre-check; `_grant_locked(app, role, user.id, None, None, None)`; `# R4 … actor system:cli` marker; DB-level tests (zero rows, no `email_tokens`/`email_send_log`, DB clock).
  - **Rejected:** `ON CONFLICT DO NOTHING` — the savepoint path reuses `repo.add` and matches sign-up, as the consult allowed.
  - `display_name` stays NULL (no flag in scope).

## Assumptions
1. **`--join` mirrors the HTTP join exactly** (the default the orchestrator chose for the consults' open question). It refuses `admin`, unknown or inactive apps, `closed`, `approval`, a missing default role, and a default role with an `is_admin` permission. `org_mode` is handled as the endpoint does today: no check, app-scope grant. The "already a member" short-circuit cannot apply to a user created in the same transaction.
2. **Email confirmation** is a retype on the TTY. It is prompted on stderr and read from stdin, with echo on, after the TTY check and before the password prompts.
3. **Exit codes:** 1 for every refusal and failure (the ticket), including non-TTY, `--join admin`, a mismatch, a weak password and an existing email; 64 only for usage. This differs from `grant-super-admin` (2/3) and from `register-app` (64 for `admin`), deliberately, per the ticket.
4. **Audit:** before ADM-004 there is one ids-only log line `account_created actor=system:cli …` plus an R4 comment. The actor name is `system:cli`, aligned with `rbac/service.py:842`; the ticket's "actor `cli`" is read as the same actor. `AuditRecorder` is not called: its protocol needs a user actor and an `app_id`. The CLI configures no logging handler, so INFO lines do not reach the container output today; that is why the runbook requires a manual record.
5. **Output:** the ticket's three lines plus `email: <masked>` as the second line after `user_id`. The ticket says "The email is masked, as in `grant-super-admin`", which implies an email line. Final stdout:
   ```
   created account
   user_id: <uuid>
   email: y***@e***.com
   joined: <app_key>|-
   ```
6. **All `APP_ENV`s** are allowed; production is the purpose. The existing production settings validation (`TRUSTED_PROXY_CIDRS` etc.) applies through `_load_settings`.
7. **Password length cap:** `len(password) > 128` on the raw string, as the `SignupRequest` `Field(max_length=PASSWORD_MAX_LENGTH)` validates the raw `SecretStr`. Reported as the reason code `too_long`, a CLI-only code; sign-up answers 422 for the same input.
8. **App key format** is not pre-validated; a malformed `--join` value is an unknown app (exit 1, not echoed). The operator types the password before learning the app is unknown, because the app check is inside the one transaction. Accepted.
9. **Refactor of `JoinService`** is behaviour-preserving and in scope, because the consults required a shared helper. The existing `tests/test_auth_join.py` is the regression net.

## Blocking questions
None.

## Split proposal
None. One feature, no migration. Roughly: CLI ~170 lines, service ~70, helper refactor ~30, docs and ADR ~140, tests ~450. The total is above the ~400-line guide mainly because of tests and docs; the product code stays at ~270. It fits one implement/review cycle.

## Data model / migration
None. The command uses the existing `users.email_verified_at timestamptz NULL` (set to `now()` at insert) and the `uq_users_email` CITEXT unique constraint. `role_assignments` is unchanged. `alembic check` must stay clean.

## API contract
None (CLI only; no endpoint change).

## Follow-ups (not in this diff)
- ADM-004: extend `AuditRecorder` with a system actor (`system:cli`) and an optional `app_id`. Record `user.create_verified` and `app.join` for `create-account`, and the grant of `grant-super-admin`, in the same transaction. Fix the ticket wording `cli` → `system:cli`.
- Deployment repo (`mmlabs-online/deployment`): never add `create-account` to the `ops-org-admin-api` workflow choices; add a note there.
- Optional: a `make create-account email=… [app=…]` target.
- Optional: an early read-only check (existing email, joinable app) before the password prompts, for UX.
- Optional: `RLIMIT_CORE=0` in the CLI process.
