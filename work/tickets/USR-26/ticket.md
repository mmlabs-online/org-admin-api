# USR-26: Role definitions — assignment count, org-owner flag, stable reasons on 409 conflicts

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P3 |
| Areas | api, db |
| Attempts | 0 |
| Claimed by | claude@2026-11-27T03:59Z |
| Created | 2026-11-19 |
| PR | — |

## Goal
The admin-pan roles table (PAN-015b) can show holders, pre-disable Delete for a role with holders, flag the org-owner role, and show fixed translatable copy for each 409 instead of the raw server message.

## Spec reference
§5.3 Authorization (roles staff API). USR-16 follow-ups (`assignment_count` deferred in its plan D5; `is_org_owner`).

## Acceptance criteria
- [ ] `RoleDefinitionRead` gains `assignment_count` (on the detail at least; on the list if it costs no N+1). Its definition matches what the delete guard counts, so a role showing 0 can be deleted (USR-16 plan D5 noted the guard counts expired assignments too) — the planner fixes one definition and documents it.
- [ ] `RoleDefinitionRead` gains `is_org_owner` (`apps.org_owner_role_id == id`) next to `is_default`.
- [ ] `DELETE …/roles/{role_id}` 409 responses keep `code: conflict` and add `details: [{"reason": …}]` with `system_role`, `default_role`, `org_owner_role`, `role_has_assignments`, `changed_concurrently`.
- [ ] `PUT …/roles/{role_id}/permissions` 409s likewise: `default_role_admin_permission`, `org_owner_admin_permission`, `default_role_org_permission`, `super_admin_must_keep_wildcard`, `changed_concurrently`.
- [ ] Additive only (R9); OpenAPI documents the reasons. Tests for each reason and both new fields.

## Dependencies
USR-16, USR-17. Consumer: admin-pan PAN-015b. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-27 (Ming, chat): "duyệt hết; câu 1–24 theo mặc định; cho merge USR-28".
- Plan approved (2026-11-27, batch 2026-11-27-1); all blocking questions answered with the plan defaults.
