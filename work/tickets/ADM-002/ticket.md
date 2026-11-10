# ADM-002: Staff authentication for admin endpoints

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Only authenticated mmlabs staff can call `/api/v1/*`; health endpoints stay public.

## Spec reference
§5.2 Staff authentication (depends on §8 Q2)

## Acceptance criteria
- [ ] Requests to `/api/v1/*` without valid credentials get 401 in the shared error envelope
- [ ] A valid staff credential gives access; the current staff identity is available to handlers via a dependency
- [ ] `/healthz`, `/readyz` remain public
- [ ] Auth configuration comes from env settings only; `.env.example` lists the keys without values
- [ ] Tests cover missing, invalid, expired and valid credentials

## Notes / answers from owner
<Answer §8 Q2 here (login method) before /run, or the planner will BLOCK.>
