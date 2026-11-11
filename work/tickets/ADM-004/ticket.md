# ADM-004: Audit log (admin actions, login, join, role changes, org membership)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | db, api, security |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Every mutating admin action and every security-relevant identity event is recorded in an append-only audit log that auditors can query.

## Scope
`audit_log` table; an `AuditLogger` used in the same transaction as the change; wire it into existing mutations and identity events (replacing the interim interface from USR-03/03b); read endpoint.

## Spec reference
§5.4, §6 ("100% of mutating endpoints write an audit entry"); identity-architecture.md §5 S8

## Acceptance criteria
- [ ] `audit_log` table + migration: actor user id (nullable for anonymous events), action, target type/id, app key, org id, changes (diff, no secrets), ip, user agent, request id, timestamp; no update/delete path in code
- [ ] Written in the same transaction for: organization create/update/delete; user disable/enable; app/role/permission changes; role assign/revoke; join (and approval decisions); org create and membership changes (USR-06); login success/failure, logout (one/all), device-session revocation on reuse
- [ ] Never stores passwords, tokens, cookie values or full emails of unknown accounts (R11)
- [ ] `GET /api/v1/audit-logs` paginated, filterable by actor, action, target, app and time range; permission `admin:audit:read`
- [ ] Tests prove an entry per covered event and none when the action fails

## Dependencies
ADM-002 (actor identity + permission). Events from USR-03/USR-03b use the interim audit interface until this lands; USR-06 events are added there.

## Notes / answers from owner
- 2026-11-11: scope extended to identity events (login, join, role changes, org membership).
