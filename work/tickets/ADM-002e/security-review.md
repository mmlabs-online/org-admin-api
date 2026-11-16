## Attempt 1

## Security review: ADM-002e, attempt 1 (appsec-engineer)

Scope: `git diff origin/main...HEAD` (9937599). I worked read-only. I ran no `make check`, pytest, alembic or psql. I fuzzed DSNs offline with the repo venv, and dry-ran the runbook against a local mock HTTP server in the scratchpad.

### Findings

**🟡 1. An empty `TEST_DATABASE_URL=` silently points the test suite at the dev DB.** `tests/conftest.py:9-12` together with `.env.example:10`
- **Risk:** `.env.example` now holds `TEST_DATABASE_URL=` with no value. A developer may copy it to `.env` and load it into their shell (`set -a; . .env`, direnv, or an IDE env-file). Then `os.environ.get("TEST_DATABASE_URL", default)` returns `""` and sets `DATABASE_URL=""`. Settings ignores empty values (`env_ignore_empty=True`), so it falls back to the default `.../org_admin`, which is the **dev** database.
- **Impact:** the suite then commits and cleans up data there. `tests/test_rbac_migrations.py` also runs downgrade/upgrade against it, so dev data can be lost. Before this change the example carried an explicit `_test` URL.
- **Fix:** in conftest, use `TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or "postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin_test"`.

**💭 2. Lock order in resend is safe only while both user-row locks stay `FOR SHARE`.** `app/modules/auth/email_verification.py:132`, `app/modules/auth/service.py:168,197`
- **Order today:** resend takes the device row lock (from `DeviceDep` touch/rotate), then the user row `FOR SHARE`. Login takes the user row `FOR SHARE`, then the device row (`start_for_login`). The two orders are opposite.
- **Why it doesn't deadlock today:** both user locks are `FOR SHARE`. Postgres grants a share request that is compatible with the current share holders without queueing it behind a waiting `NO KEY UPDATE`. So the three-way case (login + resend on the same device + a concurrent disable or reset) has no cycle.
- **Other paths, all consistent:**
  - `set_status`: admin app → user → account_sessions/email_tokens. It never takes a device lock.
  - verify/reset: user `NO KEY UPDATE` → tokens.
  - forgot: user `FOR SHARE` → advisory locks → tokens.
  - resend: user `FOR SHARE` → per-email advisory → global advisory → tokens.
- **Risk:** if resend's lock is ever raised to `FOR UPDATE` or `NO KEY UPDATE`, a login and a resend on the same device can deadlock.
- **Fix:** state this lock order in the `get_for_share` / `resend` docstring.

**💭 3. The concurrency test change hides no production regression, but it narrows coverage.** `tests/test_email_lock_concurrency.py:1-6,196-210`
- **What changed:** resend now takes the user lock first, so in `test_resend_vs_reset_no_deadlock` and `test_resend_vs_verify_no_deadlock` the second request waits on the user row instead of token X. The S1 cycle (FK `KEY SHARE` against `get_for_update`) is now exercised only by the control test, and only after patching out the share lock.
- **Effect:** if `get_for_update` went back to plain `FOR UPDATE`, no non-control test would fail.
- **Why there is no live regression:** I found no production path left where an unlocked token inserter races `get_for_update`. Signup creates a new user; forgot and resend share-lock the user.
- **Fix:** add a variant that patches only `get_for_share → get`, keeps the real `get_for_update`, and asserts `[202, 204]` / `[202, 422]`. Also reword the module docstring. The implementer already listed this as a follow-up.

**💭 4. The runbook deletes the body file only after logout.** `docs/deploy.md:446`
- **Risk:** the body file can still hold the password if login failed before `: > "$body"`, and the trap removes it only after a logout that can take up to 10 s. A SIGKILL in that window leaves a 0600 temp file containing the password.
- **Fix:** delete `$body` together with `$hdr` before the logout curl; only the jar is needed for logout.

**💭 5. The runbook exits with 130 for every signal.** `docs/deploy.md:447`
- HUP and TERM also exit with 130, not 129/143. This is cosmetic; the trap keeps whatever status it is given. Optional fix: `trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM`.

