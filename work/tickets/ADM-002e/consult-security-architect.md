# consult-security-architect — ADM-002e

**Findings**

1. **Gate (item 1).** I checked offline against asyncpg 0.32.0 and SQLAlchemy 2.1.8, with `PGHOST` and `PGSERVICE` set.
   - SQLAlchemy always passes the netloc host explicitly. So `PGHOST`, `PGSERVICE`/service files, `?service=` and `?dsn=postgresql://evil/` cannot redirect a `localhost` DSN; asyncpg only reads them when `host` is empty.
   - The only redirect is a lowercase `?host=` query key (it also works with a socket path).
   - `?hostaddr=` and `?Host=` reach `asyncpg.connect` as unknown kwargs, so they fail at connect rather than redirect.
   - A deny-list of `host`/`hostaddr` is still fragile: any future driver or dialect key would slip through it.
2. **Runbook trap (item 2).** The bug is confirmed. There are also three issues the ticket does not list:
   - `jar=$(mktemp) body=$(mktemp) hdr=$(mktemp)` is one assignment-only command. Its exit status is that of the last substitution only, so a failing first or second `mktemp` is masked even under `set -e`.
   - A signal that arrives during the EXIT trap re-enters `exit 130` and can abort cleanup.
   - The logout curl has no timeout. After an SSH drop it can hang while the 0600 bearer token file still exists.
3. **Resend lock (item 3).** The lock order is safe.
   - resend: user `FOR SHARE` → advisory lock (VERIFY, email) → `email_tokens`.
   - set_status: admin app → user `FOR NO KEY UPDATE` → tokens.
   - There is no cycle. The `KEY SHARE` that the token insert takes on the user row (foreign key) is compatible with the share lock.
   - Under READ COMMITTED, a locking SELECT that had to wait returns the newest row version, so the status re-check sees `disabled`.
4. **`.env.example` (item 6).** Stripping the values is safe: `env_ignore_empty=True` makes empty values fall back to the defaults. I checked this offline: `APP_ENV=` and `DATABASE_URL=` give `local` and the localhost DSN, and `app_env` stays out of `model_fields_set`.
   - The only change: `APP_DEBUG` becomes false after `cp .env.example .env`.
   - `tests/conftest.py` reads `TEST_DATABASE_URL` from `os.environ` with its own fallback, so tests are unaffected.

**Recommendations**

- **Item 1: use an allow-list.** Default to `local` only when there is exactly one host, it is in `_LOOPBACK_HOSTS`, and `self.database_url.query` is empty. Any query string at all requires an explicit `APP_ENV`.
  - Tests: `?host=prod-db.internal`, `?host=/var/run/postgresql`, `?hostaddr=1.2.3.4`, `?Host=x`, `?sslmode=disable` all raise. Plain loopback still passes. The error message contains neither the host nor the password.
- **Item 2: use this trap.** Dry-run showed cleanup and logout completing under dash and bash with stdin from `/dev/null`, for HUP, INT and TERM. `dash -n` passes.
  ```sh
  jar= body= hdr=
  trap 'rc=$?; trap "" HUP INT TERM; set +e; stty echo 2>/dev/null || true; [ -n "$hdr" ] && rm -f -- "$hdr"; if [ -n "$jar" ] && [ -s "$jar" ]; then curl -sS --max-time 10 -o /dev/null -b "$jar" ... 2>/dev/null || true; fi; for f in "$jar" "$body"; do [ -n "$f" ] && rm -f -- "$f"; done; exit $rc' EXIT
  trap 'exit 130' HUP INT TERM
  jar=$(mktemp); body=$(mktemp); hdr=$(mktemp)
  ```
  - The trap deletes the token file before logging out.
  - A Ctrl-C during `mktemp` can leak at most an empty 0600 file. No secret is written before the assignments complete, so this is cosmetic.
  - Acceptance: the reviewer runs `sh -n` and `dash -n`, then a dry-run with a `curl` stub on `PATH` under `dash` and `bash --posix`. Stdin is `</dev/null`, and `kill -HUP` goes to the subshell. Pass means the stub logged the logout and all three files are gone.
- **Item 3.** Add `UserRepository.get_for_share(id)` (`with_for_update(read=True)`, `populate_existing`). In `resend`, re-check status and verified state on the locked row. Acceptance: a deterministic test that captures SQL with `before_cursor_execute` and asserts `FOR SHARE` on `users` before the `email_tokens` insert. This avoids a two-connection test that the per-test rollback would complicate.
- **Item 4.** Tests run `alembic upgrade head`, so the seed rows exist. Assert that admin-app permissions count more than 0 and that none have `NOT is_admin`. Without the first assertion the test can pass on an empty table.

**Risks**

- The allow-list rejects any loopback DSN that carries a query string (for example `?ssl=disable`). Local developers would then have to set `APP_ENV`. This is intended (fail closed), but it is a behaviour change.
- "Loopback" is not the same as "dev". A container using `network_mode: host`, or a sidecar, reaches the runner's shared Postgres through `localhost`. The gate cannot detect that.
- On macOS `/bin/sh` is bash 3.2 in POSIX mode, so the dry-run should cover `bash --posix` too.

**Open questions**

- Does any deployment compose file use `network_mode: host` or a `localhost` DSN to reach the shared Postgres?
- Should the gate also refuse the default when `DATABASE_URL` is unset but `PGHOST` is exported? The URL default always sets `localhost`, so it is not a bypass today.
- Should an empty query string (`?`) count as "no query"? My recommendation: treat any `?` as non-default.
