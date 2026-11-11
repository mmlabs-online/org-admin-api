# USR-02e: Role catalog with ETag (flag-gated)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | api, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Apps (and staff) can fetch an app's role → permissions catalog cheaply with ETag / 304, without making it public before the owner decides.

## Spec reference
§5.3; ADR-0001 §5; split from USR-02b (`work/tickets/USR-02b/plan.md` → "Split proposal" → slice 02b-3)

## Acceptance criteria
- [ ] `GET /api/v1/apps/{key}/roles` in its own router, behind `ADMIN_RBAC_API_ENABLED` (until the owner answers Q1)
- [ ] Canonical sorted document; strong ETag = hash of the exact response bytes; `If-None-Match` → 304 (lists, `W/`, `*`), empty body, same ETag; `Cache-Control: no-cache`
- [ ] 404 for unknown, disabled and `admin` apps
- [ ] Tests: ETag stable, changes after a permission/role change, 304 variants, 404 cases, flag off → 404

## Notes / answers from owner
- 2026-11-11: created by the orchestrator from the USR-02b planner's split (slice 02b-3). Can merge into USR-02d if Q1 (public catalog) is answered first.
- Depends on USR-02d.
