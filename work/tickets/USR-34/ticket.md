# USR-34: Users list — opt-in active-access summary (`include=access`)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Size | M |
| Areas | api, db |
| Gate | plan-approval |
| Depends on | ADM-013, ADM-023 |
| Needs Ming | — |
| Attempts | 1 |
| Created | 2026-11-26 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/106 |

## Goal
Staff see each user's current access (active roles per app, with scope) directly in the users list, without one request per row. Requested by admin-pan for PAN-032 (access column).

## Scope reference
- `docs/spec-v1.md` §5.15 Part 1 (owner, 2026-11-25; details 2026-11-26: OQ74 cap, OQ75 disabled apps / no names); §6 (p95 < 200 ms).
- Owner decision: "access" = active role assignments only; expired excluded.

## Scope
- `GET /api/v1/users?include=access` (permission `admin:users:read`, unchanged). Without `include` the response is unchanged.
- Each item gains `access: [{app, app_status, roles: [{assignment_id, role_id, role_key, org_id | null, expires_at | null}]}]` and `access_truncated`; active assignments only (`expires_at IS NULL OR > now()`), grouped by app key; assignments in disabled apps included with `app_status`.
- Cap: at most 20 assignments per user, ordered by app key, role key, app scope before org scope; `access_truncated: true` when the user has more.
- One extra query per page for all users of the page (no N+1).
- Proposed defaults (spec, not answered): user with none → `access: []`; `include` accepts only `access`, anything else → 422.

## Out of scope
- Role or org names (USR-27); filters (USR-35); any write.

## Acceptance criteria
- [ ] `include=access` returns `access` and `access_truncated` per item with the shape above; without it the response matches today's `UserRead` page.
- [ ] Expired assignments are excluded; app-scope and org-scope rows both appear; disabled-app assignments appear with `app_status: disabled`.
- [ ] A user with more than 20 active assignments gets exactly 20, in the spec order, and `access_truncated: true`.
- [ ] The number of SQL queries per request does not grow with page size (test asserts it).
- [ ] Unknown `include` value → 422; no token → 401; token without `admin:users:read` → 403.
- [ ] OpenAPI documents the new parameter and fields (additive, R9).

## Technical notes
- Files: users router/schemas/service; the access read in the rbac or users repository (where it lives decides Medium vs High review; rbac is a High path).
- Query: second query `user_id = ANY(:page_ids) AND active`, joined to roles and apps; `row_number() OVER (PARTITION BY user_id …)` up to cap+1 for `truncated`. Uses `uq_role_assignments_user_role_org` (leading `user_id`); no migration expected (architect consult, 2026-11-25).
- Shares the "active assignment" predicate with USR-35.

## Test plan
- API tests per criterion against the Postgres test DB, including a query-count assertion.

## Notes / answers from Ming
- 2026-11-26 (Ming, chat): "duyệt hết; câu 1–34 theo mặc định; cho merge USR-34, USR-35".
- Plan approved (2026-11-26, batch 2026-11-26-2); all blocking questions answered with the plan defaults.
- Merge approved (2026-11-26, batch 2026-11-26-2).

## Handoff

### 2026-11-26 — claude — DONE (batch 2026-11-26-2, attempt 1)
- Agent calls: 5, tokens ~286k total (api-platform-engineer 31k, database-optimizer 29k, planner opus 101k, implementer sonnet 69k, reviewer opus 56k).
- Done: `include=access` on `GET /api/v1/users` (repeatable `include`, separate `UserWithAccessRead`, one windowed access query per page, cap 20 + `access_truncated`, `COLLATE "C"` order), shared `users.repository.active_assignment_clause()` for USR-35; no High path. Review PASS (4416 passed; ruff/mypy/alembic clean; tier medium); no appsec trigger.
- Follow-ups: move rbac's inline active-assignment copies onto one shared definition (High, separate ticket); tell admin-pan (PAN-032) the 200 response is now `anyOf` two page types; nits in review.md.
- PR: https://github.com/mmlabs-online/org-admin-api/pull/106 (tier medium, merge approved by Ming).
