# consult-identity-access-engineer — USR-02f

**Findings**
- `app/db/session.py` builds the engine when the module is imported, with `echo=settings.db_echo` and no `hide_parameters`. If the CLI reused it, the email could show up in echo output and in the text of a `DBAPIError`. The `get_session` commit/rollback pattern is still the right model to follow.
- `AssignmentService._grant_locked` assumes the caller already holds `lock_app`. It raises `ConflictError` both for an active duplicate and for an `IntegrityError` on insert (for example, the user was deleted at the same moment). It replaces expired rows in place through a savepoint. With `expires_at=None` and `actor=None`, the CLI never reaches the `expiry_not_allowed` branch.
- No repository method looks up a user by exact email; `users/repository.py` only has an `ilike` search. Sign-up normalizes with `str(EmailStr).strip().lower()`, and the column is CITEXT.
- The disable path (`UsersService.set_status`) does not take the `admin` lock (USR-02c FU1). A disable running at the same time as the CLI could therefore slip in after the CLI's status check.
- `app/core/text.py` already holds the display-text validators, so the mask helper fits there.
- The tests use a session bound to a connection, with savepoints and rollback per test.

**Recommendations**
1. **Session (AC1, AC4):** don't import `app.db.session`. In `app/cli.py`, build a separate engine: `create_async_engine(url, hide_parameters=True, echo=False, poolclass=NullPool)`. If `settings.db_echo` is true, exit 1 before connecting. Run everything in one `async with sessionmaker() as s, s.begin():` and call `engine.dispose()` in `finally`. Split the code into a testable `async def grant_super_admin(session, email) -> Outcome` and a thin `main(argv) -> int` that does `asyncio.run` and catches every exception. This lets tests inject `db_session`.
2. **Order inside the transaction (AC1):** `lock_app("admin")` (None means exit 1, "run migrations"), then the seeded `admin:super-admin` role by `(app.id, key)` (missing means exit 1), then the user via a new repository method that does `select(User).where(User.email == normalized).with_for_update(read=True)`. That `FOR SHARE` lock blocks a concurrent disable or delete until commit. Then check status (`disabled` means exit 3, and nothing is written), then `_grant_locked(app, role, user.id, None, None, None)`.
3. **Normalization (AC1):** validate with `TypeAdapter(EmailStr)`, then `strip().lower()`, the same as sign-up. An invalid address is a usage error (exit 64). It must not reach the DB.
4. **Idempotency (AC2):** while the CLI holds the lock and the user row, `ConflictError` can only mean "already held". Still, before calling `_grant_locked`, check with `find_assignment` and treat an active row as "already held", rather than reading meaning into the exception. Any other `AppError` exits 1. `created=False` with no exception means an expired row was replaced, which counts as "granted".
5. **Output and errors (AC3, AC4):** never print `str(exc)`, `repr`, or the argv value. Override `parser.error` to exit 64, and make sure argparse's own messages never echo the email. Don't configure SQLAlchemy logging. A test should force a DB error and assert the email appears in neither captured stream.
6. **Never (AC5, D16):** create users, prompt for or accept a password, or touch `email_verified_at`. Check no flag. Leave a `# R4` marker for `system:cli`.

**Risks**
- Pre-registration hijack remains possible until USR-04 (security consult finding 3). The runbook step and printing `created_at` and `email_verified: no` are the only mitigation.
- Calling a private method (`_grant_locked`) couples the CLI to the service's internals. Consider a public `grant_locked` wrapper. Ruff's `SLF001` is not enabled, so nothing will flag it.
- Settings validation errors (missing `TRUSTED_PROXY_CIDRS`) must also map to exit 1 with one generic line. Pydantic errors can be verbose even with `hide_input_in_errors`.
- The email in argv ends up in shell history and `ps` output (low risk).

**Open questions**
1. Once USR-04 ships, should the CLI refuse unverified accounts unless `--allow-unverified` is passed? The default for now is to grant and warn.
2. Should the email be read from stdin or a prompt instead of argv? The default is argv.
3. Is an exit code for "user disabled during the race" needed, or is reusing 3 under the `FOR SHARE` lock enough?
4. Should the CLI get a public `AssignmentService.grant_locked` instead of calling the private `_grant_locked`?
