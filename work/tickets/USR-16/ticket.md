# USR-16: Staff role listing with ids (admin Bearer), including the admin app and disabled apps

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can list an app's roles with their ids from a Bearer admin endpoint, so `admin-pan` can manage roles (every role write is addressed by `role_id`) for any app, the `admin` app and disabled apps included.

## Spec reference
§5.3 Authorization — multi-app RBAC (roles per app, staff manage them); §5.9 context (admin-pan consumes admin read models). The public catalog (D17) stays as it is.

## Acceptance criteria
- [ ] A new Bearer admin endpoint (path chosen at PLAN, e.g. `GET /api/v1/apps/{key}/role-definitions`) that does not collide with the public catalog `GET /api/v1/apps/{key}/roles`, declares one `admin:*` permission (`admin:apps:read` or `admin:roles:manage`, chosen at PLAN) in `x-permission`, and is not rate-limited like the public catalog.
- [ ] Returns `Page[T]` (`limit` ≤ 100, `offset`, stable order with `id` tie-breaker). Each item has `id`, `app`, `key`, `name`, `is_system`, `is_default` (`apps.default_role_id == id`), `permissions` (sorted keys), `privileged` (any permission `is_admin`, or app is `admin`), `created_at`, `updated_at`.
- [ ] Lists roles of the `admin` app and of disabled apps; 404 only for an unknown app key.
- [ ] Optional (planner decides, skip if costly): `assignment_count` of active (non-expired) assignments per role, computed without N+1; optional detail `GET …/{role_id}`.
- [ ] Tests: 401, 403, 404 unknown app, admin app listed, disabled app listed, paging, `is_default` and `privileged` flags; OpenAPI shows the new schema.

## Dependencies
USR-02d (roles staff API), ADM-002b, ADM-012, ADM-013. Consumer: admin-pan PAN-015b, PAN-015c. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- R9: the path shape (apps addressed by `key`, D18) follows the existing exception; record any new exception at PLAN.
