# ADM-022: Server-side CSV export of the audit log

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P3 |
| Areas | api, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can download a filtered audit log as CSV from the API (admin-pan removed its client-side export, PAN-016).

## Spec reference
§5.4 Audit log. ⚠️ Overlaps §5.11 report type `audit_activity` (scope **not decided**, OQ9–OQ18): "no ticket may implement reports before then". Owner must confirm before PLAN that this export is separate from §5.11.

## Acceptance criteria
- [ ] An export endpoint (e.g. `GET /api/v1/audit-logs/export`) takes the same filters as the list and returns `text/csv` with a row cap (value set at PLAN; the response says when it was truncated).
- [ ] Permission `admin:audit:read` or a new `admin:audit:export` (owner/PLAN decides; a new permission ships with a migration).
- [ ] Each export writes an audit entry (filters and row count, no row data) in the same transaction (R4 spirit for a privileged read).
- [ ] No PII beyond the list: same `user_agent` stripping; CSV cells are protected against formula injection.
- [ ] Tests: happy path with filters, row cap, audit row written, 401/403, CSV-injection escaping.

## Dependencies
ADM-021 (shared filters), ADM-004b. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- Needs owner answer before PLAN: is this in scope separately from §5.11 reports (blocked, ADM-017)?
- **Answered (owner, 2026-11-18):**
  - In scope separately from §5.11. This is a synchronous CSV export on the same filters as the list: no saved definitions, no schedules, no xlsx. §5.11 stays blocked, and the export is recorded under spec §5.4 as part of the audit log.
  - Permission: the existing `admin:audit:read`, so no migration.
  - Row cap: 10,000 rows. The response says when the export was truncated.
