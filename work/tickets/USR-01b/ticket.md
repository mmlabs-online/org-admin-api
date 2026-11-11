# USR-01b: Staff user management endpoints (list, get, enable/disable)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | api, security |
| Attempts | 1 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/9 |

## Goal
Staff can list, look up, and enable/disable mmlabs users, without exposing user data publicly before ADM-002 protects admin endpoints.

## Spec reference
§5.8 (Admin); identity-architecture.md §4; split from USR-01 (plan.md "Split proposal")

## Acceptance criteria
- [ ] `GET /api/v1/users?email=&status=&limit=&offset=` → `Page[UserRead]`; `email` = case-insensitive contains with LIKE escaping; order `created_at desc, id`
- [ ] `GET /api/v1/users/{id}` → `UserRead` or 404; `PATCH /api/v1/users/{id} {status: active|disabled}` (extra fields / explicit null → 422)
- [ ] `UserRead {id, email, status, email_verified_at, display_name, created_at, updated_at}` — exact keys, never `password_hash` (test asserts the key set)
- [ ] Permissions declared per route for ADM-002: `admin:users:read` (list/get), `admin:users:write` (PATCH) (R3)
- [ ] Exposure before ADM-002 handled per owner decision (recommended: router mounted only when `ADMIN_USERS_API_ENABLED=true`, default false; tests enable it)
- [ ] Tests: filters, pagination, 404, status change, 422 cases, key set

## Notes / answers from owner
- 2026-11-11: created by the orchestrator from the USR-01 planner's split (former USR-01 AC 6).
- Open for the owner: may these endpoints be reachable in production before ADM-002 (they expose user emails and a disable action)? If unanswered, the planner applies the default-off flag or BLOCKs.
- Depends on USR-01.
