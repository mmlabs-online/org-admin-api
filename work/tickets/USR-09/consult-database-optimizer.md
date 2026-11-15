# Consult — database-optimizer — USR-09 (2026-11-15)

USR-09 database consult (read-only, no files edited)

**Short answer:** no new migration is needed. The duplicate-email check is already race-safe through the existing unique constraint on the `CITEXT` email column. The main design trap: the CLI session scope commits whenever the block exits without an exception. So every refusal after the user insert must be raised as an exception. Returning a result enum, the way `bootstrap_super_admin` does, would commit the new user. No audit table exists yet, and there are no triggers to respect.

## Findings

1. **No migration needed.**
   - `users.email` is `CITEXT NOT NULL` with `uq_users_email` (migrations/versions/20261111_7b847bdc5230_create_users.py:24,48; app/modules/users/models.py:23).
   - The verified flag is `email_verified_at timestamptz NULL`, not a boolean (create_users.py:31; models.py:30). "Verified" everywhere means `email_verified_at IS NOT NULL`: app/modules/membership/service.py:66, app/modules/tokens/service.py:71, app/modules/admin_auth/service.py:30, app/modules/auth/router.py:103.
   - The only check constraint on `users` is `ck_users_status_valid` (create_users.py:46). `status` defaults to `active`.
   - No migration has a trigger: a grep for TRIGGER/trigger across migrations/versions/ found none.
   - `role_assignments` has `uq_role_assignments_user_role_org` with NULLS NOT DISTINCT, so `org_id=NULL` duplicates are blocked. It also has FK `user_id → users ON DELETE CASCADE` and `granted_by → users ON DELETE SET NULL` (migrations/versions/20261111_b638a7c84c31_create_rbac.py:150-173).
   - `apps.default_role_id` has a composite FK to a role of the same app (create_rbac.py:179-184).

2. **The existing-email race is already handled.**
   - Because the email is `CITEXT`, the unique index compares case-insensitively.
   - If two inserts of the same email run concurrently, the second waits on the index until the first commits, then gets a unique violation. No index on a normalized expression is needed.
   - `UserRepository.add` inserts inside a SAVEPOINT (`begin_nested`) and then refreshes the row (app/modules/users/repository.py:75-81). An `IntegrityError` therefore leaves the outer transaction usable.
   - Sign-up already maps that `IntegrityError` to "email taken" (app/modules/auth/service.py:64-68).
   - Email normalization: sign-up uses `normalize_email`, which is `strip().lower()` (auth/service.py:36-37). The CLI's `_parse_email` does the same: pydantic `EmailStr`, then `strip().lower()` (app/cli.py:122-134). Reuse it for `create-account` (generalize the parser).

3. **Transaction semantics of the CLI scope.**
   - `_cli_session_scope` wraps the block in `session.begin()` (app/cli.py:150-164). It commits on a clean exit and rolls back only if an exception escapes.
   - `bootstrap_super_admin` returns outcome enums and lets the scope commit (app/modules/rbac/service.py:819-850). That is safe there only because it writes nothing on its refusal paths.
   - For USR-09, an "unknown/disabled/admin app" outcome returned after the user insert would commit the user.
   - `register-app` already uses the correct pattern: raise inside the `async with` and catch outside it (`_Rollback`, `AppError`; cli.py:341-357).

4. **Join logic to mirror (USR-03b).** `JoinService.join` in app/modules/membership/service.py:45-98 does, in order:
   - Refuses the `admin` app key before any read (:50).
   - Takes `lock_app(key)` first (`FOR NO KEY UPDATE`, app/modules/rbac/repository.py:54-66). The lock order is app → user rows (ADR-0003 §10).
   - Treats an inactive app as not found (:54).
   - Refuses `closed` and `approval` join policies (:73-76).
   - Refuses a NULL or missing default role (:78-84), and a default role holding any `is_admin` permission (:85-87).
   - Then calls `AssignmentService._grant_locked(app, role, user.id, None, None, None)` (:89).
   - `_grant_locked` (rbac/service.py:852-908) inserts in a savepoint through `add_assignment` (rbac/repository.py:402-407). It raises `ConflictError` on a unique or FK race, which cannot happen for a user created in the same transaction.
   - `JoinService` needs a `ResolvedDevice`, an account session and a calling app, so it cannot be called directly from the CLI.

5. **Audit (ADM-004) is not available yet.**
   - ADM-004 is `BACKLOG` (work/backlog.md:33; work/tickets/ADM-004/ticket.md:5). No audit table, model or migration exists. app/db/models.py does not register one.
   - `app/modules/audit/recorder.py` holds only a placeholder: the `AuditRecorder` Protocol plus `LogAuditRecorder` (:10-25).
   - The Protocol signature requires `actor_user_id: UUID` and `app_id: UUID` (:11-15). An actor `cli` cannot be expressed, and neither can a create without `--join` (no `app_id`).
   - The existing CLI precedent is a comment only: `# R4: ADM-004 audit entry (actor system:cli) in the same transaction.` (rbac/service.py:842).

6. **Side effects that create-account must skip.**
   - Sign-up runs `VerificationEmailHook`, which issues an `email_tokens` row and writes to `email_send_log` (app/modules/auth/hooks.py:25-57; auth/service.py:70).
   - For a pre-verified account, the CLI should not call the hook or `SignupService` at all.
   - The CLI engine sets `hide_parameters=True` and refuses to run with `DB_ECHO` (cli.py:137-147, 156-157). The Argon2 hash in the INSERT parameters therefore never shows up in DB error text.

