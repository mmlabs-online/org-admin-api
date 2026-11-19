## Attempt 1 — 2026-11-19
Verdict: PASS

Reviewed product commit 1c2ffb2 (branch `claude/festive-volta-rikup7`). The batch bookkeeping in `work/` in the branch
diff is not part of this implementation. Plan approved by Ming 2026-11-19 with all defaults: AC2 amended so a
disabled user gets 401 `session_invalid`, and the ADR renumbered to 0019 because ADM-027 took 0018.

### Checks (run by reviewer)
- `make typecheck` — Success: no issues found in 164 source files.
- `uv run ruff check . --exclude .github/scripts` — All checks passed!
- `uv run ruff format --check app tests migrations` — 317 files already formatted.
- `make test` (full) — **2921 passed, 1 warning in 398.43s**. My first run gave 4 failed and 2 errors, all with
  `relation "apps" / "audit_log" does not exist` or alembic "Nothing to drop", across unrelated files
  (`test_audit_migrations`, `test_auth_cors`, `test_email_lock_concurrency`, `test_audit_logs_export`). This means
  another process reset the shared `org_admin_test` DB during the run. With no other pytest running, the re-run was
  fully green and matches impl-log (2921 passed).
- `uv run alembic check` — No new upgrade operations detected.
- Migration by hand on `org_admin`: `alembic downgrade d9e4b2a61f73` dropped `bug_reports` (`to_regclass` NULL), and
  `alembic upgrade head` recreated it with pk, 4 FKs, 6 CHECKs and 5 named indexes as in the plan. `alembic check`
  was still clean afterwards. `tests/test_bug_reports_migration.py::test_bug_reports_down_up` also passes on a
  throwaway DB.
- Pre-existing on main, not this ticket: plain `ruff check .` / `ruff format --check .` fail only on the kit-managed
  `.github/scripts/risk_tier.py`.

### Acceptance criteria
- [x] AC1: a verified member gets 201 with its id, and the reporter and app come from the session and `X-MM-App`.
  Evidence: `test_submit_201_records_reporter_and_app`, `test_submit_rotated_cookie_is_set`,
  `test_submit_body_extra_field_rejected_422` (mass assignment). `service.py` takes `app_id` from
  `ReportAppContextDep` and `user_id` from the session. The schema uses `extra="forbid"` (`schemas.py:20`).
- [x] AC2 (amended): the refusals are covered by `test_submit_no_cookie_401`, `_invalid_cookie_401`,
  `_foreign_account_id_401`, `_disabled_user_401_session_invalid`, `_missing_app_header_403`,
  `_origin_of_other_app_403`, `_missing_origin_403`, `_unknown_app_403`, `_disabled_app_403`, `_admin_app_403`
  (`deps.py:15`), `_unverified_403_email_unverified` and `_non_member_403_not_member`. Each refusal test asserts
  `_count(db_session) == (0, 0)`, meaning no report row and no audit row (17 such assertions).
- [x] AC3: 422 for empty or too-long description and for over-long or invalid optional fields; the page URL is
  reduced. Evidence: `tests/test_bug_reports_schemas.py` (URL rejections, userinfo/query/fragment strip, default
  port, Unicode path, description/app_version/UA bounds and control characters), `test_submit_validation_422`,
  `test_submit_page_url_stored_reduced`. DB backstops: `test_description_length_check`, `test_page_url_reduced_check`.
- [x] AC4: 429 with the existing envelope, and the caps are enforced in the DB. Evidence:
  `test_submit_per_ip_rate_limited_429`, `_per_user_burst_429`, `_daily_cap_429_retry_after` (spans two apps;
  `Retry-After` ≈ 22 h and equals `details.retry_after`), `_old_reports_do_not_count`, `_open_cap_429_per_app`
  (another app is still accepted, and marking one report done frees a slot). Race:
  `test_bug_reports_concurrency.py::test_parallel_submits_at_cap_one_wins`. The caps run under a per-user advisory
  lock and count on the DB clock (`repository.py:22-40`).
