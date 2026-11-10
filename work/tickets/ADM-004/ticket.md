# ADM-004: Audit log for mutating admin actions

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | db, api, security |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Every mutating admin action is recorded in an append-only audit log that auditors can query.

## Spec reference
§5.4 Audit log, §6 ("100% of mutating endpoints write an audit entry")

## Acceptance criteria
- [ ] `audit_log` table + migration: actor, action, target type/id, changes, timestamp, request id; no update/delete path in code
- [ ] Organization create/update/delete write an audit entry in the same transaction as the change
- [ ] `GET /api/v1/audit-logs` paginated, filterable by actor, target and time range
- [ ] Tests prove an entry is written for each mutation and not written when the mutation fails

## Notes / answers from owner
Actor identity comes from ADM-002; if run before ADM-002, the planner should define a placeholder actor and note it.
