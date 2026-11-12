# consult-identity-access-engineer — USR-02c

## Findings
- `role_assignments` has no `app_id`. `role_id` is a plain FK (NO ACTION) and `org_id` has no FK until USR-06. The role alone fixes the app, so no composite FK applies to assignments. Org existence is checked only in the service.
- `uq_role_assignments_user_role_org` is `NULLS NOT DISTINCT`. So "replace an expired duplicate" must be an UPDATE of the existing row: an INSERT would violate the constraint.
- Today a role delete racing an assignment insert is caught only by the FK (409). Writers of `role_assignments` do not call `lock_app`.
- `effective_*` ignores user status (A7). Nothing yet enforces the last-super-admin invariant against disabling a user or against expiry.
- Signup exists (`app/modules/auth`) and `users.email` is CITEXT. So the CLI can grant to an existing account and never needs to handle a password.
- argparse exits with code 2 on usage errors. That clashes with the ticket's "unknown email → 2".

## Recommendations
1. **Locking (ADR-0003 §10):**
   - POST: read the role unlocked to get its app key, call `lock_app(key)`, then re-read the role by `(app_id, role_id)` and decide every guard after that.
   - DELETE: lock the role's app.
   - Revoking an `admin:super-admin` assignment: lock the `admin` app first, then count. This serializes two concurrent revokes (write skew).
   - With this, `RoleService.delete`'s "has assignments" check becomes reliable.
2. **Validation order:**
   - User missing → 404. Assignment looked up by `(user_id, id)` → 404 if it belongs to another user.
   - Unknown role → 422.
   - `expires_at` naive or ≤ DB `now()` → 422.
   - `org_id` on an `org_mode=none` app → 422 (this covers `admin:super-admin`, which is always app scope).
   - Unknown org → 422: read the org with `FOR KEY SHARE` so it cannot be deleted mid-transaction.
   - Active duplicate → 409. A racing insert raises `IntegrityError` in a savepoint → 409.
   - `extra="forbid"`; `granted_by` is never accepted as input (set it to `actor`).
3. **Last-super-admin guard:** "active" means app scope, `expires_at IS NULL OR > now()`, and user `status='active'`. Count the holders other than the target; zero → 409. Also add `expires_at` on `admin:super-admin` → 422, so the last holder cannot silently lapse.
4. **`# ADM-002 guard` markers:**
   - POST and DELETE of any `admin`-app role, or of a role holding any `is_admin` permission: requires `admin:*`.
   - No self-grant or self-revoke of `admin`-app roles (`actor == user_id`).
   - GET needs nothing extra.
   - Add `# R4` markers on POST, replace and DELETE.
5. **CLI:**
   - Steps, in one transaction: `lock_app("admin")` → load `admin:super-admin` (missing means migrations were not run: exit 1, generic message) → find the user by email, normalized the same way signup does.
   - Unknown email → 2; disabled user → 3.
   - Active assignment → exit 0, "already held". Expired assignment → replace in place with `expires_at = NULL`, `granted_by = NULL`.
   - No user creation and no password prompt. The operator signs up first. If creation is ever added, use `getpass` only, never argv or env.
   - Do not gate on `ADMIN_RBAC_API_ENABLED` and do not refuse in production: this CLI is the production bootstrap.
   - Output: user id plus a masked email, e.g. `j***@e***.com`. Make sure SQLAlchemy echo or logging cannot print bound parameters.
   - Tests: run twice → same row and exit 0; capture stdout and stderr and assert the full email never appears.

## Risks
- While the flag is on (local/test only), anyone can grant `admin:super-admin` over HTTP. This is acceptable only because the config refuses the flag in staging and production (`config.py:97-109`).
- The last-super-admin guard can be bypassed by `UsersService.set_status(disabled)` or by deleting the user (cascade). Follow-up for ADM-002: apply the same `admin`-lock check in those paths.
- An org can be deleted later and leave orphan `org_id` rows until the USR-06 FK lands.
- `--email` on the command line ends up in shell history and the process list. This is low risk; reading the email from stdin is optional.

## Open questions
1. Exit codes: should unknown email stay 2 (it collides with argparse usage errors) or move to 4? Should unexpected and config errors use 1?
2. Should assigning roles to a disabled user be refused (409) or allowed, as A7 implies? Should `admin`-app roles be refused for disabled users?
3. On an `org_mode=required` app, is an app-scope assignment (`org_id` NULL) allowed? Today it would grant the role in every org.
4. Should a super-admin with an unverified email count toward the last-super-admin guard? Under D16 they cannot get a token.
5. Should the CLI print a warning when the user's email is unverified?
