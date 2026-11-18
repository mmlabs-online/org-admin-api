# USR-17: Holders of a role — list assignments by role (admin Bearer)

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can see who holds a given role (the "who holds this role" sheet in admin-pan) without one call per user; today assignments can only be listed per user.

## Spec reference
§5.3 Authorization (users get roles per app, optionally per org); §5.8 Admin bullet (staff manage users' roles per app).

## Acceptance criteria
- [ ] A Bearer admin endpoint listing a role's assignments (path chosen at PLAN, e.g. `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`, consistent with USR-16), permission `admin:users:read` (or `admin:roles:assign` if the planner justifies it), declared in `x-permission`.
- [ ] Returns `Page[T]` (`limit` ≤ 100, `offset`), order `created_at DESC, id`. Each item has the `AssignmentRead` fields plus the holder's `user_id`, `email` and `display_name`, read without N+1.
- [ ] Filters: `active` (true/false; default includes expired rows marked `active=false`, the same computed `active` flag as the per-user list; that list has no `active` filter) and `org_id`.
- [ ] 404 for an unknown app or a role that does not belong to that app.
- [ ] Tests: 401, 403, 404 (unknown app, unknown role, role of another app), expired rows, org-scoped rows, each filter, paging.

## Dependencies
USR-16 (role listing / path shape), USR-02c (assignments). Consumer: admin-pan PAN-015c. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- The response exposes users' emails (PII, §8 Q6) to holders of the chosen permission, same as the per-user list and `GET /users` today.
