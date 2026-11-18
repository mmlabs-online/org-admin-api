# Implementation log: BUG-001

## Attempt 1 — 2026-11-18

### Step 0: reproduced (audit, send_log, tokens); join_requests not forced
Environment: local Postgres 16 (not 17 as the plan assumed), `org_admin_test`.

Recipe (`tests/purge_plan.py::force_rescanned_semijoin`, `SET LOCAL` in the test transaction):
`enable_hashjoin = off; enable_mergejoin = off; enable_material = off; enable_hashagg = off; enable_sort = off` (plan recipe 0a; index/seq scans left on). It worked on the first try; recipes 0b were not needed for audit.

5 rows aged 405..401 days inserted oldest first; `EXPLAIN` of the old `delete_batch` statement (limit 2):
```
Delete on audit_log
  ->  Nested Loop Semi Join
        Join Filter: ("ANY_subquery".id = audit_log.id)
        ->  Seq Scan on audit_log
        ->  Subquery Scan on "ANY_subquery"
              ->  Limit
                    ->  LockRows
                          ->  Index Scan using ix_audit_log_occurred_at_id on audit_log audit_log_1
                                Index Cond: (occurred_at < ...)  Filter: (action <> 'audit_log.purged')
```
The limited subquery is the inner side of the semi join with no Materialize and is rescanned per outer row. Observed: `deleted == 5` for `batch = 2` (`assert 5 == 2`).

Regression test before the fix, unchanged `app/`:
`test_delete_batch_respects_limit_when_subquery_rescanned`, run 10 times: **failed 10/10**, each `assert 5 == 2`.
Other sites before the fix: `test_purge_send_log_respects_limit_when_subquery_rescanned` FAILED (`assert 5 == 2`), `test_purge_tokens_respects_limit_when_subquery_rescanned` FAILED (`assert 5 == 2`).

join_requests (step 0c): not forced. EXPLAIN tried (all with seed 5 approved rows 205..201 d):
- A: hashjoin/mergejoin/material/hashagg off: `Nested Loop -> Unique -> Sort -> Subquery Scan -> Limit -> LockRows -> Sort -> Seq Scan` (subquery outer, Memoize + pk index scan inner).
- B: A + `enable_sort = off`: same shape (disabled Sort is paid on both shapes).
- C: A + `enable_seqscan = off`: same shape.
- D: A + indexscan/bitmapscan/indexonlyscan off: same outer-unique shape, inner `Seq Scan on join_requests`.
- E: D + memoize off; F: A + memoize off: same outer-unique shape.
The subquery always needs a Sort (no `decided_at` index), so unique-ifying it as the outer side always wins. The stats recipe (iii) was not tried. `test_purge_respects_limit_when_subquery_rescanned` is therefore a plain bound test: passes before and after, **not forced, fixed by analogy**.

### Step 2: fix
Compiled SQL of the new audit statement (renders as planned):
```
WITH batch AS MATERIALIZED (SELECT audit_log.id AS id FROM audit_log WHERE audit_log.occurred_at < $1::TIMESTAMP WITH TIME ZONE ORDER BY audit_log.occurred_at, audit_log.id LIMIT $2::INTEGER FOR UPDATE SKIP LOCKED)
DELETE FROM audit_log WHERE audit_log.id IN (SELECT batch.id FROM batch)
```
(the `action <> 'audit_log.purged'` condition is added by `_purgeable`; the compile snippet used a simplified where.) EXPLAIN under the forced recipe after the fix: `Delete -> CTE batch (Limit -> LockRows -> Index Scan using ix_audit_log_occurred_at_id)` and `Nested Loop Semi Join` with inner `CTE Scan on batch`: the LockRows runs once.

### Files touched
| File | Required because |
|---|---|
| `app/modules/audit/purge.py` | `delete_batch`: MATERIALIZED CTE (confirmed bug site) |
| `app/modules/auth/repository.py` | `purge_send_log`, `purge_tokens`: same statement shape |
| `app/modules/join_requests/repository.py` | `purge`: same statement shape |
| `tests/purge_plan.py` (new) | `force_rescanned_semijoin`, `explain` helpers |
| `tests/test_cli_audit_purge.py` | audit regression test (+ import) |
| `tests/test_cli_cleanup.py` | send_log and tokens regression tests (+ import) |
| `tests/test_join_requests_retention.py` | join_requests bound test (+ import) |

`grep -rn scalar_subquery` in the three purge files: no remaining purge statement uses it. `explain()` is defined but not used by any test (kept per plan for impl-log captures; used ad hoc for the captures above).

### Tests added
- `test_delete_batch_respects_limit_when_subquery_rescanned` (audit)
- `test_purge_send_log_respects_limit_when_subquery_rescanned`, `test_purge_tokens_respects_limit_when_subquery_rescanned`
- `test_purge_respects_limit_when_subquery_rescanned` (join_requests, not forced)
Existing tests unchanged; `test_purge_batches_share_cutoff` not rescoped; `tests/conftest.py` untouched.

### Check results
Targeted (audit purge, cleanup, join retention, email lock concurrency): 59 passed.
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree (ruff, format, mypy: "Success: no issues found in 147 source files"):
- 1: **1 failed**, 2423 passed (`tests/test_app_manifest_api.py::test_changing_push_writes_one_row`, see Follow-ups; not a purge test)
- 2: 2424 passed; 3: 2424 passed; 4: 2424 passed; 5: 2424 passed; 6: 2424 passed
So 5 consecutive green runs (runs 2-6) after one unrelated failure. An earlier pair of attempts failed `ruff` E501 in `auth/repository.py` (fixed before the runs above).
`uv run alembic check`: "No new upgrade operations detected."

### Out-of-plan files
None.

### Follow-ups
- `tests/test_app_manifest_api.py::test_changing_push_writes_one_row` is itself flaky: `assert 'c' not in <audit row text>`; the row text contains random uuids/hex that sometimes include `c` (seen in run 1). Needs its own bug ticket.
- Plan assumed PG 17; local is PG 16. The reviewer should confirm the recipe on CI's PG 17 (GitHub Actions).
- The CI failure `[5]` matches this overrun; without the GUCs the natural plan shape was not reproduced locally.
- Severity S3 -> S2 recommendation stands (plan, owner's decision).

### Scope self-check
Only the planned files changed; the repository change per site is the CTE swap; no commit made (as instructed).

## Attempt 2 (REWORK)

Fixes only the review's findings from Attempt 1.
- `tests/purge_plan.py`: deleted unused `explain()` and the imports only it used (`Executable`, `postgresql`).
- `tests/test_cli_cleanup.py`: one-line docstring on `test_purge_tokens_respects_limit_when_subquery_rescanned`.
- Removed untracked `tests/__pycache__/_explore_tmp*.pyc`.

Check: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff, format, mypy clean; 2424 passed.
Out-of-plan files: none. Follow-ups: unchanged. No commit made.
