# ADM-038: Bug reports carry the release the user was running

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | S |
| Areas | db, api |
| Gate | none |
| Depends on | ADM-028, ADM-033, ADM-036 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
Staff see, next to an end-user bug report, the release the user was running: version, deploy time, changes and deployment PR.

## Scope reference
`docs/spec-v1.md` §5.13 "Bug reports carry the release" (owner, 2026-11-19) and §5.12 (release link line); ADR-0018.

## Scope
- On submit (§5.12 cookie route): environment from the validated `Origin` (`staging-<app>.mmlabs.online` → `staging`, else `production`), never from the body.
- Matching in order: (a) the app version sent by the app → `app_releases` (app, environment, version) → `release_match = reported`; (b) no or unknown version → the `deployed` release with the latest `last_deployed_at` for (app, environment) → `inferred`; (c) none → no link, `none`. Never reject a report for this.
- Store on the report: version string as sent (capped), `release_id` (nullable FK, `ON DELETE SET NULL`), `release_match`; migration with a working `downgrade()` (R2).
- Staff report list/detail (`admin:bug-reports:read`): linked release summary (version, environment, status, `last_deployed_at`, deployment PR) and `release_match`; list filters by release and by version.
- Release detail (`admin:*`, ADM-033): count of linked reports (open / done).
- Audit unchanged: `bug_report.created` ids and counts only (may add the release id).

## Out of scope
- Changing how apps send their version (documented in ADM-032 / ADM-037).

## Acceptance criteria
- [ ] Each match path tested: reported, inferred, none; staging vs production by `Origin`; unknown version falls back to inferred.
- [ ] Deleting a release keeps the report, nulls `release_id`, keeps the version string.
- [ ] Report list filter by release / version, and the release detail report counts, are covered.
- [ ] `make check` and `alembic check` green.

## Technical notes
- Files: `app/modules/bug_reports/` (from ADM-028), `app/modules/releases/` (ADM-033), migration.
- ADM-032 (guide for app teams): apps must send their build version (e.g. `VITE_APP_VERSION`).

## Test plan
- API tests per criterion; migration round-trip.

## Notes / answers from Ming

## Handoff
