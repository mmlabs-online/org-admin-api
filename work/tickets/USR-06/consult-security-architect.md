USR-06 security consult (security-architect, read-only)

I read the ticket, docs/identity-architecture.md (§3–§5, D10/D15/D20), and the code in app/modules/{tokens,auth,membership,rbac,organizations,audit}.

## Findings

F1. **Critical: org permission checks will see app-scope grants.** `RbacRepository._active_assignments` (/home/user/org-admin-api/app/modules/rbac/repository.py:278-292) builds the scope as `org_id IS NULL OR org_id = :org`. So `effective_permissions(user, app, org_id)` returns app-scope permissions plus org permissions. D9 makes the default role `<app>:member` hold every non-`is_admin` permission of the app. If `calechip:org:invite` (or `calechip:*`) sits in the member role, any joined user passes the "right org permission" check for **any** org id, including orgs they don't belong to. This is horizontal privilege escalation into other orgs.

F2. **High: the token org check passes for non-members.** `effective_roles(user, app, org_id)` is non-empty for anyone holding an app-scope role. If USR-06 reuses the current `if not roles` test in /home/user/org-admin-api/app/modules/tokens/service.py:73, any app member gets a token with `org=<any org id>`. That breaks AC "user must hold a role in that org → 403", and the app backend then trusts the `org` claim. The check needs its own query: an org-scope assignment where `org_id = :org` (equality, not NULL), the role belongs to this app, it hasn't expired, and the org is `active`.

F3. **High: invitations can escalate roles.** `{email, role}` lets the inviter name any role. Nothing yet requires that:
- the role belongs to the calling app (`X-MM-App`), not another app or `admin`;
- the role is meant for org scope;
- the role holds no `is_admin` permission;
- the role's permissions are no greater than the inviter's own org permissions.

`_guard_privileged` (rbac/service.py:1250) would raise `require_super` for an end user on an `is_admin` role. That is an accidental fail-closed, not a designed rule. The same applies to the "org owner role per app": if it ever holds an `is_admin` permission, org create breaks or needs a bypass.

F4. **Roles have no scope marker.** Nothing stops `calechip:org-owner` being granted at app scope (by an admin), which would make that user owner-level in every org they select. The token's `roles` array also merges app-scope and org roles (D7), so apps can't tell where a role came from.

F5. **Org status is never checked.** `org_exists_key_share` and `_active_assignments` ignore `organizations.status`. A `suspended` org would still issue tokens, accept invitations and allow invites.

F6. **Adding the FK can fail on orphan rows.** `role_assignments.org_id` has no FK today. Admin `DELETE /organizations/{id}` (organizations/service.py:57) deletes the org without touching assignments. Existing orphan rows would make the FK migration fail, or would need a cleanup decision. ON DELETE behaviour has to be chosen: CASCADE would silently strip access with no audit row.

F7. **The invite token in the URL path leaks.** The spec's `POST /auth/invitations/{token}/accept` puts the bearer secret in the path, where it lands in uvicorn/Cloudflare access logs and request-id traces. Existing flows send the token in the body (`verify-email {token}`) and build links as `#token=` fragments (auth/email_verification.py:102, password_reset.py:85).

F8. **Invitations are an email-abuse channel.** Any verified user can join calechip (`open`), create an org and send invitations to arbitrary addresses through the shared Gmail account (~500/day, §7). That allows spam and quota exhaustion, which blocks verification and reset emails for everyone. The org name is attacker-controlled text inside a mail from mmlabs, so it is a phishing vector.

F9. **Enumeration oracles.**
- Invite: a "user not found" or "already member" response reveals whether an email has an account or is in the org.
- Org ids: 403 vs 404 differences on token, invite and list reveal whether an org exists.
- Slug: a 409 on create reveals that a slug exists. Low impact, since orgs are platform-wide.

F10. **Doc conflict to flag.** CLAUDE.md R11 still says cookie `mm_device`, `Path=/api/v1/auth`, but D20 (`__Host-mm_device`, `Path=/`) is what the code implements (auth/cookies.py). It doesn't affect USR-06, but the planner should follow D20 and list the R11 text as a follow-up.

## Recommendations

**Authorization (AC3, AC4, AC5; fixes F1–F5)**

R-a. Add a repo/authz method for org-only permissions, e.g. `org_permission_keys(user, app_key, org_id)`, with a strict `org_id = :org` filter. It also requires role app = calling app, the assignment not expired (DB clock), and org `status='active'`. Use it for every org decision (invite now, manage later) and for the token's org membership check. Never use the merged `effective_*` for org decisions.

R-b. In the token flow, add these checks in order after the existing ones:
1. `org_id` given and app `org_mode = none` → 422 (the AC; reuse `OrgNotSupportedError`).
2. No active org-scope role for this app in that org, **or** the org doesn't exist or isn't active → the **same** 403 (e.g. `org_access_denied`). Unknown, suspended and non-member must look identical.

The `org` claim is set only on success. `roles` = app-scope roles + org-scope roles (equality filter) for that org only. Add a test: an app member with no org role + a valid org id → 403 (catches F2).

