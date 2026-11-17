## Consult report (api-platform-engineer), USR-06c

I read the ticket, consumers.md, ADR-0013 and ADR-0014, the spec §5.8 claims line, `tokens/service.py`, `rbac/schemas.py`, `rbac/models.py`, and `build_default_role` / `set_default_role_if_null`. I changed nothing.

### Findings
1. **Current token.** For an org token, `roles` is `effective_roles(user, app, org_id)`. That is the union of app-scope roles and org-exact roles, so a consumer cannot tell them apart. `org` is the org UUID, present only when requested. Org access is already checked org-exact (`org_role_keys`, `org_permission_keys`), so the server-side authorization decisions in ADR-0013 and ADR-0014 are correct. The S1 problem exists only for consumers that read `roles` as "roles in this org".
2. **R9 classification.**
   - Adding a claim, for example `org_roles`, is additive and safe in v1. Consumers already ignore unknown claims, and the checklist in consumers.md does not forbid extra claims.
   - Narrowing `roles` to org-exact only on org tokens is breaking. App-scope roles would disappear from org tokens, which breaks any consumer that gates on them. Do not do it.
   - Renaming `roles` or changing its type is breaking.
3. **Spec line.** Spec §5.8 line 67 (the claims list) needs `org_roles` added, which needs the owner's approval. The ticket says so already.
4. **Where the default role is built.** `_build_for_app` assigns `list_permissions(app.id, is_admin=False)` to `<app>:member`. It is called from seed, app creation, `register` and the USR-05 manifest. The repo has no way to tell an org-level permission from an app-level one, so a marker is unavoidable for the "default role excludes org-level permissions" criterion. Deriving "org-level" from the owner role's permissions is fragile: it depends on ordering and on the owner role being a full superset.
5. **Owner and default can collide.** `set_default_role_if_null` only checks `default_role_id IS NULL`. Nothing stops `default_role_id` from becoming `org_owner_role_id` there. The service guard `same_as_default_role` covers PATCH only.

### Recommendations

**A. Token (AC1, R9).**
- Add `org_roles: [role keys]` to org tokens. Make it sorted, built from the existing `authz.org_role_keys` (org-exact, unexpired, org active), and put it next to `claims["org"]`.
- Omit the claim when the token has no `org`, mirroring `org`. Document that a missing `org_roles` means no org roles.
- Keep `roles` unchanged (the union) and document it as legacy-compatible and not to be used for org decisions.
- Consumer rule for `org_mode = required` apps:
  - Org-scoped authorization: map `org_roles` through the catalog.
  - App-level actions such as `org:create`: use a token without `org_id`.
- This also closes the residual risk in consumers.md (a staff-granted app-scope role carrying org permissions). Such a role is never in `org_roles`, so it cannot reach an org decision for a consumer that follows the rule.
- Reject a per-role scope marker as the token fix. The same role can be held in both scopes, and the claim has to be per assignment.
- Record the decision in a new ADR (0015 or an amendment to 0013). Include the alternatives rejected above.

**B. Admin API surface (AC2).**
- Add a marker on permissions, not roles. Follow the `is_admin` precedent: a boolean `is_org_level`, defaulting to false.
  - Alternative: an enum `scope` of `app` or `org`. Choose one. I lean to the boolean because it matches `is_admin` and R9 consistency.
- Database: `permissions.is_org_level boolean NOT NULL server_default false`, with a reversible migration. Add CHECKs:
  - `NOT (is_org_level AND is_admin)`.
  - `<app>:org:create` stays app-level. Enforce that in the service, since a CHECK on a key suffix is awkward.
- `PermissionCreate` and `PermissionUpdate` get an optional `is_org_level` (null or absent on create means false; explicit null on PATCH is rejected, like `is_admin`). `PermissionRead` gets `is_org_level`. These are additive.
- Flipping a permission to org-level while the default role holds it is a ripple:
  - Refuse with 422 and a field reason, for example `held_by_default_role`.
  - This follows ADR-0003 §9, where `is_admin` changes that would break the default role are refused.
  - Alternatively, auto-remove it from the default role, with an audit entry. I prefer refusing, because D9 says role content is data.
- Setting `is_org_level` is only meaningful when `app.org_mode = required`. Options: allow it on any app but ignore it for `none` apps, or refuse (422) on `org_mode = none` apps. I recommend allowing it. Refusing would make the `org_mode` PATCH order awkward.
- `RoleRead` and `RolePermissionsReplace` need no new fields. The PUT must refuse org-level permissions on the default role of an `org_mode = required` app (422), so the default role cannot be hand-widened either. Same reasoning as the ADR-0003 §9 guard for `is_admin`.
- Optional additive list filter: `GET /apps/{key}/permissions?is_org_level=`.
- Public catalog `GET /apps/{key}/roles` (D17): no change is needed. Consumers map `org_roles` to permissions through the existing role to permission lists.
  - Adding a field to `RoleCatalog` or `RoleCatalogEntry` changes the bytes, so every ETag changes once. That is harmless but pointless.
  - The model docstring says fields are append-only and in the ETag, so any addition must be appended at the end.
  - Recommend not touching the catalog.
- `build_default_role` and `_build_for_app`: when `app.org_mode == required`, use `list_permissions(is_admin=False, is_org_level=False)`. `removed` already reports permissions dropped from the role. Keep `none` apps identical (AC2).

