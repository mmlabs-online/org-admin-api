## Attempt 1 — 2026-11-19

Environment: no Docker; local Postgres started with `service postgresql start` (databases `org_admin`, `org_admin_test`, default TEST_DATABASE_URL). Alembic head was `d9e4b2a61f73` (confirmed with `uv run alembic heads`) before the new revision `b3f7a9c1d452`.

Approved decisions applied (Ming, 2026-11-19, batch questions 7-15 = plan questions 1-9, all defaults): **AC2 amended: a disabled user gets 401 `session_invalid` (not 403)**; caps 10/24 h and 10 open per user per app, both 429 with own codes; `org_id` validated; field limits; `app_id` RESTRICT; `user_id` NOT NULL RESTRICT; audit `org_id` NULL; page URL keeps a non-default port. Decision record: **ADR-0019** (ADM-027 took 0018, so the plan's 0018 is renumbered) plus D28.

### Files touched
| File | Required because |
|---|---|
| `app/modules/bug_reports/{__init__,models,schemas,text,errors,repository,service,deps,auth_router}.py` | new module (table, strict schema, pure text rules, 429 codes, advisory-lock caps, submit service, `admin` refusal, route) |
| `app/api/router.py` | mount the route |
| `app/db/models.py` (High path) | import `BugReport` (R2) |
| `migrations/versions/20261119_b3f7a9c1d452_adm028_bug_reports.py` (High path) | table, 6 CHECKs, 5 indexes, working `downgrade()` |
| `app/core/config.py`, `.env.example` (High path) | 6 `BUG_REPORT_*` settings (R5), no values in `.env.example` |
| `app/core/rate_limit.py` (High path) | `bug_report_create` per-IP scope |
| `app/modules/audit/actions.py` | `BUG_REPORT` target, `bug_report.created` action |
| `tests/test_route_auth_classes.py` | route added to COOKIE |
| `tests/test_audit_coverage.py` | route added to `IDENTITY_AUDITED` with a success scenario (the classification test failed otherwise) |
| `docs/adr/0019-bug-report-submit.md`, `docs/identity-architecture.md` (§4 row, D28), `CLAUDE.md` (R3 class 2) | decision record and route lists |

### Tests added / changed
- `tests/test_bug_reports_schemas.py`: URL reduction/rejections, description/app_version/user_agent rules, extra fields forbidden, model/constant limits agree.
- `tests/test_bug_reports_schema_db.py`: CHECKs (status, handled_iff_done, handler_only_when_done, description length, page_url_reduced), app delete RESTRICT.
- `tests/test_bug_reports_submit.py` (46 cases): 201 and stored fields, rotated cookie, org_id valid/denied/unsupported, 401/403 refusals (incl. `admin` app, unverified, non-member, disabled user 401), 422 matrix, per-IP and per-user 429, daily cap with `Retry-After`, old reports not counted, open cap per app, audit row ids only, logs without content, audit failure rolls the report back, CORS preflight/post.
- `tests/test_bug_reports_concurrency.py`: three parallel submits at the cap, one 201 and two 429; verified that it fails (3x 201) with `lock_reporter` removed.
- `tests/test_bug_reports_migration.py`: downgrade to `d9e4b2a61f73` drops the table, upgrade recreates it.
- Changed: `tests/test_audit_coverage.py`, `tests/test_route_auth_classes.py` (registries).

### Checks (actual output summary)
- `uv run ruff check . --exclude .github/scripts`: All checks passed!
- `uv run ruff format --check app tests migrations`: all files formatted (run after the last edit via `ruff format`)
- `make typecheck`: Success: no issues found in 164 source files
- `uv run alembic check`: No new upgrade operations detected.
- `make test` (full): 2921 passed, 1 warning in 405.71s

### Out-of-plan files
- `tests/test_audit_coverage.py` — its route-classification test requires every cookie POST to be listed; the route writes exactly one audit row, so it is added to `IDENTITY_AUDITED` with a scenario.

### Follow-ups (noticed, not done)
- Global request-body size cap -> 413 (R10).
- `@mmlabs/auth-client` helper and a `docs/consumers.md` note for apps.
- admin-pan must render description/URL/UA as plain text (ADM-029 / admin-pan).
- Shared `_reporter`/`_user` helper across cookie services (now duplicated in orgs, tokens, bug reports).
- Precise app-delete 409 reason when only reports remain (touches `rbac`).
- Optional double-submit dedupe / Idempotency-Key.
- Reject Unicode bidi overrides in descriptions (spoofing in admin-pan), if wanted.

### Scope self-check
- Could the diff be smaller? No: every file is in the plan; one extra registry edit (`test_audit_coverage.py`) was forced by an existing guard test.
- Diff size: about +1,500 lines including tests and docs (about 450 production, rest tests/docs/ADR).
