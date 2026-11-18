Status: READY

# Plan: BUG-001, purge batch limit not respected (`test_purge_batches_share_cutoff` intermittent)

## Spec summary
This is a bug ticket, so no spec section is implemented. The contract the bug breaks is in these lines:
- `docs/deploy.md:842-845` (audit purge): "The cutoff (`now() - N days`, database clock) is computed once and reused by every batch. Batches of 1000 (`FOR UPDATE SKIP LOCKED`, `lock_timeout` 2 s, one commit per batch); each batch that deletes rows writes one `audit_log.purged` row (`system:cli`, `{cutoff, older_than_days, deleted}`) in the same transaction."
- `docs/adr/0009-audit-log.md` amendment (ADM-004c): "Each batch transaction that deletes rows writes one `audit_log.purged` row … The email cleanup follows the same rule: one `email_retention.purged` row per deleting batch."
- `docs/adr/0007-email-verification-and-delivery.md:96-97`: the retention cleanup "deletes `email_send_log` rows older than 48 h and used or expired `email_tokens` older than 7 d, in batches".
- `docs/deploy.md:892-895` (USR-15): every join-requests batch that deletes rows writes one `join_request_retention.purged` summary.
- ADR-0012 §4: "Every fix ships with a test that reproduces the bug and fails before the fix: a pytest test against the Postgres test DB. Only when pytest cannot see the bug … a scripted reproduction outside the repo is accepted, its command and output recorded in impl-log and re-run by the reviewer."

