# USR-06c: Org-scoped roles in tokens and default-role hygiene for org apps

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
App-scope roles must stop acting as roles inside every org: an app with `org_mode = required` can tell, from the access token, which roles the user holds in the selected org, and the default role of such an app is never re-widened with org-level permissions. This replaces the USR-06b fail-closed guard as the real fix for USR-06a security finding S1, and must land before USR-05 (manifest push).

## Spec reference
§5.8 (per-app access tokens, claims: `roles`, `org`), §5.3 (default role holds the app's permissions; policy is data); identity-architecture.md D7, D9, D10; ADR-0013, ADR-0014; USR-06 security-review.md S1 and USR-06b security-review.md follow-ups.
**Possible spec change:** if the chosen design adds a token claim (e.g. `org_roles`), spec §5.8's claims list must gain a line, which needs the owner's approval (the spec wins).

## Acceptance criteria
- [ ] For an org token, a consumer can determine the roles the user holds **in that org** (org-exact) separately from app-scope roles, without breaking existing v1 consumers of `roles` (R9); the chosen design (e.g. additive `org_roles` claim, or a role/permission scope marker) is recorded in an ADR and in the docs.
- [ ] For `org_mode = required` apps, building or rebuilding the default role (`build_default_role`, seed, app creation, manifest) never adds org-level permissions to it; existing behaviour for `org_mode = none` apps is unchanged.
- [ ] `set_default_role_if_null` (and any other write path) can no longer make `default_role_id` equal `org_owner_role_id` (service guard and/or DB CHECK, with a reversible migration if a CHECK is added).
- [ ] The USR-06b guard (`org_invite_unavailable`) is kept, relaxed or removed consistently with the new design, and documented (ADR-0014).
- [ ] `docs/consumers.md` tells org apps which claim/rule to use for org-scoped authorization; `mmlabs-auth` impact is listed as a follow-up.
- [ ] Tests: org token exposes org-exact roles separately; app-scope-only role not reported as an org role; default-role build for an org app excludes org-level permissions; owner≠default enforced on every write path; invitations still work for a correctly configured org app.

## Dependencies
Depends on USR-06 (06a), USR-06b. Must land before USR-05.

## Notes / answers from owner
- 2026-11-17 (owner): create USR-06c and run it now.
- 2026-11-17 (owner, answering the consults' blocking questions):
  1. **Approved:** add an additive `org_roles` claim to org tokens (org-exact role keys, present only when `org` is present); `roles` stays the D10 union. Spec §5.8 claims line may be amended accordingly.
  2. **Approved:** classify org-level permissions with a data marker `permissions.scope` (`app` | `org`); the default role of an `org_mode = required` app only takes `scope = 'app'` permissions. Spec §5.3 default-role wording may be amended accordingly.
