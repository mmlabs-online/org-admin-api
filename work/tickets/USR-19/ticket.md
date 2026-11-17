# USR-19: Join requests — reject cooldown controls and pending count per app

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can see and clear a user's reject cooldown, and admin-pan can show a pending-requests badge per app cheaply.

## Spec reference
§5.3 Membership (`join_policy = approval`); USR-03g follow-ups (`JOIN_REJECT_COOLDOWN_SECONDS`).

## Acceptance criteria
- [ ] The reject cooldown length is readable by staff (e.g. on app detail or the join-requests list; exact place chosen at PLAN).
- [ ] A staff action clears the cooldown for one user on one app (permission `admin:roles:assign`, or as chosen at PLAN), audited in the same transaction (R4).
- [ ] A pending count per app is available without listing requests (either `pending_join_requests` on `AppRead`, without N+1, or `GET …/join-requests/count`).
- [ ] Tests: cooldown shown, clear cooldown lets the user request again, audit row, 401/403/404, pending count with pending/decided rows.

## Dependencies
USR-03g. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- Optional, not in this ticket unless the planner finds it free: a cross-app "pending inbox" endpoint.
