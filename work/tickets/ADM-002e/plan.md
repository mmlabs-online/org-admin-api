Status: READY

# Plan — ADM-002e: Close the APP_ENV ?host= bypass and fix the admin runbook trap

## Spec summary
- §6 Non-functional: "No secrets in code or logs; config via env only." (R5: "Config from env only via `app/core/config.py`; keys listed in `.env.example` without values.")
- §6.1 Deployment: "Database: the shared mmlabs Postgres 17 (compose on the Mac runner), database `org_admin` … Runbook: `docs/deploy.md`." The APP_ENV gate (ADM-002d) exists so a deployment pointed at that DB never falls back to `local` behaviour.
- §5.8 / R11: "device-session, reset and verification tokens stored only as hashes … **Verified email** is required to join an app or get any app token." Item 3 keeps a disable from missing a freshly issued VERIFY token.
- §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`." Item 2 is the staff runbook for getting an admin token; it must always log out and remove the token/cookie files.
- Ticket scope items 1, 2, 3, 4, 6 (item 5 already done in solo 2026-11-16 — out of scope).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Without explicit `APP_ENV`, the `local` default applies only when the DSN has exactly one host, that host is loopback, and there is **no** query string and **no** fragment (any `?` or `#`, even empty, refuses). Refused DSNs include `?host=prod-db.internal`, `?host=/var/run/postgresql`, `?host=%2Ftmp`, `?ho%73t=evil`, `?HOST=evil`, `?hostaddr=10.0.0.5`, `?dsn=postgresql://evil/db`, `?service=prod`, `?sslmode=disable`, bare `?`, `#?host=evil`. | `tests/test_config.py › test_app_env_required_for_loopback_with_query` (parametrized) raises `ValidationError` matching `APP_ENV must be set` |
| 1a | Plain loopback DSN still defaults to `local`; explicit `APP_ENV` + query DSN is accepted. | `test_app_env_default_ok_for_loopback` (existing) passes; new `test_app_env_explicit_ok_with_query` |
| 1b | Error message contains no DSN, host, query value or password. | new `test_app_env_query_error_does_not_echo_dsn` |
| 1c | `PGHOST`/`PGSERVICE` in the environment do not change the result for a loopback DSN (regression, consult finding). | new `test_app_env_pghost_env_ignored` — passes with default `local`, and `Settings().database_url.hosts()` still `localhost` |
| 2 | Runbook block (docs/deploy.md step 6) passes `sh -n` and `dash -n`; under `dash` and `bash --posix`, with stdin `</dev/null` and a `curl` stub on `PATH`, `kill -HUP` to the subshell ends with: the stub logged a `POST …/auth/logout` call (when the jar is non-empty), all three temp files removed, exit status 130. A normal exit keeps its original status. | Reviewer extracts the fenced block to a file and runs `sh -n f`, `dash -n f`, then the dry-run (procedure in Approach §2). Recorded in impl-log by the implementer and repeated by the reviewer. |
| 3 | Resend/disable race closed: `resend` reads the user `FOR SHARE` and re-checks status/verified on the locked row before any `email_tokens` write. | new `tests/test_email_verification_api.py › test_resend_locks_user_for_share_before_token_insert` (SQL capture: index of `FROM users … FOR SHARE` < index of `INSERT INTO email_tokens`); existing `test_resend_disabled_user_401`, `test_resend_already_verified_409` still pass; lock-order note in Approach §3 |
| 4 | After `upgrade head`, admin-app permissions exist and every one has `is_admin = true`. | new `tests/test_rbac_migrations.py › test_admin_app_permissions_all_is_admin_at_head` (two assertions: count > 0, count of `NOT is_admin` = 0) |
| 6 | `.env.example` lists keys only (no values); defaults moved into comments; `cp .env.example .env` still gives a working local setup (empty → default via `env_ignore_empty=True`). | new `tests/test_config.py › test_env_example_keys_only` (every non-blank, non-comment line matches `^[A-Z0-9_]+=$`) and `test_env_example_copy_gives_local_defaults` (`Settings(_env_file=".env.example")` with `APP_ENV`/`DATABASE_URL` unset in os.environ → `app_env == "local"`, `app_env not in model_fields_set`, localhost DSN) |
| 7 | `make check` green. | Reviewer runs `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/config.py` | Rewrite `_app_env_explicit_gate` as an allow-list (one host, loopback, `query is None`, `fragment is None`, and the SQLAlchemy `make_url(str(url))` view agrees: empty query, loopback host). Update docstring. Same fixed error message. | Item 1 |
| `tests/test_config.py` | New tests listed below in the ADM-002d section; keep existing ones. | Item 1, 6 |
| `docs/deploy.md` | Step 6 block: replace the `jar=…`, `trap … EXIT`, `trap 'exit 130'` lines and the `stty` calls in the password line; update the bullet describing the EXIT trap (token file deleted first, logout timeout 10 s, signals ignored during cleanup). | Item 2 |
| `app/modules/users/repository.py` | Add `UserRepository.get_for_share(user_id)` (`select(User).where(User.id == user_id).with_for_update(read=True).execution_options(populate_existing=True)`), docstring mirrors `get_by_email_for_share`. | Item 3 |
| `app/modules/auth/email_verification.py` | `resend`: `self.users.get(...)` → `self.users.get_for_share(...)`; existing status / `email_verified_at` checks now run on the locked row (no other logic change). | Item 3 |
| `tests/test_email_verification_api.py` | New SQL-capture test. | Item 3 |
| `tests/test_rbac_migrations.py` | New head-state test. | Item 4 |
| `.env.example` | Strip values from `APP_ENV`, `APP_DEBUG`, `DATABASE_URL`, `TEST_DATABASE_URL`; put each default in a comment above the key (see Approach §6). | Item 6 |
| `README.md` | One-line note under Quick start: `.env.example` has no values (defaults apply); an older `.env` containing `APP_ENV=local` disables the APP_ENV gate — delete that line. | Item 6 (orchestrator decision: document the stale-`.env` risk) |

