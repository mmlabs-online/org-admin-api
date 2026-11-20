## Attempt 1 — 2026-11-20

Environment: no Docker; local Postgres started with `service postgresql start` (databases org_admin and
org_admin_test, default TEST_DATABASE_URL). Alembic head was `b3f7a9c1d452` (confirmed); new head `c4e8a2f7b916`.
Plan defaults for Q1-Q7 implemented as approved.

### Files touched
| File | Required because |
|---|---|
| migrations/versions/20261120_c4e8a2f7b916_adm029_bug_report_permissions.py | seed of 2 keys, working downgrade (R2) |
| app/modules/bug_reports/{router,staff_service,triage}.py | new admin router, HTTP-free service, pure rules |
| app/modules/bug_reports/{repository,schemas,text}.py | staff repository/views, schemas, `clean_resolution_note` |
| app/modules/audit/actions.py | 3 audit actions |
| app/api/router.py | mount |
| tests/* (staff_api, triage, permissions_migration, schemas, concurrency, route_auth_classes, audit_coverage, rbac_schema, openapi_contract) | plan test list, registries |
| docs/adr/0020-bug-report-triage.md, docs/identity-architecture.md, docs/deploy.md | ADR, §4 row + D29, audit query 2a |

### Tests added / changed
- test_bug_reports_staff_api (42 cases incl. parametrized 422s), test_bug_reports_triage, test_bug_reports_schemas (update tests), test_bug_reports_permissions_migration, test_bug_reports_concurrency::test_concurrent_mark_done_one_row
- Registries: BEARER (3), `_fill` report_id, AUDITED (27), ADMIN_LEAF_PERMISSIONS, `_EXPECTED_ERRORS`, `test_bug_reports_operations`, `test_bug_report_untrusted_text_documented`
- Deviation: one parametrized `test_patch_invalid_422` replaces the separate 422 tests. Audit rows written in one transaction share `occurred_at`, so API tests order them by action.

### Checks (actual output summary)
- ruff check (`. --exclude .github/scripts`): All checks passed
- format:check (app tests migrations): 324 files already formatted
- typecheck: Success: no issues found in 167 source files
- alembic check: No new upgrade operations detected
- test (full `make test`): 2998 passed, 1 warning in 411.49s

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- New ticket: tighten `page_url` host grammar and `app_version` charset (+ CHECK migration), optional Unicode Cf stripping.
- admin-pan: render report fields as text, show bidi chars, build links only after re-checking http(s).
- Global `X-Content-Type-Options: nosniff` on JSON (app/core, High path).
- Status-only list index if bug_reports passes ~100k rows.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: see `git show --stat`.
