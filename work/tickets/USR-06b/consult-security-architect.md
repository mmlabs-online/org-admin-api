USR-06b security consult: org invitations

**Bottom line:** the S1 role-scope issue does not block USR-06b, but only if 06b includes a small fail-closed guard (R1 below). The full scope marker / `org_roles` claim can be its own ticket, but it must land before USR-05 (manifest push). USR-05 is what re-widens `<app>:member` automatically. Invitations are the first end-user path into someone else's org, so S1 is not a latent issue here: 06b is where it starts to matter.

## Findings

**F1. S1 matters once invitations exist (High, depends on the app's data).**
- `TokenService.issue` (`/home/user/org-admin-api/app/modules/tokens/service.py:74-90`) requires an app-scope role first (otherwise 403 `not_member`). It then puts app-scope roles plus org roles into `roles`.
- B1 has every invitee join with `<app>:member`. Once USR-05 runs `build_default_role` (`/home/user/org-admin-api/app/modules/rbac/service.py:252-289`), that role holds every non-admin permission. Today the staff build endpoint does the same.
- Result: a "viewer" invited into org X gets a token for org X that also carries `calechip:member`. In calechip that is effectively member-level rights in X (e.g. `projects:delete`). The inviter's chosen role means nothing inside calechip.
- This server's own decisions stay safe only if every org decision uses org-exact permissions.
- Precondition: calechip's default role contains org-level permissions. Today that is a config question; after USR-05 it happens automatically.

**F2. The subset rule is only sound if computed org-exact.**
- `effective_permissions(user, app, org_id)` returns the app-scope + org union (`repository.py:331-349` via `_active_assignments`).
- If the planner uses it for the B2 subset check or for the `<app>:org:invite` check, any member can invite into any org they belong to with any non-admin role, owner included. The member role satisfies the subset check on its own.
- There is no org-exact permission query yet. Only `org_role_keys` exists (`repository.py:309-329`), so a new one is needed.

**F3. `_grant_locked` does not check org status.**
- `org_exists_key_share` (`repository.py:365-372`) ignores `organizations.status`. Accepting into a suspended org would grant a role.
- The invitation service must check that the org is `active` under the lock.

**F4. Without an app-scope role the accepted invitation does nothing.**
- If the app has no `default_role_id`, or `join_policy` is not `open`, accept can grant the org role, but `/token` still answers `not_member`.
- B1 is therefore required, not optional. Refuse such cases up front, not at accept time.

**F5. The email cap split would let invites starve password reset (High, availability).**
- `cap_refusal` (`/home/user/org-admin-api/app/modules/auth/email_throttle.py:43-46`) counts every non-`verify` purpose against reset's share (50%). Invite spam would eat the reset budget.
- `email_send_log` and `email_tokens` have `CHECK purpose IN ('verify','reset')` (`/home/user/org-admin-api/app/modules/auth/models.py:50,83`). Adding `invite` needs a migration.
- Shares today: verify 80%, reset 50%. Adding invite at x% leaves reset a guaranteed 20 − x % only. At x = 20, reset can be starved completely.

**F6. Org name in the email body: phishing and content issues (Medium).**
- `AuthOrgCreate.name` (`/home/user/org-admin-api/app/modules/organizations/schemas.py:43`) accepts 255 characters of any kind: URLs, `@`, control and bidi characters, brand names.
- Header injection is not possible: `EmailMessage` refuses CR/LF (`/home/user/org-admin-api/app/modules/email/sender.py:98`). Still, a name in the Subject could break sending, and a name like "mmlabs Security – verify at evil.example" in the body reads like an official mail from mmlabs.

**F7. `OutgoingEmail.user_id` is a required UUID and is logged** (`sender.py:35`, `dispatcher.py:128`). The invitee may have no account. Do not resolve the email to a user just to fill this field: that adds a timing/enumeration path. Use the inviter's id or the invitation id.

**F8. The invite path does not need a user lookup.** Binding by `email_hmac` at accept time is enough. If the invite path never touches `users`, there is no existence oracle from responses or timing.

**F9. Authority can go stale between invite and accept (7-day window).** Before accept, any of these can change: the inviter loses `<app>:org:invite` or their role, the inviter is disabled, staff adds an `is_admin` permission to the invited role, or the org is suspended. If accept trusts the state at invite time, a revoked inviter's pending invites still grant access.

## Recommendations

**R1. S1 guard, in scope and required for shipping (AC4, AC7 "cannot grant beyond own permissions", R3).** Invites and accepts are available only when all of these hold:
- the app has `org_mode = required`;
- `org_owner_role_id` and `default_role_id` are set;
- `join_policy = open`;
- the default role and the org owner role share no permission other than `<app>:org:create`.

The owner role is in effect the definition of "org-level permissions", so this is a data-only check with no schema change. Otherwise answer 403 `org_invite_unavailable` (same pattern as `org_create_unavailable`). Check it at invite and again at accept; at accept the invitation is not consumed. Document this in consumers.md and ADR-0013. It fails closed if staff or the manifest re-widen the member role.

Follow-up ticket (call it USR-06c), sequenced before USR-05: a permission scope marker or an additive `org_roles` claim, and `build_default_role` skipping org-level permissions for `org_mode = required` apps. Also the staff-builder bypass noted in the 06a re-check: `set_default_role_if_null` should add an `is_distinct_from(org_owner_role_id)` condition, or add a DB CHECK.

**R2. Org-exact authorization for every invite decision (AC4, B2).**
- Add `RbacRepository.org_permission_keys(user, app_key, org_id)`: strict `org_id = :org`, role's app = calling app, unexpired by DB clock, org `active`.
- Use it for both the `<app>:org:invite` check and the subset check.
- Rules for the invited role:
  - it is a role of `X-MM-App`'s app;
  - the app is never `admin` (use `MailLinkAppContextDep`);
  - it has no `is_admin` permission;
  - its permission set ⊆ the inviter's org-exact set. `<app>:*` held in the org counts as all non-admin permissions.
- Unknown org, suspended org, org where the inviter has no invite permission: one 403 `org_access_denied`, `details={}` (no existence oracle).
- Disallowed role: one 403, e.g. `invite_role_forbidden`. Record it with `audit.record_denied`; it is an escalation signal.

**R3. Token handling (AC4, R11).**
- Token: `secrets.token_urlsafe(32)`; store only its SHA-256.
- Link: `{validated Origin}{INVITE_PATH}#token=…`, with the origin from `get_link_origin` (a registered origin of the inviting app). TTL 7 days, from env (R5).
- Accept: `POST /api/v1/auth/invitations/accept {account_id, token}` with the token in the body (B3). This deviates from identity-architecture §4. Update §4 and flag it in plan.md.

**R4. Accept order (AC4, AC7).** Run these steps in this order:
1. `rate_limit("invite_accept")` → app context (Origin + `X-MM-App`) → device.
2. Account is active (else 401); email is verified (else 403 `email_unverified`; checked before the token, so it reveals nothing).
3. Look up the token by hash, together with `app_id == calling app`. Unknown, expired, used, revoked or other-app tokens all get the same 422 `invalid_token`.
4. `email_hmac` (same normalization and hasher as login/`email_send_log`) must equal HMAC(account email), else 403 `invitation_not_for_account` with no email in details. Not consumed.
5. `lock_app`, then check: org `active` (`FOR KEY SHARE`), role still in the app and admin-free, inviter still active and still passing R2 org-exact, R1 guard. If any check fails → `invalid_token` (or `org_invite_unavailable` for R1).
6. Join per B1 if the account has no app-scope role (reuse `join_default_role` + `_grant_locked` + `APP_JOINED` audit). If the app is closed, refuse and do not consume.
7. Consume atomically: `UPDATE invitations SET accepted_at = now(), accepted_by = :u WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING id`. Zero rows means `invalid_token` (lost race).
8. Grant the org role through `_grant_locked`. If the role is already held, catch the `ConflictError`, still consume, and write `invitation_accepted` with `granted: false`. My view: kill the live secret rather than leave it pending. This differs from the earlier "nothing written" note; the planner should pick one.

Everything from step 5 on happens in one transaction. Lock order is the same as every other grant path (app lock first), so there are no new deadlocks.

**R5. Invite creation (AC4, AC7 no-oracle).**
- No `users` lookup.
- One partial unique index on `(org_id, email_hmac) WHERE accepted_at IS NULL AND revoked_at IS NULL`. A re-invite revokes the previous pending invite in the same transaction and audits `invitation_revoked` (reason `superseded`). Serialize with the app lock or an advisory lock on `(org, email_hmac)`, and map `IntegrityError` to a retry or 409. A 409 here is safe because the inviter supplied the email.
- Response is always 201 `{id, role, expires_at}`. It never contains `user_exists`, `already_member` or the email.
- FKs:
  - org → `ON DELETE CASCADE`;
  - `role_id` → CASCADE (pending invites die with the role; accept re-checks anyway);
  - `invited_by` → users `SET NULL`. Accept then fails the step 5 check, so the invite is effectively dead.

**R6. Abuse limits (R11, R5; all from env, listed in `.env.example`).**
- Per IP: `org_invite` and `invite_accept`.
- Per inviter: ~20 per 24 h (counted from the `invitations` table) → 429.
- Per org: ~50 pending → 429 or 409.
- Per target email: 1/min and 3/day across all orgs, via `email_send_log` purpose `invite`. Over the limit → silent success: the row is created but no mail is sent, so the response is the same.
- Give invite its own share in `cap_refusal`, e.g. `INVITE_CAP_PERCENT = 10`, counted separately from reset (separate count in `window_totals`). Recommendation: invite ≤ 10%, and confirm with the owner whether verify should drop to 70% to keep reset's guaranteed 20%. Migration: extend the purpose CHECK on `email_send_log` with a working downgrade (R2).

**R7. Email content (phishing).**
- Fixed Subject with no org name, e.g. "You've been invited to an organization on calechip".
- Plain-text body: "A calechip user invited you to the organization “<name>”. If you didn't expect this, ignore this email." Do not include the inviter's email (that would expose PII to an arbitrary address).
- Render the org name only if it passes a safe-name check:
  - no Cc/Cf/bidi characters;
  - no `://`, no `www.`, no `@`;
  - no domain-like token (`\w\.\w{2,}`);
  - ≤ 100 characters.
  Otherwise render the slug or nothing. Also apply that check to `AuthOrgCreate.name`. This tightens a 06a schema; it is justified because the name now goes into email. List it in the plan as an R7 scope note.

**R8. Audit (AC6, R4).** Every row is written in the same transaction, with `app_key` and `org_id`.
- `organization.invitation_created`: actor `EndUser(inviter)`, target `invitation`, `changes` = `{role_key, expires_at}`.
- `organization.invitation_revoked` (superseded).
- On accept:
  - `organization.invitation_accepted`: actor `EndUser(invitee)`, target `invitation`, `{role_key, invited_by}` (UUID);
  - `role_assignment.granted` (org);
  - `app.joined` when B1 applies.
- Denied escalation attempts go through `record_denied`.
- Never write the email, the email HMAC (it is pseudonymous but linkable across orgs) or the token.
- Register both routes in the COOKIE route-class sweep and the audit coverage harness. Accept writes several rows, so it goes in `IDENTITY_MULTI_ROW_AUDITED`.
- Logs: UUIDs and the app key only.

**R9. Tests to add on top of the AC7 list.**
- Subset check is org-exact: an inviter whose app-scope `<app>:member` holds the owner role's permissions cannot invite as owner.
- R1 guard: an overlapping default role or owner role makes invite and accept 403 `org_invite_unavailable`.
- Re-checks at accept: the inviter's role revoked, the invited role gains `is_admin`, the org suspended.
- Invitee who never joined gets the default role plus the org role, and `/token` with `org_id` works.
- Closed app: refused, not consumed.
- Other-app token gives `invalid_token`.
- Per-target cap gives the same 201.
- Invite share cannot consume the reset share.
- The `email_send_log` downgrade works.

## Risks
- Residual S1 risk until USR-06c ships: the R1 guard covers the default role only. Staff-granted app-scope roles with org-level permissions still leak into org tokens. This is a config responsibility; document it.
- There is no member removal, no invitation revoke endpoint and no ownership transfer (out of scope). Invited co-owners or a compromised member can only be removed by staff. B2 allows inviting owners, which makes this worse.
- The shared Gmail quota remains a cross-app availability risk. Caps limit it but do not remove it.
- Rotating `IDENTITY_HMAC_KEY` silently voids pending invites and send-log counting.
- Invitation rows (`email_hmac`) accumulate. A retention purge is a follow-up.
- Mailbox compromise of the invitee is equivalent to account takeover via reset. Accepted as out of scope.

## Open questions (owner)
1. Do you accept the R1 guard in 06b and the scope marker as USR-06c before USR-05, rather than blocking 06b on the marker? Recommended: yes.
2. Should the org owner role be invitable (co-owners with no removal path), or should invitable roles be strictly below the owner role? Recommended: allow it per B2, but say so in consumers.md. A stricter option is to exclude `org_owner_role_id` until member management exists.
3. Email cap shares: invite ≤ 10%. Lower verify to 70% to keep reset's guaranteed 20%?
4. Accept when the org role is already held: consume and audit (recommended), or a no-op that writes nothing (the earlier consult)?
5. Tighten `AuthOrgCreate.name` in 06b (R7)? It touches a 06a schema.

Key files:
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/auth/email_throttle.py
- /home/user/org-admin-api/app/modules/auth/models.py
- /home/user/org-admin-api/app/modules/auth/deps.py
- /home/user/org-admin-api/app/modules/email/sender.py
- /home/user/org-admin-api/app/modules/membership/service.py
- /home/user/org-admin-api/app/modules/organizations/schemas.py
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/docs/identity-architecture.md
