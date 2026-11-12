# consult-security-architect: USR-02f

**Findings**
1. **The engine is built when the module is imported, and it inherits `DB_ECHO`.** `app/db/session.py:14-20` creates the engine as soon as it is imported. It uses `echo=_settings.db_echo` and has no `hide_parameters`. If the CLI reuses it, any `DBAPIError` text includes the bound email, and `DB_ECHO=true` logs it as well. `get_settings()` also runs at import time. A config error would then print a traceback before the CLI's `try` block can catch it. The pydantic error itself hides values (`hide_input_in_errors`), but the traceback still escapes.
2. **`hide_parameters` does not cover everything.** asyncpg/Postgres `DETAIL` lines (for example `Key (email)=(…)`) are part of the server's message, not the bound parameters. Only "never print `str(exc)`" is safe.
3. **"Already held" is reported as an error.** `_grant_locked` raises `ConflictError` for an active row (`service.py:552-554`). The CLI must map that one case to exit 0 "already held" and must not treat it as a failure.
4. **The production compose cannot run the CLI today.** `apps/org-admin-api/compose.yml` on deployment `origin/main` has no `TRUSTED_PROXY_CIDRS` under `x-app.environment`. With `APP_ENV: production`, `Settings()` refuses to load (`config.py:66-71`). That breaks the CLI, `migrate` and the next API start alike.
5. **`run org-admin-api` attaches the container to the `tunnel` network.** The `migrate` service is on `db` only, so it is the smaller footprint.

**Recommendations**
- **AC4: one engine just for the CLI.** Build it inside the CLI from `get_settings().database_url` with `hide_parameters=True`, `echo=False` and `pool_size=1`. Exit 64 if `db_echo` is true. Import `app.db.*` and the services lazily inside `main()`, under a single catch-all. Silence the `sqlalchemy`/`asyncpg` loggers below `WARNING`. Set `sys.tracebacklimit = 0` as a backstop.
- **AC3: fixed messages only.** Each exit code prints one fixed line, never `str(exc)` or `repr(exc)`. A test forces a DB error (bad DSN or a monkeypatched repo raising `DBAPIError` with the email in its params) and asserts that neither the email nor the DSN password appears in `capsys`.
- **AC1: normalize the email exactly like sign-up.** Use `strip().lower()` (`auth/service.py:31`), ideally as a shared helper, so the lookup cannot miss and the operator's input is not echoed back.
- **AC6 runbook:** `docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env run --rm --no-deps migrate /app/.venv/bin/python -m app.cli grant-super-admin --email …`.
  - Use the `migrate` service: it is on the db network only.
  - Prerequisite: `TRUSTED_PROXY_CIDRS` is present in `x-app`. This is a deployment-repo PR, not part of this diff (R7 follow-up).
  - Order: sign up yourself, log in once, then run the CLI.
  - Check the `created_at` and `email_verified` the CLI prints against your own sign-up. If they don't match, revoke the grant and investigate.
  - After USR-04 ships: verify the email, then reset the password (which revokes sessions).
  - Never set `DB_ECHO`.
  - Shell history (zsh on the Mac): prefix the command with a space under `setopt HIST_IGNORE_SPACE`, or run `fc -p`. The email is also visible in `docker inspect`/`ps` while the container runs. `--rm` limits that. The email is PII, not a secret, so this mitigation is enough for v1.
- **R4:** add a `# R4` marker on the CLI grant. Once ADM-004 lands, write the audit entry with `actor=system:cli` in the same transaction. Print a single-line operator notice (no email) to stdout.

**Risks**
- **High (latent):** the pre-registration hijack until USR-04 lands. The runbook and the printed `created_at`/`verified` values only mitigate it.
- **Medium:** the missing `TRUSTED_PROXY_CIDRS` blocks the break-glass path and the next deploy.
- **Low:** the email remains in terminal scrollback and Docker event history on the host.

**Open questions**
1. After USR-04: should the CLI refuse unverified accounts unless `--allow-unverified` is passed?
2. Should the owner add `TRUSTED_PROXY_CIDRS` to the deployment compose before this PR merges?
3. Should the CLI also accept the email from stdin (`--email -`) to keep it out of history and argv? This would be optional and would leave the AC unchanged.
