## Attempt 1 — 2026-11-20
Verdict: PASS

Reviewed product commit 901d91b (`git diff origin/main...HEAD`). Local Postgres (`service postgresql start`), no Docker.

### Checks (run by reviewer)
- `make typecheck` — `Success: no issues found in 173 source files` (exit 0).
- `make test` (full) — `3060 passed, 1 warning in 412.82s` (exit 0). The warning is the existing pty/forkpty DeprecationWarning. Matches impl-log.
- `uv run ruff check . --exclude .github/scripts` — `All checks passed!` (the pre-existing failure in the kit-managed `.github/scripts/risk_tier.py` is excluded, as agreed).
- `uv run ruff format --check app tests migrations` — `335 files already formatted`.
- `uv run alembic heads` — one head, `89fd8d667a25`. `uv run alembic check` — `No new upgrade operations detected.`
- Migration round trip on the dev DB: `alembic downgrade -1` (89fd8d667a25 → c4e8a2f7b916), after which `to_regclass('app_releases')` is NULL. Then `alembic upgrade head`: all 11 `ck_app_releases_*` CHECKs, the FK, the PK, the unique constraint and the 3 indexes are back, with the partial `WHERE status = 'deployed'` on `ix_app_releases_live`. `alembic check` is still clean afterwards.
- CHECK text parity (Alembic does not compare CHECKs): a scripted AST comparison found all 11 CHECK texts in `models.py` and in the migration identical (`11 11 True`, symmetric difference empty).

### Acceptance criteria
- [x] Migration up/down clean; `alembic check` no drift — evidence: `tests/test_releases_migration.py › test_app_releases_down_up` passed, and my own downgrade/upgrade run and `alembic check` above.
- [x] DB CHECKs reject bad version/environment/status combinations — evidence: `tests/test_releases_schema_db.py` (all `test_rejects_*` cases, each insert in `begin_nested()` and asserting the constraint name; `changes={}` raises `IntegrityError`, not `DataError`, through the `CASE` form), `test_accepts_report_created_and_boundaries`, `test_unique_app_environment_version`, `test_app_with_release_cannot_be_deleted_at_db`. All passed.
- [x] List/detail/live/delete require `admin:*` — evidence: `test_releases_api.py › test_routes_401_without_token`, `test_routes_403_without_wildcard` (and the row survives the 403 DELETE), `test_openapi_declares_admin_wildcard`; `tests/test_route_auth_classes.py` passes with the 4 `BEARER` entries and `release_id` in `_fill`.
- [x] Filters, paging (`limit` ≤ 100), newest-first order; detail 404 — evidence: `test_list_newest_first_with_id_tiebreak`, `test_list_filters` (inclusive `created_from`, exclusive `created_to`), `test_list_sort_deployed_at_nulls_last`, `test_list_paging_and_bounds` (`limit=101` and `limit=0` → 422), `test_list_validation_422` (6 cases), `test_list_unknown_app_is_empty_page`, `test_detail_unknown_404_and_bad_id_422`.
- [x] Live = latest `deployed_at` among deployed records per app + environment, rollback case seeded — evidence: `test_live_rollback_returns_redeployed_older_version`, `test_live_ignores_failed_and_pending`, `test_live_environments_do_not_leak`, `test_live_tie_breaks_on_id`, `test_live_route_not_shadowed`. Implementation: `app/modules/releases/repository.py:144-180` (DISTINCT ON subquery ordered `deployed_at DESC, id DESC`).
- [x] DELETE writes an audit row in the same transaction (ids only) — evidence: `test_delete_writes_one_audit_row_ids_only` (exact `changes` dict, no title or URL), `test_delete_unknown_404_writes_no_row`; `tests/test_audit_coverage.py` with the route in `AUDITED` (count 28). The audit row is recorded on the request session before commit (`app/modules/releases/service.py:38-60`).
- [x] OpenAPI schemas for admin-pan — evidence: `test_openapi_release_schemas` (components present, no bodies in `ReleaseSummary`, nullable fields use `anyOf … null`).
- [x] `make check` equivalent green — all of the checks above.

Ming verification after merge (not verified here; for the PR): once the production deploy has run `alembic upgrade head`, `GET https://api.mmlabs.online/api/v1/releases` with an `admin:*` token returns an empty page, and so does `/api/v1/releases/live`.

### Scope
Every product file in 901d91b is in the plan's file list. The `_fill` edit is inside the planned `tests/test_route_auth_classes.py`. High paths changed: `migrations/versions/20261120_89fd8d667a25_adm033_app_releases.py` and `app/db/models.py`. Both are named in the plan, which Ming approved on 2026-11-20. The other files in the branch diff (`work/backlog.md`, `work/batches/2026-11-20-1.md`, `work/tickets/ADM-041/ticket.md`, `work/tickets/ADM-033/ticket.md`) are orchestrator bookkeeping commits, not product changes.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the router builds schemas from frozen `ReleaseView` dataclasses and never touches the ORM. The service raises only `NotFoundError` / `UnprocessableError`. SQLAlchemy stays in the repository.
- R2: the migration has a working downgrade (verified), the model is registered, and there is no drift.
- R3: class 1, `admin:*`.
- R4: same-session audit.
- R5/R11: the log line holds ids only.
- R6: 204, 404 and 422 use the envelope.
- R9: plural `/releases`, `Page[T]`, UUID ids, timestamptz. `/releases/live` is a `Page[T]` sub-collection under the Q1 default.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴; one 🟡 (document `ReleaseChange.pr_url` as untrusted in OpenAPI) → follow-up.

### Nits (do not block)
- 💭 `repository.py:90-92` `_view(row)` builds `ReleaseView` positionally. It depends on `_columns()` keeping the dataclass field order. Keyword construction (`row._mapping`) would be sturdier when ADM-034 adds columns.
- 💭 `repository.py:12` imports `ReleaseSort` from `schemas.py` into the repository layer. This is harmless (a plain StrEnum), but it could live in `models.py` next to the other enums.
- 💭 `router.py:36-41` `_detail` re-validates the jsonb items into `ReleaseImage` / `ReleaseChange`. A stored item missing `pr_url`/`title` or with an unknown `risk` would cause a 500. The plan accepts this (only ADM-034/036 write, with strict validation), but ADM-034 must enforce exactly these shapes.
- 💭 The plan's `test_live_environments_do_not_leak` mentions "the same version string in both environments". The CHECKs make that impossible (production `vX.Y.Z` vs staging `sha-…`), so the test instead uses the same version across two apps. That is fine.

### Follow-ups (out of scope)
- rbac app-delete 409 message ("App has role assignments; disable it instead") is misleading when releases block the delete (`rbac/service.py`, High path); already noted in plan/impl-log.
- ADM-034/036 write validation must be at least as strict as every CHECK here, and must also enforce the item shapes that `ReleaseDetail` re-validates (`ReleaseImage{name,digest}`, `ReleaseChange{pr_url,title,ticket_id?,risk∈low|medium|high?}`) and `version` ≤ 32 chars.
