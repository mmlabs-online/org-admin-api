# USR-28: Exact email lookup for staff user search

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P3 |
| Areas | api, security |
| Attempts | 0 |
| Claimed by | claude@2026-11-27T03:59Z |
| Created | 2026-11-19 |
| PR | — |

## Goal
Staff assigning a role can find exactly one user by email: `GET /api/v1/users?email=` is a case-insensitive substring match (`ilike %x%`), so `an@x.com` also returns `joan@x.com`. The email also travels in the URL and can reach proxy/API access logs.

## Spec reference
§5.8 Admin bullet (staff list and look up users); R5/R11 (no full emails in logs).

## Acceptance criteria
- [ ] An exact, case-insensitive match after the same normalisation as sign-up: `email_exact=` or `email_match=exact|contains` (default `contains`, so current clients keep working); chosen at PLAN.
- [ ] Either a `POST /api/v1/users/search` with the email in the body (stays out of URLs and logs), or access logs drop query strings for `/api/v1/users` — chosen at PLAN and tested.
- [ ] Tests: exact hit, case-insensitive hit, no partial hit in exact mode, 403 without `admin:users:read`.

## Dependencies
USR-01b, ADM-002b. Consumer: admin-pan PAN-015c. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-27 (Ming, chat): "duyệt hết; câu 1–24 theo mặc định; cho merge USR-28".
- Plan approved (2026-11-27, batch 2026-11-27-1); all blocking questions answered with the plan defaults.
- Merge approved (2026-11-27, batch 2026-11-27-1).
