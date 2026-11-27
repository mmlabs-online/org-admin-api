# USR-28: Exact email lookup for staff user search

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api, security |
| Attempts | 1 |
| Claimed by | — |
| Created | 2026-11-19 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/113 |

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

## Handoff

### 2026-11-27 — claude — DONE (batch 2026-11-27-1, attempt 1)
- Agent calls: 6, tokens ~295k total (api-platform-engineer 26k, security-architect 29k, planner opus 76k, implementer sonnet 65k, reviewer opus 62k, appsec-engineer 37k).
- Done: `email_match=exact|contains` on `GET /api/v1/users` (exact = sign-up normalisation + CITEXT equality; default unchanged; 422 `email_required`); read-only `POST /api/v1/users/search` (same filters/pagination/include, 200, 4 KiB body cap, `admin:users:read`, no audit, shared `_users_page`); route registry; D43 + R9 exception; narrow `READ_ONLY_POST` audit-coverage exclusion. Review PASS (4569 passed; ruff/mypy/alembic clean; tier medium); appsec PASS.
- Follow-ups: separate High ticket to strip query strings from the access log; admin-pan PAN-015c switches to `POST /users/search` + `email_match: "exact"`, then decide on deprecating GET `email`; `assert READ_ONLY_POST <= set(BEARER)`; identity-architecture §4 and spec §5.15 snapshot mention the search route.
- PR: https://github.com/mmlabs-online/org-admin-api/pull/113 (tier medium, merge approved by Ming).
- Merge rule not met: CI `check` failed only on the 13 known kit-lint errors (job log checked); tier medium, label agrees, merge approved. Stopped for Ming: merge #113 or "merge cho tôi #113", then `/run-batch --resume` builds ADM-026 (after M-10).

