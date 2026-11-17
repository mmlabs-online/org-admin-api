USR-06 consult (api-platform-engineer, read-only). Based on the ticket, identity-architecture.md §3-§4/D7/D10/D15, tokens/service.py, organizations/router.py, rbac models and service, and tests/test_route_auth_classes.py. I did not read every module (not read: auth/router.py bodies, membership service, audit actions, consumers.md in full).

## Findings
1. **Admin CRUD.** `/api/v1/organizations` is Bearer (`current_admin` + `admin:organizations:read|write`). It is in the `BEARER` map of test_route_auth_classes. `OrganizationRead`, `OrganizationCreate` and `OrganizationUpdate` are public contract (R9, no breaking changes in v1).
2. **Token endpoint.** `TokenService.issue` currently rejects any `org_id` with 422 `org_not_supported`, and the error docstring says "until USR-06". `AuthzService.effective_roles(user, app, org_id)` already merges app-scope and org roles. `TokenRequest.org_id` already exists.
3. **Org grants.** `RBACService._grant_locked` checks `app.org_mode == NONE` (422 `org_id_not_allowed`) and `org_exists_key_share` (422 `unknown_org`). The `RoleAssignment.org_id` FK is deliberately left for USR-06 (models.py:185).
4. **Auth classes.** The cookie class is a hard-coded `COOKIE` set in the test, with a "no bearer guard" check and a runtime sweep expecting 400 `app_header_required` then 401 `session_invalid` for every route in `COOKIE`.
5. **Planned endpoints** (architecture §4):
   - `POST /auth/orgs {account_id, name, slug}`
   - `GET /auth/orgs?account_id=&app=`
   - `POST /auth/orgs/{id}/invitations {email, role}`
   - `POST /auth/invitations/{token}/accept`
6. **Audit.** The `audit_log` table and `actions.py` exist (ADM-004), and the existing `test_audit_coverage` probably enumerates mutating routes. Check whether it needs the new cookie routes registered.
7. **Docs.** `docs/consumers.md` line 128 says `org_id` is "not supported until USR-06", and line 312 says "`org` arrives with USR-06". Both must be updated.

## Recommendations

### Endpoint shapes (all cookie class: `mm_device` cookie + Origin check + `X-MM-App`; app is the caller from the header)

**`POST /api/v1/auth/orgs`**
- Body: `{account_id, name, slug, description?}`, `extra=forbid`.
- Response: 201 `OrgRead {id, name, slug, description, status, created_at, updated_at}`.
- Also return `roles: [owner role key]` so the SPA can request a token immediately, or at least document that it must call `/auth/token` with `org_id` next.
- Share the schema field validators with the admin `OrganizationCreate` (slug regex).
- Errors:
  - 401 `session_invalid`: bad device or account.
  - 403 `email_unverified`: reuse the token-endpoint error.
  - 403 `not_member`: the user has no role in the calling app.
  - 403 `permission_denied`: the user lacks `<app>:org:create`.
  - 409 `conflict`: slug taken (a global unique constraint, as the admin API does).
  - 422 `validation_error`.
  - 422 `org_not_supported`: the calling app has `org_mode=none`. Keep this existing code, but change its meaning to "app does not use orgs".
- The org owner role per app needs a defined key (e.g. `<app>:org-owner`, seeded for `org_mode=required` apps; see Open Questions). Create the org and the assignment in one transaction, with audit entries in the same transaction (R4).

**`GET /api/v1/auth/orgs?account_id=<uuid>`**
- Drop the `app=` query param from the architecture doc. The app is already known from `X-MM-App`, and a second app param invites a cross-app lookup.
- Response: `Page[MyOrg]` (limit ≤ 100, offset, `total`), ordered `name, id` for stable paging.
- `MyOrg = {id, name, slug, roles: [role keys held in this org for this app]}`. The `roles` field lets the UI choose the org without a token round trip.
- Do not return `status` or `description` unless needed. Return only orgs where the account's user holds an unexpired role for this app.
- An `org_mode=none` app gets 422 `org_not_supported`, not an empty page.
- Suspended orgs: still list them, but with `status`. Decide whether the token endpoint refuses them (see Open Questions).

**`POST /api/v1/auth/orgs/{org_id}/invitations`**
- Body: `{account_id, email, role}`. `role` is the role key or name within the calling app, and must be an org-assignable role for that app.
- Authorization: a member of the org with a declared org permission, e.g. `<app>:org:invite`. Check it via `effective_permission_keys(user, app, org_id)`, which already takes `org_id`.
- Response: 201 `InvitationRead {id, org_id, email, role, expires_at, status}`. Never return the raw token. It goes only in the email (R11, hashed at rest).
- 403 for a caller without the permission.
- 404 when the org does not exist or the caller is not a member of it. Use 404 rather than 403 to avoid an org-existence oracle.
- 409 `already_member` or `invitation_pending`. Be careful: revealing membership by email leaks account existence. Prefer to silently re-issue or replace the pending invitation, and return the same 201.
- 422 for an invalid role, or a role not assignable by the inviter (no privilege escalation: the inviter cannot grant a role above their own).
- Rate-limit per inviter and per org, and cap pending invitations per org.

