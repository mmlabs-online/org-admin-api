# USR-02: Apps registry and multi-app RBAC

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
One authorization model for every mmlabs app: registered apps, namespaced permissions, app-scoped roles, and role assignments per user (optionally limited to one organization).

## Spec reference
§5.3 Authorization — multi-app RBAC; §5.5 (merged); ADR-0001 §2–§4

## Acceptance criteria
- [ ] Tables + migration exactly as ADR-0001 §4: `apps`, `permissions`, `roles`, `role_permissions`, `role_assignments` (org_id nullable, expires_at nullable), with the unique constraints listed; downgrade works
- [ ] A role can only hold permissions of its own app (enforced in the service; 422 otherwise); permission keys match `<app>:<resource>:<action>` or `<app>:*`
- [ ] Staff endpoints: CRUD apps; list/create permissions and roles of an app; set a role's permissions; assign/revoke a role for a user (app-wide or for one org); list a user's roles per app
- [ ] Service function `effective_permissions(user_id, app_key, org_id=None)` resolving roles → permissions (wildcard included, expired assignments ignored) — used later by USR-03 and ADM-002
- [ ] Seed/bootstrap: app `admin`, role `admin:super-admin` with `admin:*`, and a way to grant it to a first user from config/CLI without editing the DB by hand
- [ ] Tests: cross-app permission rejected, duplicate keys 409, assignment app-wide vs org-scoped, expired assignment ignored, wildcard resolution, 404s

## Notes / answers from owner
- 2026-11-11: merges former ADM-003 (staff RBAC) and ADM-005 (products registry) into one generic model (ADR-0001).
- Depends on USR-01 (users). Endpoints are unauthenticated until ADM-002; note the `admin:*` permission each needs (R3).
