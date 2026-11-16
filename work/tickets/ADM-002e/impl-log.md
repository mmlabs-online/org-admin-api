# Implementation log — ADM-002e

## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| `app/core/config.py` | Item 1: `_app_env_explicit_gate` rewritten as an allow-list (one loopback host, `query is None`, `fragment is None`, and the `make_url(str(url))` engine view has an empty query and a loopback host; `make_url` failure → same fixed message). Imports `make_url`. |
| `app/modules/users/repository.py` | Item 3: `UserRepository.get_for_share(user_id)` (`FOR SHARE`, `populate_existing`). |
| `app/modules/auth/email_verification.py` | Item 3: `resend` reads the user via `get_for_share`; existing status / verified checks now run on the locked row. |
| `docs/deploy.md` | Item 2: step 6 block — `jar= body= hdr=` before the trap, new EXIT trap (`rc=$?; set +e`, ignores HUP/INT/TERM, `stty echo 2>/dev/null \|\| true`, header file deleted first, logout `--max-time 10` only with a non-empty jar, `exit $rc`), three separate `mktemp` after the traps, `stty … 2>/dev/null \|\| true` on the password line; EXIT-trap bullet updated. Admin origin left as `https://admin.mmlabs.online` (current block after ADM-012; rename is ADM-014). |
| `.env.example` | Item 6: `APP_ENV`, `APP_DEBUG`, `DATABASE_URL`, `TEST_DATABASE_URL` values removed; defaults in comments. |
| `README.md` | Item 6: note under Quick start about keys-only `.env.example` and a stale `APP_ENV=local` in an old `.env`. |
| `tests/test_config.py` | Items 1, 6: new tests (below); `import re`. |
| `tests/test_email_verification_api.py` | Item 3: SQL-capture test (imports `event`, `Callable`). |
| `tests/test_rbac_migrations.py` | Item 4: async head-state test; docstring line; `AsyncSession` import. |
| `tests/test_email_lock_concurrency.py` | Out of plan, see below. |

### Tests added / changed
- `tests/test_config.py › test_app_env_required_for_loopback_with_query` (12 cases: `?host=prod-db.internal`, `?host=/var/run/postgresql`, `?host=%2Ftmp`, `?ho%73t=evil`, `?HOST=evil`, `?hostaddr=10.0.0.5`, `?dsn=postgresql://evil/db`, `?service=prod`, `?sslmode=disable`, `?`, `#?host=evil`, `#frag`)
- `tests/test_config.py › test_app_env_explicit_ok_with_query` (4 cases: local/test × `?host=prod-db.internal`/`?sslmode=disable`)
- `tests/test_config.py › test_app_env_query_error_does_not_echo_dsn`
- `tests/test_config.py › test_app_env_pghost_env_ignored`
- `tests/test_config.py › test_env_example_keys_only`
- `tests/test_config.py › test_env_example_copy_gives_local_defaults` (deletes every `.env.example` key from os.environ first)
- `tests/test_email_verification_api.py › test_resend_locks_user_for_share_before_token_insert` — `client` shares `db_session`'s connection (conftest override), so the capture works through the API. Verified it fails with `resend` reverted to `users.get`.
- `tests/test_rbac_migrations.py › test_admin_app_permissions_all_is_admin_at_head`
- Changed: `tests/test_email_lock_concurrency.py › test_control_plain_for_update_deadlocks` (see Out-of-plan)

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff `All checks passed!`; format `222 files already formatted`; mypy `Success: no issues found in 101 source files`; pytest `1713 passed, 1 warning in 102.54s` (warning: pre-existing `forkpty` DeprecationWarning in `test_cli_create_account.py`). Exit 0.
- First run (before the out-of-plan fix): `2 failed, 1711 passed` — `test_control_plain_for_update_deadlocks[reset|verify]`.
- alembic check: no migrations changed (n/a).
- Runbook (block extracted from docs/deploy.md step 6, 3-space indent removed):
  - `sh -n` ok, `dash -n` ok, `bash --posix -n` ok.
  - Dry-run with `curl` stub (logs args, writes a cookie line to the `-c` file, prints `{"account_id":"x","access_token":"t"}` for login/token, `sleep 5` on `/users`) and a `mktemp` wrapper recording created files; stdin never a tty, so every `stty` fails.

    | Shell | (a) stdin 2-line file, `kill -HUP` subshell during admin call | (b) `</dev/null` | (c) normal exit |
    |---|---|---|---|
    | `dash` | exit 130; 1 logout call (`--max-time 10`); 3 mktemp files, 0 left | exit 1; 0 logout calls; 0 files left | exit 0; 1 logout call; 0 files left |
    | `bash --posix` | exit 130; 1 logout call (`--max-time 10`); 3 mktemp files, 0 left | exit 1; 0 logout calls; 0 files left | exit 0; 1 logout call; 0 files left |

### Out-of-plan files
- `tests/test_email_lock_concurrency.py` — the plan expected it to pass unchanged, but the control test `test_control_plain_for_update_deadlocks` depends on resend inserting the token without holding the user row. With the planned `FOR SHARE` lock, the other request now waits on the user row, so even a plain `FOR UPDATE` cannot close the S1 cycle and the expected 500 never comes. Minimal fix (+3/−1): the control also patches `UserRepository.get_for_share` back to `UserRepository.get` (the pre-ADM-002e resend), and its docstring says why. The control still proves that `get_for_update` must stay `FOR NO KEY UPDATE` against an unlocked token inserter.