## Recommendations

1. **No migration.** Keep `email_verified_at` and set it at insert from the DB clock: `User(email=..., password_hash=..., email_verified_at=func.now())`. This is consistent with `mark_email_verified` (users/repository.py:35-39). `repo.add` already refreshes, so the value is loaded afterwards. Do not add a boolean column.

2. **Order of work:**
   - Validate argv and the TTY.
   - Read the password twice and run `PasswordPolicy.check` (app/modules/auth/passwords.py:37).
   - Run `PasswordHasher(settings).hash(pw)` (passwords.py:53-72) **before** opening the DB scope. This way the app row lock is not held during Argon2.
   - Then, in one `_cli_session_scope`:
     - a. If `--join` is given: refuse `admin` before any DB read. Then `lock_app(key)`; refuse if missing or not `active`. Then validate the join policy, the default role and `role_has_admin_permission`, all **before** inserting the user. Each refusal is an exception raised inside the scope.
     - b. `UserRepository.add(User(...))`. Catch `IntegrityError` (a savepoint rollback), then raise an `_EmailTaken`-style exception inside the scope so the whole transaction rolls back. Map it to exit 1 with a fixed message. Never `str(exc)`: the DETAIL line contains the email.
     - c. If joining: `_grant_locked(app, role, user.id, None, None, None)`, the same call `JoinService` makes. `granted_by=NULL` means a system grant.
   - Print output only after the scope has committed, like `_print_result` (cli.py:359).

3. **Put the logic in a service, not cli.py (R1).** Use a small `AccountBootstrapService.create_account(email, password_hash, join_key)`, or an `AssignmentService` method next to `bootstrap_super_admin`. It should raise a refusal exception that carries a reason; the CLI maps reasons to fixed messages and exit codes, like `_REASONS`. Do not return enum outcomes after a write.
   - Better still: extract `JoinService`'s app and default-role checks (membership/service.py:50-87) into a shared helper, for example `resolve_joinable_default_role(app_key) -> (app, role)` under the lock. HTTP join and CLI join then cannot drift apart.

4. **Race safety:** rely on the unique constraint and catch the exception. A pre-check `SELECT` can make the common case fail fast, but it is not the guarantee and must not replace the catch.
   - `INSERT ... ON CONFLICT (email) DO NOTHING RETURNING id` (`pg_insert(User)...on_conflict_do_nothing(index_elements=["email"])`, as the repo already does for role_permissions at rbac/repository.py:241) is an equivalent alternative. It avoids the savepoint and the exception.
   - Either is fine. The savepoint path reuses the existing `repo.add` and matches sign-up.

5. **Audit:** do not build a table here (R7). Leave a `# R4: ADM-004 audit entry (actor cli) in the same transaction` marker at the user insert and at the grant, as at rbac/service.py:842. Optionally emit an ids-only log line. Add a follow-up to ADM-004: extend the `AuditRecorder` contract with a system/CLI actor and an optional `app_id`, so `user.create` (no app) and `app.join` (actor cli) can be recorded.

6. **Tests** (DB-level):
   - A duplicate email in any case (`A@x` vs `a@x`) gives exit 1 and leaves `password_hash`, `email_verified_at` and `updated_at` of the existing row unchanged.
   - `--join` with an unknown, disabled or `admin` app, or with a `closed` app, a missing default role or an admin default role, leaves zero rows in `users` and zero in `role_assignments`.
   - No `email_tokens` or `email_send_log` rows are created.
   - `email_verified_at` is not NULL and comes from the DB clock.
   - Through the `session_scope` injection, the join and the user insert are committed together.

## Risks

- **High:** returning instead of raising on a post-insert refusal commits an orphan verified user with no membership. Ordering the app checks before the insert lowers the risk, but `_grant_locked` can still raise `AppError`s, which must propagate out of the scope.
- **Medium:** holding `lock_app` during Argon2 hashing would block every join and RBAC write for that app for the hash time. Hash before the transaction.
- **Medium:** HTTP join and CLI join drift apart if the default-role and admin-permission checks are copied instead of shared (recommendation 3).
- **Low:** lock order. If the CLI takes app → user (insert), as join does, there is no deadlock risk. Never lock or select-for-update a user row before `lock_app`.
- **Low:** a leak through the DETAIL line of `IntegrityError` or `str(exc)`. Keep the fixed-message rule (cli.py:232-235).
- **Low:** the 128-character pydantic `EmailStr` limit does not apply. `CITEXT` has no length limit, and the parse uses the same validator as sign-up.

## Open questions (for the planner or owner)

1. Should `--join` on an app with join policy `closed` or `approval` be refused (the same as the HTTP join, which I recommend), or allowed because the operator vouches? The ticket names only unknown, disabled and `admin` apps.
2. Exit code for an existing email: the ticket says exit 1. The CLI has `EXIT_UNKNOWN_EMAIL=2` and `EXIT_DISABLED=3` (cli.py:94-98). Should "email taken" keep 1, or get its own code? A disabled app maps to 3 in `register-app` (cli.py:252), but USR-09 says 1.
3. Should `display_name` stay NULL? It has no flag in scope.
4. Audit: is a log-only placeholder plus an ADM-004 follow-up acceptable, given that the `AuditRecorder` Protocol cannot represent actor `cli` or a create without an app?