- [x] AC5: the audit row is written in the same transaction and carries ids only; nothing with content is logged.
  Evidence: `test_submit_audit_row_ids_only` (`changes` keys == `{"attachment_count"}`, target, app_key, actor,
  `org_id` NULL), `test_submit_logs_no_content`, `test_submit_audit_failure_rolls_back_report`. The route is also
  registered in `tests/test_audit_coverage.py`.
- [x] AC6: CORS answers registered origins with credentials. Evidence:
  `test_bug_reports_preflight_registered_origin`, `_preflight_unregistered_origin_no_acao`,
  `_post_response_has_acao`. The route is in the `COOKIE` set of `tests/test_route_auth_classes.py`.
- [x] Plan item 7 (schema and migration, R2): verified as described under Checks, plus the `test_bug_reports_schema_db.py` CHECK and RESTRICT tests.
- [x] Plan item 8 (docs): the `bug reports — D28` route is added to CLAUDE.md R3 class 2. `identity-architecture.md`
  gets a new §4 row and a new D28 entry. `docs/adr/0019-bug-report-submit.md` is new.

Ming verification after merge (not verified here; it goes on the PR):
- After deploy, run the migration as the separate deploy step (`docs/deploy.md`). Then, from a real app origin (e.g.
  ricotdin) signed in through `@mmlabs/auth-client`, submit one report and confirm the `bug_reports` row and the
  `bug_report.created` audit entry.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the router does HTTP only, the service raises only `AppError` subclasses, and the repository holds only
  SQLAlchemy.
- R2: hand-reviewed migration with a working downgrade; the model is in `app/db/models.py`.
- R3 class 2: the route uses `AppContextDep` (Origin + `X-MM-App`) and `DeviceDep`, with no `require_permission`.
- R4/AC5: the audit entry is written in the same session.
- R5: 6 settings, and `.env.example` has the keys without values.
- R6: existing envelope only.
- R7: one out-of-plan file, justified (see Scope).
- R8: tests cover happy path, 401/403, 422 and 429. No 404 applies because there is no path id.
- R9: `/api/v1/auth/bug-reports`, UUIDs, timestamptz.
- R11: no content or email in logs or audit; verified email is required; Origin + `X-MM-App` are checked.

Scope: every changed product file is in the plan's file list. The plan's ADR path is `0018`; the diff uses `0019`, a
rename Ming approved. The one file outside the plan, `tests/test_audit_coverage.py`, is justified in impl-log because
an existing guard test requires every cookie POST to be classified. The High paths changed (`migrations/**`,
`app/db/models.py`, `app/core/config.py`, `app/core/rate_limit.py`, `.env.example`) are all named in the plan.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 (security-review.md). Its 🟡 (page_url host / app_version accept HTML metacharacters; stored-XSS precursor for the ADM-029 admin UI) is a follow-up.

### Nits (do not block)
- `service.py:535`: the in-process per-user burst hit is taken before the DB cap checks, so a request refused by the
  daily or open cap still uses up a burst slot. This is harmless at 3/min.
- Some test names differ from the plan. `test_submit_body_app_key_rejected_422` became
  `test_submit_body_extra_field_rejected_422`, and `test_submit_refusals_write_nothing` was folded into per-test
  `_count == (0, 0)` asserts. `test_submit_org_id_unknown_403_same_body` was merged into
  `test_submit_org_id_not_member_403`. Coverage is the same.

### Follow-ups (out of scope)
- Kit-managed `.github/scripts/risk_tier.py` fails `ruff check .` and `ruff format --check .` on main; fix it in
  spaceM project-kit.
- The shared `org_admin_test` DB is reset by DDL/migration tests, so two pytest runs at the same time (e.g. reviewer
  and appsec agent) break each other. Consider a per-run test DB name.
- From impl-log:
  - global request-body size cap → 413;
  - `@mmlabs/auth-client` helper and a `docs/consumers.md` note;
  - admin-pan renders description, URL and UA as plain text;
  - shared `_reporter`/`_user` helper across the cookie services;
  - precise app-delete 409 reason when only reports remain;
  - Idempotency-Key / double-submit dedupe;
  - reject Unicode bidi overrides in descriptions.
