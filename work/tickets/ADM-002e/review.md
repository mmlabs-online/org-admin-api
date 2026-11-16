# Review — ADM-002e

## Attempt 1 — 2026-11-16
Verdict: REWORK

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff `All checks passed!`; format `222 files already formatted`; mypy `Success: no issues found in 101 source files`; pytest `1713 passed, 1 warning in 101.47s`. Matches impl-log.
- alembic check: n/a (no migrations changed).
- Runbook (docs/deploy.md step 6 block, extracted to a scratch file, 3-space indent removed, 21 lines): `sh -n` ok (`/bin/sh` → dash), `dash -n` ok, `bash --posix -n` ok.
- Runbook dry-run: `curl` stub on PATH (logs args, writes a cookie line to the `-c` file, returns `{"account_id":"x","access_token":"t"}` for login/token, `sleep 30` on `/users`), `mktemp` wrapper logging created files, `TMPDIR` in scratch; stdin never a tty, so every `stty` fails:

  | Shell | HUP to the subshell (`pgrep -P`) during `/users` | `</dev/null` | normal exit |
  |---|---|---|---|
  | dash | exit 130; 1 logout call with `--max-time 10`; 3 files created, 0 left | exit 1; 0 logout calls; 0 left | exit 0; 1 logout; 0 left |
  | bash --posix | exit 130; 1 logout call with `--max-time 10`; 3 files created, 0 left | exit 1; 0 logout calls; 0 left | exit 0; 1 logout; 0 left |

- Extra probes (scratch only, no repo files):
  - Gate probe on `Settings(_env_file=None, database_url=…)` with APP_ENV unset: accepts `localhost`, `127.0.0.1`, `[::1]`; refuses `?`, `#`, `#x?host=e`, `%23?host=evil`, `localhost,evil`, `LOCALHOST`, `/db/?host=e`. `localhost/db%3Fhost=evil` and `localhost/db;host=e` are accepted, but the engine view is `host=localhost`, query `{}`, and only the database name is odd (harmless).
  - `test_resend_locks_user_for_share_before_token_insert` with `get_for_share` patched back to `get` → fails (StopIteration). The test is not vacuous.
  - Lock-mode probes for Issue 1, run against the real `_race` harness: (a) **plain `FOR UPDATE` with the share lock in place** → `reset [202, 204]`, `verify [202, 422]`, so no deadlock; (b) real `FOR NO KEY UPDATE` with `get_for_share`→`get` → `[202, 204]` / `[202, 422]`.

### Acceptance criteria
- [x] AC1 `?host=` / `hostaddr` / any query or fragment, without explicit APP_ENV, refuses startup. Evidence: allow-list at app/core/config.py:136-149 (one host, loopback, `url.query is None`, `url.fragment is None`, `make_url` view has an empty query and a loopback host; `make_url` failure gives the same message, :137-140). Tested by `tests/test_config.py:313` (12 cases). Passes.
- [x] AC1a The plain loopback default still works, and an explicit APP_ENV accepts query DSNs. Evidence: existing `test_app_env_default_ok_for_loopback` and `tests/test_config.py:323` (local/test × `?host=`/`?sslmode=`). Passes.
- [x] AC1b The error is fixed text and does not echo the DSN. Evidence: one constant message at app/core/config.py:134 and `tests/test_config.py:331` (no password marker, host or `host=`). Passes.
- [x] AC1c PGHOST/PGSERVICE are ignored. Evidence: `tests/test_config.py:344`. Passes.
- [x] AC2 The runbook passes `sh -n`/`dash -n` (and `bash --posix -n`). The trap logs out and cleans up with stdin not a tty, and keeps the status (130 / 1 / 0). Evidence: docs/deploy.md:445-452 and the dry-run table above.
- [x] AC3 Resend/disable race closed. Evidence: `UserRepository.get_for_share` at app/modules/users/repository.py:45-55 (`FOR SHARE`, `populate_existing`), used at app/modules/auth/email_verification.py:132, with the status/verified re-check on the locked row at :133-136. `tests/test_email_verification_api.py:539` asserts the `FROM users … FOR SHARE` index is lower than the `INSERT INTO email_tokens` index. It fails when reverted (probe above). Lock order: user → advisory → tokens on every path, so no cycle with `set_status`/`verify`/`reset`.
- [x] AC4 Every admin-app permission has `is_admin` at head. Evidence: `tests/test_rbac_migrations.py:94-98` asserts count > 0 (non-vacuous) and `NOT is_admin` = 0. Passes.
- [x] AC6 `.env.example` has keys only and a copy still gives local defaults. Evidence: .env.example has no values and the defaults are in comments. Tested by `tests/test_config.py:356` (regex `[A-Z0-9_]+=`) and `:365` (deletes all keys, `_env_file=".env.example"` gives `local`, not in `model_fields_set`, localhost). `env_ignore_empty=True` is at app/core/config.py:25. README note at README.md:17-18.
- [x] AC7 `make check` green (see above).

