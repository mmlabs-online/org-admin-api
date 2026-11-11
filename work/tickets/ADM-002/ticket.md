# ADM-002: Protect admin endpoints with mmlabs Identity tokens and admin permissions

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | auth, security, api |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Admin endpoints under `/api/v1/*` accept only access tokens with audience `admin` and require the named `admin:*` permission; health and public identity endpoints stay public (R3, ADR-0001).

## Spec reference
§5.2 Staff authentication, §5.3 Authorization, §5.8 mmlabs Identity; ADR-0001

## Acceptance criteria
- [ ] Admin requests without a valid access token, or with a token for another audience, get 401 in the shared error envelope
- [ ] A reusable dependency `require_permission("admin:<resource>:<action>")` returns 403 when the token's roles do not grant it; the current user is available to handlers
- [ ] Every existing admin endpoint declares a permission (e.g. `admin:organizations:read`, `admin:organizations:write`, `admin:users:*`, `admin:apps:*`)
- [ ] `/healthz`, `/readyz` remain public
- [ ] Auth configuration comes from env settings only; `.env.example` lists the keys without values
- [ ] Tests cover missing, invalid, expired, wrong-audience and valid tokens, and 403 for a missing permission

## Notes / answers from owner
- 2026-11-11: §8 Q2 answered: staff are §5.8 users (email + password, ADM-008/ADM-009). This ticket requires a valid access token and a staff marker on the user (a staff flag, or a role once ADM-003 lands; the planner decides).
- 2026-11-11: consumes USR-02 (roles) and USR-03 (tokens); the generic RBAC model moved to USR-02 (former ADM-003).
- Depends on USR-02 and USR-03. 2FA for staff: §8 Q10.
