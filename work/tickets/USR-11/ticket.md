# USR-11: Caller's effective admin permissions endpoint for the admin UI

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/41 |

## Goal
The admin UI (`admin-pan`) can read the signed-in caller's own `user_id`, admin roles and effective `admin:*` permissions with its `aud=admin` Bearer token, so it can hide or disable what the caller may not do. The API keeps enforcing every permission; tokens stay without a permissions claim.

## Spec reference
§5.3 Authorization (admin app RBAC), §2 (admin web UI consumer); ADR-0008 (admin API authentication, "no permissions claim"); CLAUDE.md R3 (auth classes), R9.
**Conflict to resolve at PLAN:** §5.3 and R3 say every admin `/api/v1` endpoint declares the `admin:*` permission it needs. This endpoint is proposed as Bearer `aud=admin` with **no** `require_permission` (self-scoped). The planner must either record it as an explicit, narrow exception (ADR-0008 amendment + R3/§5.3 wording) or propose an alternative; the owner confirms.

## Acceptance criteria
- [ ] `GET /api/v1/me/permissions` (outside `/api/v1/auth/*`) authenticated by Bearer `aud=admin` through `current_admin`, returning `{"user_id", "roles", "permissions"}` for the caller only: `permissions` comes from the same effective-permissions logic enforcement uses (reloaded per request, sorted, `admin:*` returned unexpanded); no email or other PII.
- [ ] Response has `Cache-Control: no-store`; no token → 401, token with another `aud` → 401, both in the R6 error envelope.
- [ ] A role grant or revoke for the caller is reflected on the next request; a disabled caller gets the same result as on other admin endpoints.
- [ ] The route is an explicit entry in the auth-class allow-list (`tests/test_route_auth_classes.py`) as "Bearer, self-scoped, no permission" — no placeholder permission is invented; ADR-0008 records the exception.
- [ ] The route appears in the OpenAPI document (ADM-013 envelope on 401) and in `docs/consumers.md` under "Calling the admin API from the admin UI".
- [ ] Tests: 401 (missing token, wrong `aud`), own data only, role change visible on next request, `admin:*` unexpanded, allow-list entry present.

## Dependencies
Depends on ADM-002 (`current_admin`, `require_permission`), ADM-013 (OpenAPI contract).

## Notes / answers from owner
- 2026-11-16 (request from admin-pan, PAN-011b): the token's `roles` claim carries role keys only (`app/modules/tokens/service.py:92`); the role catalog for `admin` is refused (D17); `GET /users/{id}/role-assignments` needs `admin:users:read` and admin-pan must not decode the token to learn its own `sub`. Do not add permissions to the token. Final design is the org-admin-api planner's call.
