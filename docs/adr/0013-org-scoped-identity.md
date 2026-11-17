# ADR-0013: Org-scoped identity (organizations, owner role, org tokens)

Status: Accepted (owner, 2026-11-17: "defaults OK" on the USR-06 plan questions)
Date: 2026-11-17

## Context
Apps with `org_mode = required` (calechip first) need users to create organizations and get
tokens scoped to one. ADR-0001 §6 says Identity does not interpret an app's permission catalog;
creating an org needs one convention that does.

## Decision
1. **Reserved permission key `<app>:org:create`.** `POST /api/v1/auth/orgs` requires it, checked
   against **app-scope** permissions only (`org_id` NULL), never the app+org union: a permission
   held inside some org must not allow creating new orgs. `<app>:org:invite` (USR-06b, ADR-0014)
   is checked in the org, org-exact.
2. **Org owner role is data:** `apps.org_owner_role_id` (nullable, same-app composite FK, only
   when `org_mode = required`, never on `admin`, never a role holding `is_admin` permissions),
   set by staff via `PATCH /api/v1/apps/{key}`. It follows `default_role_id` (D9: policy is
   data); the same guards apply (role delete and `is_admin` changes that would break it are
   refused). The creator gets it **in the new org**, granted by `system:org_create`.
3. **Org-exact reads for decisions.** Token `org_id` requires an unexpired role of this app in
   exactly that org and an `active` org; unknown, suspended and not-a-member orgs share one 403
   `org_access_denied` (no existence oracle). `effective_*` with an org (the union) is never used
   to authorize anything on its own.
4. **RESTRICT, not CASCADE.** `role_assignments.org_id -> organizations.id ON DELETE RESTRICT`;
   admin delete of an org with any assignment answers 409 `org_has_assignments`. Cascading would
   drop grants without per-row audit. The migration aborts on orphan `org_id`s.
5. **Platform-wide orgs** (spec OQ1 working assumption stays open). Adding `organizations.app_key`
   later is additive: a nullable column, a backfill, and `(app_key, slug)` uniqueness.
6. **Token claim `org`** is the org UUID as a string, present only when requested.

## Consequences
- Staff must set `org_owner_role_id` and give the default role `<app>:org:create` (catalog data)
  before users can create orgs; until then 403 `org_create_unavailable`.
- `org_owner_role_id` and `default_role_id` must differ (422 `same_as_default_role`).
  `build_default_role` / the manifest re-widens `<app>:member` to all non-admin permissions, so for
  `org_mode = required` apps org-level permissions must not be declared as non-admin app
  permissions (or the default role must be maintained by hand).
- Admin UI (`admin-pan`) must handle the new 409 on org delete.
- Per-IP limits (`ORG_CREATE_*`, `ORGS_LIST_*`) and a per-user lifetime cap
  (`ORG_CREATE_MAX_PER_USER`) bound abuse.
