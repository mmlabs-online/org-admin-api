## Attempt 1

# Security review: ADM-002d, attempt 1 (appsec-engineer)

I only read code. I ran no tests, alembic or psql. I checked a few library behaviours offline: PyJWT 2.15.1 `_validate_aud` from source, DSN parsing with pydantic and the SQLAlchemy asyncpg dialect's `create_connect_args` (no database connection), and trap behaviour in a throwaway dash snippet in the scratchpad.

## Findings

🟡 **1. You can bypass the APP_ENV gate with a `?host=` query parameter.** `app/core/config.py:129`
- **Risk:** the gate only looks at pydantic's `database_url.hosts()`, which reads the netloc. SQLAlchemy's asyncpg dialect then does `opts.update(url.query)` and `_split_multihost_from_url`, so a `host` in the query string overrides the netloc host. I confirmed this offline:
  - `postgresql+asyncpg://u:p@localhost/db?host=prod-db.internal`: pydantic sees `['localhost']` and passes the gate, but SQLAlchemy connects to `prod-db.internal`.
  - `...@localhost/db?host=/var/run/postgresql` also passes the gate and connects over a Unix socket.
  - The result is a non-loopback (or socket) database running with the implicit `local` defaults. That is exactly what the gate exists to stop.
- **Cases that already fail closed (fine):** userinfo tricks (`localhost@evil`), comma multi-host (`localhost,evil`), `localhost.evil`, `localhost.`, `LOCALHOST`, IPv4-mapped IPv6 (`[::ffff:7f00:1]`) and a hostless socket DSN (`@/db?host=/tmp` is a pydantic error).
- **Error leakage:** none. The settings use `hide_input_in_errors=True` and the message is a fixed string.
- **Fix:** treat any `host`, `hostaddr` or `port` query key as non-loopback. For example:
  ```python
  q = self.database_url.query or ""
  if any(k in {"host", "hostaddr"} for k, _ in parse_qsl(q)): raise ValueError(...)
  ```
  Or resolve the hosts the same way the engine does (`make_url(...)`, then `PGDialect_asyncpg()._split_multihost_from_url`). Add a test case with `?host=` to `tests/test_config.py`.

🟡 **2. The runbook's EXIT trap stops part-way when `stty` fails, so it skips logout and file cleanup.** `docs/deploy.md:428`
- **Risk:** the trap runs under `set -eu`. Its first command is `stty echo 2>/dev/null;` with no `|| true`. When there is no tty, `stty` fails and `set -e` ends the trap right there. I checked this in dash and bash: with stdin not a tty, nothing after `stty` runs.
- This hits the SIGHUP path in particular, for example an SSH drop after login. `trap 'exit 130' HUP` then runs the EXIT trap with no terminal. The result:
  - no `logout {"all":true}` is sent, so the device session stays alive;
  - `$jar` (device cookie) and `$hdr` (a live bearer token, valid up to 15 min) are left in `$TMPDIR`. Both are mode 0600, but they persist on the runner host.
- The snippet passes `sh -n`. Everything else checks out:
  - the password and token never reach argv, history or stdout;
  - python3 builds the JSON safely;
  - the body file is emptied right after login;
  - the token rotation is written back to the jar (`-b -c`);
  - the logout body matches `LogoutRequest`;
  - without pipefail, a `curl -f` failure still trips `set -e` through python3's non-zero exit.
- **Fix:** start the trap with `set +e;` and use `stty echo 2>/dev/null || true;`. Optionally create the temp files after setting the trap (or initialise `jar= body= hdr=` first) so that a Ctrl-C during `mktemp` does not leave files behind.

💭 **3. A verification token can be issued after a user is disabled, so it outlives `invalidate_all`.** `app/modules/auth/email_verification.py:131`
- `resend` reads the user with `users.get` and takes no lock. Signup and forgot do lock: forgot uses `get_by_email_for_share`, reset and verify use `get_for_update`, so they serialise correctly with `set_status`'s `FOR UPDATE`.
- A resend that runs at the same moment as a disable can therefore insert a VERIFY token that the disable's `invalidate_all` (`app/modules/users/service.py:83`) never sees.
- **Impact is low:** redeeming it still requires `status=active`, and it can only verify the user's own email after a later re-enable. RESET tokens are serialised correctly.
- **Fix (follow-up):** use a share lock in `resend`.

