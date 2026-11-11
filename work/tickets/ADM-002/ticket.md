# ADM-002: Lock admin endpoints (aud=admin + require_permission)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | auth, security, api |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Admin endpoints under `/api/v1/*` accept only Bearer access tokens with audience `admin` and require the named `admin:*` permission; health and public identity endpoints stay public.

## Scope
FastAPI dependencies `current_user` (Bearer JWT, `aud=admin`) and `require_permission("admin:<resource>:<action>")`; apply to every existing admin endpoint (organizations, users, apps/roles/assignments, join requests); admin permissions seeded. Uses the same verification code path as `mmlabs-auth`.

## Spec reference
§5.2, §5.3; ADR-0001; identity-architecture.md §4; decision D9 (admin app is `closed`)

## Acceptance criteria
- [ ] No token, invalid/expired token, or token with another `aud` → 401 in the error envelope
- [ ] Missing permission → 403; `admin:*` grants everything
- [ ] Every existing admin endpoint declares a permission (e.g. `admin:organizations:read|write`, `admin:users:read|write`, `admin:apps:*`, `admin:roles:manage`); a test enumerates routes and fails if one has no declared permission (except the public allow-list)
- [ ] `/healthz`, `/readyz`, `/.well-known/jwks.json`, and the public `/api/v1/auth/*` endpoints stay reachable without a Bearer token
- [ ] Tests: missing, invalid, expired, wrong-audience, valid token; 403 for a missing permission

## Dependencies
USR-02, USR-02b, USR-02c, USR-02d, USR-02e, USR-03 (staff get an `aud=admin` token via `/api/v1/auth/token`; admin join policy is `closed`, so roles are granted by staff/bootstrap).

## Notes / answers from owner
- From the USR-02d security review: escalation via the member role — staff can clear `default_role_id` then PUT `<app>:*` onto `<app>:member`, escalating every member. Require `admin:*` for granting `is_admin`/`<app>:*` (covers it) and consider 409 for an `is_admin` grant on any role with assignments. Before removing the flag: `admin:*` on every `# ADM-002 guard` site; consider restricting `admin:*` to `admin:super-admin`; review `admin:`-prefixed rows created while the flag was on.
- 2026-11-11: consumes USR-02 (roles) and USR-03 (tokens); generic RBAC moved to USR-02 (former ADM-003). 2FA for staff: OQ3.
- 2026-11-11 (D16): staff need a verified email to get an `aud=admin` token, which is only possible after USR-04. **Release this ticket together with or after USR-04**, otherwise the admin endpoints are locked for everyone in production.
