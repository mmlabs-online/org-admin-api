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

## Attempt 2 — 2026-11-15 (appsec-engineer)

Scope `git diff 7d8f0ec HEAD -- app tests docs` (USR-09 code in af4e125; the rest is the ADM-002b/ADM-004 merge 67f1121). `make check` not run by appsec (no Postgres in its sandbox); the reviewer ran it green (1445 passed). **No 🔴.**

### 🟡 should-fix
- **CLI grant uses a privileged actor** (`app/modules/auth/provisioning.py:79-81`, `SYSTEM_CLI` is `privileged=True`, `app/modules/rbac/actor.py:21`). `_guard_privileged` (`app/modules/rbac/service.py:1137-1147`) is the only actor-dependent check in `_grant_locked`; a privileged actor passes it for admin-app roles or roles with `is_admin` permissions. Not exploitable today (`lock_joinable_app` refuses admin `membership/service.py:41`; `join_default_role` refuses admin-permission roles under the app lock `:51-61`; `expires_at`/`org_id` None; new user), but HTTP join keeps the extra layer with `SYSTEM_JOIN` (`privileged=False`, `:106-108`) while the CLI path loses it. Fix: grant with a non-privileged actor (e.g. `SYSTEM_JOIN`, or a new `SystemActor("system:cli", privileged=False)`), audit actor stays `system:cli`. Test: monkeypatch `join_default_role` to return an admin-permission role → `AdminWildcardRequiredError`, no user, assignment or audit row.
- **Carried from attempt 1:** nothing enforces that only `app/cli.py` imports `app.modules.auth.provisioning` (`provisioning.py:1`, `:48`). Fix: `ast` walk over `app/` in `tests/test_cli_create_account.py`.

### 💭 notes
- `changes` keys don't follow ADR-0009 §4 (`provisioning.py:74`: `verified` not in `diff.USER`; `"active"` hard-coded). No leak. Optional: `USER_CREATE = ("status", "verified")` in `diff.py` and use `user.status`.
- `docs/identity-architecture.md:199-201` (S8) omits `create-account` from the CLI audit list.
- `user.created` is recorded before the grant's guards (`provisioning.py:70`), unlike ADR-0009 §3's "after its last guard"; harmless (rollback asserted).

### 🟢 verified
No email/password/hash in audit rows (tests `tests/test_cli_create_account.py:191-202`); ADM-004 rules kept (`AuditLogger.record` only; `actor_consistent` `app/modules/audit/models.py:25-29`; `USER_CREATED` needs no migration); same transaction, nothing left after refusals (`tests/test_cli_create_account.py:306, 476, 493`); `app.joined` matches HTTP join; merged `JoinService` keeps every check in order (`membership/service.py:76-118`); ADM-002b guards intact for HTTP join; ADR-0009/0010 numbering consistent.

### Follow-ups
Operator identity for CLI audit rows; runtime DB role with INSERT/SELECT only on `audit_log` (ADR-0009 §1); deployment-repo guard so `create-account` never becomes an ops-workflow command; optional `RLIMIT_CORE=0`.

## Attempt 3 — 2026-11-15 (appsec-engineer)

Re-check of the two Attempt 2 🟡 items (a703242, diff af4e125..HEAD); code read only (no Postgres in the appsec sandbox). **Both fixed; no 🔴, no 🟡.**
- 🟢 **Non-privileged grant:** `app/modules/auth/provisioning.py:80-82` uses `SYSTEM_JOIN` (`privileged=False`, `app/modules/rbac/actor.py:22`), so `_guard_privileged` (`app/modules/rbac/service.py:1137-1146`) raises `AdminWildcardRequiredError` for admin-app or `is_admin` roles, as on HTTP join (`app/modules/membership/service.py:106-107`). Audit rows still `system:cli` (`provisioning.py:70-76`, `:84-91`; log line `:93`). The grant actor is used only by `_guard_privileged` and `granted_by` (NULL for any `SystemActor`, `actor.py:38-39`); `_grant_locked` does no auditing (`service.py:1090`). Test `tests/test_cli_create_account.py:497-515` asserts exit 1 with the generic message and no user, assignment or audit row. 💭 The test doesn't assert the cause is `AdminWildcardRequiredError` (optional: `pytest.raises` on `create_verified` or caplog).
- 🟢 **Import guard:** `tests/test_cli_create_account.py:518-534` walks every `app/**/*.py` and requires the importer set to be exactly `{"app/cli.py"}`; covers `import`, `import ... as`, `from ... import`, `from app.modules.auth import provisioning`, relative imports (incl. `__init__.py`), and imports inside functions. 💭 Not covered: `importlib`/`__import__` strings (none in `app/`); optional extra string check.
- 🟢 `docs/identity-architecture.md:201` lists `create-account` among CLI audit writes.
Follow-ups unchanged from Attempt 2.
