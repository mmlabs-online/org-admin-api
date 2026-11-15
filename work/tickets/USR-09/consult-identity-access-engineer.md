# Consult — identity-access-engineer — USR-09 (2026-11-15)

USR-09 consult (identity-access-engineer): `create-account` CLI

I only read code; nothing was changed or run. Overall the ticket is sound. The operator chooses the password, and the "existing email refuses" rule closes the takeover path. One gap needs a decision before planning: a mistyped email address, combined with the verified flag, hands the account to a stranger (Risk 1).

## Findings

**CLI patterns to copy (`/home/user/org-admin-api/app/cli.py`)**
- Each subcommand has its own `_Parser`, which swallows argparse messages so a submitted value is never echoed.
- Failures print one fixed line and map to an exit code. The code never calls `str(exc)`.
- `_cli_session_scope` uses `hide_parameters=True`, `NullPool` and a single `session.begin()`.
- `_load_settings` refuses `DB_ECHO`.
- `main()` sets `tracebacklimit=0` and detaches the sqlalchemy and asyncpg loggers.
- `amain` dispatches on `argv[:1]`, and `grant-super-admin` is the fall-through branch. A new branch must go before it.

**Sign-up path (`app/modules/auth/service.py`, `passwords.py`, `schemas.py`)**
- `SignupService.signup` does these steps in order:
  1. `normalize_email(str(EmailStr))`, which is `strip().lower()`.
  2. `PasswordPolicy.check`: NFKC normalization, `password_min_length` (default 12), and the bundled common-password list.
  3. Turnstile.
  4. Argon2id `hasher.hash`, which runs in a thread behind a capacity limiter.
  5. `UserRepository.add`, an insert inside a savepoint. A unique violation on CITEXT raises `IntegrityError`, which becomes `EmailTakenError`.
  6. `SignupHook`, which issues the verification token and email.
- The 128-character maximum lives in `SignupRequest` (`PASSWORD_MAX_LENGTH`), not in `PasswordPolicy`. A CLI that only calls `policy.check` would accept passwords sign-up rejects.
- `UserRepository.mark_email_verified` sets `email_verified_at = func.now()`, which uses the database clock.

**Join (`app/modules/membership/service.py`)**
- `JoinService.join` needs a device and an account session, so the CLI cannot call it.
- The rules it applies:
  - refuse `admin` before taking any lock;
  - take `lock_app` (lock order is app first, then user, per ADR-0003 §10);
  - the app must be `active`;
  - refuse `closed` and `approval`;
  - the default role must exist;
  - refuse a default role that holds an `is_admin` permission (logged as a warning);
  - grant with `AssignmentService._grant_locked(...)` and actor `None`;
  - record `app.join` through `AuditRecorder`.
- The ticket only lists unknown, disabled and `admin` apps as refusals. It is silent on `closed` and `approval` apps, a missing default role, and an admin-permission default role.

**Audit**
- `AuditRecorder` is still the log-only placeholder (ADM-004 has not landed).
- It requires `actor_user_id: UUID`, and a CLI has no actor user.
- `bootstrap_super_admin` handles the same situation with a comment ("ADM-004 audit entry (actor system:cli)") and no recorder call.

**Verified-flag policy**
- D16 in `docs/identity-architecture.md` and ADR-0007 line 8 say "no interim mark verified path".
- `docs/deploy.md` line 268 and the `app/cli.py` docstring say "never marks an email verified". Today that is a statement about `grant-super-admin` only, but readers take it as the policy.
- D2 and S5 (verified email required before join or token) are not weakened: the flag is set, not bypassed.

**Token path**
- `tokens/service.py` checks `email_verified_at` and then `effective_roles`.
- So AC1 ("log in at once, then get a token after `--join`") holds once the user row is verified and the assignment exists. No other state is needed: no device session and no `login_failures` row.

## Recommendations

1. **Service layer (R1).** Add one service method, for example `AccountProvisioningService.create_verified(email, password, join_key | None)` in `app/modules/auth/`.
   - Reuse `normalize_email`, `PasswordPolicy`, `PasswordHasher`, `UserRepository.add` and `_grant_locked`.
   - Do not reuse `SignupService` with a stub Turnstile verifier or a no-op hook. That creates a quiet Turnstile bypass that someone will later wire to HTTP.
   - The CLI layer only parses arguments, reads the TTY, and maps reasons to messages. (AC1, AC3)

2. **Order of operations.**
   - Before any database work: run the policy check (including max 128), confirm the second entry matches, then Argon2-hash. This keeps the app lock short and the hash cost out of the transaction.
   - Then, in one transaction:
     - if `--join` is given: reject `admin` before locking, take `lock_app(key)`, and check active, join policy and default role (Rec 3);
     - insert the user with `email_verified_at=func.now()`; an `IntegrityError` maps to the fixed "already registered" message and exit 1;
     - call `_grant_locked`.
   - Any refusal raises inside the scope so everything rolls back. Print only after commit, as `_register_app` does. (AC2, AC5)

3. **Join guards: mirror `JoinService` exactly.** Refuse `closed` and `approval` apps, a missing default role, and a default role with an `is_admin` permission, each with exit 1.
   - The operator vouches for the email, not for bypassing the app's join policy.
   - The admin-permission guard matters most. Without it, a misconfigured default role turns this command into an admin-grant path that sidesteps `grant-super-admin`'s checks.
   - Factor the guard sequence into a shared helper so it cannot drift from the join endpoint. (AC5, R11 "verified email required to join")

