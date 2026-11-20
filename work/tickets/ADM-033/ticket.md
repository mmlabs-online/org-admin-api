# ADM-033: Release log — data model and staff read (list, detail, live version, delete)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | M |
| Areas | db, api |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
The owner sees in admin-pan every app's releases (production and staging): what, when, which changes, and whether the deploy succeeded — starting with the table and the staff read side, before any machine write path exists.

## Scope reference
`docs/spec-v1.md` §5.13 Release log (scope decided 2026-11-19, OQ36–OQ49); `docs/adr/0018-release-log-and-deployer-credential.md` (Accepted 2026-11-19).

## Scope
- `app_releases` table + hand-reviewed migration with working `downgrade()` (R2): app FK, environment (`production`|`staging`), version (CHECK per environment: `vX.Y.Z` strict / `sha-<7 hex>`), status (`pending`|`deployed`|`failed`), `git_sha` (40 hex; staging version must match its first 7), `images` jsonb (bounded), `deployment_pr`, `changes` jsonb (≤ 200 items), `has_migrations`, `deploy_run_url`, `created_via`, `created_at`, `updated_at`, `deployed_at` (first deploy, set iff deployed), `last_deployed_at` (latest deploy of this version, OQ40). Unique (app, environment, version). Indexes for newest-first lists and the live-version query (ADR-0018 / architect consult).
- Bearer `admin:*` routes: `GET /api/v1/releases` (`Page[T]` summary without `changes`/`images` bodies; filters app, environment, status, date range; newest first; sort by `deployed_at` or `last_deployed_at` available), `GET /api/v1/releases/{id}` (full detail), a live-version read per app + environment (shape per PLAN), `DELETE /api/v1/releases/{id}` (204, audited).
- OpenAPI schemas so admin-pan can generate types.

## Out of scope
- Machine write routes and credentials (ADM-034, ADM-035, ADM-036).

## Acceptance criteria
- [ ] Migration up/down clean; `alembic check` no drift; DB CHECKs reject bad version/environment/status combinations.
- [ ] List/detail/live/delete require `admin:*` (401/403 tests; `x-permission` declared; route-class map updated).
- [ ] Filters, paging (`limit` ≤ 100) and newest-first order covered; detail 404 for an unknown id.
- [ ] Live version = the deployed record with the latest `last_deployed_at` per app + environment (seeded rows include a rollback case: an older version redeployed after a newer one is live) — spec §5.13 OQ40 (owner, 2026-11-19).
- [ ] DELETE writes an audit row in the same transaction (ids only).

## Technical notes
- New module `app/modules/releases/`. Tests seed rows through the repository (no write route yet).
- Note at PLAN: org-admin-api and admin-pan both map to the `admin` app and could collide on (app, environment, version) — resolution per spec §5.13 (decided at PLAN of ADM-034 at the latest).

## Test plan
- API tests per criterion; migration round-trip.

## Notes / answers from Ming

## Handoff