### Issues
#### Issue 1 — 🟡 should-fix — test meaning / out-of-plan justification (tests/test_email_lock_concurrency.py)
- **Where**: tests/test_email_lock_concurrency.py:1-7 (module docstring), :152-189 (`test_resend_vs_reset_no_deadlock`, `test_resend_vs_verify_no_deadlock`), :192-209 (control).
- **Expected**: the USR-04d S1 suite still pins "`get_for_update` must be `FOR NO KEY UPDATE`" through a pair of tests that differ only in the lock mode. This is what impl-log "Out-of-plan files" claims: "The control still proves that `get_for_update` must stay `FOR NO KEY UPDATE` against an unlocked token inserter."
- **Actual**: the control now changes two variables (plain `FOR UPDATE` **and** no resend share lock). The two positive tests run with the share lock, so the second request waits on the user row and never reaches the interleave the docstring describes. Reviewer probe (a): plain `FOR UPDATE` with the share lock passes both positive tests (`[202, 204]`, `[202, 422]`). The positive tests therefore no longer fail if `get_for_update` regresses to `FOR UPDATE`. No test runs `FOR NO KEY UPDATE` against an unlocked inserter, so the claim "must stay NO KEY UPDATE" has no positive half. Probe (b) shows that half passes today.
- **Fix instruction**: in tests/test_email_lock_concurrency.py, restore the control pair. For example, make `test_resend_vs_reset_no_deadlock` and `test_resend_vs_verify_no_deadlock` also `monkeypatch.setattr(UserRepository, "get_for_share", UserRepository.get)`, so they keep testing NO KEY UPDATE against an FK-only inserter (expected `[202, 204]` / `[202, 422]`, as in probe (b)). Alternatively add parametrized variants with and without the share lock. Then update the module docstring: the interleave is for the unlocked resend that the tests simulate, and ADM-002e's share lock now serializes the real resend on the user row. Update the impl-log out-of-plan justification. No production code change.

### Rules R1–R11
No violations found. R1: SQL lock in the repository, service calls only (repository.py:45, email_verification.py:132). R5: fixed error text and keys-only `.env.example`. R7: one out-of-plan file, justified (see Issue 1 for the accuracy of that justification). R8: green. R11: the runbook keeps the token out of argv/vars, the header file is deleted first, logout is bounded.

### Nits (do not block)
- 💭 app/core/config.py:139 `except Exception` around `make_url` is broad. It is fine here because the message is fixed and `from None` is used.
- 💭 The gate accepts `localhost/db%3Fhost=evil` and `localhost/db;host=e`. The engine sees host `localhost` with an empty query (only the database name is odd), so this is not a bypass. Optionally add one as a regression case.

### Follow-ups (out of scope)
- Remove the unused `APP_DEBUG` setting and its `.env.example` key (from plan).
- With every existing-user `email_tokens` inserter now holding a user share lock (forgot, resend; the sign-up hook works on a row not yet visible), consider recording in docs/identity-architecture.md that `FOR NO KEY UPDATE` in `get_for_update` is now defence in depth for S1, not the only guard.
