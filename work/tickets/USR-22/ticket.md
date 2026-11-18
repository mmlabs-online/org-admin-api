# USR-22: Join requests — optional reject reason and expiry of stale pending requests

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can give a reason when rejecting a join request, and pending requests no longer stay open forever (USR-03g security-review follow-up).

## Spec reference
§5.3 Membership (`join_policy = approval`); USR-03g security-review follow-ups.

## Acceptance criteria
- [ ] Reject accepts an optional `reason` (length cap, ADM-011 display-text rules), stored and recorded in the audit row. Whether the user sees it is decided by the owner at PLAN.
- [ ] Pending requests older than a configurable age (env via `app/core/config.py`, R5) are treated as expired/cancelled and no longer block a new request; mechanism (on-read check or CLI purge) chosen at PLAN.
- [ ] Hand-reviewed migration with a working `downgrade()` if columns are added (R2).
- [ ] Tests: reject with and without reason, reason too long → 422, stale request treated as expired, a fresh request after expiry works.

## Dependencies
USR-03g. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- 2026-11-18 (orchestrator): overlaps USR-15 (join requests hardening: cancel on disable, pending cap, retention purge). Plan after USR-15 and reuse its cleanup path for expiry.
