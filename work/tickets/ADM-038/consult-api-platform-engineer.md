# Consult — api-platform-engineer — ADM-038 (2026-11-22)

**Findings**
- The submit field already exists: `app_version` is optional on the cookie route (`app/modules/bug_reports/schemas.py:35`). It is 1..64 printable ASCII, with no format check (`text.py:11,17,47`, ADR-0019 §5). Over-long or invalid values return 422, never truncation. `BugReportCreate` is `extra="forbid"` (schemas.py:29), so adding a body field breaks no one but is unnecessary.
- `BugReportRead.app_version` is already exposed on staff detail (schemas.py:93). `BugReportListItem` (60-72) has no version or release fields.
- Staff list filters today are app_key, status, reporter_id, from and to (`router.py:123-129`). There is no release or version filter.
- Release formats are `^v\d+\.\d+\.\d+$` (production) and `^sha-[0-9a-f]{7}$` (staging) (`releases/models.py:45-46`).
- `ReleaseSummary` (`releases/schemas.py:56-73`) already carries version, environment, status, `deployed_at` and `deployment_pr`. It can be reused as the linked-release summary.
- Spec conflict: spec §5.12 (lines 168-172) says "inferred" means the latest `deployed_at` at or before report `created_at`, and environment comes from the version grammar (OQ50). The ticket says environment comes from the validated `Origin`, and inferred is the latest deployed release. Spec §5.12 and OQ50 must be amended in the same PR. The spec wins until it is changed.
- `docs/consumers.md` has no "bug report" matches in the search I ran. The app-facing contract lives in `docs/identity-architecture.md:179` and ADR-0019. Both need a note that `app_version` is the build version, plus the Origin-to-environment rule.

**Recommendations**
1. Keep the submit body unchanged. `app_version` stays optional and format-agnostic: store the string as sent, never validate it against the grammar, and accept an unknown version. An unknown version falls back to `inferred`. Apps that don't send it work unchanged, and the response `{id, status, created_at}` stays the same, so this is additive within v1 (R9).
2. Match case-sensitively and exactly on `(app, environment, version)`. Do not normalize, and do not accept a missing `v` prefix, so matching stays predictable. Document that apps send exactly the string they pass to the deploy report (`vX.Y.Z` or `sha-xxxxxxx`).
3. Add to `BugReportRead` and `BugReportListItem` (all additive):
   - `release_match: "reported" | "inferred" | "none"` (a `StrEnum`).
   - `release: ReleaseSummary | None`.
   - Keep `app_version` as the raw string.
   - The enum doc must say a client should tolerate unknown values.
   - The `inferred` flag is just `release_match == "inferred"`. Do not add a separate boolean.
4. Add `release_id: uuid.UUID | None = Query()` and `app_version: str | None = Query(max_length=64)` to the list (exact match). Document that `release_id` matches the linked release regardless of `release_match`, and that `app_version` matches the version string as sent. Invalid UUID gives 422. Both filters combine with AND, like the existing filters.
5. Store at submit. A later redeploy then cannot rewrite which release an `inferred` report points to, and the filter is a plain indexed column. If the Ming-gated choice is compute-at-read, `inferred` would drift. Add an index on `bug_reports.release_id`.
6. Environment comes from the validated Origin only, never from the body. Staging is the host `staging-<app>.mmlabs.online`; everything else is production. Reuse the existing Origin validation and add a unit test for the host-to-environment mapping.
7. Release detail count: add `bug_reports: {open, done}` to `ReleaseDetail` only (not to `ReleaseSummary`, so lists stay cheap). Use a grouped count query in the releases repository. Do not import bug_reports models into the router. Put it behind a service call to keep R1.
8. R3, R6 and R9: no new routes, and the auth classes are unchanged. Staff reads stay on `admin:bug-reports:read`. The release detail keeps its existing permission, and the report counts are aggregates only. No new error codes.
9. Audit: add `release_id` and `release_match` to `bug_report.created` details, ids only.

**Risks**
- Spec drift: §5.12/OQ50 must be updated together with the code, or the reviewer will flag a spec conflict.
- `ON DELETE SET NULL` leaves `release_match = reported` with a null `release`. Document that this combination means the release was deleted, and keep `app_version` visible. The tests must cover this case (ticket AC2).
- Inferred links can be wrong when a user runs a stale cached build, and `inferred` reports that. The filter by `release_id` will include inferred reports. Staff may want `release_match` as a filter too (see Q2).
- The Origin to environment rule misclassifies any other non-production origin (preview, localhost). Check `apps.origins` for such entries.

**Open questions for Ming (with defaults)**
1. Replace the OQ50 grammar rule with environment-from-Origin, and drop the `<= created_at` clause for inferred? Default: yes, amend the spec in this PR.
2. Add a `release_match` list filter? Default: yes (`release_match=reported|inferred|none`), because it is cheap and avoids misleading counts.
3. Store at submit or compute at read? Default: store at submit.
4. Should the release detail counts include `inferred` reports? Default: count all linked reports, and show reported and inferred counts separately only if wanted later.
