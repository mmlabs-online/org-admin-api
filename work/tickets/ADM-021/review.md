## Attempt 1 — 2026-11-18
Verdict: REWORK

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest):
  - `ruff check`: All checks passed. `ruff format --check`: 327 files already formatted. `mypy app`: Success, no issues in 148 source files.
  - pytest on the shared `org_admin_test`: 8 failed, 2540 passed, 3 errors. Not counted against the ticket: another process was running pytest, including migration downgrades, against the same DB at the same time (relations missing, deadlocks).
  - pytest on an isolated throwaway test DB, branch HEAD unmodified: **two full runs, both `1 failed, 2550 passed`**. Both times the failure was `tests/test_stats_repository.py::test_login_count_uses_action_index`.
  - A third HEAD run, with only a diagnostic print added, passed (2551). A fourth run, with `ANALYZE audit_log` added to the new migration test's `finally`, failed the same way.
  - Controls, each on its own fresh DB:
    - `origin/main` full suite: `2534 passed`.
    - HEAD with `--ignore=tests/test_audit_org_index_migration.py`: `2550 passed`.
  - The test passes in isolation and in pairs with the new test files. The failure needs the full-run order.
  - The test asserts the planner picks `ix_audit_log_action`. In the failing runs it picked `Bitmap Index Scan on ix_audit_log_occurred_at_id` instead (`GroupAggregate (cost=9.55..9.57)` vs `12.57` when it passes).
  - Likely mechanism: a plain `CREATE INDEX` re-sets `pg_class.reltuples/relpages` for `audit_log` mid-run. The new down/up test therefore changes the planner's estimates for later EXPLAIN-based tests.
- alembic: `alembic heads` → `d9e4b2a61f73 (head)`. After `alembic upgrade head`, `alembic check` → `No new upgrade operations detected.`
- No commits on `origin/main` are missing from this branch (`git log HEAD..origin/main` is empty).

Mismatch with impl-log: the impl-log reports `2551 passed`. The failure is order- and stats-dependent, so the implementer's run may really have passed. Still, `make check` is not reliably green on this branch, and on main it is.

### Acceptance criteria
- [x] AC1 `org_id` filter (target OR column, ANDed with the other filters).
  - Code: `app/modules/audit/repository.py:70-79`, `app/modules/audit/router.py:38-59`.
  - Tests: `test_filter_org_id_target_or_column`, `test_filter_org_id_row_matching_both_once`, `test_filter_org_id_other_target_type_not_matched`, `test_filter_org_id_with_other_filters` (action, outcome, app_key; both arms), `test_unknown_org_id_empty_page`, and `test_422_validation[org]`. All pass.
- [x] AC2 `GET /api/v1/audit-logs/{audit_log_id}`.
  - Code: `router.py:75-81`, `service.py:273-277`, `repository.py:51-52`.
  - Same `AuditLogRead` as the list; `user_agent` is sanitized on write, so no extra handling is needed.
  - `test_get_audit_log_matches_list_item` checks full JSON equality with the list item. `test_get_audit_log_404` and `test_get_audit_log_422_malformed_id` pass.
- [x] AC3 index-backed.
  - `ix_audit_log_org_id (org_id, occurred_at, id) WHERE org_id IS NOT NULL` is in `models.py:70-76` and in migration `d9e4b2a61f73`. `alembic check` is clean.
  - `test_org_filter_uses_indexes` shows a BitmapOr over both indexes.
  - impl-log EXPLAIN ANALYZE on 200k rows: 3.7 ms (page query) and 1.0 ms (count query).
- [x] AC4 tests. `test_get_audit_log_401` and `test_get_audit_log_403_without_permission` (including the guard-passes → 404 case) are present. The list's existing 401/403 tests still pass. The new route is in `BEARER` in `tests/test_route_auth_classes.py:110`.
- [ ] R8 / "make check is green" (plan AC4 row): FAILED, see Issue 1.

