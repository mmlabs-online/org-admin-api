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

## Attempt 2 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff `All checks passed!`; format `222 files already formatted`; mypy `Success: no issues found in 101 source files`; pytest `1716 passed, 1 warning in 113.67s` (the warning is the existing `forkpty` DeprecationWarning). This matches impl-log Attempt 2 (1716 = 1713 + 2 `unlocked` variants + 1 conftest test).
- alembic check: n/a (no migrations changed).
- Working tree is clean after every probe (`git status --short` empty). The concurrent commit 8920b56 (security re-check) touches only `work/tickets/ADM-002e/security-review.md`.

### Verification of the listed fixes
1. **Review Issue 1 (fixed).** tests/test_email_lock_concurrency.py:156 `SHARE_LOCK` parametrizes `share-lock`/`unlocked`. :159-162 `_maybe_unlock_resend` patches only `get_for_share → get`. The positive tests use it at :165-169 and :184-188. The control at :209-227 uses the same helper (:225), so the `unlocked` positives and the control now differ only in the `get_for_update` lock mode. The module docstring (:1-11) and the control docstring now describe this accurately.
   - Reviewer probe: I changed app/modules/users/repository.py:31 to `.with_for_update()` (plain `FOR UPDATE`). Result: `2 failed, 5 passed`, and the failures are `test_resend_vs_reset_no_deadlock[unlocked]` and `test_resend_vs_verify_no_deadlock[unlocked]`. After `git checkout -- app/modules/users/repository.py`: `7 passed`.
2. **Security 🟡1 (fixed).** tests/conftest.py:11-14 now reads `os.environ.get("TEST_DATABASE_URL") or "<…/org_admin_test>"`. The new test is tests/test_config.py:380 (subprocess, `TEST_DATABASE_URL=""`, cwd anchored to the repo root).
   - Reviewer probe: I swapped in the attempt-1 conftest (`git show ae413d6:tests/conftest.py`). The test failed: `AssertionError` on `endswith("/org_admin_test")`. After restoring: `1 passed`. A direct check with `TEST_DATABASE_URL=` and the current conftest gives `TEST_DATABASE_URL` and `DATABASE_URL` both equal to `…/org_admin_test`.
3. **Security 💭2 (docstring).** app/modules/users/repository.py:46-51: the `get_for_share` docstring states the device-row/user-share order and why the lock must stay `FOR SHARE`. Only the docstring changed; there is no code change.
4. **Security 💭4 (runbook).** docs/deploy.md:446: the trap removes `$hdr` and `$body` before the logout curl and `$jar` after it. The bullet at docs/deploy.md:469-472 matches.
   - Syntax: I extracted the block (docs/deploy.md:441-461, 3-space indent removed, 21 lines). `sh -n`, `dash -n` and `bash --posix -n` all pass.
   - Dry-run: `curl` stub on PATH (writes the cookie to the `-c` file, returns JSON for login/token, `sleep 5` on `/users`, lists `$TMPDIR` at logout); `mktemp` wrapper logging the creation order (jar, body, hdr); stdin never a tty, so every `stty` fails:

     | Shell | `kill -HUP` subshell during `/users` | `</dev/null` | normal |
     |---|---|---|---|
     | dash | rc 130; 1 logout `--max-time 10`; at logout only the 1st mktemp (jar) present; 3 created, 0 left | rc 1; 0 logouts; 0 left | rc 0; 1 logout; only jar at logout; 0 left |
     | bash --posix | rc 130; 1 logout `--max-time 10`; only jar at logout; 3 created, 0 left | rc 1; 0 logouts; 0 left | rc 0; 1 logout; only jar at logout; 0 left |

### Scope
`git diff --stat 9937599 084693f` lists the following files:
- the 5 files in impl-log Attempt 2 "Files touched": repository.py (+2 docstring), deploy.md, conftest.py, test_config.py, test_email_lock_concurrency.py;
- the work/ bookkeeping files: backlog, ticket, impl-log, review, security-review.

There are no other code changes, and the Attempt 1 ACs (config gate, resend share lock, rbac migration test, `.env.example`) are untouched and still green in the full run.

### Acceptance criteria
All the Attempt 1 evidence still holds, and the line numbers are unchanged except where noted above. Every AC is met: AC1–AC1c, AC2 (re-verified above), AC3, AC4, AC6 and AC7 (`make check` green).

### Rules R1–R11
No violations. R5: the test fallback can no longer reach the dev DB. R7: every change maps to a listed review or security item. R8: green, and the new tests fail when their fixes are reverted.

### Nits (do not block)
- 💭 work/tickets/ADM-002e/ticket.md still shows `Attempts | 1`. The orchestrator should bump it.

### Follow-ups (out of scope)
- These are unchanged from Attempt 1: remove `APP_DEBUG`; per-signal exit codes in the runbook (security 💭5); anchor the `.env.example` test paths in tests/test_config.py to `Path(__file__)`; record in docs/identity-architecture.md that `FOR NO KEY UPDATE` is now defence in depth for S1.
