# BUG-001: `test_purge_batches_share_cutoff` fails intermittently in `make check`

| Field | Value |
|---|---|
| Type | bug |
| State | DONE |
| Severity | S3 |
| Priority | P3 |
| Areas | db |
| Found in | local `make check` in cloud sessions (main after v0.1.5) |
| Introduced in | unknown (test added in ADM-004c, commit c3da104) |
| Attempts | 1 |
| Created | 2026-11-17 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/61 |

## Report
Owner/orchestrator, 2026-11-17: `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` fails now and then during the full `make check` (seen several times while running USR-05 … USR-10, e.g. the USR-05a / ADM-015 attempts) and passes when re-run. It does not block CI today but makes `make check` untrustworthy and forces manual reruns.

## Environment
- Instance: local test DB (`org_admin_test`, Postgres 17 in the cloud container) · full test suite order · no HTTP request (service-level test, `AuditPurgeService(db_session, batch_size=2)`).

## Steps to reproduce
1. `service postgresql start`
2. `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (full suite), repeated.
3. Occasionally the test fails; running the test alone 15 times in a row passed 15/15 (2026-11-17), so it depends on suite state/order.

## Expected
- The 5 seeded rows aged 400 days are deleted in batches of 2/2/1; three `audit_log.purged` summary rows with `deleted` = [1, 2, 2] and one shared cutoff; the test passes every run.

## Actual
- Intermittent assertion failure. Captured in CI on PR #54 (run 44134412773, 2026-11-17): `assert [5] == [1, 2, 2]` at `tests/test_cli_audit_purge.py:171` — one batch deleted all 5 rows although `batch_size=2`, so only one summary row (`deleted: 5`) was written.

## Root cause
**Suspected**, not reproduced in isolation. The purge works on the *whole* `audit_log` table (`app/modules/audit/purge.py:59-71`, `_purgeable(cutoff)`), and the test asserts exact counts (`tests/test_cli_audit_purge.py:165-172`). Candidates (the CI capture points to the first one being a **product** issue: the batch limit was not respected):
- `DELETE … WHERE id IN (SELECT id … ORDER BY … LIMIT :batch FOR UPDATE SKIP LOCKED)` (`purge.py:61-70`): Postgres may re-evaluate or plan the `IN (subquery … LIMIT … FOR UPDATE)` so that more rows than `LIMIT` match (a known pitfall with `LIMIT` + locking subqueries in `DELETE`), which deletes more than one batch at once; the usual fix is a CTE that selects and locks the ids first (`WITH batch AS (SELECT id … LIMIT n FOR UPDATE SKIP LOCKED) DELETE … USING batch`);
- rows **committed** by other tests outside the per-test rollback (e.g. `record_denied` commits the denied row alone; ADM-004d backdate fixture; earlier purge/CLI tests that commit) leave purgeable or `audit_log.purged` rows in the shared test DB, so `deleted`, the batch split or `_summaries` (`tests/test_cli_audit_purge.py:85-92`, filters only on `occurred_at >= now() - 1 day`) see extra rows;
- `FOR UPDATE SKIP LOCKED` (`purge.py:66`) skipping rows held by another open connection from an earlier test, which ends the run early with a short batch.

## Scope
- Make the test (and, if needed, the test DB hygiene) deterministic; nothing else (R7). Product behaviour of the purge stays the same unless the root cause is a real product bug.

## Out of scope
- Changing the retention floor, batch size defaults or the purge CLI contract.

## Acceptance criteria
- [ ] A regression test reproduces the bug: it fails on `main` before the fix and passes after (name the test) — e.g. a test that first commits a stray purgeable/`audit_log.purged` row (the suspected leak) and shows the current assertions break, then the fixed test isolates its own rows.
- [ ] The expected behaviour above holds: the test passes in repeated full-suite runs (record e.g. 5 consecutive green `make check` runs in impl-log).
- [ ] `make check` passes (and `uv run alembic check` if a migration changed).

## Technical notes
- Files: `tests/test_cli_audit_purge.py`, possibly `tests/conftest.py` (cleanup of committed audit rows), `app/modules/audit/purge.py` only if the cause is in the product code.
- Test level: a pytest test against the Postgres test DB (`db_session` fixture, `backdate_audit` marker).

## Dependencies
None.

## Notes / answers from owner
- 2026-11-17 (owner): record as a bug (chosen from the follow-ups list).
- 2026-11-17 (orchestrator): failure reproduced in CI (PR #54, board-only change): `deleted` summaries `[5]` instead of `[1, 2, 2]`. If the root cause is the batch limit not holding, the purge deletes in larger batches than configured (lock time on a big table) — planner to confirm and decide whether severity should be S2.
