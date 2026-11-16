## Consult: software-architect, USR-06 (org-admin-api)

I only read files; nothing was changed. Files I read: `work/tickets/USR-06/ticket.md`, `docs/identity-architecture.md`, `docs/adr/0001-multi-app-authorization.md`, `docs/spec-v1.md` §5.1/§5.3/§5.8/OQ1, `app/modules/{organizations,rbac,tokens,membership,audit,auth}/…`, `work/backlog.md`.

### Findings

1. **No single "Identity module" folder exists.** Identity is spread across `users`, `sessions`, `auth`, `tokens`, `membership` and `rbac`. Read literally, D15's "move to the Identity module" has no target folder. The table is already used as an Identity concept: `rbac/repository.py:343` `org_exists_key_share` imports `Organization`, and `rbac/service.py:1204-1210` already accepts an `org_id` on assignments when `org_mode != none`.
2. **`role_assignments.org_id` has no FK** (`rbac/models.py:185`). Admin `DELETE /api/v1/organizations/{id}` is a hard delete (`organizations/service.py:57`). Any org-scoped assignment created by staff through `/users/{id}/role-assignments` before that delete is now an **orphan**, and adding the FK will fail on it.
3. **The token path is stubbed.** `tokens/service.py:57` raises 422 `org_not_supported`. `AuthzService.effective_roles(user, app, org_id)` already returns app-scope plus org roles (`rbac/repository.py:278-292`). But "holds a role *in that org*" is a different test: the union is non-empty for any app member, so a separate check is needed.
4. **Membership is defined inconsistently.** Join and `/token` treat "member" as having an app-scope role. An invited user who never joined would hold only org roles. Today that user gets `not_member` without `org_id` but succeeds with `org_id`. This has to be decided, not left to happen by accident.
5. **Org-owner grants depend on a private method.** The owner/invite grant path would naturally reuse `AssignmentService._grant_locked` (private; `membership/service.py:106` already calls it). `_guard_privileged` requires `admin:*` for any role holding an `is_admin` permission, so a `SYSTEM`/end-user actor granting such an owner role would fail. The owner role must never hold `is_admin` permissions.
6. **ADR-0001 §6 says Identity "does not interpret" app catalogs.** `<app>:org:create` (and an invite permission) makes Identity interpret app permission keys. That is a new reserved-key convention with no ADR yet.
7. **`organizations.slug` is globally unique.** Under OQ1's later `app_key`, uniqueness would naturally become `(app_key, slug)`. That is not purely additive: it is a constraint change, still non-breaking for the API.
8. **The audit log is ready.** It already has `org_id` (snapshot, no FK; ADR-0009) and `AuditTarget.ORGANIZATION`. S8 says "org membership events come with USR-06".
9. **The proposed accept path leaks the secret.** `POST /api/v1/auth/invitations/{token}/accept` (§4) puts a secret in the URL path, where it ends up in access logs and proxies. `verify-email` and `password/reset` take `{token}` in the body.
10. **The ticket has five areas, two new tables/columns, an FK migration with a data precondition, about four cookie endpoints, email sending, a token change and new audit actions.** That is too large for one loop under the "max 2 reworks" rule.

### Recommendations

- **R-A, module boundaries (AC1, D15, R1, R7).** Keep `app/modules/organizations/` where it is. It is the CLAUDE.md reference module, and moving it would be churn with no behavior gain. Implement D15 as an ownership change:
  - update docs (`identity-architecture.md` §3, spec §5.1) to say the table is owned by Identity;
  - add `created_by → users ON DELETE SET NULL`, nullable;
  - no table rename;
  - add a cookie router `organizations/auth_router.py` mounted under `/api/v1/auth/orgs` next to the existing admin `router.py`;
  - put invitations in a new module `app/modules/invitations/` (models, repository, service, router), mirroring `email_tokens`.

  Promote `_grant_locked` to a public `AssignmentService.grant_system(...)` (or `grant_in_org`) instead of calling a private method across modules. Dependency direction: tokens → organizations repo + rbac authz; organizations service → rbac service; never rbac → organizations service. The existing repo-level import is fine. Ask the owner to confirm that "move" means ownership plus docs (see Open questions).
- **R-B, FK and delete semantics (AC2, R2, R9).** Make the FK `ON DELETE RESTRICT` and have admin delete return 409 `org_has_members`, following the `role_has_assignments` precedent; staff suspend instead. CASCADE would silently drop memberships with no per-row audit. The migration should pre-check for orphan `org_id`s and abort with a clear message rather than delete them, so `downgrade()` stays honest. "No org" stays NULL, enforced by the existing `NULLS NOT DISTINCT` unique.
- **R-C, platform-wide orgs (OQ1).** Keep orgs platform-wide. Do not add `app_key` now. Scope per app through role assignments: "my orgs for app X" = distinct orgs where the user has a non-expired assignment of a role whose `app_id = X`. Record in the plan that a later `app_key` means a nullable column, a backfill from the creating app, and moving slug uniqueness to `(app_key, slug)`. Consider letting the server generate end-user slugs (or prefix them) to limit global-namespace squatting.
- **R-D, org owner role per app (AC3, D9-style "policy is data").** Add `apps.org_owner_role_id NULL`, with a composite same-app FK like `fk_apps_default_role_same_app`. Avoid a hard-coded key such as `<app>:org-owner`. Reject configuring a role that holds `is_admin` permissions (same rule `join_default_role` applies). If `org_mode = required` and no owner role is set, `POST /auth/orgs` returns 403 `org_create_unavailable`. Expose it on the admin apps PATCH; USR-05 manifest can declare it later.
- **R-E, reserved permissions (AC3, AC4, ADR-0001 §6).** Write a short ADR (or an ADR-0003 amendment) reserving `<app>:org:create` (checked at app scope) and `<app>:org:invite` (checked in the org, via `effective_permissions(user, app, org_id)` with `permits`). Identity interprets only these keys.
- **R-F, invitation shape (AC4, R11, S6).**
  - Table `org_invitations`: id, org_id FK CASCADE, app_id, role_id (same-app composite FK), email_hmac (32 bytes), token_hash unique, invited_by, expires_at, used_at, revoked_at, created_at.
  - Accept: `POST /api/v1/auth/invitations/accept {token, account_id}`. The token goes in the body, not the path (deviation from §4, so update the doc).
  - Accept is cookie + Origin + `X-MM-App` = invitation's app. The account's normalized email HMAC must equal `email_hmac`, the email must be verified, the org must be active, the token must be unexpired and unused. Consume and grant in one transaction under the app lock.
  - Invitable roles: same app, no `is_admin` permission, and the role's permissions must be a subset of the inviter's effective permissions in that org. Otherwise an inviter could escalate, including inviting someone as owner.
  - Limits: cap pending invitations per org, rate-limit per inviter, reuse `email_send_log` with a new purpose `invite` (CHECK change). Same 404-style error for unknown/expired/used tokens.