4. **Reading the password.**
   - Inject the reader and the TTY check for tests, for example `read_secret` and `stdin_isatty` parameters on `amain`.
   - Refuse unless `sys.stdin.isatty()` is true. Also turn `getpass.GetPassWarning` into an error with `warnings.catch_warnings()` + `simplefilter("error", GetPassWarning)`. Without a usable `/dev/tty`, `getpass` falls back to an echoing stdin read, which is the exact leak this ticket forbids. `docker compose run -T` would hit this.
   - Prompts go to the TTY or stderr only.
   - Provide no `--password` option at all. A test should assert that `--password x` gives usage exit 64 and that the usage line does not contain the value.
   - Treat EOF and `KeyboardInterrupt` during the prompt as a fixed refusal. (AC3, AC4)

5. **Confirm the email (fixes Risk 1).** Ask the operator to retype the email on the TTY (`input`, not argv) and compare the normalized values. A mismatch exits 1 with nothing created.
   - A cheaper alternative is to print the full address only to `/dev/tty` and require typing "yes".
   - Record which option the owner picks in the ADR. (AC1, R11)

6. **Secrecy tests.** Use capsys plus caplog at DEBUG for root, sqlalchemy and asyncpg loggers.
   - Assert the password, its NFKC form and the Argon2 hash appear in none of stdout, stderr or log records.
   - Also cover a forced database failure: patch the insert to raise an `IntegrityError` or `DBAPIError` carrying parameters, and check the fixed message is printed with no hash and no full email.
   - Stdout is exactly `created account` / `user_id:` / `joined:`, with the email masked by `mask_email`. (AC4)

7. **Audit (R4).** Do not call `AuditRecorder` with `actor_user_id=user.id`; that misattributes a staff action to the new user.
   - Emit one structured log line, for example `account_created source=cli user_id=… app=…|-` (ids only), and write the same `# R4: ADM-004 … actor cli` comment that `bootstrap_super_admin` uses.
   - Add a follow-up to ADM-004: the recorder needs a nullable or system actor for CLI events. (AC4, R4)

8. **ADR and docs.**
   - New ADR (0009): an amendment to D16 and ADR-0007 that allows exactly one path, with these points:
     - TTY-only;
     - root-equivalent trust, the same as `grant-super-admin`;
     - never touches existing accounts;
     - never exposed through the ops workflow or HTTP;
     - the typo threat and its mitigation;
     - `create-account` plus `grant-super-admin` together yield a verified admin without email proof, accepted at the same trust level.
   - Update the `app/cli.py` docstring, the D16 row, and `docs/deploy.md`.
   - The new deploy.md section should say:
     - use `docker compose ... run --rm --no-deps migrate ...` without `-T`;
     - start the line with a space or run `fc -p` first;
     - never set `DB_ECHO`;
     - log in at once and check `user_id` before copying it into `TUDO_OWNER_SUB`.
   - Keep line 268 accurate: `grant-super-admin` still never verifies. (AC6)

9. **Deployment repo.** The `ops-org-admin-api` workflow must not gain a `create-account` command; say so in deploy.md. The TTY refusal enforces this technically too. (Ticket scope)

## Risks

1. **Typo leads to account takeover (High).** If the operator mistypes the owner's email (say gmial.com), whoever controls that address can use forgot/reset: the reset email goes to that address, and the reset also marks the email verified. They then own an account that already holds a TuDo membership and whose `sub` is about to be hardcoded as `TUDO_OWNER_SUB`. Rec 5 mitigates this.
2. **Join-policy or admin-role bypass** if the CLI reimplements only the three refusals the ticket lists (Rec 3).
3. **Echoed password** through the `getpass` fallback when there is no TTY (Rec 4).
4. **Leaks through database errors.** The hash and full email appear in `IntegrityError` DETAIL and in parameters. `hide_parameters` covers parameters, and the fixed-message rule must cover the DETAIL lines; test both (Rec 6).
5. **Precedent creep.** A "verified on create" service method is one router away from a staff HTTP endpoint (out of scope). Mark the method CLI-only in its docstring, and list the restriction in the ADR so the reviewer and appsec can enforce it.
6. **Lock order.** Taking the user insert before `lock_app` would invert ADR-0003 §10. Lock the app first, as Rec 2 says.

## Open questions (for the owner, at PLAN)

1. Should `--join` honor `closed` and `approval` apps, refusing them (my recommendation), or may the operator override the join policy?
2. Which email confirmation: retype on the TTY, or show the full address on the TTY and require "yes"?
3. Exit codes: the ticket says 1 for everything. Should an existing email get its own code, the way `grant-super-admin` uses 2 and 3? (Optional; 1 is fine.)
4. Should the command be allowed when `APP_ENV` is local/test only, or in all environments? I assume all, since production is its purpose.
5. ADM-004: confirm the follow-up for a system/`cli` actor in `AuditRecorder`, so this command and `grant-super-admin` get real audit entries when ADM-004 lands.

Files read (under `/home/user/org-admin-api/`): `work/tickets/USR-09/ticket.md`; `app/cli.py`; `app/modules/auth/service.py`, `passwords.py`, `schemas.py`, `hooks.py`, `email_verification.py`; `app/modules/membership/service.py`; `app/modules/rbac/service.py` (780–910); `app/modules/users/models.py`, `repository.py`; `app/modules/audit/recorder.py`; `app/core/config.py`; `app/modules/tokens/service.py`; `docs/deploy.md` (265–330); `docs/identity-architecture.md` (D2, D16, D19); `docs/adr/0007-email-verification-and-delivery.md`; `docs/spec-v1.md` §5.8.
