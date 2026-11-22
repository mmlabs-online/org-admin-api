# Security review — ADM-038

## Attempt 1
Verdict: PASS

Static review of the ADM-038 changes to `app/` and `migrations/` in commit 1f88761 against origin/main. I did not run pytest or `make check`, as you asked.

**Checked and safe**
- **Release link stays within the app (cookie class).** Both lookups filter on the submitting app's own id, `app.id`: `find_for_report_link` and `live_for_report_link` (app/modules/releases/repository.py:233-262). That app comes from the resolved `X-MM-App`, never from the request body (`extra="forbid"`). `test_submit_other_apps_release_never_links` covers this. Staff reads join the release by `release_id` only. The `app_key` shown with a release in `_view` (app/modules/bug_reports/repository.py:268) is the report's app. That is correct because the link can only ever point at a release of that same app.
- **User-supplied app_version.** On submit it is checked by `text.check_app_version` (a full-string regex match) and capped by the String(64) column. The release lookup runs only after `environment_for_version` matches the version exactly against the same regexes the release CHECK uses. There is no normalisation, so a trailing newline cannot slip through. The submit response (`BugReportCreated`) does not echo it back, and the audit entry and log lines do not include it. The staff filter has `min_length=1, max_length=64` and uses a parameterised exact `==` match.
- **No IntegrityError 500s.**
  - `ON DELETE SET NULL` always leaves `release_id` NULL, and the `release_link_consistent` CHECK accepts that.
  - `release_match` is always set from the enum, so `release_match_valid` cannot fail.
  - The `FOR KEY SHARE` lock on the chosen release closes the race with a concurrent release delete. If the delete commits first, the locked re-check (READ COMMITTED) skips the deleted row and returns None. The report then gets no link instead of an FK error.
  - `app_releases.app_id` is RESTRICT, so deleting an app cannot cascade into this.
- **Staff filters.** `release_id` is parsed as a UUID (422 if invalid), `release_match` is an enum (422 if invalid), and `app_version` is length-limited. They sit on the existing list route, which still requires `admin:bug-reports:read`, and they are ANDed with the existing filters.
- **Release detail counts.** The query is `WHERE release_id = :id` (one release, therefore one app). It is served only on the staff route `GET /api/v1/releases/{id}` under `admin:*`. The machine routes still return `ReleaseDetail` / `ReleaseSummary` with no bug-report data.
- **Audit.** The new details are only `release_id` (as a string) and `release_match`; the log line adds only `release_match`. No description, version or user agent is included.
- **Migration** (migrations/versions/20261122_b7d3f1a9c542_adm038_bug_report_release_link.py). It adds a nullable column and a NOT NULL column with a constant default, so Postgres does not rewrite the table. The FK validates quickly because every existing row is NULL. `downgrade()` reverses everything in the correct order: index, both CHECKs, FK, then both columns. The only loss is the link data, which is expected.

**Findings**

💭 app/modules/bug_reports/router.py:76 and :104 — Bug-report list and detail now include a `ReleaseSummary` (git_sha, deployment_pr, deploy_run_url) for anyone with `admin:bug-reports:read`. Release routes themselves require `admin:*` (app/modules/releases/router.py:27). Today this is low sensitivity: internal staff, build metadata only, no secrets. The plan approved it (plan.md:129). If release metadata is ever scoped more tightly, either remove `deploy_run_url` and `deployment_pr` from the summary embedded in bug reports, or return `release` only when the caller also holds the release read permission.

💭 app/modules/releases/repository.py:249-262 — If the release chosen by `live_for_report_link` is deleted while a submit waits on its lock, the query returns no row instead of the next-latest deployment. The report is then saved as `none` instead of `inferred`. It is harmless and the link is informational. Optionally document it in ADR-0028 alongside the existing note on deleted links.

💭 app/modules/releases/repository.py:11 — The releases repository now imports `BugReport`, so the two modules depend on each other (bug_reports already imports from releases). This is not a security issue. Follow-up: move `bug_report_counts` into the bug_reports repository and call it from the release service, to avoid import-cycle problems later.

**Follow-ups (out of scope)**
- If release permissions are split away from `admin:*` later, review the release summary shown on bug reports at the same time (first finding above).