### Issues
#### Issue 1 — 🔴 blocker — test suite not reliably green (R8)
- **Where**: `tests/test_audit_org_index_migration.py:34-44`, which interacts with `tests/test_stats_repository.py:52-66` (`test_login_count_uses_action_index`).
- **Expected**: the full `make check` is green on every run, as it is on `origin/main`. Adding a test must not change the planner choices of other EXPLAIN-based tests.
- **Actual**:
  - With the new test in the suite, `test_login_count_uses_action_index` failed in 2 of 3 unmodified full runs on an isolated DB.
  - It never failed on `origin/main` or with the new file ignored.
  - Cause: `command.downgrade` + `command.upgrade` run DDL against the shared test DB. The `CREATE INDEX` rewrites the heap's `reltuples/relpages`. That moves the costs of `ix_audit_log_occurred_at_id` and `ix_audit_log_action` close enough for the planner to flip.
  - Running `ANALYZE audit_log` in the `finally` does not fix it (verified: still failed).
- **Fix instruction**:
  - **Preferred (stays inside planned files)**: make `test_org_index_down_up` run its down/up cycle against a dedicated throwaway database, so the shared test DB's `audit_log` statistics are never touched. In the test:
    1. Open an AUTOCOMMIT connection to the server's `postgres` maintenance DB (same host and credentials as `TEST_DATABASE_URL`).
    2. `CREATE DATABASE` a uniquely named DB.
    3. Point `cfg.attributes["url"]` at it.
    4. `command.upgrade(cfg, "head")`, then the existing downgrade → assert absent → upgrade → assert `WHERE (org_id IS NOT NULL)` steps.
    5. `DROP DATABASE` in `finally`.
  - **Alternative**: make `test_login_count_uses_action_index` independent of stale heap statistics. For example, `ANALYZE audit_log` inside the test before the EXPLAIN, provided you show that this makes the plan deterministic. This changes a file outside the plan, so list it under `Out-of-plan files` with this issue as the justification.
  - **Either way**: run the full suite three times in a row on an isolated, otherwise idle test DB, and paste the three pytest summary lines into impl-log. No other process may use that DB during the runs.

### Rules R1–R11
No violations found.
- R1: SQLAlchemy stays in the repository, `NotFoundError` is raised in the service, and the router is HTTP-only.
- R2: the migration has a working `downgrade()` and the model matches it.
- R3: the new route is class 1 Bearer with `require_permission("admin:audit:read")` and is in the auth-class map.
- R4: n/a (read-only).
- R6: 404 `not_found` and 422 `validation_error` use the envelope.
- R7: every changed file is in the plan's list, plus workflow files (backlog, ticket state).
- R9: `/api/v1/audit-logs/{audit_log_id}`, UUID id, `Page[T]` kept for the list.
- R11: the 404 message does not echo the id.

R8 is not met only because of Issue 1.

### Nits (do not block)
- `router.py:21-25` `_read_route` duplicates `organizations/router.py:_route`. This follows the plan (R7 forbids moving it to shared code here), but it is a candidate for a shared helper later.
- impl-log "Scope self-check" says the working tree is not a git repo. It is, and `git diff --stat` works.
- AC3 EXPLAIN evidence: in the seed, the column arm matched 0 rows. A seed where the column arm returns rows would make the evidence stronger.

### Follow-ups (out of scope)
- Rows written via `record_in_savepoint` (hard-coded `org_id=None`) and identity-side rows that record the org only in `changes` are invisible to the org filter (plan owner question 3).
- `CONCURRENTLY` index builds for audit DDL once production `audit_log` is large (plan owner question 1).
- The EXPLAIN-based tests (`test_login_count_uses_action_index`, `test_outcome_partial_index_used`, `test_org_filter_uses_indexes`) depend on planner statistics. Consider a shared fixture that runs `ANALYZE` or pins the stats, so future index migrations cannot flip them.

## Attempt 2 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): two full back-to-back runs at HEAD `d000fba`. The Postgres at localhost:5432 had no other sessions, and no other process was running tests.
  - RUN1: `All checks passed!` / `327 files already formatted` / `Success: no issues found in 148 source files` / `2551 passed, 1 warning in 360.69s`, exit 0.
  - RUN2: `All checks passed!` / `327 files already formatted` / `Success: no issues found in 148 source files` / `2551 passed, 1 warning in 360.93s`, exit 0.
  - These agree with the impl-log's three runs (2551 passed each). No mismatch.
  - In Attempt 1, the same unmodified-HEAD setup failed `test_login_count_uses_action_index` in 2 of 3 runs.
