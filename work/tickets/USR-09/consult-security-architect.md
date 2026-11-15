# Consult — security-architect — USR-09 (2026-11-15)

USR-09 consult (security-architect): `create-account` CLI

## Findings

1. **Trust level.** The command can only run with a shell on the Mac, through the `migrate` one-shot service on the `db` network. Anyone who can do that can already use `psql` on `org_admin`, so this adds no new privilege. What changes is policy: it creates accounts without Turnstile, without the per-IP limit and without proof that the person controls the email address (D2). The real attack surfaces are the invariants the command could get wrong:
   - verifying an existing account
   - granting more than the member role
   - leaking the password
   - leaving no audit trail

2. **getpass behaviour matters more than the ticket says.**
   - `getpass.getpass` reads from `/dev/tty`, not stdin. When it cannot open `/dev/tty`, it falls back to stdin **with echo on**, raising only a `GetPassWarning`.
   - So "refuse when stdin is not a TTY" needs two explicit checks: `sys.stdin.isatty()` before prompting, and `GetPassWarning` turned into an error (e.g. `warnings.simplefilter("error", getpass.GetPassWarning)` inside a `catch_warnings` block).
   - `EOFError` (Ctrl-D) and `KeyboardInterrupt` need fixed messages. `getpass` restores terminal echo in its own `finally`, and `sys.tracebacklimit = 0` is already the backstop.

3. **Docker TTY.**
   - `docker compose run` allocates a pseudo-TTY by default when the calling terminal has one. `-T`, or `ssh` without `-t`, gives no TTY, and the command then correctly refuses.
   - With a TTY, the typed password travels over the attach stream. It does not appear in argv, env, `docker inspect`, or in container logs (the log driver records output, and echo is off).
   - Prompts do reach the json-file log, but `--rm` deletes it. The `--email`/`--join` arguments show in `ps`/`docker inspect` while the container runs. Because the command now waits on an interactive prompt, that window is longer than for `grant-super-admin` (accepted risk, same as USR-02f).

4. **Existing pieces can be reused, with caveats:**
   - `PasswordPolicy(settings.password_min_length)` and `PasswordHasher(settings)` (`app/modules/auth/passwords.py`).
   - `UserRepository.add` uses a savepoint, and the CITEXT unique constraint (`app/modules/users/models.py:22`) turns a duplicate into `IntegrityError`. That closes the race with a concurrent sign-up.
   - `SignupService.signup` **cannot** be reused as is: it calls Turnstile and `VerificationEmailHook`, which would issue a token and write `email_send_log`.
   - `PASSWORD_MAX_LENGTH` (`app/modules/auth/schemas.py`) also has to apply.
   - Pydantic `ValidationError` carries `input_value`. If the password goes through a model, a printed error would leak it.

5. **Join logic.** `JoinService.join` (`app/modules/membership/service.py`) is bound to a device/account session, so the CLI cannot call it. Its invariants are:
   - `admin` refused before any read
   - `lock_app` first (lock order app → users, ADR-0003 §10)
   - the app must be active
   - closed/approval refused
   - default role must not be NULL
   - `role_has_admin_permission` refused
   - `_grant_locked`
   - `recorder.record("app.join")`

   Copying these into the CLI path is a drift risk.

6. **Audit (ADM-004 status).**
   - ADM-004 is still BACKLOG. `LogAuditRecorder` only writes a log line, and its protocol requires `actor_user_id: uuid.UUID`, so there is no system/CLI actor yet.
   - The ticket says actor `cli`; the code comment in `bootstrap_super_admin` says `system:cli`. These are inconsistent.
   - The other ops commands are audited for now by the GitHub workflow run (`docs/deploy.md`: "Until ADM-004 the workflow run … is the audit trail"). `create-account` runs only from a Mac shell, and `--rm` throws the container logs away, so **today it would leave no durable trace at all**. The only evidence would be the user row and an `email_verified_at` equal to `created_at`.

7. **Existing rules this contradicts.** D16 ("no interim mark-verified path") was an ordering decision before USR-04. `docs/deploy.md` and the `app/cli.py` docstring say the CLI "never marks an email verified". Both have to be amended, not silently broken. Next free ADR number: 0009.