R-c. Make role scope explicit. Preferred is a data marker such as `roles.scope app|org` or an `apps.org_owner_role_id` plus an "org-assignable" flag. Enforce it:
- org roles can only be granted with `org_id`, and app roles only without;
- the owner role must hold no `is_admin` permission (validate at configuration time and again at create);
- for `org_mode=required` apps, the default (join) role must not contain org-level permissions, only e.g. `<app>:org:create`.

If the owner won't add a column now, the minimum is a key convention plus validation, and the open question goes to the owner.

R-d. Invite role rule:
- the role belongs to the calling app;
- it is org-assignable;
- it holds no `is_admin` permission;
- its permission set is a subset of the inviter's org permissions (owner may invite owner; `<app>:*` in the org counts as everything);
- never the `admin` app.

Violations → 403 `role_not_allowed`, with no role details for other apps. Grant through `_grant_locked` with a dedicated actor (`EndUser`), never `SYSTEM_*`.

R-e. Org endpoints refuse `X-MM-App: admin` and apps with `org_mode=none`: 403 `origin_not_allowed`-style for admin, as in `get_mail_link_app_context`, and 422 for org-less apps. For `GET /orgs?app=`, `app` must equal `X-MM-App`, else `app_mismatch`. Better still, drop the param and derive the app from the header.

**Invitations (AC4; fixes F7, F8, F9)**

R-f. Token storage and transport:
- ≥256-bit `secrets.token_urlsafe`; store only the SHA-256 hash (R11).
- Accept as `POST /api/v1/auth/invitations/accept {account_id, token}` with the token in the **body**, not the path. This is a spec deviation, so the planner should flag it in plan.md for the owner.
- Email link: `{validated Origin}{INVITE_PATH}#token=…`, reusing the `get_link_origin` pattern so the link always points to a registered origin of the inviting app.
- TTL around 7 days. Single use enforced atomically: `UPDATE … SET accepted_at=now() WHERE token_hash=:h AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING …`, in the same transaction as the grant and the audit row.
- A new invite for the same (org, email) revokes the previous pending one. Use a partial unique index on (org_id, app_id, email_hmac) where pending.

R-g. Email binding:
- Accept requires the cookie account (class 2), an active user and a verified email (S5, else 403 `email_unverified`).
- The invited address is compared to the account's email after normalization. Storing `email_hmac` (IDENTITY_HMAC_KEY, as in `login_failures`/`email_send_log`) is enough for binding and avoids storing PII.
- A mismatch gives one generic 403 (e.g. `invitation_not_for_account`) with no email in the details.
- Unknown, expired, used and revoked tokens all give the same error (e.g. 422 `invalid_token`, as verify-email does).
- The invitation's app must equal `X-MM-App`.
- Accepting while already holding that org role → 200 no-op, nothing written.

R-h. Abuse limits (R11/S6 spirit):
- per-IP `rate_limit("org_invite")`, `("org_create")` and `("invite_accept")`;
- per inviter around 20 invites/day;
- per org around 50 pending;
- per target email at most 1/min and 3/day across all orgs, via `email_send_log` with purpose `invite`;
- invite mails count toward `email_daily_cap`;
- per user around 10 org creations/day and a total cap.

Over the limit → 429 for the actor-based limits. For the per-target limit, return silent success so it isn't an oracle. The invite response is always the same (201/202 with the invitation id, no `user_exists` or `already_member`).

The email body is plain text. The org name is validated (length ≤ 100, no control chars, no URLs or `@`, recommended), and the mail clearly says "you were invited by an mmlabs user".

R-i. Org create:
- Requires `<app>:org:create` at **app scope**.
- Locks the app, inserts the org with `created_by`, grants the owner role with `org_id`, and writes the audit rows, all in one transaction.
- A slug conflict → 409 is acceptable (low value). Keep the message generic and don't echo other orgs' data.

**Data model and migration (AC1, AC2; fixes F5, F6)**

R-j. The migration must:
- detect orphan `role_assignments.org_id` rows before adding the FK. Fail loudly, or delete them in the migration with a count logged; the owner decides. Never silently NULL them, because that would turn an org role into an app-scope role, which is privilege expansion.
- use FK `ON DELETE RESTRICT`, so admin delete of an org with assignments → 409 (e.g. `org_has_assignments`, consistent with `role_has_assignments`);
- invitations → org `ON DELETE CASCADE`; `created_by` → users `ON DELETE SET NULL`;
- `_grant_locked` must also require org `status='active'` for org grants.

**Audit (AC6, R4, S8 / ADR-0009)**

R-k. Write in the same transaction, after the last guard. Actor `EndUser` except for admin routes. `app_key` and `org_id` are set on every row. `changes` is allowlisted and **never contains email, token, hash or email_hmac**.
- `organization.created` (target organization; name, slug)
- `role_assignment.granted` for the creator's owner role (target role_assignment; role_key), or a dedicated `organization.member_added`
- `organization.invitation_created` (new target `invitation`; role_key, expires_at)
- `organization.invitation_accepted` (target role_assignment or invitation; role_key)
- `organization.invitation_revoked` when a re-invite supersedes one

