# USR-09: Create a staff-vouched account from the CLI (bootstrap for a new app's first user)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | auth, security, db |
| Attempts | 1 |
| Created | 2026-11-15 |
| PR | — |

Depends on: USR-01, USR-02f, USR-07.

## Goal
An operator can create one verified account from the command line, so the first user of a new app can exist before any app that hosts sign-up is live. First consumer: TuDoCuaTui. Its next release cannot start without the owner's `sub` (`TUDO_OWNER_SUB`, needed by its ownership migration), its own sign-up page ships only in that release, and no registered app runs sign-up in production yet.

## Spec reference
`docs/spec-v1.md` §5.8 (users, passwords, verified email); `docs/deploy.md` "Bootstrap the first super-admin" (USR-02f CLI pattern); `docs/identity-architecture.md` D16 (verified email before any token), D19 (origins). The verified-flag exception below needs an ADR.

## Scope (proposal; owner decides at plan)
- **New subcommand:** `python -m app.cli create-account --email <e> [--join <app_key>]`.
- **Password:**
  - Read twice from the TTY with `getpass` and checked against the sign-up password policy.
  - Never taken from argv, env or a workflow input. Refused when stdin is not a TTY.
  - So the command runs from a shell on the Mac (the `migrate` one-shot service, like the `grant-super-admin` fallback), not from the `ops-org-admin-api` workflow.
- **Email verification:**
  - The account is created with `email_verified = true`, because the operator vouches for the address.
  - This is the one deliberate exception to "the CLI never marks an email verified" (`docs/deploy.md`), and it gets an ADR.
  - Creating the account unverified does not solve the bootstrap, because the verification link needs an app origin that hosts `/verify-email`.
- **Existing email:** exit 1 with a fixed message. Never change an existing account's password or verification state, so the command cannot take over an account.
- **`--join <app_key>`:** assigns the app's default member role, as the join endpoint does, in the same transaction. Unknown, disabled or `admin` app: exit 1, nothing created.
- **Audit and secrecy:** an audit entry with actor `cli` once ADM-004 lands (R4). The password never appears in output, logs, audit or errors (R5, R11).
- **Output:**
  ```
  created account
  user_id: <uuid>
  joined: <app_key>|-
  ```
  The email is masked, as in `grant-super-admin`.
- **Docs:** a `docs/deploy.md` section "Create an account from the CLI (bootstrap only)" with the shell command (leading space / `fc -p`, `--rm --no-deps`, never `DB_ECHO`).

## Acceptance criteria
- [ ] Creates a verified user that can log in at once and, after `--join`, obtain an access token for that app.
- [ ] An existing email gives exit 1 and leaves the existing user unchanged.
- [ ] A weak password is refused with the same rules as sign-up. Non-TTY stdin and a mismatched confirmation are refused.
- [ ] The password never appears in stdout, stderr, log records, audit or the process list (tests check all of them).
- [ ] `--join` with an unknown, disabled or `admin` app exits 1 and creates nothing (one transaction).
- [ ] An ADR records the verified-flag exception, and `docs/deploy.md` is updated. `make check` passes.

## Out of scope
- Bulk import.
- Staff HTTP endpoints for creating users.
- Setting passwords for existing users (use forgot/reset).
- Org assignment (USR-06).

## Notes / answers from owner
- 2026-11-15 (owner, in the TuDoCuaTui session: "chọn B. Xin org-admin-api thêm lệnh CLI tạo tài khoản cho staff"). The other bootstrap options were declined: a temporary localhost origin plus localhost Turnstile hostname for a local sign-up, and a TuDo "setup mode" release.
- TuDo flow afterwards:
  1. USR-07 `register-app --key tudocuatui --origin https://tudocuatui.mmlabs.online` (join `open`).
  2. `create-account --email <owner> --join tudocuatui`.
  3. Copy `user_id` into TuDo's `TUDO_OWNER_SUB`.
  4. Release TuDo.
- Security note: this is a root-equivalent operator action on the DB host, at the same trust level as `grant-super-admin`. Pre-registration hijack does not apply, because the operator chooses the password. The verified flag is the main policy decision.
