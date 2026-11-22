# ADR-0028: Bug reports link to the release the user ran

Status: Proposed (accepted when the owner merges ADM-038)
Date: 2026-11-22

Implements spec §5.12 "Link to the release the user ran" and OQ50 (default answer).

## Context
Staff triaging a report need to know which release the reporter ran. A report carries the app's
optional free-text `app_version` and no environment.

## Decision
- **Stored at submit**, in the report's transaction: `bug_reports.release_id` (FK
  `app_releases.id`, `ON DELETE SET NULL`) and `release_match` (`reported` | `inferred` | `none`).
  Later redeploys or rollbacks never change a stored link. No backfill: old rows are `none`.
- **Rules:** (1) `app_version` matches by exact, case-sensitive string equality (no `v`
  normalization) a release of that app whose environment follows from the version grammar
  (`vX.Y.Z` is production, `sha-<7 hex>` is staging), any status: `reported`. (2) Else the
  production release of the app with the latest `deployed_at` <= the report's `created_at`
  among `status = deployed` (OQ50 default: production only): `inferred`. (3) Else `none`.
  The link never blocks submit.
- **Release delete** nulls `release_id` (DB `SET NULL`); `release_match` keeps its value, so
  `reported`/`inferred` with `release: null` means "the linked release was deleted".
  CHECK `release_id IS NULL OR release_match <> 'none'` allows this.
- **Reads:** staff list/detail return `release_match` and a `ReleaseSummary` (one joined query);
  the list filters by `release_id`, `app_version` (exact) and `release_match`. Release detail
  (`GET /api/v1/releases/{id}`, staff only) adds `bug_reports: {open, done}`; machine routes keep
  `ReleaseDetail` without bug-report data.
- **Audit** `bug_report.created` adds `release_id` and `release_match` (ids and enum only).

## Consequences
- Partial index `(release_id, status) WHERE release_id IS NOT NULL` serves the filter, the counts
  and the `SET NULL` scan on release delete.
- Choosing the environment from the validated `Origin` instead is possible later (spec OQ50).
- Apps should send exactly the release version string as `app_version`.