**C. Owner is not default (AC3).**
- In the repository, change `set_default_role_if_null` to also require `App.org_owner_role_id.is_distinct_from(role_id)`.
- Add a service guard before it. `build_default_role` should either raise a 409 or 422 with a clear message, or return a flag. Do not silently leave the default NULL. The manifest must not 500 or hide the problem.
- Add a DB CHECK: `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id`.
  - The migration must pre-check existing rows and abort with a clear message.
  - Give it a working `downgrade()` (R2).
  - Run `alembic check`.
- Cover every write path with a test: PATCH, `set_default_role_if_null`, seed, register, and the org PATCH.

**D. S1 guard (AC4, ADR-0014 §11).**
- Replace the "no shared permission except `<app>:org:create`" check and its owner-superset precondition with a marker check. The test becomes: the default role holds no `is_org_level` permission.
- Keep the other conditions (`org_mode = required`, `join_policy = open`, default and owner roles set, no admin permission in the default role, default ≠ owner).
- Because consumers on `roles` are still exposed until they migrate, I recommend keeping the guard, now marker-based. State in the ADR the condition under which it could be dropped: `mmlabs-auth` ships `org_roles`.

**E. Docs (AC5).** In `docs/consumers.md`:
- Claims line (about line 373): add `org_roles`.
- The `org_mode = required` paragraph (about line 377): rewrite so org decisions use `org_roles` plus the `org` claim.
- Remove the "re-widening" warnings in the Organizations section (about lines 142-145).
- Rewrite the "S1 guard" paragraph under Invitations.
- Remove "until USR-06c" and the residual-risk sentence.
- Update ADR-0013 consequences and ADR-0014 §11 and consequences, and `docs/identity-architecture.md` (token claims and data model).
- Spec §5.8: add `org_roles` (owner approval).
- OpenAPI is generated by FastAPI. Check that the `PermissionCreate`, `PermissionUpdate` and `PermissionRead` field descriptions and the `/docs` output show `is_org_level`.
- There is no versioned OpenAPI snapshot in the repo. If there is no diff check, say so; do not invent one.

**F. `mmlabs-auth` (follow-up only, AC5).**
- Python lib:
  - Read the `org_roles` claim, and tolerate its absence (older server) and unknown claims.
  - Add an org-aware helper such as `require_org_permission()` that resolves `org_roles` through the cached catalog.
  - Deprecate or document that `require_permission()` over `roles` is app-scope only for org apps.
- TS client: expose `org_roles` in the typed claims.
- Library version bump, and update the pinned tag in consumers.md.
- Update the calechip integration.
- Order: server ships first (additive), then the library.

### Risks
- **Consumers that keep reading `roles`.** An org app that ignores the docs is still exposed to S1. The marker-based guard and default-role hygiene are the server-side mitigation. A residual risk remains for a staff-granted app-scope role carrying org-level permissions. That is mitigated by the staff-facing docs and the claim, not by the server.
- **Token size and TTL.** `org_roles` adds a few role keys. A role change reaches tokens within the TTL (15 minutes), same as `roles`.
- **Marker backfill.** Existing org-level permissions default to false (app-level). Staff must flag them before the marker-based guard and default-role exclusion mean anything. Existing calechip data needs an ops note; a migration cannot guess which permissions are org-level. Mitigation: after the migration, the marker-based guard on an app with no marked permissions would pass wrongly. Consider keeping the old overlap check alongside the marker check until staff have marked the permissions, or fail closed (`org_invite_unavailable`) when an `org_mode = required` app has zero marked permissions.
- **CHECK migration.** It fails if an existing row has default = owner. The pre-check must handle that. It is unlikely, since PATCH already refuses it.
- **Reserved keys.** `<app>:org:create` must stay app-level and `<app>:org:invite` org-level. A wrong marker breaks the creation flow, or makes the invite permission leak at app scope.
- **Scope creep.** R7: the audit entry for marker changes belongs under the existing permission-update audit, not a new event type. Anything else goes to follow-ups.

### Open questions for the planner and owner
1. Spec §5.8 gets an `org_roles` line: owner approval is needed.
2. Marker form: boolean `is_org_level` or enum `scope`? I lean to the boolean.
3. When a permission is flipped to org-level while the default role holds it: refuse (422) or auto-remove? I recommend refusing.
4. Keep the S1 guard (marker-based), relax it, or remove it? I recommend keeping it, marker-based, until `mmlabs-auth` supports `org_roles`.
5. When `build_default_role` meets default = owner: 409 or 422, or return a warning in `DefaultRoleResult`?
6. Fail closed when an `org_mode = required` app has no org-level permissions marked at all?
7. Is `org_roles` omitted or an empty list on tokens without `org`? I recommend omitted.

Files read: /home/user/org-admin-api/work/tickets/USR-06c/ticket.md, /home/user/org-admin-api/docs/consumers.md, /home/user/org-admin-api/docs/adr/0013-org-scoped-identity.md, /home/user/org-admin-api/docs/adr/0014-org-invitations.md, /home/user/org-admin-api/app/modules/tokens/service.py, /home/user/org-admin-api/app/modules/rbac/schemas.py, /home/user/org-admin-api/app/modules/rbac/service.py, /home/user/org-admin-api/app/modules/rbac/repository.py, /home/user/org-admin-api/app/modules/rbac/models.py.
