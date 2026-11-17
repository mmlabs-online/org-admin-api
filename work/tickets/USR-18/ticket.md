# USR-18: Join requests — richer decision response

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Areas | api, auth |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
admin-pan can tell whether approving a join request actually granted a role, and show who decided without resolving user ids.

## Spec reference
§5.3 Membership (`join_policy = approval`, D8–D9); USR-03g follow-ups.

## Acceptance criteria
- [ ] The approve response carries `granted_role_key: string | null` (null when nothing was granted, e.g. the user was already a member). Additive field, no breaking change (R9).
- [ ] Join-request reads carry a display value for the decider next to `decided_by` (display name, else masked email; never the full email, R11).
- [ ] OpenAPI documents both fields.
- [ ] Tests: approve that grants the default role, approve of an existing member (`null`), reject, decider display with and without display name.

## Dependencies
USR-03g. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- 2026-11-17 (owner): join-request follow-ups split into USR-18, USR-19, USR-20; self-approval policy not ticketed now.
