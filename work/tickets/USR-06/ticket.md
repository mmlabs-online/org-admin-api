# USR-06: Organizations and org-aware tokens (first consumer: calechip)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Apps with `org_mode = required` (calechip first) can work inside an organization: users create orgs (becoming owner) or are invited, and tokens carry the selected org and the roles held in it.

## Scope
the existing `organizations` table moved to the Identity module (+ `created_by`; D15); FK from `role_assignments.org_id`; org endpoints (create, list mine per app, invite, accept); org owner role per app; `/api/v1/auth/token` with `org_id`.

## Spec reference
§5.3 Membership, §5.1 (reconcile), §5.8 Organizations; identity-architecture.md §3–§4; decision D10; OQ1

## Acceptance criteria
- [ ] The existing `organizations` table (ADM-001) moves to the Identity module (D15): add `created_by` (nullable for existing rows), keep `description` and `status`; existing data kept; migration reversible; the admin CRUD endpoints keep working (R9 no breaking change)
- [ ] `role_assignments.org_id` FK to organizations; "no org" stays NULL (never 0)
- [ ] `POST /api/v1/auth/orgs` requires the app-scope permission `<app>:org:create` (granted by joining, e.g. `calechip:org:create`); creator gets the app's org owner role in that org
- [ ] Invitations: create (by a member with the right org permission), accept by the invited verified email; expiring, single-use, stored hashed
- [ ] `/api/v1/auth/token` with `org_id`: user must hold a role in that org for this app; token `org` = org id, `roles` = app-scope roles + roles in that org; for `org_mode = required` apps, no `org_id` gives only app-scope roles (enough to create/select an org)
- [ ] Org events emitted to the audit log (ADM-004)
- [ ] Tests: create org → owner role; invite/accept; token roles with/without org; user without a role in that org → 403; org-less app ignores `org_id` (422)

## Dependencies
USR-03b, USR-03c (token endpoint), ADM-004 (audit), USR-04 (verified email for invitations).

## Notes / answers from owner
- New ticket (2026-11-11). OQ1 (platform-wide vs per-app orgs) open; working assumption platform-wide, design so `organizations.app_key` can be added later.