Token issuance with an org writes no audit row (a read; it is logged with `org_id` in the existing `token_issued` line). 429s, validation failures and end-user 403s write nothing, per ADR-0009, which limits `denied` rows to staff principals. Add the new `AuditTarget`/`AuditAction` enum members. Existing admin org CRUD audit is unchanged.

**Cookie/CORS class (R3, R11/D12, ADR-0006)**

R-l. All new routes are **R3 class 2**: `AppContextDep` declared **before** `DeviceDep` (403 before any cookie lookup or rotation, as in `list_accounts`), `account_id` resolved with `get_active_account(... created_before=...)`, rotation `Set-Cookie` echoed, `Cache-Control: no-store`, and a per-route `rate_limit`. Accept is class 2 as well; it is not public, because email binding needs the signed-in account. They live under `/api/v1/auth/*`, so the ADR-0006 CORS allowlist covers them automatically. Add a test for an unregistered Origin and a missing `X-MM-App` on each route.

**List endpoint exposure (AC3 list; R9)**

R-m. `GET /api/v1/auth/orgs` returns only orgs where the account's user holds an active **org-scope** role for the calling app and the org is `active`. Suspended orgs are hidden or flagged; the owner decides. Fields per item: `id`, `name`, `slug`, the caller's own `roles` in that org, and optionally `status`. **No** `created_by`, member lists, other members' emails, counts, `description` (optional) or invitation data. The list is bounded (`Page[T]`, limit ≤ 100). An empty list is returned, never 404. `account_id` in the query string is acceptable because it is useless without the cookie, but a body-less GET must still go through `AppContextDep`.

**Tests (AC7, R8), beyond the AC list**
- An app member with no org role gets 403 on token with an org.
- An app-scope `<app>:*` or member with an org permission cannot invite into a foreign org.
- Invites to a higher role, an `is_admin` role, another app's role or an admin-app role → 403.
- Accept with a different verified account → 403; with an unverified account → 403 `email_unverified`.
- Replay after accept, expired and revoked tokens → the same 422.
- Two concurrent accepts → one succeeds.
- A suspended org → token 403 and accept refused.
- `X-MM-App: admin` → 403.
- Invite responses are identical for existing and unknown emails.
- Audit rows hold no email or token.
- Migration upgrade/downgrade with orphan rows.

## Risks
- **Shared accounts through the platform-wide org model (OQ1).** One org id is valid across all org-mode apps. Org-scope roles are app-bound, so this is safe today. If `organizations.app_key` is added later, existing cross-app usage must be checked.
- **Apps can't separate org roles from app roles in the token.** `roles` merges both scopes (D7), so an app backend can't tell them apart, and apps must scope all data by the `org` claim, never by a request parameter. Document this in docs/consumers.md; a separate `org_roles` claim could be a later additive change.
- **Default role vs org permissions.** If the calechip default role keeps org-level permissions, app backends that check permissions with the role catalog will still treat every member as an org admin, even with R-a in place. This is a data/config risk outside this server's checks.
- **The Gmail quota is shared**, so invite abuse degrades verification and reset for every app.
- **Removing an owner or member, transferring ownership and revoking invitations are not in scope.** A compromised org-owner account can't be contained except by an admin revoking assignments.
- **Org names are visible in invitation mails**, which is a phishing and impersonation surface. Brand names ("mmlabs Support") can't be blocked without a denylist.

## Open questions (owner)
1. **Role scope marker:** add `roles.scope` (app|org) and `apps.org_owner_role_id`, or use a key convention? And can the `org_mode=required` default role be restricted to `<app>:org:create` plus app-level permissions?
2. **Accept path:** may we deviate from the spec's `/invitations/{token}/accept` and use a body token, to keep the secret out of logs?
3. **Invited user who never joined the app:** does accept also create app-scope membership (default role under the join policy), or may the org role alone count as membership for the token? This affects the "no org_id → app-scope roles" path and `not_member`.
4. **Who may invite, and to which roles:** owner only, or anyone with `<app>:org:invite` in the org? Is the subset rule acceptable?
5. **Org delete:** FK `RESTRICT` (409 while assignments exist) or `CASCADE`? What happens to existing orphan `org_id` rows in the migration?
6. **Suspended orgs:** hidden from the list, or shown with their status?
7. **Limits:** per-user org creation cap, invitation TTL (proposed 7 days) and daily invite caps.

Key files: /home/user/org-admin-api/app/modules/rbac/repository.py (lines 278-327), /home/user/org-admin-api/app/modules/tokens/service.py, /home/user/org-admin-api/app/modules/rbac/service.py (1193-1259), /home/user/org-admin-api/app/modules/organizations/service.py, /home/user/org-admin-api/app/modules/auth/router.py, /home/user/org-admin-api/app/modules/auth/deps.py, /home/user/org-admin-api/app/modules/audit/actions.py, /home/user/org-admin-api/docs/identity-architecture.md.
