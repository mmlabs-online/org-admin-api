## Attempt 1 — 2026-11-20
Verdict: PASS

Reviewed product commit `8040b1a` (branch diff vs `origin/main`, excluding `work/` bookkeeping).

### Checks (run by reviewer)
- `make typecheck` — `Success: no issues found in 167 source files`
- `make test` (full) — `2998 passed, 1 warning in 401.22s`, exit 0 (matches impl-log)
- `uv run ruff check . --exclude .github/scripts` — `All checks passed!`
- `uv run ruff format --check app tests migrations` — `324 files already formatted`
- `uv run alembic check` — `No new upgrade operations detected.`; single head `c4e8a2f7b916`
- Seed migration by hand on the dev DB: `alembic downgrade b3f7a9c1d452` → no `admin:bug-reports:%` rows;
  `alembic upgrade head` → `admin:bug-reports:read|t`, `admin:bug-reports:write|t`
- Pre-existing on main (not this ticket): `ruff check .` / `ruff format --check .` fail only on kit-managed
  `.github/scripts/risk_tier.py`.

### Acceptance criteria
- [x] AC1 List/detail need `:read`, PATCH needs `:write`; 401/403, `x-permission`, BEARER — evidence:
  `test_routes_401_without_token`, `test_read_routes_403_with_unrelated_permission` (caller holds WRITE only),
  `test_patch_403_with_read_only`, `test_admin_wildcard_allows_all`; `tests/test_route_auth_classes.py` 3 BEARER
  entries + `report_id` in `_fill`; `test_openapi_contract.py::test_bug_reports_operations`; router
  `app/modules/bug_reports/router.py:29-31` (`current_admin` on the router), `require_permission` per route. All green.
- [x] AC2 Done records caller + time; reopen clears; DB constraint — evidence: `staff_service.py` sets
  `handled_by=actor.user_id, handled_at=func.now()` in one UPDATE (`repository.py` `apply`);
  `test_mark_done_sets_handler_and_time`, `test_reopen_clears_handler_and_time_keeps_note`,
  `test_done_again_keeps_original_handler`; CHECKs `handled_iff_done` / `handler_only_when_done`
  (`models.py:28-29`) covered by `tests/test_bug_reports_schema_db.py`, green in the full run.
- [x] AC3 Audit rows without text; no-op writes none — evidence: `triage.audit_entries` builds changes by hand
  (no `audit/diff`); `test_audit_rows_ids_and_status_only` (SENTINEL absent, `app_key`, target, `org_id is None`),
  `test_status_and_note_in_one_patch_writes_two_rows`, `test_noop_writes_nothing`,
  `test_done_again_keeps_original_handler` (1 row, ETag unchanged), `test_entries_never_contain_text`;
  `test_audit_coverage.py` `AUDITED` = 27. Audit uses the request session, same transaction (R4).
- [x] AC4 404 / 422 / 412 — evidence: `test_get_unknown_404`, `test_patch_unknown_404`, parametrized
  `test_patch_invalid_422` (bad status, null status, empty body, `handled_by`, empty/blank/5001-char note; replaces
  the separately named plan tests, declared in impl-log), `test_patch_stale_if_match_412` (no change, no audit,
  ETag), `test_patch_stale_if_match_on_noop_412`, `test_patch_if_match_current_200`, `test_patch_if_match_star_200`.
  If-Match is checked under the row lock before the no-op short-circuit, as planned.
- [x] AC5 Filters, paging, sort, several apps — evidence: `test_list_across_apps_newest_first` (tie on
  `created_at` broken by `id DESC`), `test_list_filter_app_key`, `_unknown_app_key_empty`, `_status`,
  `_reporter_id`, `_date_range` (to exclusive), `test_list_from_after_to_422` (details `after_to`),
  `test_list_naive_datetime_422`, `test_list_invalid_status_422`, `test_list_paging_bounds`, `test_list_item_shape`.
- [x] AC6 Untrusted text verbatim + documented — evidence: `test_metacharacters_round_trip`,
  `test_bug_report_untrusted_text_documented`.
- [x] AC7 Seed migration downgrade — evidence: `test_downgrade_removes_bug_report_permissions`,
  `test_rbac_schema.py::test_admin_leaf_permissions_seeded`, manual down/up above.
- [x] AC8 Concurrent done → one handler, one audit row — evidence:
  `test_bug_reports_concurrency.py::test_concurrent_mark_done_one_row` (FOR NO KEY UPDATE +
  `populate_existing` re-reads the committed row under READ COMMITTED).

Ming verification after merge (not reviewer-verifiable, carry into the PR):
- Run `alembic upgrade head` in production; run `docs/deploy.md` audit query 2a and confirm the two new keys are not
  reported "outside the seed".
- Decide which staff role(s) get `admin:bug-reports:read` / `:write` (until then only `admin:*`).
- In admin-pan, once its ticket lands: multi-app list, done/reopen, every field rendered as text.

### Scope
Every changed product file is in the plan's file list (migration file is dated `20261120` instead of the plan's
`20261119` placeholder; same file). Two schema classes `BugReportReporter` / `BugReportHandler` instead of
`BugReportPerson` — explicitly allowed by the plan. No `app/core/**` or `app/api/deps.py` change. The only High path
touched is the planned migration (approved 2026-11-20).

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: router builds schemas from frozen `BugReportView`s only, service raises `AppError`
subclasses, repository holds all SQLAlchemy. R2: data-only migration with working, documented-lossy downgrade.
R3: class 1 Bearer + `require_permission`. R4: same-session audit, no row on no-op. R5/R11: log line has ids and
status only; errors carry no resource data. R6: 412 via `PreconditionFailedError`. R9: `/api/v1/bug-reports`,
`Page[T]`, `PATCH`, `limit` ≤ 100, aware datetimes. No N+1 (explicit-column select with joins, one count).

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 or 🟡 (security-review.md). Note: write permission can read reporter email via a no-op PATCH; decide when assigning roles.

### Nits (do not block)
- `tests/test_bug_reports_staff_api.py:232` `test_detail_done_with_deleted_handler` inserts `handled_by=NULL`
  directly instead of deleting a handler account; it proves the response shape, not the FK `SET NULL` path.
- `tests/test_bug_reports_staff_api.py:376-377` the null-on-null case is checked after the audit count assertion,
  so it only proves 200, not "no audit row".
- `tests/test_bug_reports_staff_api.py:263` the round trip puts metacharacters in the `page_url` path only, not the
  host (plan said "path and host"); same code path, so low value.
- `staff_service.py` re-reads the app key with an extra query (`repo.app_key`) after the UPDATE; could reuse the
  join from `get_view`. Harmless.

### Follow-ups (out of scope)
- Kit-managed `.github/scripts/risk_tier.py` fails `ruff check .` / `ruff format --check .` on main; fix in spaceM
  project-kit.
- From impl-log: tighten `page_url` host grammar and `app_version` charset (+ CHECK migration), optional Unicode
  `Cf` stripping; admin-pan render-as-text / link re-check; global `X-Content-Type-Options: nosniff` (app/core,
  High); status-only list index past ~100k rows.