8. **Interaction with grant-super-admin.**
   - `create-account` + `grant-super-admin` gives a verified super-admin without any proof of email control. That is acceptable at DB-host trust, but it changes the `grant-super-admin` runbook: step 1 "sign up yourself", the `created_at` check, and "email_verified stays no".
   - `grant-super-admin` has nothing to defend against here as long as `create-account` never assigns admin-app roles.

## Recommendations

**Password handling (AC3, AC4, R5, R11)**
- R-1. Order of checks:
  1. Parse args with `_Parser` (no `--password`; unknown args give a fixed usage line that never echoes argv).
  2. `_load_settings` (refuses `DB_ECHO`).
  3. `sys.stdin.isatty()`, else exit 64.
  4. `getpass` twice, with `GetPassWarning` as an error.
  5. Compare the two entries; on mismatch print one fixed line, no retry loop.
  6. Check the length cap, then `PasswordPolicy.check`, printing only the reason codes (`too_short`/`too_common`).
  7. Hash, then start the DB transaction.

  Keep the password in a local variable only, never in an exception, dataclass or model. Do not read it from env, a file or stdin.
- R-2. Tests:
  - Patch `getpass.getpass`, then use `capsys` + `caplog` to assert the password is absent from stdout, stderr and every log record. Once ADM-004 lands, also assert it is absent from audit rows.
  - Assert the parser rejects `--password` without echoing it.
  - Assert non-TTY stdin refuses before any prompt and before any DB access.
  - Assert the `GetPassWarning` fallback is refused.
  - "Process list" can only be tested structurally (no argv or env path exists); say so in the plan instead of claiming a runtime `ps` test.

**Creating the account (AC1, AC2)**
- R-3. Add a separate service method, e.g. `AccountProvisioningService.create_verified(email, password_hash, join_key)` in auth/users. It sets `email_verified_at = func.now()` in the INSERT itself.
  - It does not call `SignupHook`, Turnstile, `email_send_log` or `email_tokens`.
  - Normalize the email exactly as sign-up does (EmailStr → strip → lower, as `_parse_email` does).
- R-4. Existing email:
  - Detect it with the unique constraint (`IntegrityError` from `UserRepository.add`), not only a pre-check, so the race with sign-up is covered.
  - Print one fixed message whatever the existing account's state (verified, unverified or disabled), and touch nothing: no password, `email_verified_at`, status, `login_failures` or sessions.
  - Test: the row's hash, `email_verified_at` and `updated_at` are byte-identical afterwards.
  - Consider a distinct exit code (e.g. 2, as `grant-super-admin` does for its email case) so scripts can tell "taken" from "failure". The ticket says 1, so this is the owner's call.

**`--join` (AC5)**
- R-5. Pull the default-role core out of `JoinService.join` into one shared function, e.g. `assign_default_role_locked(app, user)`, used by both the HTTP join and the CLI. Rules:
  - Refuse `--join admin` while parsing, before any DB read.
  - Then `lock_app(key)` **before** inserting the user (lock order app → users).
  - Refuse if the app is unknown or not active, if `join_policy` is closed or approval (mirror the endpoint; staff must not get a back door around closed apps), if the default role is NULL, or if the role holds any `is_admin` permission.

  Everything runs in the one `_cli_session_scope` transaction, so any refusal rolls back the user insert. Tests: each refusal leaves zero `users` rows for the email.
- R-6. No `--role`, no `--org` and no repeated `--join`. The command can only produce `<app>:member` at app scope. State this in the ADR as the boundary with `grant-super-admin`.

**Audit (R4)**
- R-7. Until ADM-004:
  - Emit one structured log line with ids only: `account_created_cli user_id=… app_id=…|-`. Call `recorder.record` only if the protocol allows a non-user actor. Do not invent an actor UUID.
  - Have the runbook tell the operator to record the run (date, `user_id`, app key, operator) in the deployment repo or `work/decisions.md`, because the container logs vanish with `--rm`.
  - Add a follow-up on ADM-004: support a system actor (pick **one** name, `system:cli` or `cli`, and fix the other place), and record `user.create_verified` + `app.join` for this command in the same transaction.

