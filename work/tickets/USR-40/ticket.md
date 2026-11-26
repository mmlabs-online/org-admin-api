# USR-40: Org picker lists every org of the app for super-admin holders (requested by simap SIM-043)

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Size | S |
| Areas | api, auth |
| Gate | plan-approval |
| Depends on | USR-38 |
| Needs Ming | OQ84, OQ89 (spec §8; answer at the plan gate) |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | claude@2026-11-26T03:59Z |
| PR | — |

## Goal
In the app's org picker (AUT-06 flow), a staff holder of the app's super-admin role sees every org of the app, each marked "member" or "super-admin access".

## Scope reference
- `docs/spec-v1.md` §5.16 Org list (proposal; OQ84, OQ89). ADR-0030 / D42 (written in USR-38).
- Cross-repo requester: simap SIM-043.

## Scope
- `GET /api/v1/auth/orgs` (cookie class, `X-MM-App`): every item gains `access: "member" | "super_admin"` (additive, always present). For a holder, list every active org of the app (definition per OQ89; proposed: orgs with an active role assignment of the app or an entitlement for it); orgs where the holder is a member stay `member` with their roles; others are `super_admin` with `roles: []`.
- Non-holders: same list as today, every item `access: "member"`.
- Pagination and ordering unchanged; no N+1 (one paged query + count + the existing batch roles query).
- `docs/consumers.md` picker section.

## Out of scope
- Token minting (USR-39); suspended orgs listed read-only (proposed hidden, OQ89).

## Acceptance criteria
- [ ] A holder sees every org of the app per the OQ89 definition, `member` items with roles, `super_admin` items with `roles: []`; orgs of other apps and suspended orgs are not listed.
- [ ] A non-holder's list is unchanged apart from `access: "member"` on each item.
- [ ] Losing the role (or `admin:super-admin`, per OQ82) removes the extra orgs on the next call.
- [ ] Pagination `total` is correct; query count does not grow with page size.
- [ ] 401 / 403 / 422 behaviour of the route unchanged; `docs/consumers.md` updated.

## Technical notes
- Files: `app/modules/organizations/auth_service.py` (`list_mine`), repository, schemas (`MyOrgRead`), `docs/consumers.md`.
- Architect consult: `EXISTS` on role assignments of the app's roles with `org_id = Organization.id`; `ix_role_assignments_org_id` suffices for now. Shares the "org of the app" and holder check with USR-39.

## Test plan
- API tests per criterion against the Postgres test DB, including a query-count assertion.

## Notes / answers from Ming

## Handoff