**`POST /api/v1/auth/invitations/{token}/accept`**
- Cookie class.
- Do NOT put the token in the path. Paths end up in access logs and referrers (R11, "never log tokens"). Prefer `POST /auth/invitations/accept {account_id, token}`. If the architecture doc's path is kept, the planner must justify it and scrub the logs. I recommend changing the doc, since it is internal.
- Body: `{account_id, token}`.
- Requirements: the signed-in account's verified email equals the invited email. Compare normalized values.
- Response: 200 `{org_id, roles}`.
- Errors:
  - 422 `token_invalid`: unknown, expired, used or revoked. One code for all, as in password reset and verify-email.
  - 403 `email_mismatch`: the verified email differs. Consider folding this into `token_invalid`, to avoid confirming that the token is valid.
  - 403 `email_unverified`.
- Single-use via an atomic `UPDATE ... WHERE used_at IS NULL AND expires_at > now() RETURNING`.
- Accepting also creates the role assignment (idempotent if it already exists) and writes an audit entry.
- A decision is also needed on whether accepting auto-joins the app (see Open Questions).

### Token endpoint (`POST /auth/token` with `org_id`)

| Case | Status / code |
|---|---|
| App `org_mode=none` and `org_id` given | 422 `org_not_supported`, `details=[{field: org_id, reason: not_supported}]` (keep the existing code, as the ticket says 422) |
| App `org_mode=required`, no `org_id` | 200, app-scope roles only, no `org` claim |
| `org_id` given, org does not exist | Same response as no role in the org: 403 `not_org_member` |
| `org_id` given, user has no unexpired role in that org for this app | 403 `not_org_member` (new code, a sibling of `not_member`, with `details={}`) |
| User not a member of the app at all | Keep 403 `not_member` first (existing check order) |

- Do not use 404 or 422 for the org lookup, to avoid an org-existence oracle. Use one 403 for both unknown org and no membership.
- The check order stays: audience, org_mode, session, user, verified, app membership, then org membership.
- The `org` claim is `str(org_id)` and appears only when `org_id` is given.
- The suspended-org question: refuse the token with 403 `org_suspended`, or issue it. This is a decision for the planner.
- `OrgNotSupportedError` docstring ("until USR-06") needs updating.

### Keeping the admin CRUD non-breaking (R9)
- Moving the table to the Identity module must not change `OrganizationRead/Create/Update` fields, status codes, or URLs.
- `created_by` is nullable and optional in the admin read (additive). If it is exposed, add it as a new optional response field only. Do not make it required in `OrganizationCreate`. Admin-created orgs get `created_by = NULL`, or the admin principal id (decide).
- Keep the slug-uniqueness 409 code unchanged.
- Admin DELETE of an org that now has `role_assignments` or invitations: define the behavior (FK `ON DELETE CASCADE` vs 409). Today the admin delete succeeds, so a new 409 would be a behavior change, which strictly is breaking. Recommend cascading the assignments and invitations, with an audit entry, which preserves the 204.
- Keep the module path stable or re-export. The reference module is `app/modules/organizations`, so moving it affects imports only, not the API.
- Add a regression test that the admin CRUD OpenAPI schema is unchanged apart from the additive field. Also check that ADM-013's OpenAPI snapshot, if it exists, is regenerated and diffed.

### R3 classification and test_route_auth_classes
- Add the 4 new routes to the `COOKIE` set in tests/test_route_auth_classes.py:
  - `("POST", V1/auth/orgs)`
  - `("GET", V1/auth/orgs)`
  - `("POST", V1/auth/orgs/{org_id}/invitations)`
  - `("POST", V1/auth/invitations/accept)`, or the path variant if the token stays in the path
- Extend `_fill` with any new path param names (`org_id` is already handled).
- The runtime sweep `test_cookie_routes_not_bearer_guarded` sends `{}` to POST routes and expects 400 `app_header_required` without the header, and 401 `session_invalid` with only the header. The route dependency order must be AppContext, then Device, then body validation, so an empty body does not 422 first. This matches the existing pattern in `auth/router.py` (AppContextDep before DeviceDep); copy it.
- `GET /auth/orgs` needs `account_id` as a query param. The sweep sends no params, so it would 422 before the device check unless `account_id` is validated after the dependencies, or the sweep supplies it. Prefer making the sweep fill it, or using a dependency-based approach.
- Do NOT add any of these to `SELF_SCOPED` (that list forbids `/auth/` paths and path/query/body input).
- Permissions: the cookie routes do not use `require_permission("admin:…")`. The `<app>:org:*` permissions are app-scope permissions evaluated in the service via `AuthzService`. They must not be marked with `x-permission` in OpenAPI, because `test_openapi_bearer_scheme` asserts no `x-permission` outside BEARER.
- Add the app permissions to the role catalog seed. Confirm the key format against ADR-0003 (`<app>:org:create`). Per ADR-0003 matching rules, check whether `calechip:org:create` is in a join default role (the ticket says "granted by joining").
- CORS scope: the new routes sit under `/api/v1/auth/`, so they fall in the cookie CORS scope automatically. No change to the matcher; test_cors_matcher already asserts the disjointness.