No other files. No ADR (no decision outliving the ticket beyond tightening an existing gate).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_config.py › test_app_env_required_for_loopback_with_query[...]` — parametrize over `localhost:5432/org_admin` + each suffix: `?host=prod-db.internal`, `?host=/var/run/postgresql`, `?host=%2Ftmp`, `?ho%73t=evil`, `?HOST=evil`, `?hostaddr=10.0.0.5`, `?dsn=postgresql://evil/db`, `?service=prod`, `?sslmode=disable`, `?`, `#?host=evil`, `#frag` | AC1 — refusal with `APP_ENV must be set` |
| `tests/test_config.py › test_app_env_explicit_ok_with_query[local, test]` — `?host=prod-db.internal` and `?sslmode=disable` with explicit `app_env` | AC1a |
| `tests/test_config.py › test_app_env_query_error_does_not_echo_dsn` — `DATABASE_URL=postgresql+asyncpg://u:DsnSecretMarker42@localhost:5432/x?host=dbhost.internal`; assert neither marker nor `dbhost.internal` nor `host=` value appear in `str(exc.value)` | AC1b, R5 |
| `tests/test_config.py › test_app_env_pghost_env_ignored` — `monkeypatch.setenv("PGHOST","evil")`, `setenv("PGSERVICE","prod")`, loopback DSN → `app_env == "local"` | AC1c |
| `tests/test_config.py › test_env_example_keys_only` | AC6 |
| `tests/test_config.py › test_env_example_copy_gives_local_defaults` | AC6 (copy still works) |
| `tests/test_email_verification_api.py › test_resend_locks_user_for_share_before_token_insert` — signed-in unverified user; listen `before_cursor_execute` on `db_session.bind.sync_connection` (pattern from `tests/test_cli_grant_super_admin.py › test_grant_locks_admin_app_then_user`); call `_resend`; assert 202 and `idx(FROM users … FOR SHARE) < idx(INSERT INTO email_tokens)` | AC3 |
| `tests/test_rbac_migrations.py › test_admin_app_permissions_all_is_admin_at_head` — async, `db_session`; `SELECT count(*) … WHERE a.key='admin'` > 0 and `… AND NOT p.is_admin` = 0 | AC4 |

Existing tests that must keep passing unchanged: all ADM-002d gate tests in `tests/test_config.py` (`test_app_env_socket_dsn_fails_closed` still raises — now from either validator), resend tests in `tests/test_email_verification_api.py`, `tests/test_email_lock_concurrency.py`.

## Approach
1. **Config gate (item 1, R5).** In `_app_env_explicit_gate`, after the `model_fields_set` short-circuit:
   ```python
   url = self.database_url
   hosts = [h.get("host") for h in url.hosts()]
   engine_url = make_url(str(url))  # what app/db/session.py, cli.py and migrations/env.py receive
   if (
       len(hosts) != 1
       or hosts[0] not in _LOOPBACK_HOSTS
       or url.query is not None        # any "?", even empty
       or url.fragment is not None     # pydantic puts "#?host=x" in the fragment, but make_url
                                       # parses it as a query (verified offline, see below)
       or engine_url.query
       or engine_url.host not in _LOOPBACK_HOSTS
   ):
       raise ValueError("APP_ENV must be set explicitly when DATABASE_URL is not localhost")
   ```
   - Import `make_url` from `sqlalchemy.engine`. Wrap `make_url` in `try/except Exception: raise ValueError(<same message>) from None` so a parse failure never echoes the URL (`hide_input_in_errors` already applies, but the message stays fixed).
   - Fixed message, no formatting of URL/host (R5). Docstring: allow-list; why the fragment and engine-view checks exist.
   - Multi-host (`localhost,db`) is already refused by `len(hosts) != 1` (was `any(...)`; existing test keeps passing).