- **R-G, token (AC5, D7).**
  - With `org_id`: require an active org plus at least one non-expired assignment of this app's roles with `org_id = X`. Otherwise 403 `org_access_denied`, the same code for unknown, suspended and not-a-member, so org ids can't be enumerated.
  - `org` claim = org UUID as a string; no slug (slugs are mutable).
  - `roles` = `effective_roles(user, app, org_id)`.
  - `org_mode = none` with `org_id` → keep the existing 422 `org_not_supported`. Update the `consumers.md` row and the ADR-0005 note.
  - Resolve Finding 4 explicitly. Preferred: invite-accept also performs the app join in the same transaction, following `join_policy` (a closed app refuses and the invitation is not consumed). "Member" then keeps meaning "has an app-scope role" everywhere.
- **R-H, audit (AC6, R4, S8).** New actions:
  - `organization.created` with actor `EndUser`;
  - `org_invitation.created`, `org_invitation.accepted`;
  - `role_assignment.granted` with `org_id`.

  No emails or hashes in `changes`; record `role_key` and `org_id` only.
- **R-I, split the ticket (R7, rework budget).**
  - **USR-06a, org foundation and org tokens:** ownership docs, `created_by`, the FK + RESTRICT/409 migration, `apps.org_owner_role_id` + admin PATCH, `POST /auth/orgs` (owner grant), `GET /auth/orgs?account_id=&app=`, `/token` with `org_id`, audit. This unblocks calechip with single-owner orgs (staff can already add members through admin assignments).
  - **USR-06b, invitations:** table, create, accept (+ implicit join), email template, limits, audit.
  - **USR-06c, org member management (follow-up, needs owner scope):** list members, remove/leave, revoke invitation, last-owner guard, ownership transfer, end-user org rename/suspend.

### Risks

- **Abuse:** open sign-up plus open join means any verified user can create unlimited orgs and send invitation emails, which strains the Gmail daily limit (§7). Mitigate with per-user org-creation caps and per-inviter/IP limits.
- **Privilege escalation:** an inviter assigning a role broader than their own, or an owner role holding `is_admin`. Mitigated by the R-D/R-F checks; this needs dedicated tests.
- **Revocation lag:** org removal or suspension takes effect only at token expiry (~15 min, ADR-0001 consequence). Document it for calechip.
- **Migration:** the FK fails on orphan `org_id`s in production. Run the pre-check query before the release.
- **Behavior change:** admin delete of an org with members now returns 409. Within R6/R9 codes and precedent, but tell admin-pan.
- **Platform-wide slugs** create cross-app collisions and squatting, and a later move to per-app uniqueness is a constraint migration.
- **Invitation links** need a target app URL. `apps.origins` is a list with no canonical "invite landing URL".

### Open questions (owner)

1. D15 "move to the Identity module": is ownership + docs enough (keep the `app/modules/organizations/` folder, no rename)? Recommended: yes.
2. OQ1: confirm platform-wide for v1. Should end users choose slugs, or should the server generate them?
3. Org owner role: `apps.org_owner_role_id` (data) or a fixed key convention? Who configures it for calechip (admin PATCH, CLI, or USR-05 manifest)?
4. Does accepting an invitation also join the app, and what happens with `join_policy = closed`/`approval`?
5. Invitation email: store only the HMAC (no "pending invitations" list showing emails) or the plain email? Which URL does the invite link point to (a new `apps.invite_url`, or a `return_url` from the app checked against `origins`)?
6. Admin delete of an org with members: 409 (recommended) or cascade?
7. Is the split into USR-06a / 06b / 06c acceptable, and is 06c (member management, last-owner guard) in v1 scope at all? The spec does not cover it, so per CLAUDE.md the spec wins and it needs a spec line first.

Relevant files:
- /home/user/org-admin-api/work/tickets/USR-06/ticket.md
- /home/user/org-admin-api/docs/identity-architecture.md
- /home/user/org-admin-api/docs/adr/0001-multi-app-authorization.md
- /home/user/org-admin-api/docs/adr/0003-rbac-keys-and-matching.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/app/modules/organizations/models.py
- /home/user/org-admin-api/app/modules/organizations/service.py
- /home/user/org-admin-api/app/modules/rbac/models.py
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/tokens/errors.py
- /home/user/org-admin-api/app/modules/membership/service.py
- /home/user/org-admin-api/app/modules/auth/models.py
- /home/user/org-admin-api/app/modules/audit/models.py