### Follow-ups (noticed, not done)
- Remove the unused `APP_DEBUG` setting and its `.env.example` key (from plan).
- `tests/test_email_lock_concurrency.py` module docstring: the non-control tests (`test_resend_vs_reset_no_deadlock`, `test_resend_vs_verify_no_deadlock`) still pass, but with the share lock the second request now waits on the user row instead of token X. The docstring's interleave describes the pre-ADM-002e order. Consider rewording it or adding a variant without the share lock.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: production + docs +59 / −17 (config 24/6, repository 12/0, email_verification 1/1, deploy.md 10/5, .env.example 9/5, README 3/0); tests +132 / −3.

## Attempt 2 — 2026-11-16 (REWORK)

Fixes only review.md Attempt 1 Issue 1 and security-review.md 🟡1, 💭2, 💭4.

### Files touched
| File | Required because |
|---|---|
| `tests/test_email_lock_concurrency.py` | Review Issue 1 / security 💭3: `test_resend_vs_reset_no_deadlock` and `test_resend_vs_verify_no_deadlock` are parametrized `share_lock` = `share-lock` (real resend) / `unlocked` (`get_for_share` patched to `UserRepository.get`, real `get_for_update`); both variants expect `[202, 204]` / `[202, 422]`. Helper `_maybe_unlock_resend`; the control uses it, so the `unlocked` positive tests and the control differ only in the `get_for_update` lock mode. Module and control docstrings reworded. |
| `tests/conftest.py` | Security 🟡1: `TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or "<org_admin_test default>"`, so an empty value never falls back to the dev DB. |
| `tests/test_config.py` | Security 🟡1: `test_conftest_empty_test_database_url_uses_test_db` (subprocess imports `tests.conftest` with `TEST_DATABASE_URL=""`, asserts the URL ends in `/org_admin_test`). Imports `os`, `subprocess`, `sys`, `Path`. Verified it fails against the attempt-1 conftest. |
| `app/modules/users/repository.py` | Security 💭2: `get_for_share` docstring states the lock-order constraint (resend: device row → user `FOR SHARE`; login: user `FOR SHARE` → device row; keep it `FOR SHARE`). Docstring only. |
| `docs/deploy.md` | Security 💭4: the EXIT trap deletes `$hdr` and `$body` before the logout curl and only `$jar` after it; the EXIT-trap bullet is updated to match. |

### Correction to Attempt 1 "Out-of-plan files"
The attempt-1 claim, "The control still proves that `get_for_update` must stay `FOR NO KEY UPDATE`", was inaccurate. The control changed two things (plain `FOR UPDATE` and no share lock), and the positive tests ran with the share lock, so a regression to `FOR UPDATE` failed no non-control test. Now the `unlocked` positive variants plus the control form a pair that differs only in the lock mode. The `share-lock` variants cover the real ADM-002e resend, where the other request waits on the user row. The attempt-1 follow-up on the docstring/variant is done.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0. ruff `All checks passed!`; format `222 files already formatted`; mypy `Success: no issues found in 101 source files`; pytest `1716 passed, 1 warning in 99.06s` (+2 `unlocked` variants, +1 conftest test; warning is the pre-existing `forkpty` DeprecationWarning).
- Regression probe: `get_for_update` temporarily changed to `.with_for_update()` (plain `FOR UPDATE`) → `tests/test_email_lock_concurrency.py`: `2 failed, 5 passed` (`test_resend_vs_reset_no_deadlock[unlocked]`, `test_resend_vs_verify_no_deadlock[unlocked]`). Restored `key_share=True` → `7 passed`.
- alembic check: n/a (no migrations).
- Runbook (step 6 block extracted, 3-space indent removed, 21 lines): `sh -n` ok, `dash -n` ok, `bash --posix -n` ok.
- Dry-run with a `curl` stub (logs args, writes a cookie line to the `-c` file, returns `{"account_id":"x","access_token":"t"}` for login/token, `sleep 5` on `/users`, records the number of temp files present at logout) and a `mktemp` wrapper; stdin never a tty:

  | Shell | `kill -HUP` subshell during admin call | `</dev/null` | normal exit |
  |---|---|---|---|
  | `dash` | exit 130; 1 logout (`--max-time 10`); 1 file (jar) present at logout; 3 created, 0 left | exit 1; 0 logout calls; 0 left | exit 0; 1 logout; 1 file at logout; 0 left |
  | `bash --posix` | exit 130; 1 logout (`--max-time 10`); 1 file (jar) present at logout; 3 created, 0 left | exit 1; 0 logout calls; 0 left | exit 0; 1 logout; 1 file at logout; 0 left |

### Out-of-plan files
- None beyond attempt 1 (`tests/conftest.py` and `tests/test_config.py` changes are the security-review fix).

### Follow-ups (noticed, not done)
- Unchanged from attempt 1 (remove unused `APP_DEBUG`); security 💭5 (per-signal exit codes) and the relative `.env.example` path in tests were not in this rework's list.

### Scope self-check
- Diff for this attempt: +57 / −16 across 5 files (production: docstring only, +2).
