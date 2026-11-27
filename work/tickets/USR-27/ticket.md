# USR-27: Role assignments — return role and org names

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P3 |
| Areas | api |
| Attempts | 0 |
| Claimed by | claude@2026-11-27T03:59Z |
| Created | 2026-11-19 |
| PR | — |

## Goal
The admin UI can name a role and its org scope without extra lookups: `AssignmentRead` (`GET /users/{id}/role-assignments`) and `RoleHolderRead` (`GET /apps/{key}/role-definitions/{role_id}/assignments`) carry `role_key` and `org_id` only.

## Spec reference
§5.3 Authorization; §5.8 Admin bullet (staff manage users' roles).

## Acceptance criteria
- [ ] `AssignmentRead` and `RoleHolderRead` gain `role_name` and `org_name` (nullable; null for app-scope assignments), read in the same query (no N+1). Additive (R9).
- [ ] Every endpoint returning those schemas fills them (per-user list, assignment create, role holders).
- [ ] Tests: names present, `org_name` null for app-scope rows; bounded-query test still passes.

## Dependencies
USR-02c, USR-17. Consumer: admin-pan PAN-015c. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
