USR-06 consult (identity-access-engineer, read-only)

## Findings

F1. **Effective-role reads merge scopes.** `RbacRepository._active_assignments` (`/home/user/org-admin-api/app/modules/rbac/repository.py:277-292`) returns app-scope roles when `org_id=None`. With an org it returns `org_id IS NULL OR org_id = X`, so it is the union of both scopes. `AuthzService.effective_roles/effective_permissions` (`/home/user/org-admin-api/app/modules/rbac/authz.py`) pass this through unchanged. That is the right shape for the token `roles` claim (AC "app-scope + roles in that org"). It is the wrong shape for any check that asks "does the user hold something *in org X*". Called with an org, it is true for every app member.

F2. **Default role holds every non-admin permission (D9).** For calechip this means any org-management permission (for example `calechip:org:invite`) that is not flagged `is_admin` is granted at app scope to every joined user. If the invite check uses `effective_permissions(user, app, org_id)`, any calechip member can invite into any org. This is a cross-tenant privilege escalation. The same applies to `<app>:org:create`, which should be checked at app scope only. The ticket intends that anyway.

F3. **Token service today** (`/home/user/org-admin-api/app/modules/tokens/service.py`):
- Any `org_id` gets 422 `org_not_supported`.
- Membership means `effective_roles(user, app.key)` is non-empty, which is app scope only. The same test is used by join (`/home/user/org-admin-api/app/modules/membership/service.py:100`).
- There is no `org` claim yet.

Side effect: a user who holds only an org role (invited but never joined) gets `not_member` when they request a token without `org_id`.

F4. **Org scope on writes.** `AssignmentService._grant_locked` (`/home/user/org-admin-api/app/modules/rbac/service.py:1193`):
- refuses `org_id` when `org_mode = none` (422 `org_id_not_allowed`);
- checks the org exists with `FOR KEY SHARE`;
- requires `admin:*` (`_guard_privileged`) for any role holding an `is_admin` permission;
- never audits; the caller does.

It runs under `lock_app`. Org creation and invitation acceptance can reuse it, as join does with `SYSTEM_JOIN`.

F5. **Admin app.** `AdminAuthService.principal` (`/home/user/org-admin-api/app/modules/admin_auth/service.py:39`) calls `effective_permissions(user, "admin")` with no org, so admin enforcement already ignores org scope. `AppService.update` forces the admin app to stay `org_mode = none` (`/home/user/org-admin-api/app/modules/rbac/service.py:382-391`), so `_grant_locked` can never create an org-scoped admin assignment.

F6. **No "org owner role" exists.** There is no column, convention or catalog marker for it. Tests use arbitrary keys (`stoktok:org-owner` in `/home/user/org-admin-api/tests/test_auth_join.py:158`). Also, the wildcard `<app>:*` must be `is_admin` (CHECK `wildcard_is_admin`). An owner role holding `calechip:*` would therefore be "privileged": `_grant_locked` with a system actor would refuse it, and `join_default_role`'s logic would also reject it.

F7. **No FK on `role_assignments.org_id`** (`/home/user/org-admin-api/app/modules/rbac/models.py:185`). The admin `DELETE /api/v1/organizations/{id}` (`/home/user/org-admin-api/app/modules/organizations/service.py:57`) is a plain hard delete. Once the FK exists, deleting an org that has assignments will either fail with an IntegrityError (a 500 today) or cascade, depending on the FK action.

F8. **Hashing patterns to reuse.** Email tokens are hashed as SHA-256 of the raw token (`/home/user/org-admin-api/app/modules/auth/email_verification.py:46`). Send limits use `email_send_log` keyed by an HMAC of the email, plus an advisory lock (`/home/user/org-admin-api/app/modules/auth/repository.py:212`).

F9. **Doc conflict (outside the ticket, flag only).** CLAUDE.md R11 says the cookie is `mm_device` with `Path=/api/v1/auth`. `identity-architecture.md` §4 (D20) says `__Host-mm_device` with `Path=/`.

## Recommendations

