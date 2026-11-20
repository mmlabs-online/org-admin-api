# ADM-039: Usage metrics, defaults and periods (staff config)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
Staff configure, per app, the AI usage metrics (e.g. ricotdin chat turns, speech-to-text seconds; tudocuatui image analyses) with a default limit every member gets (free-trial-like) and a reset period.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ61, OQ63, OQ64, OQ68.

## Scope
- Tables for metrics (app, key, display name, unit, default limit, period `day`|`week`|`month` defaulting to the app's setting / weekly, status) with migration + `downgrade()` (R2); refused for the `admin` app.
- Admin permissions `admin:usage:read` / `admin:usage:write` (seed migration, `is_admin`, granted to no role; `admin:*` covers them).
- Bearer CRUD under `/api/v1/apps/{key}/usage-metrics` (`Page[T]`, detail, create, PATCH, archive), audited (R4).
- One repository helper computing the calendar period start in Asia/Ho_Chi_Minh (day 00:00, week Monday, month 1st), tested at boundaries.

## Acceptance criteria
- [ ] CRUD with 401/403/404/409/422 tests; audit rows on every change; `x-permission` declared.
- [ ] Period helper correct at Sunday 23:59:59 / Monday 00:00 ICT and month end.
- [ ] Migration round-trip; `alembic check` clean.

## Notes / answers from Ming

## Handoff