💭 **4. Only the service and a one-off backfill keep admin-app permissions `is_admin=true`.**
- All API and CLI paths are covered:
  - create with the field omitted or null is forced to `true`, and `false` gets a 422 (`service.py:714`);
  - PATCH `false` gets a 422 (`service.py:749`), and PATCH null was already refused by the schema;
  - `register` refuses the `admin` key;
  - the seed migrations insert `true`.
- No database constraint or trigger protects this, so a future data migration could reintroduce `false`.
- **Follow-up:** a test that asserts every `admin`-app permission row is `is_admin` after `upgrade head`, or a trigger.

💭 **5. The new 422 is raised before `require_super`.** `app/modules/rbac/service.py:715,749`
- A non-super caller holding `admin:roles:manage` who sends `is_admin:false` gets a 422 instead of a 403, and no denied-audit row is written. The wildcard check already behaves the same way.
- No data is disclosed. Optionally run `require_super` first.

💭 **6. The migration's `downgrade()` is a no-op.** `migrations/versions/20261115_a7c3e5f19b42_admin_permissions_is_admin.py`
- This is a deliberate R2 exception and the docstring justifies it. The reviewer or owner should acknowledge it.

## Verified, no issue

- **(1) `strict_aud`:**
  - The only verifier is `app/modules/admin_auth/deps.py:60`, and it passes the string constant `audience=ADMIN_APP_KEY`.
  - In PyJWT 2.15.1, strict mode rejects a non-string `audience`, a non-string `aud` (list, int or dict) and any mismatch with `InvalidAudienceError`, which maps to reason `audience`.
  - An absent or empty `aud` raises `MissingRequiredClaimError`, which maps to reason `claims`.
  - I found no remaining way for a list or non-string `aud` to pass.
- **(3) `--clear` on a drifted row:** it still locks the admin row and refuses a missing one. It writes only `app.origins = []` plus the audit row; the CLI cannot send an empty `--origin`, and `--clear` cannot be combined with `--origin`. No other column is written, and a non-empty set still runs the full drift check. Clearing an already empty list is `UNCHANGED` and writes nothing.
- **(4) Mail-link refusal:**
  - The only routes that mail a link are signup (through `get_signup_hook` → `get_link_origin`), forgot and resend. All three now depend on `MailLinkAppContextDep`. No other route builds a link.
  - The 403 is the same `OriginNotAllowedError("Origin is not allowed")` that an unregistered app gets, and it is raised only after the same database lookup and origin check, so neither the body nor the timing is an oracle.
  - It runs before Turnstile (called inside the service), every write and the email send. The decorator `rate_limit(...)` runs before it, exactly as it does for an unregistered app, so this is consistent.
  - CORS echoes the registered admin origin, but that was already the case and origins are public.
- **(7) Admin `org_mode` pin:** any PATCH that sends `org_mode` with a value other than `none`, null included, gets a 409.
- **(8) Request id:** it is always a UUID4. A grep finds no other consumer of inbound `x-request-id` or forwarded-style headers. `client_ip` still trusts `CF-Connecting-IP` only from trusted peers, as designed.
- **(5) Docker image:** `.env` is in `.dockerignore`, so no baked-in `APP_ENV` can defeat the gate. Alembic builds `Settings` when it loads (unless `-x url=` is given explicitly).

## Follow-ups (out of scope)

- Take a share lock in `resend` (finding 3).
- Add a check or trigger that keeps admin-app permissions `is_admin` (finding 4).

**Verdict: PASS with 2 should-fix items (🟡1 the APP_ENV gate bypass through `?host=`, 🟡2 the runbook trap aborting under `set -e`); no blockers.**