2. **Runbook trap (item 2).** Merged block (both consults; orchestrator decision). Replace the current line 427–429 trio and the password line with, in this order, inside `( set -eu; umask 077; api=… `:
   ```sh
     jar= body= hdr=
     trap 'rc=$?; set +e; trap "" HUP INT TERM; stty echo 2>/dev/null || true; if [ -n "$hdr" ]; then rm -f -- "$hdr"; fi; if [ -n "$jar" ] && [ -s "$jar" ]; then curl -sS --max-time 10 -o /dev/null -b "$jar" -X POST "$api/auth/logout" -H "Origin: https://admin.mmlabs.online" -H "X-MM-App: admin" -H "Content-Type: application/json" -d "{\"all\":true}"; fi; for f in "$jar" "$body"; do if [ -n "$f" ]; then rm -f -- "$f"; fi; done; exit $rc' EXIT
     trap 'exit 130' HUP INT TERM
     jar=$(mktemp)
     body=$(mktemp)
     hdr=$(mktemp)
   ```
   and `printf 'password: '; stty -echo 2>/dev/null || true; IFS= read -r pw; stty echo 2>/dev/null || true; printf '\n'`.
   - `rc=$?` must be the first command (captures the original status; 130 after a signal). Use `if … fi` rather than `[ … ] && rm` so no command in the trap leaves a stray non-zero status that matters (`set +e` is on anyway).
   - Separate `mktemp` statements so `set -e` sees each failure (security consult finding 2).
   - Token header file deleted **before** the network call; logout bounded to 10 s.
   - Update the explanatory bullet: "The `EXIT` trap runs on any exit, Ctrl-C or SSH drop included (it ignores further signals while it runs): it restores echo if there is a tty, deletes the header file, logs out (`{"all": true}`, at most 10 s) if a session cookie exists, deletes the jar and body files, and keeps the exit status."
   - **Dry-run procedure** (implementer records output in impl-log; reviewer repeats): extract the fenced block to `$TMPDIR/rb.sh`; create `$TMPDIR/bin/curl` stub that appends its args to `$TMPDIR/curl.log`, writes a cookie line into the `-c` file when given, prints `{"account_id":"x","access_token":"t"}` for login/token, and for the admin call (`/users`) runs `sleep 30`; stdin is never a tty in these runs, so `stty` always fails, which is the condition under test. Runs: for `sh_ in dash "bash --posix"`: (a) stdin from a 2-line file, start in background, `kill -HUP <subshell pid>` while the stub sleeps → expect `curl.log` contains `auth/logout`, no files left from `mktemp` (record their names via a stub `mktemp` wrapper or `ls $TMPDIR` before/after), exit 130; (b) `</dev/null` → no logout call (jar empty), files removed, non-zero exit. Also `sh -n` and `dash -n` pass. If `python3` is unavailable to the stub run, stub it too.
3. **Resend lock (item 3).** Add `get_for_share` and use it in `resend`. Lock order after the change: user row `FOR SHARE` → `(VERIFY, email_hmac)` advisory xact lock → global `email_daily_cap` advisory lock → `email_tokens` INSERT (FK `KEY SHARE` on users, compatible). `set_status`: admin app → user `FOR NO KEY UPDATE` → device sessions → `email_tokens` (never takes the send advisory locks). `verify`: user `FOR NO KEY UPDATE` → tokens. Every path takes the user row before tokens, so there is no cycle. Under READ COMMITTED, a `FOR SHARE` that waited behind a disable returns the new row version, so the existing `status != ACTIVE` check refuses with 401 — the disable's `invalidate_all` can no longer run between resend's check and its token insert.
   - **Evidence choice: SQL-capture test, not a two-connection concurrency test.** Justification: deterministic (no timing), works inside the per-test rollback fixture used by `client`, follows the established pattern (`test_cli_grant_super_admin.py`, `test_rbac_locking.py`), and the correctness argument (lock order + READ COMMITTED re-read) is a property of Postgres, not of our code — the code-level property to pin is "share lock on users is taken before the token insert", which the capture asserts directly. `test_email_lock_concurrency.py` already covers the advisory-lock path with real connections.
   - The implementer verifies `client` uses `db_session`'s connection (`conftest.py` overrides `get_session` with `_override_session`); if statements are not captured there, call `EmailVerificationService(db_session, …).resend(...)` directly in the test instead and record why.
