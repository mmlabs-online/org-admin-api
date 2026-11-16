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
