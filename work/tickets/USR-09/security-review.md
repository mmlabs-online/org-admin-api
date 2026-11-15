# Security review — USR-09

## Attempt 1 — 2026-11-15 (appsec-engineer)

Reviewed `git diff 4c01328 HEAD` (shallow clone has no merge base for `...`), the same five USR-09 commits 0311bea..c6e98aa. `make check`: 1254 passed, 1 warning (stdlib forkpty DeprecationWarning from the pty test). **No 🔴.**

### Verified
- **Existing accounts never touched:** INSERT only via `UserRepository.add` in a savepoint (`app/modules/auth/provisioning.py:58-62`, `app/modules/users/repository.py:75-81`); CITEXT unique clash → `EmailTakenError ... from None`; no pre-check/UPDATE/upsert; `email_verified_at=func.now()` only on the inserted row. Test `test_existing_email_exit_1_unchanged` (unverified, verified, disabled, mixed case; byte-identical; no assignment with `--join`).
- **Not reachable from HTTP:** only `app/cli.py:326/362` imports/calls `AccountProvisioningService`.
- **No role beyond the default member role:** `--join admin` refused at `app/cli.py:316` before settings/DB and in `lock_joinable_app` (`app/modules/membership/service.py:36-42`); exact key match + `ck_apps_key_format`; `join_default_role` (`:46-59`) refuses closed/approval/missing default role/admin-permission role, read under the `FOR NO KEY UPDATE` app lock; no `--role`/`--org`, repeated `--join` is a usage error (`cli.py:296-304`); `_grant_locked` with org/expiry/actor `None`.
- **HTTP join refactor preserves every check and order;** moved code verbatim; `tests/test_auth_join.py` unchanged and passing.
- **Password secrecy:** no `--password`, `allow_abbrev=False`; never from env; weak-password output only whitelisted reason codes (`cli.py:349-354`); fixed messages `from None`, catch-all never `str(exc)` (`cli.py:390`); engine `hide_parameters=True, echo=False` (`cli.py:179-181`); `DB_ECHO` refused before any prompt (`cli.py:168`); loggers silenced, `tracebacklimit=0`; audit line ids only; tests cover capsys, caplog DEBUG, forced DB error, `/proc/<pid>/cmdline` and `environ` via pty.
- **getpass echo fallback refused:** `GetPassWarning` → error inside `catch_warnings` (`cli.py:283-291`); non-TTY refused before any prompt or DB access.
- **Email normalization identical to sign-up** (`cli.py:141-148` vs `auth/service.py:36-37,58`); password checks match sign-up (128 raw, `policy.check`, NFKC in the hasher).
- **Every refusal rolls back;** hash before the transaction; success printed after commit; tests `test_join_refusals_create_nothing`, `test_failure_after_insert_rolls_back_user`.
- **No new secrets/config,** no migration. **Runbook safe** (`docs/deploy.md:545-573`, One-time setup step 6): no `-T` (`ssh -t` remote), `--rm --no-deps migrate`, never `DB_ECHO`, never an ops-workflow command; ADR lists accepted risks a–f.

### 🟡 should-fix
- **Nothing enforces "never call from a router"** (`app/modules/auth/provisioning.py:1`, `:44`). The ADR-0009 promise rests on a docstring; wiring `create_verified` into a router later would create an unauthenticated (until ADM-002) verified-account + join path without Turnstile. Fix: a regression test in `tests/test_cli_create_account.py` that walks `app/` with `ast` and asserts the only importer of `app.modules.auth.provisioning` is `app/cli.py` (or an import-linter contract).

### 🟢 notes
- Ctrl-C on the email retype (`app/cli.py:276-280`) isn't caught (EOF is); contained by `tracebacklimit=0`, no secret read yet. Optional: catch `KeyboardInterrupt` in `read_line`.
- History hygiene (`docs/deploy.md:545-546`): leading space needs `HISTCONTROL=ignorespace` (bash) / `setopt HIST_IGNORE_SPACE` (zsh); `fc -p` is zsh-only. Only the email is on the command line.
- Any `IntegrityError` on the users insert → "email exists" (`provisioning.py:61`), same as sign-up.
- On password mismatch `del password` is skipped (`cli.py:357`); accepted as ADR-0009 risk f.

### Follow-ups (out of scope)
ADM-004 durable audit with `system:cli` actor for `create-account` and `grant-super-admin`; deployment-repo guard so `create-account` never becomes an ops-workflow choice; optional `RLIMIT_CORE=0`; optional read-only pre-check before the password prompts.