### Pagination and conventions (R9)
- Use `PageDep` and `Page[T]` for `GET /auth/orgs`, and for any future invitation listing.
- Not needed: a single-document response.
- Dates are ISO 8601 `timestamptz`. IDs are UUIDs. Fields are snake_case.
- Do not add a list-invitations endpoint in this ticket (scope, R7). Note it as a follow-up, since inviters will want to see and revoke pending invitations. Revoke is also not in scope; log it as a follow-up.

### Error codes (R6: only 201, 204, 401, 403, 404, 409, 422)
| Condition | Status | Code |
|---|---|---|
| Missing app header | 400 | `app_header_required`, already present |
| Slug taken | 409 | existing conflict code |
| No `<app>:org:create` | 403 | `permission_denied`; reuse the existing class |
| No role in org (token) | 403 | `not_org_member` (new) |
| Invalid, expired or used invitation | 422 | `token_invalid` |
| Wrong role or email on invitation | 422 | `validation_error` with details |
| App does not use orgs | 422 | `org_not_supported` |
- R6 lists no 400, yet `app_header_required` is 400 in the sweep. Verify what the existing handler really returns before relying on this table.
- All errors use `{"error": {"code","message","details"}}`.

### OpenAPI (ADM-013) and docs
- Each new route needs: a summary, request/response models, the `DeviceCookieDoc` security annotation as in auth/router.py, an `X-MM-App` header doc, and documented error responses (401, 403, 404, 409, 422).
- Register new schemas (`OrgCreate`, `OrgRead`, `MyOrg`, `InvitationCreate`, `InvitationRead`, `InvitationAccept`) with explicit names. Avoid name clashes with the admin `OrganizationRead`.
- Update `docs/consumers.md`:
  - lines 128 and 312: replace the "not supported until USR-06" text.
  - Add a calechip-oriented flow: join, `POST /auth/orgs`, `GET /auth/orgs`, `getToken(audience, {orgId})`.
  - Add the `org` claim description and the new 403 `not_org_member`.
  - Add the invitation email link flow, and the invitation page origin.
  - Add the token client behavior: cache per `(audience, orgId)`; on 403 `not_org_member`, re-list orgs.
- Update `docs/identity-architecture.md` §4 (the `app=` param removal, the accept path) and the OQ1 note.
- Spec §5.1/§5.3/§5.8 reconcile, as the ticket says. A follow-up ADR may be warranted for the invitation design.

## Risks
- **Org-existence and email oracles** on token (404 vs 403), invitation create (409), and accept (mismatch vs invalid). Use uniform codes.
- **Invitation token in a URL path** is logged. Use a body field.
- **Privilege escalation via invite role**: the inviter must not be able to grant roles beyond their own, or a super-admin or admin-app role. Restrict to org-assignable roles for the calling app.
- **Admin DELETE of an org with an FK** changes behavior unless cascaded (see above).
- **Migration**: `created_by` nullable; the FK from `role_assignments.org_id` may fail on existing rows with orphan org ids, so the migration must validate or clean first, and `downgrade()` must work (R2). A global `slug` unique constraint is shared with the admin API and is a platform-wide assumption (OQ1). Keep `app_key` additive later.
- **Org status**: suspended orgs and tokens are undefined today. Also what happens to tokens already issued (short TTL ~15 min bounds it).
- **Rate limiting** is missing on create and invite (spam, mail bombing). Apply the existing per-IP and per-user limits (R11).
- **Mail sending** for invitations reuses the email dispatcher and link-origin logic (USR-04). The link origin must come from the allow-listed `apps.origins`, not a request header.
- **Owner lockout**: the last owner can be removed through admin role assignments. It is out of scope; note it as a follow-up.
- **Test_audit_coverage** may fail until the new mutating routes have audit actions registered.

## Open questions for the planner/owner
1. What is the owner role key, and how is it seeded for `org_mode=required` apps? Is it created by the calechip registration, by the `app register` CLI, or by migration? Which permissions does it carry?
2. Does accepting an invitation auto-join the user to the app (app-scope role), or only grant the org role? The `GET /auth/orgs` and token "app member first" check suggest the user must already be an app member. Recommend: accept grants the org role and also an app-scope default role if the app is open, otherwise requires an explicit join.
3. Suspended orgs: refuse tokens (403 `org_suspended`) or ignore?
4. Which org permission gates invitations (`<app>:org:invite`?), and which roles are invitable?
5. Invitation expiry (suggest 7 days), and the pending cap per org.
6. Accept path: token in body (recommended) vs path (the architecture doc). Needs the doc updated.
7. Should `created_by` be exposed in the admin `OrganizationRead`? (additive, optional)
8. Is `app=` in `GET /auth/orgs` dropped in favor of `X-MM-App`? Recommend yes.
9. Admin DELETE of an org with assignments: cascade or 409? Cascade keeps R9.
10. Is a 403 `not_org_member` acceptable as a new code under R6, which lists only status codes?
