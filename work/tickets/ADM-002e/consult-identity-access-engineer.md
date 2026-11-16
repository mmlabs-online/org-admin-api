# consult-identity-access-engineer — ADM-002e

## Findings

1. **Item 1 (verified offline: pydantic 2.14.3, SQLAlchemy 2.1.8, asyncpg 0.32.0, no DB connection).** `PGDialect_asyncpg.create_connect_args` runs `opts.update(url.query)`, so these all pass the gate today but connect elsewhere:
   - `?host=prod-db.internal` connects to the remote host.
   - `?host=/var/run/postgresql` and `?host=%2Ftmp` connect over a socket.
   - `?ho%73t=evil` also connects to `evil`, because the key is percent-decoded. A raw substring check on the query string would miss it.

   Inert today, but should still be blocked:
   - `?hostaddr=` and `?HOST=` reach `asyncpg.connect` as unknown kwargs, so the connect fails.
   - `?dsn=` and `?service=` are overridden by the netloc `host` kwarg.
   - `?host=` with an empty value is dropped by `make_url`.

   **`PGHOST`, `PGSERVICE` and the service-file host cannot be reached.** Pydantic rejects hostless DSNs (`@/db` and `@:5432/db` fail validation). SQLAlchemy therefore always passes `host=`, and asyncpg reads `PGHOST` or the service-file host only when `host` is falsy. With `PGHOST=evil PGSERVICE=prod`, a localhost DSN still resolved to `('localhost', 5432)`.
2. **Item 2.** I confirmed the old trap aborts at `stty` when stdin is not a tty: no logout is sent and the temp files stay behind. I dry-ran the corrected block below in dash and bash with stdin not a tty, a stubbed curl and a real SIGHUP sent to the subshell. Both shells sent the logout, removed all 3 files and exited 130. The block passes `dash -n` and `sh -n`.
3. **Item 3.** `resend` uses an unlocked `users.get`. `set_status` takes the `admin` app row, then the user row `FOR NO KEY UPDATE`, then the device-session rows and `email_tokens`. It never takes the send advisory locks.
4. **Item 6.** `.env.example` has values for `APP_ENV=local`, `APP_DEBUG=true`, `DATABASE_URL` and `TEST_DATABASE_URL`. `APP_ENV=local` also matters for security: a `.env` copied from it puts `app_env` in `model_fields_set`. The gate is then skipped for good, even after `DATABASE_URL` is pointed at a remote host. `APP_DEBUG` is declared in config but nothing reads it.

## Recommendations

**1. Config gate (AC1, R5).** Parse the query with the same parser the engine uses, and reject any key that can redirect the connection. Do not reject `port`, since a netloc port is already allowed and a port does not change the host. Inside `_app_env_explicit_gate`:
```python
from sqlalchemy.engine import make_url
_DSN_REDIRECT_KEYS = frozenset({"host", "hostaddr", "dsn", "service", "servicefile"})
query = make_url(str(self.database_url)).query   # same str() the engine receives; keys decoded
if any(k.lower() in _DSN_REDIRECT_KEYS for k in query): raise ValueError(<same fixed message>)
```
   - Prefer this over resolving through the dialect, which relies on private asyncpg internals.
   - It also refuses `?host=localhost` and `?host=/tmp` socket DSNs. Those need an explicit `APP_ENV`, which is intended and fails closed.
   - Tests to add to `tests/test_config.py`, all expecting a refusal: `?host=prod-db.internal`, `?host=/var/run/postgresql`, `?host=%2Ftmp`, `?ho%73t=evil`, `?HOST=evil`, `?hostaddr=10.0.0.5`, `?dsn=postgresql://evil/db`, `?service=prod`.
   - Positive test: `localhost:5432/db?port=6543` still passes.
   - Optional regression test: a localhost DSN plus `monkeypatch.setenv("PGHOST", "evil")` still passes. That env var has no effect, as shown above.

**2. Runbook trap (AC2).** Replace the `jar=…`, `trap … EXIT` and `trap 'exit 130'` lines with these, in this order:
```sh
  jar= body= hdr=
  trap 'set +e; trap "" HUP INT TERM; stty echo 2>/dev/null || true; if [ -s "$jar" ]; then curl -sS -o /dev/null -b "$jar" -X POST "$api/auth/logout" -H "Origin: https://admin.mmlabs.online" -H "X-MM-App: admin" -H "Content-Type: application/json" -d "{\"all\":true}"; fi; rm -f -- ${jar:+"$jar"} ${body:+"$body"} ${hdr:+"$hdr"}' EXIT
  trap 'exit 130' HUP INT TERM
  jar=$(mktemp); body=$(mktemp); hdr=$(mktemp)
```
   - `trap "" HUP INT TERM` stops a second Ctrl-C or HUP from cutting the cleanup short.
   - The `|| true` after the logout curl is not needed once the trap starts with `set +e`.
   - Also change the password line to `stty -echo 2>/dev/null || true` and `stty echo 2>/dev/null || true`.

**3. Resend lock (AC3).**
   - Add `UserRepository.get_for_share(user_id)`, using `with_for_update(read=True)` and `populate_existing`.
   - Use it in `resend` in place of `users.get`, before `prepare`.
   - Resulting lock order: user row `FOR SHARE`, then the `(VERIFY, hmac)` advisory lock, then the global advisory lock, then `email_tokens` writes (the INSERT's FK `KEY SHARE` is compatible).
   - This matches `verify` (user row before tokens) and `set_status` (user row before `email_tokens`), so no cycle is possible.
   - Forgot takes the advisory lock before the user row, but its key is `RESET` (a different advisory key) and share locks do not conflict with each other.
   - Evidence for the reviewer: a concurrency test in the style of `test_email_lock_concurrency.py`. Resend holds its transaction open, a disable blocks behind it, and after both commit the VERIFY token has `used_at` set.

**4. Item 4 test.** Add to `tests/test_rbac_migrations.py`, using `db_session` (the session fixture already runs `base` to `head`). Assert both:
   - `SELECT count(*) FROM permissions p JOIN apps a ON a.id = p.app_id WHERE a.key = 'admin' AND NOT p.is_admin` returns 0;
   - the count of admin-app permissions is greater than 0, so the first check cannot pass vacuously.

**6. `.env.example`.** Leave every key empty and move each default into a comment. For example:
   - `# Default local. Must be set explicitly (local|test|staging|production) when DATABASE_URL is not loopback.`
   - `# Default postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin`

   This works because `env_ignore_empty=True` makes an empty value fall back to the default, so `cp .env.example .env` still works for local dev. Optionally add a test that every non-comment line matches `^[A-Z0-9_]+=$`.

## Risks
- `?host=` and socket DSNs that were used deliberately now need `APP_ENV`. CI uses netloc localhost and compose already sets `APP_ENV`, so neither is affected.
- Developers who already copied `APP_ENV=local` into `.env` stay ungated until they edit it. Mention this in the PR.

## Open questions
- Remove the unused `APP_DEBUG` setting? That is a follow-up (R7).
