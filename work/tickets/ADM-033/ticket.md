# ADM-033: Release log — data model and staff read (list, detail, live version, delete)

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P2 |
| Size | M |
| Areas | db, api |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/84 |

## Goal
The owner sees in admin-pan every app's releases (production and staging): what, when, which changes, and whether the deploy succeeded — starting with the table and the staff read side, before any machine write path exists.

## Scope reference
`docs/spec-v1.md` §5.13 Release log (scope decided 2026-11-19, OQ36–OQ49); `docs/adr/0021-release-log-and-deployer-credential.md` (Accepted 2026-11-19).

## Scope
- `app_releases` table + hand-reviewed migration with working `downgrade()` (R2): app FK, environment (`production`|`staging`), version (CHECK per environment: `vX.Y.Z` strict / `sha-<7 hex>`), status (`pending`|`deployed`|`failed`), `git_sha` (40 hex; staging version must match its first 7), `images` jsonb (bounded), `deployment_pr`, `changes` jsonb (≤ 200 items), `has_migrations`, `deploy_run_url`, `created_via`, `created_at`, `updated_at`, `deployed_at` (set iff deployed). Unique (app, environment, version). Indexes for newest-first lists and the live-version query (ADR-0021 / architect consult).
- Bearer `admin:*` routes: `GET /api/v1/releases` (`Page[T]` summary without `changes`/`images` bodies; filters app, environment, status, date range; newest first; sort by `deployed_at` available), `GET /api/v1/releases/{id}` (full detail), a live-version read per app + environment (shape per PLAN), `DELETE /api/v1/releases/{id}` (204, audited).
- OpenAPI schemas so admin-pan can generate types.

## Out of scope
- Machine write routes and credentials (ADM-034, ADM-035, ADM-036).

## Acceptance criteria
- [ ] Migration up/down clean; `alembic check` no drift; DB CHECKs reject bad version/environment/status combinations.
- [ ] List/detail/live/delete require `admin:*` (401/403 tests; `x-permission` declared; route-class map updated).
- [ ] Filters, paging (`limit` ≤ 100) and newest-first order covered; detail 404 for an unknown id.
- [ ] Live version = latest `deployed_at` among deployed records per app + environment (seeded rows include a rollback case).
- [ ] DELETE writes an audit row in the same transaction (ids only).

## Technical notes
- New module `app/modules/releases/`. Tests seed rows through the repository (no write route yet).
- Note at PLAN: org-admin-api and admin-pan both map to the `admin` app and could collide on (app, environment, version) — resolution per spec §5.13 (decided at PLAN of ADM-034 at the latest).

## Test plan
- API tests per criterion; migration round-trip.

## Notes / answers from Ming
- 2026-11-20 (Ming, chat): "merge cho tôi #84" — explicit owner instruction to merge this High-tier PR (exception to "High never merged by an agent"); merged by claude (CI red only on the pre-existing kit lint).
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 1, 9–12, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 3, tokens ~202k (consultants + planner×1 opus).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
### 2026-11-20 — claude — DONE (PR #84, waiting for Ming's merge)
- Agent calls (build): 3, tokens ~227k (implementer×1 sonnet 99k, reviewer×1 opus 75k, appsec-engineer×1 54k). Ticket total incl. plan: 6 calls, ~429k.
- Done: app_releases table + migration 89fd8d667a25, four admin:* routes, OpenAPI schemas; review PASS; appsec no 🔴 (one 🟡 pr_url description → follow-up). PR https://github.com/mmlabs-online/org-admin-api/pull/84.
- In progress / next: Ming merges #84 (High); then ADM-035.
