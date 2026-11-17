# ADM-017: Operational reports (placeholder)

| Field | Value |
|---|---|
| State | BLOCKED |
| Priority | P3 |
| Areas | architecture, db, api, security |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | — |

## Goal
Placeholder for the `admin-pan` reports screen: define, run and download operational reports. The scope is not decided.

## Spec reference
§5.11 Reports, scope not decided (2026-11-16). §5.11 says no ticket may implement reports until the owner answers OQ9–OQ18.

## Acceptance criteria
- [ ] The owner has answered OQ9–OQ18 in `docs/spec-v1.md` §8.
- [ ] The scope is reshaped with `/idea`, and this placeholder is replaced by concrete tickets.

## Dependencies
ADM-012, ADM-004b.

## Notes / answers from owner
- 2026-11-16 (owner): the reports scope is not settled. The proposed shape is in admin-pan `src/features/reports/data/schema.ts`: types user_signups, active_users, app_memberships, org_overview, audit_activity; formats csv/xlsx; schedules none/daily/weekly/monthly.
- 2026-11-17 (owner): **out of v1.** The dashboard (ADM-015) plus existing list/audit endpoints are enough; ADM-017 stays parked (not run by `/run`) until reports are re-scoped.
