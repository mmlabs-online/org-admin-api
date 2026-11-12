# consult-security-architect — USR-02c

**Findings**
1. **The HTTP API is safe only because of the flag gate.** With `ADMIN_RBAC_API_ENABLED` on, anyone who can reach the API can POST `admin:super-admin` to themselves. That is a critical privilege escalation. It is acceptable for now because `_admin_apis_gate` (`app/core/config.py:72-82`) refuses the flag in staging and production and requires `APP_ENV` to be set explicitly. Nothing in this ticket may weaken that gate.
2. **Lockout is an availability risk, not a security one.** The CLI is the break-glass path. Still, the last-super-admin guard can be bypassed in three ways:
   - (a) Two revokes run at the same time. Each one counts the other's assignment as still active, both succeed, and no super-admin is left.
   - (b) `PATCH /users/{id}` with `status=disabled` on the last super-admin (`app/modules/users/service.py:25`).
   - (c) The last assignment has an `expires_at` and lapses on its own.
3. **Pre-registration hijack of the bootstrap account.** Sign-up is public and email is unverified until USR-04 (D16). An attacker can sign up first with the owner's address. The owner then runs the CLI on that email and gives super-admin to an account whose password the attacker knows. Later the owner clicks the USR-04 verification link, the account becomes verified, and the attacker can get an admin token.
4. **SQLAlchemy errors can leak the email.** The engine has no `hide_parameters` (`app/db/session.py:14`), so the text of a database exception includes bound parameters such as the email. A CLI traceback would print the full email, against R11.
5. **The CLI must not depend on the RBAC flag** (the flag is refused in production). It also needs no password input, because it grants a role to an existing account.

**Recommendations**
- **Locking (AC3).** Every assignment POST, every DELETE and the CLI take `lock_app("admin")` first, then do the last-super-admin count (ADR-0003 §10 pattern). Add a test that the lock is the first SQL statement, like `test_rbac_locking.py`.
- **Expiry (AC2).** Return 422 for `expires_at` on `admin:super-admin`. The simpler alternative is to count only assignments with no expiry in the guard.
- **Follow-up (R7).** Apply the same guard under the same lock to user disable. Track it in ADM-002 if it is out of scope here.
- **Service API.** Every service mutation takes `actor`, with `# ADM-002 guard` markers on these future rules:
  - only a holder of `admin:*` may grant any role in the `admin` app, or any role holding an `is_admin` permission;
  - no actor may grant permissions they do not hold themselves;
  - self-revoke still goes through the last-super-admin guard.
- **Audit (R4).** Put `# R4` markers on POST, DELETE and the CLI. Once ADM-004 lands, the CLI writes an audit entry (`actor = system:cli`, `granted_by` NULL) in the same transaction.
- **CLI.**
  - Catch every exception and print a generic message with the exit code; never print a traceback.
  - Output only the user id and the masked email.
  - Print `created_at` and `email_verified: yes/no` so the operator can spot an account they did not create.
  - Inside the `admin` lock: an expired assignment becomes `expires_at = NULL` and prints "granted"; an active one prints "already held".
  - Never import the CLI from `app.main`; add a test that no HTTP route reaches it.
- **Runbook in `docs/deploy.md`.**
  - Use `docker compose -p org-admin-api run --rm --no-deps org-admin-api python -m app.cli grant-super-admin --email …` on the server only.
  - Create the account yourself and confirm you can log in before granting.
  - After USR-04, do a password reset, which must revoke sessions.
  - Never run with `DB_ECHO=true`.
- **Tests (R8).** Cover: concurrent revokes cannot drop the count to zero; 422 for an expiring super-admin; no full email in stdout or stderr, including on a database error; CLI exit codes 0 (granted / already held), 2 (unknown email) and 3 (disabled user).

**Risks**
- **High (latent).** The finding 3 hijack stays possible until USR-04 lands and the runbook is followed.
- **Medium.** Flag-on deployments outside staging and production, such as a misconfigured `APP_ENV=local` on the server, expose a way to become super-admin without authentication. The explicit-`APP_ENV` check reduces this but does not remove it.
- **Low.** Rows created while the flag was on in local or test databases must never be copied to production. This matches ADM-002 note 3.
- **Info.** Exit codes 2 and 3 reveal whether an account exists or is disabled. That is fine, because the operator already has database access.

**Open questions**
1. Owner: should `admin:super-admin` be forbidden to expire (recommended), or only excluded from the guard count?
2. Should the CLI refuse unverified accounts once USR-04 ships, with an explicit `--allow-unverified` for the time before?
3. Is `--email` in shell history and process lists on the Mac runner acceptable, or should the CLI prompt for it?
4. Should the last-super-admin guard on user disable be part of this ticket or ADM-002?