**ADR-0009 "Operator-vouched verified email (CLI create-account)" (AC6)**
- R-8. Content:
  - **Context:** the bootstrap deadlock (the verify link needs an app origin that serves `/verify-email`; TuDo's `TUDO_OWNER_SUB` is needed before its release), D2/D16, the options the owner declined.
  - **Decision:**
    - Only `python -m app.cli create-account` may set `email_verified_at` without a token, and only on a row it inserts in the same transaction.
    - Never on an existing account, never over HTTP, never from the ops workflow or any non-TTY path.
    - The password is chosen by the operator at a TTY.
    - Optional join gives only the app's default non-admin role under the join-endpoint rules.
  - **Amends:** D16 and the `docs/deploy.md` / `app/cli.py` "never marks verified" wording, which becomes "except create-account".
  - **Accepted risks:**
    - (a) A typo verifies an address the operator does not control. The real owner of that address can then use "forgot password" (which sets the password and marks the email verified) and take over the account, including TuDo ownership via `TUDO_OWNER_SUB`.
    - (b) Before ADM-004 there is no durable audit trail.
    - (c) The email is visible in `ps` while the prompt waits.
    - (d) Turnstile and the rate limits do not apply (operator-only).
  - **Mitigations:**
    - The output masks the email. The runbook tells the operator to check the address before pressing Enter and to log in once at the target app right after.
    - Bootstrap only; the ADR is revisited if anyone proposes using it for routine onboarding (bulk import and staff HTTP endpoints stay out of scope).

**Docs and deployment (AC6, R7)**
- R-9. `docs/deploy.md`:
  - Add the section with the full `docker compose … run --rm --no-deps migrate /app/.venv/bin/python -m app.cli create-account --email … --join …` line, with a leading space or `fc -p` first.
  - Explicitly: **no `-T`**, run from an interactive terminal (`ssh -t` if remote), never `DB_ECHO`.
  - Update the "Bootstrap the first super-admin" wording.
  - Add a follow-up for the deployment repo: **do not** add `create-account` to the `ops-org-admin-api` workflow choices.
  - A `make create-account` target is fine locally (it inherits the terminal).
- R-10. Use the CLI's own engine (`hide_parameters=True`) so the Argon2 hash never shows up in a DB error. Never `str(exc)`; the catch-all prints one fixed line, as the other subcommands do.

## Risks

- **High (policy):** email typo leads to a later takeover by the real owner of that address through forgot/reset. This is inherent; the ADR accepts it and the runbook mitigates it.
- **Medium:** no audit trail before ADM-004 (R4 gap is larger than for the workflow-run commands).
- **Medium if missed:** the `getpass` fallback reads with echo when `/dev/tty` is missing, and the password appears on screen and in container output.
- **Medium if missed:** duplicating the join rules lets them drift from `JoinService` (e.g. a later change to admin-permission or policy checks).
- **Low:** the email shows in `ps`/`docker inspect` for the length of the prompt; the password string stays in process memory until exit (Python cannot zero it); core dumps (optionally set `RLIMIT_CORE` to 0).
- **Low:** if Postgres has `log_statement`/`log_min_duration_statement` turned on, it could log the INSERT parameters, which include the hash (not the password).

## Open questions (owner)

1. Should `--join` refuse `approval`/`closed` apps (recommended: yes, mirror the endpoint) or let staff override them?
2. Exit code for an existing email: 1 (ticket) or a distinct 2?
3. Should ADM-004 be a hard prerequisite instead, or is the log line + manual ops note enough for a one-off bootstrap? Which actor name: `cli` or `system:cli`?
4. Should apps with `org_mode=required` be refused for `--join` until USR-06, or should the CLI mirror the join endpoint, which currently grants app scope?
5. Should the CLI print the full email once, to the TTY only, for a typo check (conflicts with R11's masking), or keep it masked and rely on the runbook check?

Files read: /home/user/org-admin-api/work/tickets/USR-09/ticket.md, CLAUDE.md, app/cli.py, docs/deploy.md, docs/adr/0007-email-verification-and-delivery.md, docs/identity-architecture.md, app/modules/audit/recorder.py, app/modules/membership/service.py, app/modules/auth/service.py, app/modules/auth/passwords.py, app/modules/auth/hooks.py, app/modules/users/repository.py, app/modules/rbac/service.py, Makefile, work/backlog.md
