# ADM-021: Audit log — org filter and single-entry read

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P2 |
| Areas | api, db |
| Attempts | 1 |
| Created | 2026-11-17 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/66 |

## Goal
Staff can open an organization's history from admin-pan and deep-link to one audit entry without paging the list.

## Spec reference
§5.4 Audit log ("Read-only, filterable listing for auditors").

## Acceptance criteria
- [ ] `GET /api/v1/audit-logs` accepts an optional `org_id`: matches rows whose `target_type`/`target_id` is that organization OR whose `org_id` column equals it (entitlements, org-scoped assignments). Combines with the existing filters.
- [ ] `GET /api/v1/audit-logs/{id}` with `admin:audit:read` returns one `AuditLogRead` (same `user_agent` handling as the list); 404 for an unknown id.
- [ ] The `org_id` filter is index-backed or the planner shows it meets §6 (p95 < 200 ms).
- [ ] Tests: `org_id` alone and with other filters (target match and column match), single read happy path, 404, 401/403 on both routes.

## Dependencies
ADM-004, ADM-004b. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