- No `adm021_*` databases are left over after the runs, so the `DROP DATABASE ... WITH (FORCE)` cleanup works. The working tree is clean.
- alembic: `alembic heads` → `d9e4b2a61f73 (head)`. After `alembic upgrade head`, `alembic check` → `No new upgrade operations detected.`

### Acceptance criteria
- [x] AC1 `org_id` filter (target OR column, combined with the other filters).
  - Code: `app/modules/audit/repository.py:70-79`, `router.py:38-59`. Unchanged since Attempt 1.
  - Tests: `test_filter_org_id_*`, `test_unknown_org_id_empty_page`, `test_422_validation[org]`. Green in both runs.
- [x] AC2 single-entry read with 404.
  - Tests: `test_get_audit_log_matches_list_item`, `test_get_audit_log_404`, `test_get_audit_log_422_malformed_id`. Green.
- [x] AC3 index-backed.
  - `ix_audit_log_org_id` is in the model and in migration `d9e4b2a61f73`. `test_org_filter_uses_indexes` passes.
  - The down/up migration test (`tests/test_audit_org_index_migration.py`) now runs in an isolated throwaway DB: upgrade head → downgrade → assert absent → upgrade → assert partial predicate. Green.
- [x] AC4 tests. 401/403 on both routes, and the route is in the auth-class map (`tests/test_route_auth_classes.py`). Green.
- [x] R8 / green `make check`: met (Issue 1 from Attempt 1 is resolved).

### Attempt 1 Issue 1: resolved
- `tests/test_audit_org_index_migration.py` no longer runs DDL against the shared `org_admin_test`. It creates a uniquely named DB on an AUTOCOMMIT connection to `postgres` and runs the migration there. In `finally` it drops the DB with `FORCE`. This matches the preferred fix.
- **Out-of-plan change to `tests/test_stats_repository.py:54-56` is justified.**
  - The implementer reports that the throwaway DB alone did not stop the flake (4 failing runs). The reviewer's own Attempt 1 data already showed that `ANALYZE` was not enough.
  - The change is the "make the EXPLAIN test independent of statistics" alternative from the Attempt 1 fix instruction. It is listed under `Out-of-plan files` with that justification.
  - It is three lines inside the test's own rolled-back transaction: `DROP INDEX ix_audit_log_occurred_at_id`, rolled back by the `db_session` fixture. It changes no app code.
  - Precedent: the `backdate_audit` marker already takes ACCESS EXCLUSIVE locks on `audit_log` in the same fixture. The suite runs serially (no xdist in `pyproject.toml`, `Makefile` or `ci.yml`), so the lock is harmless.
  - The assertion still proves what ADM-015 needs: the login-count query can be served by `ix_audit_log_action`, with no seq scan. With the time-only competitor gone, the other remaining `audit_log` indexes are partial or lead with other columns, so the assertion is deterministic.

### Rules R1–R11
No violations found. Attempt 2 changes only test files plus workflow files (`work/backlog.md`, ticket state, impl-log). The R1–R11 assessment from Attempt 1 stands.

### Nits (do not block)
- `test_login_count_uses_action_index` now checks that the action index is *usable*, not that the planner *prefers* it over `ix_audit_log_occurred_at_id`. On test-sized data that preference was never meaningful, so this is acceptable. The test comment could say this explicitly.
- `_admin(f'CREATE DATABASE "{name}"')` interpolates a name into SQL. This is safe because the name is `adm021_` + uuid hex, but a short comment would stop someone copying the pattern with untrusted input.

### Follow-ups (out of scope)
- Carried from Attempt 1: `record_in_savepoint` rows with `org_id=None` and identity rows whose org appears only in `changes` are invisible to the org filter. Also carried: `CONCURRENTLY` builds for future audit DDL.
- Other EXPLAIN-based tests (`test_outcome_partial_index_used`, `test_org_filter_uses_indexes`) can flip the same way. Consider a shared approach, such as a fixture that pins stats or drops competing indexes, or a reusable throwaway-DB fixture for migration down/up tests.
