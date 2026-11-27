# USR-25: Scope of `self_assignment_forbidden` beyond the admin app

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Areas | auth, security |
| Attempts | 0 |
| Claimed by | claude@2026-11-27T03:59Z |
| Created | 2026-11-19 |
| PR | — |

## Goal
Close (or deliberately accept) a separation-of-duties gap: today `self_assignment_forbidden` applies only to admin-app roles, so a staff member with `admin:roles:assign` can grant themselves any non-admin product role, and with `admin:*` also privileged product roles. admin-pan's security review flagged it.

## Spec reference
§5.3 Authorization; ADR-0001, ADR-0008 (guards G1–G5).

## Acceptance criteria
- [ ] The owner picks one: (a) keep as is and document it in ADR-0001/0008; (b) forbid self-grant and self-revoke of privileged roles (any role holding an `is_admin` permission) in every app; (c) forbid all self-assignment in every app.
- [ ] Whatever is chosen: the 403 code stays `self_assignment_forbidden`; refused attempts are audited as `denied` (existing pattern); ADR-0008 records the rule.
- [ ] The guard applies to every path that grants or revokes on the caller (assignment create/revoke, join-request approval if it can grant to self).
- [ ] Tests per app type (admin app, non-admin app with and without `is_admin` permissions, org-scoped role).

## Dependencies
USR-02c, ADM-002b, USR-03g. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-19 (owner): **option (b)** — forbid self-grant and self-revoke of privileged roles (any role holding an `is_admin` permission, plus every admin-app role as today) in every app; non-privileged roles (e.g. `<app>:member`) may still be self-assigned.
