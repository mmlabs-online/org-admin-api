# ADM-025: Grant admin:entitlements:* to a staff role

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | db, auth, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff other than `admin:*` holders can use the entitlements tab (admin-pan PAN-019); ADM-006 seeded `admin:entitlements:read|write` but granted them to no role.

## Spec reference
§5.6 Org product entitlements; §5.3 (roles hold permissions as data).

## Acceptance criteria
- [ ] The owner names the role(s) that get `admin:entitlements:read` and/or `admin:entitlements:write`, or decides it stays data-only (documented, staff grant it via the roles API).
- [ ] If seeded: an Alembic migration adds the role-permission rows idempotently, with a working `downgrade()` (R2).
- [ ] spec §5.6 no longer calls ADM-006 "(backlog)" and states which role holds the keys.
- [ ] Tests: a user holding the chosen role can read/write entitlements; a user without it gets 403.

## Dependencies
ADM-006. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- **Needs owner answer before PLAN:** which staff role(s) get which key (owner's call; do not seed without it).
