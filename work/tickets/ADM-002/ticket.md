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
Only authenticated mmlabs staff can call the admin endpoints under `/api/v1/*`. Staff log in as mmlabs Identity users (ADM-009); health and public identity endpoints stay public (R3).

## Spec reference
§5.2 Staff authentication, §5.8 mmlabs Identity (§8 Q2 answered 2026-11-11)

## Acceptance criteria
- [ ] Requests to `/api/v1/*` without valid credentials get 401 in the shared error envelope
- [ ] A valid staff credential gives access; the current staff identity is available to handlers via a dependency
- [ ] `/healthz`, `/readyz` remain public
- [ ] Auth configuration comes from env settings only; `.env.example` lists the keys without values
- [ ] Tests cover missing, invalid, expired and valid credentials

## Notes / answers from owner
- 2026-11-11: §8 Q2 answered: staff are §5.8 users (email + password, ADM-008/ADM-009). This ticket requires a valid access token and a staff marker on the user (a staff flag, or a role once ADM-003 lands; the planner decides).
- Depends on ADM-009. 2FA for staff: §8 Q10.
