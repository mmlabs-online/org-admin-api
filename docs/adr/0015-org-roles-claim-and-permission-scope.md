# ADR-0015: `org_roles` claim and permission scope for org apps

Status: Accepted (owner, 2026-11-17: `org_roles` claim and `permissions.scope` marker approved)
Date: 2026-11-17
Amended by ADR-0030 (USR-38..40): the designated super-admin role takes `app` permissions only.

## Context
- S1 (USR-06 security review): an org token's `roles` is the union of app-scope roles and the
  roles held in the selected org (D10), so an app-scope role carrying org-level permissions
  reaches every org.
- N-2 (USR-06b): the USR-06b guard (default and owner roles share no permission) only held while
  the owner role was a superset of all org-level permissions.
- `build_default_role`, the manifest sync (USR-05) and `set_default_role_if_null` could re-widen
  the default role or make it equal to the org owner role.

## Decision
1. **`org_roles` claim.** An org token (`org` present) carries `org_roles`: the sorted role keys
   held in exactly that org (unexpired, org `active`, prefix-filtered). It is the result the
   403 `org_access_denied` gate already computes. A token without `org` has neither claim.
   `roles` is unchanged (the D10 union), so v1 consumers do not break (R9). Org apps authorize
   org data only from `org_roles` (mapped through the role catalog) and scope data by `org`.
2. **`permissions.scope`** (`app` | `org`, NOT NULL, default `app`, CHECK) is classification data.
   When omitted on create, a reserved key decides first (`<app>:org:create` is `app`,
   `<app>:org:invite` is `org`; an explicit contradiction is 422 `reserved_scope`), otherwise
   `org` on `org_mode = required` apps (fail closed) and `app` on others. On org apps, app to org
   is refused (409) while the default role holds the permission, and org to app needs `admin:*`. Creating a non-reserved `scope = app` permission on an
   `org_mode = required` app also needs `admin:*` (denied attempts are audited); `register-app`
   (system actor) is exempt.
   Scope changes are audited. Scope has no effect on `org_mode = none` apps.
3. **Build rule.** For `org_mode = required` apps the default-role build targets
   `is_admin = false AND scope = 'app'`; a rebuild removes `scope = 'org'` permissions. This
   covers `build_default_role`, app creation, `register-app` and the USR-05 manifest. A build is
   refused (409 `member_is_org_owner`) when the member role is the owner role. Other writes
   cannot re-widen it: PATCH `default_role_id` or `org_mode` (422 `role_has_org_permissions`)
   and the role permission PUT (409).
4. **Owner differs from default**, in three layers: `set_default_role_if_null` skips the owner
   role, the CHECK `ck_apps_default_role_not_org_owner`, and the 422 `same_as_default_role`.
5. **The ADR-0014 §11 guard stays** as defense in depth, restated by scope: the default role
   must hold no `scope = 'org'` permission. Removal criterion: every org-mode consumer pins an
   `mmlabs-auth` release that authorizes org data from `org_roles` only.
6. **Backfill** (`org_mode = required` apps only, deterministic): `org:create` is `app`,
   `org:invite` is `org`, a permission held by the default role and not by the owner role is
   `app`, everything else is `org`. The migration never writes `role_permissions` and aborts when
   an app already has default = owner.

## Rejected alternatives
- Redefining `roles` to be org-exact: breaks R9 and D10.
- Classifying by owner-role contents (N-2): depends on the owner role being complete.
- A role-level marker: roles mix scopes.
- A boolean `is_org_level`: the owner chose `scope`.

## Consequences
- The next member-role build narrows org apps' member roles; calechip has to be told.
- Residual risk (the designated super-admin role: ADR-0030): consumers still authorizing from `roles`; a staff-granted app-scope role that
  holds `org` permissions; an `org_mode` none to required flip does not reclassify permissions
  (staff mark them `org`); the reverse flip required to none is refused with 409 `org_mode_in_use`
  while org data exists (ADM-019).
- Follow-ups: `mmlabs-auth` support for `org_roles`; refuse app-scope grants of roles that hold
  `org` permissions on org apps; retire the guard; the deployment workflow input for
  `--org-permission`.
