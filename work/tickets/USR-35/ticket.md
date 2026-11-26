# USR-35: Users list filters `app` and `role_id`

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Size | S |
| Areas | api, db |
| Gate | plan-approval |
| Depends on | USR-34 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | claude@2026-11-26T09:28Z |
| PR | — |

## Goal
Staff find the users who hold any role of an app, or one specific role, from the users list. Requested by admin-pan for PAN-034 (users filters).

## Scope reference
- `docs/spec-v1.md` §5.15 Part 2 (owner, 2026-11-25; OQ76 answered 2026-11-26: unknown values → 422).

## Scope
- `GET /api/v1/users?app=<key>`: users with at least one active assignment of any role in that app (app scope or any org).
- `GET /api/v1/users?role_id=<uuid>`: users holding that role actively (app scope or any org).
- Combinable with each other, `email`, `status`, `include=access` and pagination; `total` counts distinct users.
- Unknown values → 422 `unprocessable` with `details[].reason` `unknown_app` (field `app`), `unknown_role` (field `role_id`), `role_not_in_app` (both given, mismatch). Malformed key / UUID → 422 as today. Disabled app and its roles are known values.
- Proposed default (spec): with `include=access`, `access` still shows all of the user's active access, not only the filtered app/role.

## Out of scope
- Other filters or sort orders; bulk actions (USR-36).

## Acceptance criteria
- [ ] `app` alone, `role_id` alone, and both together return only users with a matching active assignment; expired-only users are excluded; org-scope holders are included.
- [ ] Filters combine with `email`, `status` and pagination; `total` counts each user once even with several matching assignments.
- [ ] 422 with reasons `unknown_app`, `unknown_role`, `role_not_in_app`; 422 for malformed values; a disabled app is accepted.
- [ ] 401 without token, 403 without `admin:users:read`.
- [ ] OpenAPI documents both parameters and the 422 reasons.

## Technical notes
- Use `EXISTS` subqueries (not JOIN) so users are never duplicated; existing indexes `ix_role_assignments_role_id` and `uq_roles_app_id_key` suffice, no migration expected (architect consult).
- Can be bundled with USR-34 in one PR if the planner prefers (shared active-assignment predicate).

## Test plan
- API tests per criterion against the Postgres test DB.

## Notes / answers from Ming
- 2026-11-26 (Ming, chat): "duyệt hết; câu 1–34 theo mặc định; cho merge USR-34, USR-35".
- Plan approved (2026-11-26, batch 2026-11-26-2); all blocking questions answered with the plan defaults.
- Merge approved (2026-11-26, batch 2026-11-26-2).

## Handoff
