# ADM-016: `member_count` on the staff apps list and app detail

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api, db |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/52 |

## Goal
The Apps screen in `admin-pan` can show a "Members" column, through an additive field that breaks nothing.

## Spec reference
§5.9 Admin UI read models, "Members column" (2026-11-16).

## Acceptance criteria
- [ ] `GET /api/v1/apps` and `GET /api/v1/apps/{key}` include an additive `member_count` field (R9: no breaking change in v1).
- [ ] The count follows the OQ5 definition and is computed without an N+1 query on the list.
- [ ] Tests:
  - the count with app-scope and org-scope assignments;
  - expired assignments are excluded;
  - disabled and unverified users are handled per OQ5;
  - the existing app API tests still pass.

## Dependencies
USR-02c (role assignments), ADM-012. Can be merged with ADM-015, which reuses the same definition.

## Notes / answers from owner
- Needs answers before PLAN: **OQ4**, **OQ5** (`docs/spec-v1.md` §8).
- 2026-11-17 (owner) **OQ4**: new permission `admin:stats:read` for the dashboard stats document (counts only, no audit rows exposed); `member_count` uses the existing `admin:apps:read`.
- 2026-11-17 (owner) **OQ5**: a member = distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff).
- 2026-11-17 (orchestrator): consult step reuses the ADM-015 consult notes (`work/tickets/ADM-015/consult-*.md`), which already cover `member_count` (shared `RbacRepository.member_counts`, merged in ADM-015).