### Attempts that did not break it
**(1) APP_ENV gate (`app/core/config.py:123-150`)**
- **Method:** for each of 40 DSNs, run `Settings` with no APP_ENV, then SQLAlchemy `create_connect_args`, then asyncpg `_parse_connect_dsn_and_args`, and record where asyncpg would actually connect.
- **Refused:**
  - any `?` (including an empty one) and any `#` (including `#?host=`)
  - a tab before `?`, a newline, and `?options=`
  - `localhost.`, `LOCALHOST`, `127.1`, `0x7f000001`, `2130706433`
  - `[::ffff:127.0.0.1]`, `[::1%25eth0]`
  - multi-host (`localhost,evil`, `[::1]:5432,[::1]:5433`)
  - percent-encoded hosts (`local%68ost`, `localhost%2Cevil`, `localhost%2Fevil`, `:5432%3Fhost=`)
  - socket-only and empty-host forms, and `localhost\@evil`
- **Passed, and all connect to loopback only:**
  - `u:p@evil@localhost`, `u@evil:p@localhost` (the `@` is re-encoded into the userinfo)
  - `db%3Fhost=evil`, `db%23host=evil`, `db\@evil` (these become database names only)
  - `[0:0:0:0:0:0:0:1]`, which normalizes to `[::1]`
- **Parsers:** pydantic and `make_url` agreed on the host in every case. The `#?` case, where they disagree on query vs fragment, is covered by the fragment check.
- **Environment variables:** SQLAlchemy always passes `host` explicitly, so `PGHOST` and `PGSERVICE` (read only when no host is given) have no effect. asyncpg does not support `PGHOSTADDR`. `PGPORT` only changes the port on loopback.
- **Error leakage:** none. The message is fixed, `hide_input_in_errors` is on, and the `make_url` failure is raised `from None`. A test DSN with a password marker did not appear in the error.
- **`.env` handling:** `APP_ENV=` (empty) is ignored and does not count as set, so a copied `.env.example` still gets the gate. The README warns about a stale `APP_ENV=local` in an old `.env`.

**(2) Runbook (`docs/deploy.md:441-462`)**
- **Syntax:** `sh -n`, `dash -n` and `bash --posix -n` all pass.
- **Dry-run results (dash, stdin never a tty, mock server):**

  | Case | Exit status | Logout | Temp files left |
  |---|---|---|---|
  | Normal run | 0 | Sent with the cookie | 0 |
  | Failing admin call | 22 | Sent | 0 |
  | `</dev/null` | 1 | None (no cookie) | 0 |
  | HUP, TERM or INT to the process group during the admin call | 130 | Sent | 0 |

- **Passwords and the token:** a password with `"` and `\` was JSON-encoded correctly. The token never appears in argv. The header file is removed before logout.
- **Trap behaviour:**
  - The signal-ignore set inside the trap is inherited by the logout curl.
  - `set -u` is safe because `api`, `jar`, `body` and `hdr` are set before the trap.
  - The worst case for a signal during `$(mktemp)` is one empty temp file left behind.

**(3) Resend share lock**
- It closes the disable/`invalidate_all` race: under READ COMMITTED with `populate_existing`, a resend that waits re-reads the row as disabled and answers 401.
- An FK `KEY SHARE` on the token insert is compatible with the `FOR SHARE` lock resend already holds.

**(4) `.env.example`**
- Every non-comment line is `KEY=` with no value (a test enforces this), and none of the comments contains a secret.
- The defaults the comments document match `config.py`. The `APP_DEBUG` default is now false, which is the safer default.

### Follow-ups (out of scope)
- Remove the unused `APP_DEBUG` setting.
- `tests/test_config.py`: the `.env.example` tests open the file by a relative path, so they only work from the repo root. Anchor the path to `Path(__file__).parent.parent`.

**Verdict:** PASS with one 🟡: change conftest to fall back to the test DB when `TEST_DATABASE_URL` is empty. I found no way to bypass the APP_ENV gate, no secret leakage, and no deadlock in the current lock orders.