R-a (AC token, AC "user without a role in that org → 403"). Add a scope-exact repository read, e.g. `org_role_keys(user_id, app_key, org_id)`, with `org_id = X` only, not expired, and role in that app.
- Token with `org_id`:
  1. `app.org_mode == none` → 422. Reuse or rename `OrgNotSupportedError` with reason `org_not_allowed`. This also covers `aud=admin`.
  2. The org must exist and be `active`.
  3. `org_role_keys` must be non-empty. Otherwise return 403 with one code, e.g. `not_org_member`, for unknown, suspended and non-member alike, so the endpoint is not an oracle for org ids.
  4. `roles = effective_roles(user, app, org_id)` (the union), and `org = str(org_id)`.
- Token without `org_id`: keep the current app-scope behaviour and emit no `org` claim.
- Keep the email-verified, user-status and session checks ahead of the org checks, in their current order.

R-b (AC create/invite; rule R3). Every org-scoped authorization decision (invite, list members, future org management) must use org-exact permissions, never the union. `<app>:org:create` must be checked with app-scope permissions only: `effective_permissions(user, app, None)` plus `permits()`. Add tests where an app-scope member holding `calechip:org:invite` at app scope cannot invite into someone else's org.

R-c (AC "org owner role per app"). Add `apps.org_owner_role_id` (nullable) with the same composite same-app FK as `default_role_id` (`fk_apps_org_owner_role_same_app`, `use_alter`), settable through the existing admin `PATCH /apps/{key}`. Validation, mirroring the `default_role_id` guards:
- the role belongs to this app;
- it must not hold `is_admin` permissions (checked when set and re-checked at use time);
- it may only be set when `org_mode = required`;
- the admin app may never have one.

Then `POST /auth/orgs` returns 403/409 `org_create_unavailable` when the app has none. A convention key like `<app>:org-owner` is the alternative, but it is implicit and harder to audit. Owner permissions should be explicit keys (`calechip:org:invite`, `calechip:org:manage`), not `calechip:*`.

R-d (AC create org). Flow under `lock_app(app)`, following ADR-0003 §10 lock order:
1. Account, active user and verified email checks, as in join.
2. App-scope `<app>:org:create`.
3. Insert the org with `created_by`.
4. `_grant_locked(app, owner_role, user, org.id, None, actor=SYSTEM_…)`.
5. Audit `organization.created` and `role_assignment.granted` (`EndUser` actor, `org_id` set) in the same transaction (R4).

The app comes from `X-MM-App`, never from the body. Slug conflict → 409. Add a per-user creation rate limit, because open sign-up plus open join plus unlimited org creation invites slug squatting.

R-e (AC invitations). New table `org_invitations`:

| Column | Notes |
|---|---|
| id | |
| org_id | FK |
| app_id | FK |
| role_id | composite FK `(role_id, app_id)` → `roles(id, app_id)`, so the role is same-app |
| email | citext, normalized |
| token_hash | bytea, unique, SHA-256 of 32 random bytes |
| invited_by | FK users, SET NULL |
| expires_at | e.g. 7 days |
| accepted_at, accepted_by | NULL until accepted |
| revoked_at | NULL |
| created_at | |

Add a partial unique index on `(org_id, app_id, email)` where pending, so re-inviting supersedes the old invitation.

Create rules:
- The inviter needs org-exact `<app>:org:invite`.
- The invited role must be of the calling app and must not hold `is_admin` permissions.
- The inviter cannot grant more than they hold: the role's permissions must be a subset of the inviter's org-exact permissions, or only owners may invite owners.
- Rate limit per inviter and per target email through `email_send_log` with a new purpose, `invite`.
- Mask the email in logs (R11).

R-f (AC "accept by the invited verified email"; identity binding). `POST /auth/invitations/{token}/accept {account_id}` is cookie + Origin + `X-MM-App`, and the app must equal the invitation's app (else `app_mismatch`, like join). Bind to identity, not only to possession of the token: the account must be active and verified, and `lower(user.email)` must equal `invitation.email`. A forwarded link alone must not grant access.

