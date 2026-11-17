# ADM-015: Dashboard stats endpoint for the admin UI

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P3 |
| Areas | api, db |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | — |

## Goal
`admin-pan` gets one read-only stats document for its dashboard, built from data that already exists.

## Spec reference
§5.9 Admin UI read models, "Dashboard stats" (added 2026-11-16).

## Acceptance criteria
- [ ] A Bearer admin endpoint returns one document with snake_case fields:
  - users by status, and the count of unverified users;
  - organizations by status (active, suspended);
  - apps by status;
  - sign-ins in the last 24 h, succeeded and failed, from `auth.login_succeeded` / `auth.login_failed` audit rows;
  - sign-ups per month, from `users.created_at`;
  - members per app (definition per OQ5).
- [ ] It is a single document, not `Page[T]`. Record this as an R9 exception, the same way D18 was.
- [ ] The permission follows the OQ4 answer.
- [ ] The endpoint is read-only: no audit row, and it sends `Cache-Control: no-store`.
- [ ] Tests cover each counter against seeded data, plus 401/403 and the 24 h window boundary.
- [ ] Query cost is bounded: indexed queries only, with no full table scan of `audit_log` beyond the indexed time range.

## Dependencies
ADM-004b (login audit events), ADM-012 (browser access).

## Notes / answers from owner
- Needs answers before PLAN: **OQ4** (permission), **OQ5** (member definition), **OQ6** (number of months, time zone for month boundaries, whether sign-ins are split per app). See `docs/spec-v1.md` §8.
- 2026-11-17 (owner) **OQ4**: new permission `admin:stats:read` for the dashboard stats document (counts only, no audit rows exposed); `member_count` uses the existing `admin:apps:read`.
- 2026-11-17 (owner) **OQ5**: a member = distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff).
- 2026-11-17 (owner) **OQ6**: sign-ups per month: last 12 months, month boundaries in `Asia/Ho_Chi_Minh`; sign-ins last 24 h are totals (succeeded / failed), not split per app.