4. **Item 4 test.** Async test using `db_session` (session fixture already migrated to head). Plain SQL via `text()`. Module docstring of `tests/test_rbac_migrations.py` mentions command tests are plain `def`; add the async test without changing that note beyond one line ("plus one async head-state check").
5. *(Item 5 done — skipped.)*
6. **`.env.example` (item 6, R5).** Change only the four lines that carry values:
   ```
   # Default local. Must be set explicitly (local|test|staging|production) when DATABASE_URL
   # is not a plain loopback DSN (no query string).
   APP_ENV=
   # Default false.
   APP_DEBUG=
   APP_VERSION=
   # Default postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin
   DATABASE_URL=
   # Tests only (tests/conftest.py); default postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin_test
   TEST_DATABASE_URL=
   ```
   (Comments must not look like `KEY=value` lines so the keys-only test stays simple: the test skips lines starting with `#` and blank lines.) Behaviour change: `APP_DEBUG` becomes false after `cp` — nothing reads it (consult). README note per Files table.

Rules: R1 (repository holds the SQL lock; service only calls it), R5 (no URL in errors; keys-only `.env.example`), R7 (no `APP_DEBUG` removal, no other refactors), R8 (`make check`), R11 (verified-email/token invalidation integrity, never logging secrets in the runbook). No R2/R3/R4/R6/R9 impact (no schema, endpoint, or audit change).

## Specialist input used
- identity-access-engineer #1 (deny-list of `host/hostaddr/dsn/service/servicefile` via `make_url`): **rejected in favour of** security-architect's allow-list (orchestrator decision; fail-closed against future driver keys). Its refusal cases and the `PGHOST` regression test are **adopted**; its "`?port=` still passes" positive test is **rejected** (any query refuses under the allow-list).
- Both consults, `make_url` view of the URL: **adopted** as a second check — the planner verified offline (pydantic 2 / SQLAlchemy 2.1.8) that `localhost:5432/db#?host=evil` has `query=None` in pydantic but `make_url` yields `query={'host': 'evil'}`, so a query-only check would be bypassable via `#`. Hence `fragment is None` and the engine-view checks.
- security-architect #2 / identity #2 trap blocks: **merged** (orchestrator spec): `rc=$?`, `set +e`, ignore signals, `stty … || true`, token file first, logout only with non-empty jar, `--max-time 10`, separate `mktemp`. Dry-run acceptance incl. `bash --posix` (macOS `/bin/sh`) **adopted**.
- Both #3: `get_for_share` + re-check on the locked row **adopted**; evidence = security-architect's SQL-capture test (justified in Approach §3) over identity's concurrency test.
- Both #4: two-assertion test **adopted**.
- Both #6: keys only, defaults in comments, optional regex test **adopted**; `APP_DEBUG` removal → follow-up.
- security-architect open question `network_mode: host`: orchestrator verified the deployment compose uses no `network_mode: host` and no localhost DSN — no change.
- security-architect open question "refuse default when `PGHOST` exported": **rejected** (not reachable, per both consults; covered by regression test).

## Assumptions
- Refusing a loopback DSN with *any* query string or fragment (e.g. `?ssl=disable`) without explicit `APP_ENV` is intended (fail closed, owner default via orchestrator). CI (`localhost`, no query) and `docker-compose.yml` (explicit `APP_ENV`) are unaffected.
- Existing developer `.env` files with `APP_ENV=local` keep the gate disabled; documented in README and to be mentioned in the PR, not enforced in code.
- `tests/conftest.py` sets `APP_ENV`/`DATABASE_URL` in `os.environ`; the `.env.example` copy test must `monkeypatch.delenv` both (and any other key from the file that conftest sets) before loading `_env_file=".env.example"`.
- Line estimate: config ~20, test_config ~90, deploy.md ~15, repository ~13, email_verification ~2, resend test ~30, rbac test ~20, `.env.example` ~10, README ~3 → **~200 changed lines**, within one cycle.

Follow-ups (not in this diff, R7):
- Remove the unused `APP_DEBUG` setting and its `.env.example` key.

## Blocking questions
None

## Split proposal
None

## Data model / migration
None

## API contract
None (no endpoint change; `POST /api/v1/auth/verify-email/resend` keeps its contract — 202/401/409/429 unchanged).