Consume atomically: `UPDATE … SET accepted_at=now(), accepted_by=… WHERE token_hash=:h AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>now() RETURNING …`, then `_grant_locked` (an existing role in the org counts as success) and audit, all in one transaction.

On email mismatch, return 403 `invitation_email_mismatch` without consuming the invitation. Details may carry a masked hint (`j***@example.com`) so the UI can offer account switching. The client then picks another account on the device from `GET /auth/accounts`, or logs in or signs up and verifies. Do not auto-select an account server-side.

Return the same 404/410 for unknown, expired, used and revoked tokens. The token travels in the path, so make sure access logs and the audit `request` context do not record the full path for this route. Fragment-based links, or posting the token in the body, are safer. Prefer `{token}` in the body even though §4 shows it in the path; that needs a docs amendment.

R-g (AC list mine). `GET /auth/orgs?account_id=&app=`: require `app == X-MM-App` so app A cannot enumerate a user's orgs in app B. Return orgs where the user has an org-exact, unexpired role of that app, and include `status`. Do not require an app-scope role, so invitees can select an org.

R-h (AC FK). Migration:
1. Pre-check or clean orphaned `role_assignments.org_id` values.
2. `ADD CONSTRAINT … NOT VALID`, then `VALIDATE`.
3. Provide a working downgrade.

Pick the FK action deliberately. My recommendation is `ON DELETE RESTRICT`, with the admin delete returning 409 `org_has_assignments`, mirroring `role_has_assignments`. Admins should suspend rather than delete. Invitations use `ON DELETE CASCADE`.

R-i (enforcement for admin). Keep admin org-blind. Add tests showing that a token for `aud=admin` with `org_id` → 422, and that `AdminAuthService` permissions are unchanged by any org-scoped rows. Optionally add a DB CHECK or trigger so admin-app roles can never carry an `org_id`; today that rests on service logic only.

R-j (docs). Update `docs/consumers.md`:
- the `org` claim;
- apps must check that `org` matches the tenant of the resource;
- `roles` mixes app-scope and org roles, so business permissions should live only on org roles.

Recommend that, for `org_mode = required` apps, the default role holds only `<app>:org:create`. This is data, per D9, and it closes F2 at the data level as well.

## Risks

- **F2 cross-tenant escalation** if any org check uses the union read. This is the highest risk in the ticket.
- **Invitation link forwarding or account confusion** on a shared device with several signed-in accounts, if acceptance binds to the token only.
- **Spam relay:** open sign-up, then join, then create an org, then invite arbitrary addresses through the Gmail SMTP sender. This needs rate limits and possibly Turnstile on invite.
- **Revocation lag:** org role removal or org suspension only takes effect after up to the token TTL (~15 min). Acceptable, but document it.
- **Admin delete path:** after the FK it either breaks (500) or silently cascades away membership. A new 409 code on DELETE needs to count as additive under R9.
- **App switch from `required` to `none`** leaves dormant org assignments. Tokens with `org_id` then return 422. That is safe, but should be stated.
- **Ticket size:** it touches the migration, a new table, four endpoints, token changes and an apps column. Consider splitting invitations into USR-06b.

## Open questions

1. Owner role mechanism: an `apps.org_owner_role_id` column, as recommended, or a key convention? Who seeds calechip's?
2. Does accepting an invitation also grant app-scope membership (an implicit join with the default role), or is an org-only member allowed? This affects whether a token *without* `org_id` works for invitees.
3. Invitable roles: any non-admin role of the app with a subset-of-inviter rule, or a fixed list such as owner and member?
4. Org deletion with assignments: RESTRICT plus 409, or CASCADE?
5. Should a suspended org also block listing, invitations and acceptance, or only tokens?
6. Invitation token location: path (as in §4) or body? I recommend body or fragment to keep it out of logs.
7. OQ1 is still open: confirm platform-wide orgs, with the slug global and unique, so org slugs are visible across apps through 409s.
8. The cookie name and path conflict between CLAUDE.md R11 and D20 (F9) needs an owner fix outside this ticket.