Ticket evidence: CI run 44134412773 (PR #54) failed with `assert [5] == [1, 2, 2]`. One `delete_batch(cutoff, 2)` deleted all 5 rows.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 0 | The root cause is reproduced deterministically before any product change (step 0) | impl-log has a "Step 0" section with: the exact recipe (GUCs / stats setup), the `EXPLAIN` of the old `delete_batch` statement showing the limited subquery rescanned on the inner side of a semi join with no Materialize, and the observed `deleted` count (> batch). The reviewer re-runs the regression test against the pre-fix code (`git stash` the `app/` changes, or check out `main` for `app/`) and sees it fail |
| 1 | A regression test reproduces the bug: it fails on `main` before the fix and passes after | `tests/test_cli_audit_purge.py::test_delete_batch_respects_limit_when_subquery_rescanned` fails on the pre-fix `app/modules/audit/purge.py` (impl-log records the failing output, run **10 times, failing 10/10**) and passes after the fix |
| 2 | The batch bound holds at all four purge sites | The regression tests for `purge_send_log`, `purge_tokens` and `JoinRequestRetentionRepository.purge` (see Tests) pass, and each one that could be forced fails before the fix (evidence in impl-log). Review of the diff shows that no `.in_(<scalar_subquery with LIMIT … FOR UPDATE>)` is left in `app/`: `grep -rn "scalar_subquery" app/modules/{audit/purge.py,auth/repository.py,join_requests/repository.py}` finds no purge statement |
| 3 | Expected behaviour: 5 rows aged 400 d are deleted 2/2/1, three summaries `[1, 2, 2]`, one shared cutoff | `test_purge_batches_share_cutoff` passes (unchanged) |
| 4 | Repeated full-suite stability | impl-log records 5 consecutive green `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` runs after the fix |
| 5 | `make check` passes | `make check` green. There is no migration, so `alembic check` should not change (it is still part of CI) |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/audit/purge.py` | `delete_batch`: replace `id IN (scalar_subquery … LIMIT … FOR UPDATE SKIP LOCKED)` with a `MATERIALIZED` CTE that selects and locks the ids once, then `DELETE … WHERE id IN (SELECT id FROM batch)` | The confirmed bug site |
| `app/modules/auth/repository.py` | Same change in `purge_send_log` and `purge_tokens` | Identical statement shape, same root cause (consult finding 3). The fix is a few lines each (R7 justification below) |
| `app/modules/join_requests/repository.py` | Same change in `JoinRequestRetentionRepository.purge` | Same as above |
| `tests/purge_plan.py` (new) | Test helpers: `force_rescanned_semijoin(session)` (`SET LOCAL` planner GUCs, from the step-0 recipe) and `explain(session, stmt) -> str` (for the impl-log capture and an optional plan assertion) | Shared by the three test files. Keeps the recipe in one place |
| `tests/test_cli_audit_purge.py` | Add the audit regression test | ADR-0012 §4 |
| `tests/test_cli_cleanup.py` | Add regression tests for `purge_send_log` and `purge_tokens` | Criterion 2 |
| `tests/test_join_requests_retention.py` | Add a regression test for `purge` (if it can be forced; see step 0c) | Criterion 2 |
| `work/tickets/BUG-001/impl-log.md` | Step-0 evidence, failing runs before the fix, 5 green `make check` runs | ADR-0012 §4 |

Do not touch `tests/conftest.py` unless step 0 shows that stray committed rows are a second, independent cause. If that happens, record why in impl-log.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_cli_audit_purge.py › test_delete_batch_respects_limit_when_subquery_rescanned` (**first test written; ADR-0012 regression**) | The module-level `backdate_audit` marker applies. Seed 5 rows with **distinct ages inserted oldest first** (`_aged` with 405, 404, 403, 402, 401 days), so that physical (seq-scan) order equals `ORDER BY occurred_at, id` order. Rows with equal `occurred_at` order by random uuid, and the overrun count then varies from 2 to 5, which would make the regression test flaky. Apply the step-0 recipe, then call `repo.open_batch()`, `deleted = await repo.delete_batch(cutoff, 2)`, `repo.close_batch()`. Assert `deleted == 2` and that `_ids(...)` equals the 3 youngest seeded ids. Before the fix it must fail with `deleted == 5` |
| `tests/test_cli_cleanup.py › test_purge_send_log_respects_limit_when_subquery_rescanned` | Seed 5 `email_send_log` rows oldest first (`"10 days"` … `"6 days"`), force the plan, `purge_send_log(timedelta(hours=48), 2) == 2`, 3 rows remain |
| `tests/test_cli_cleanup.py › test_purge_tokens_respects_limit_when_subquery_rescanned` | Same with 5 used tokens aged 12 … 8 days, `purge_tokens(timedelta(days=7), 2) == 2` |
| `tests/test_join_requests_retention.py › test_purge_respects_limit_when_subquery_rescanned` | 5 approved rows whose `decided_at` is set oldest first (205 … 201 days), force the plan, `purge(RETENTION, COOLDOWN, 2) == 2`. The step-0c caveat applies: there is no index on `decided_at`, so the subquery needs a Sort. If no GUC/stats recipe reproduces the overrun for this table, still add the test as a plain bound test (it passes before and after), mark it in impl-log as "not forced, fixed by analogy", and keep the fix |

Existing tests stay unchanged: `test_purge_batches_share_cutoff`, `test_cleanup_batches`, `test_batches_write_one_summary_each` and the concurrency tests in `tests/test_email_lock_concurrency.py` must stay green.

## Approach

### Step 0: investigation (required before any `app/` change; record it all in impl-log)
Context: the orchestrator's check showed that the consult's recipe (`enable_hashjoin/mergejoin/material/hashagg = off`, also with `indexscan/bitmapscan/sort = off`) put the subquery on the **outer** side (`Nested Loop -> Unique -> Sort -> Subquery Scan -> Limit -> LockRows`) and deleted exactly 2 rows. Likely reason: in PG17, `disable_cost` is additive, and when the inner side is rescanned, any disabled node inside the subquery (for example a Sort, which `indexscan=off` forces) is paid once per outer row. The rescanned plan then always loses. The inner subquery must contain **no disabled node**. The temp-table copy may also have lacked the `(occurred_at, id)` index.

0a. **Audit site, GUC recipe (try first).** In a test transaction against `org_admin_test` (the real `audit_log` with `ix_audit_log_occurred_at_id`), seed the 5 rows as above. Then `SET LOCAL enable_hashjoin = off; enable_mergejoin = off; enable_material = off; enable_hashagg = off; enable_sort = off`, and **keep `enable_indexscan` and `enable_seqscan` on**. The subquery is then served by an Index Scan on `ix_audit_log_occurred_at_id` with no Sort, while unique-ifying it for the outer side needs a disabled Sort or HashAgg. `EXPLAIN` the compiled old statement. Target shape: `Delete -> Nested Loop Semi Join -> Seq Scan on audit_log` (outer) / `Subquery Scan -> Limit -> LockRows -> Index Scan using ix_audit_log_occurred_at_id` (inner, no Materialize). Then execute it and observe `deleted > 2`.
0b. **If 0a does not give that shape**, try these in order and capture EXPLAIN for each:
   - (i) add `enable_bitmapscan = off` and/or `enable_indexonlyscan = off`;
   - (ii) cost nudges: `random_page_cost = 1`, `cpu_tuple_cost` low or high, `seq_page_cost`;
   - (iii) stats skew, which is also the most likely natural cause in the full suite: before seeding, run `ANALYZE audit_log` inside the test transaction while the table holds only recent rows, so the planner estimates the outer `audit_log` as tiny and drops the Materialize. Note: `ANALYZE` updates `pg_class.reltuples/relpages` in place, and that **survives the rollback**. Record this. If it is used in the committed test, say so in a comment.
   - (iv) outside pytest only: a scripted reproduction on a scratch DB with a large `audit_log` plus `ANALYZE` (ADR-0012 §4 fallback). Record the script and output in impl-log so the reviewer can re-run it.
0c. **Other sites.** Repeat the working recipe for `email_send_log` (`ix_email_send_log_sent_at`), `email_tokens` (`ix_email_tokens_created_at`) and `join_requests`. `join_requests` has no `decided_at` index, so `enable_sort = off` penalises both shapes; try `enable_sort` on with hashagg off, or the stats recipe (iii). Record which sites reproduced.
0d. **Stop rule.** If no recipe makes the **audit** statement overrun, in pytest or in a script, the root cause stays unconfirmed. **Do not change `app/`.** Write the findings (every EXPLAIN tried) in impl-log under "Step 0: not reproduced", and hand back to the orchestrator as BLOCKED-style findings for a fresh look at the ticket's other candidates. Do not ship the CTE as a guess.
0e. Put the working GUC/stats recipe in `tests/purge_plan.py::force_rescanned_semijoin`. Settings are `SET LOCAL`, so they reset on the test's rollback.

### Step 1: regression tests first
Write the four tests above and run them against the **unchanged** `app/`. Record the failing output in impl-log, running the audit test 10 times (it must fail 10/10). Optionally assert the plan shape with `explain()` in the test only if it is stable. Do not make the test depend on it; the behavioural assertion (`deleted == 2`) is the regression check.

### Step 2: fix (all four sites)
In each purge method, build the batch as a materialized CTE and delete through it:
```python
batch = (
    select(T.id).where(<where>).order_by(<same order>).limit(batch_size)
    .with_for_update(skip_locked=True)
    .cte("batch").prefix_with("MATERIALIZED")
)
stmt = delete(T).where(T.id.in_(select(batch.c.id)))
```
- Keep the `where`, the `ORDER BY` columns, `skip_locked=True`, `synchronize_session=False` and the `rowcount` handling exactly as they are.
- Log the compiled SQL of one statement in impl-log. It must render `WITH batch AS MATERIALIZED (SELECT … FOR UPDATE SKIP LOCKED) DELETE FROM … WHERE id IN (SELECT batch.id FROM batch)`.
- Under the forced recipe the plan may still be a semi join, but its inner side is a `CTE Scan`, which reads a tuplestore on rescan and does not re-execute `LockRows`. Capture this EXPLAIN in impl-log.
- Fallback if SQLAlchemy cannot render the CTE prefix: `T.id == any_(func.array(subquery))`, i.e. an InitPlan that runs once. Record the reason.
- Update the docstrings only if they describe the statement shape. Keep the "skipping rows another transaction holds" wording.

### Step 3: verify
Run the new tests (green), the full `make check` 5 times in a row (green; record each), and `uv run alembic check` (no drift).

### Rules
- R1: the change stays in the repository layer. Services are unchanged.
- R2: no schema change.
- R4: summary rows are unchanged, and each one is still written in the batch's transaction.
- R5: no config.
- R7: the three non-audit sites are in scope because they are the same defect, with the same statement and the same fix, and each is a few lines. The ticket's Scope allows product changes "if the root cause is a real product bug". Leaving them would ship a known identical bug.
- R8: tests added.
- R11: not touched.
- Lock order and `SKIP LOCKED` semantics are unchanged: the CTE locks in the same `ORDER BY`. The `audit_log_immutable` latch and the 90-day floor are untouched.

### Decision: `test_purge_batches_share_cutoff` is **not** rescoped in this ticket
- Consult finding 1 shows that stray committed rows cannot produce the captured `[5]`. The test's own rows are already rolled back per test, and no test commits `audit_log` rows aged > 365 d.
- Rescoping it (per-id assertions only) would weaken the check of exact batch counts, which is the behaviour that broke.
- If any of the 5 post-fix full-suite runs still fails, and with a different signature, the implementer records it and scopes the test to its own ids and summaries. That case is a recorded deviation, not a silent change.

## Specialist input used
- `consult-database-optimizer.md`:
  - Finding 1 (only a broken batch limit yields `[5]`): **adopted**. It is the basis for not rescoping the test.
  - Finding 2 (rescanned inner subquery): **adopted as a hypothesis only**. Step 0 must confirm it.
  - Finding 3 / Rec. 1 (four sites, MATERIALIZED CTE, no migration): **adopted**.
  - Rec. 2 GUC recipe: **adapted**. The orchestrator's check showed it fails. The new recipe keeps index scans on and disables Sort/HashAgg so that only the outer-unique shape is penalised. Distinct ages seeded oldest first make the overrun deterministic.
  - Rec. 3 (verify with EXPLAIN): **adopted** as step 0 with a hard stop rule.
  - Rec. 4 (tests for the other sites): **adopted**, with a "fixed by analogy" escape for `join_requests` if it cannot be forced.
  - Rec. 5 (rescope the existing test): **rejected by default**, with a conditional fallback (see Decision above).
- Orchestrator note (forced plan put the subquery outer, 2 rows deleted): used to redesign the recipe and to add the stop rule 0d.

## Assumptions
- PostgreSQL 17 locally and in CI. Disabled plan nodes add `disable_cost`, which is multiplied across rescans.
- `LockRows` skips tuples already deleted by the current command (`TM_SelfModified`), so each rescan returns the next live rows. This is the consult's reasoning; step 0 confirms it empirically.
- `_aged` rows inserted in one transaction land in heap order. Step 0 verifies this by the failure being 10/10. If not, seed via a single multi-row `INSERT … SELECT … ORDER BY` in age order.
- SQLAlchemy 2.0 renders `CTE.prefix_with("MATERIALIZED")` as `AS MATERIALIZED (…)` for PostgreSQL and attaches the CTE to the DELETE.
- No change to `BATCH_SIZE`, the retention floors or the CLI contract (ticket Out of scope).

## Needs owner's decision (non-blocking)
- **Severity: recommend raising S3 → S2 (P2).**
  - The batch bound is the production safeguard for lock time, WAL volume and transaction size on `audit_log`, `email_*` and `join_requests`. One "batch" can drain the whole eligible set in one transaction.
  - It is not cosmetic, so ADR-0012's S3 does not fit. It is "broken with a workaround" (S2): run purges when the eligible set is small.
  - It is not S1: no wrong row is deleted, the floors and the latch still hold, and the summary counts match what was deleted.
  - The plan is the same either way. The orchestrator updates `ticket.md` and the board once the owner answers.

## Blocking questions
None.

## Split proposal
None. Expected diff is about 150 to 250 lines: four small repository edits, four tests and one helper.

## Data model / migration
None.

## API contract
None. There is no endpoint change. The CLIs `audit-purge` and `cleanup` are unchanged.
