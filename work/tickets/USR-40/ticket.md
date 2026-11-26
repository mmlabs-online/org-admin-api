# USR-40: Org picker lists every org of the app for super-admin holders (requested by simap SIM-043)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Size | S |
| Areas | api, auth |
| Gate | plan-approval |
| Depends on | USR-38 |
| Needs Ming | OQ84, OQ89 (spec §8; answer at the plan gate) |
| Attempts | 1 |
| Created | 2026-11-26 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/105 |

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
- 2026-11-26 (Ming, chat): "duyệt hết; câu 1–32 theo mặc định; cho merge USR-40".
- Plan approved (2026-11-26, batch 2026-11-26-1); all blocking questions answered with the plan defaults.
- Merge approved (2026-11-26, batch 2026-11-26-1).

## Handoff

### 2026-11-26 — claude — DONE (batch 2026-11-26-1, attempt 1)
- Agent calls: 6, tokens ~347k total (api-platform-engineer 31k, identity-access-engineer 43k, planner opus 88k, implementer sonnet 73k, reviewer opus 67k, appsec-engineer 46k).
- Done: `access: "member" | "super_admin"` on `GET /api/v1/auth/orgs`; holders see member orgs plus every active org of the app (`org_of_app_clause`, `roles: []`); one paged query + count with the same WHERE; live re-check via `AuthzService.app_super_admin_access`; one log line, no audit; docs/consumers.md, ADR-0030 §8, spec OQ84. No High path touched. Review PASS (4393 passed; ruff/mypy/alembic clean; tier medium); appsec PASS.
- Follow-ups: EXPLAIN / index review of the holder query beyond ~10k orgs; suspended member orgs listed but not mintable (pre-existing, documented); regression test for a device with a holder and a non-holder account; optional router docstring line.
- PR: https://github.com/mmlabs-online/org-admin-api/pull/105 (tier medium, merge approved by Ming).
- Merge rule not met: CI `check` failed (only the 13 known kit-lint errors in `.github/scripts/risk_tier.py`; tests never run in CI); tier medium, `risk:medium` label agrees, merge approved. Stopped for Ming: merge #105 or say "merge cho tôi #105", then `/run-batch --resume` closes the batch.

