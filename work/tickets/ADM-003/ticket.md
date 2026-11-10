# ADM-003: RBAC: roles, permissions and assignments for staff

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Staff access follows least privilege: each `/api/v1` endpoint requires a named permission granted through roles.

## Spec reference
§5.3 RBAC (depends on ADM-002)

## Acceptance criteria
- [ ] Tables and migration for roles, permissions and staff-role assignments
- [ ] A reusable dependency `require_permission("<name>")` returns 403 in the error envelope when missing
- [ ] Every existing `/api/v1` endpoint declares a permission (e.g. `organizations:read`, `organizations:write`)
- [ ] A bootstrap super-admin can be created from configuration/CLI without editing the DB by hand
- [ ] Endpoints to list roles and assign/revoke roles for a staff member, with tests (200/403/404)

## Notes / answers from owner
