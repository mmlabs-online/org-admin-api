# USR-23: Staff read of an organization's members and invitations (admin Bearer)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-19 |
| PR | — |

## Goal
Staff can see, in an organization's detail, who its members are (with roles and joined date) and its invitations with their status (admin-pan PAN-018). Today the only membership/invitation routes are the cookie routes under `/api/v1/auth/orgs/*` and `/api/v1/auth/invitations/accept`.

## Spec reference
§5.1 Organizations; §5.3 Authorization (org-scoped role assignments; ADR-0013); invitations (USR-06b, ADR-0014); §8 Q6 (PII).

## Acceptance criteria
- [ ] `GET /api/v1/organizations/{org_id}/members` (Bearer, `admin:users:read`, `Cache-Control: no-store`) → `Page[T]` (`limit` ≤ 100, `offset`), one row per org-scoped `role_assignments` row: `assignment_id`, `user_id`, `email`, `display_name`, `user_status`, `app`, `role_id`, `role_key`, `role_name`, `granted_by`, `created_at` (joined), `expires_at`, `active` (not expired). Optional filters `app` and `active`. No N+1.
- [ ] `GET /api/v1/organizations/{org_id}/invitations` (Bearer, `admin:organizations:read`) → `Page[T]`: `id`, `app`, `role_id`, `role_key`, `invited_by`, `created_at`, `expires_at`, `accepted_at`, `accepted_by`, `revoked_at` and derived `status` (`pending` | `accepted` | `revoked` | `expired`, database clock). Optional filter `status`. Never the token, token hash or email HMAC.
- [ ] The invitee email is stored only as an HMAC, so the list shows no email unless the owner decides to store a masked hint (e.g. `j***@example.com`) at create time; the plain address is never stored for this.
- [ ] Optional if cheap (planner decides): `DELETE /api/v1/organizations/{org_id}/invitations/{id}` → 204, revokes, audited in the same transaction (R4), `admin:organizations:write`.
- [ ] Tests: 401, 403, 404 unknown org, paging, a user with two roles in one org (two rows), expired assignment `active=false`, each invitation status, no token fields in the body.

## Dependencies
USR-06, USR-06b (invitations), ADM-002b, ADM-012. Consumer: admin-pan PAN-018 (blocked on this). Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- **Needs owner answer before PLAN:** store a masked email hint on invitations (new column, set at create time; existing rows have none) — yes or no?
