# BUG-001 consult: root cause and fix for the purge batch limit

I could not reproduce this. This session has no shell tool (no `service postgresql start`, no `psql`, no pytest), so I could not run any SQL or EXPLAIN. The root cause below comes from reading the code against Postgres executor semantics, and I have not confirmed it on a running instance. The implementer must confirm it with EXPLAIN before relying on it (see Recommendations 2 and 3).

## Findings

1. **The CI capture rules out the "stray rows" and "SKIP LOCKED" candidates in the ticket.**
   - `[5]` means the first batch deleted all 5 seeded rows, so the second `delete_batch` returned 0 and wrote no summary.
   - Stray committed rows from other tests would change the totals or the split, not collapse them into one summary row.
   - `SKIP LOCKED` can only make a batch shorter, never longer.
   - Only the batch limit not holding produces exactly `[5]`.

2. **Probable mechanism: the `IN` subquery is rescanned during the DELETE.**
   - `delete_batch` builds `DELETE FROM audit_log WHERE id IN (SELECT id … ORDER BY occurred_at, id LIMIT n FOR UPDATE SKIP LOCKED)`.
   - A subquery with `LIMIT` or `FOR UPDATE` is not pulled up, so the planner is free to use a Nested Loop Semi Join. In that plan the subquery is rescanned once per outer row, unless a Materialize node sits on top of it.
   - Within one statement, a rescan of `Limit → LockRows → Sort → Scan` sees the snapshot rows again.
   - `LockRows` skips rows the current command already deleted (`TM_SelfModified`).
   - So the second pass returns the next `n` live rows, and the whole table drains through repeated limited passes.
   - Which plan the planner picks depends on row estimates, table stats and autovacuum timing. That would explain why it fails only in the full suite, where `audit_log` is churned by many tests, and passes 15/15 alone.
   - I have not confirmed the plan shape.

3. **The same pattern exists in four places, so it is a product bug and not a test problem.**
   - `app/modules/audit/purge.py:61-69` (`AuditPurgeRepository.delete_batch`).
   - `app/modules/auth/repository.py:306-314` (`purge_send_log`) and `:318-326` (`purge_tokens`). These are the `auth/retention` purges.
   - `app/modules/join_requests/repository.py:172-180` (the USR-15 purge).
   - Impact: no wrong data is deleted, because every row removed is purgeable and the summary counts stay accurate. What breaks is the batch bound, which exists to limit lock time, WAL volume and transaction size. On a large table a single "batch" could delete everything.
   - I'd keep the severity at S3 on correctness grounds, with S2 as an option for the operational risk. That is the planner's call.

## Recommendations

1. **Replace the `IN (subquery)` with a materialized CTE in all four sites, so the ids are selected and locked exactly once.**
   - In SQLAlchemy 2.0: `batch = select(T.id).where(...).order_by(...).limit(n).with_for_update(skip_locked=True).cte("batch").prefix_with("MATERIALIZED")`, then `delete(T).where(T.id.in_(select(batch.c.id)))`.
   - Postgres already refuses to inline a CTE that has `FOR UPDATE`, but `MATERIALIZED` states the intent explicitly.
   - The alternative is `T.id == ANY(ARRAY(subquery))`. `ARRAY(subquery)` is an InitPlan that runs once. It is less pleasant to write in SQLAlchemy, so prefer the CTE.
   - The plain `in_(ids)` with `scalar_subquery()` must go. A scalar subquery inside `IN` is exactly the pitfall.
   - No migration is needed.
   - Keep `synchronize_session=False` and the `rowcount` handling unchanged.
   - Per R7, I'd fix all four sites, since they share one root cause and each is a few lines. If the planner prefers to keep the diff to the audit purge, the other three go to the follow-ups list with the same fix noted.

2. **Add a regression test that fails before the fix and does not depend on stats or suite order.** This is the ADR-0012 requirement.
   - Put it in `tests/test_cli_audit_purge.py` as e.g. `test_delete_batch_respects_limit_under_nested_loop`, with the `backdate_audit` marker.
   - Seed 5 rows aged 400 days.
   - Run `SET LOCAL enable_hashjoin = off; SET LOCAL enable_mergejoin = off; SET LOCAL enable_material = off; SET LOCAL enable_hashagg = off`. This forces the nested-loop semi join with an unmaterialized inner subquery.
   - Call `repo.open_batch()` then `deleted = await repo.delete_batch(cutoff, 2)`, and assert `deleted == 2` and that 3 of the seeded ids remain.
   - Settings reset on rollback, so there is no leakage.
   - The implementer must run this against the old code and see it fail. If it passes before the fix, the forced plan is wrong, so inspect `EXPLAIN` and adjust the settings.

3. **Verify the mechanism before coding.**
   - Run `EXPLAIN` on the old statement under the forced settings. Look for "Nested Loop Semi Join" with a "Subquery Scan → Limit → LockRows" inner and no Materialize.
   - If instead you see a unique-ified subquery on the outer side, drop `enable_hashagg` from the settings and try `enable_nestloop` combinations. If no plan reproduces it, the root cause is wrong. Report back, since the ticket's other candidates would then need a fresh look.

4. **Add similar regression tests for the other three sites.** Their existing tests are in the auth retention and join-requests test files. Use the same pattern: seed more than `batch` eligible rows, force the plan, and assert exactly `batch` rows are deleted.

5. **Harden the existing test so the 5-consecutive-green-runs criterion is meaningful.** Optionally scope `test_purge_batches_share_cutoff` to its own ids. `_summaries` counts all `audit_log.purged` rows from the last day, and the whole table is purgeable, so stray committed old rows would still skew it. This is secondary to the product fix, and I'd only do it if reruns still show noise.

## Risks

- Changing the statement shape alters lock acquisition order only trivially. The CTE still locks in `ORDER BY` order and `SKIP LOCKED` behaviour is unchanged, so existing concurrency tests should stay green. The `audit_log_immutable` trigger's `allow_purge` latch and 90-day floor are untouched, since the DELETE target is still `audit_log`.
- `DISABLE TRIGGER` in the `backdate_audit` marker holds an ACCESS EXCLUSIVE lock until rollback. Keep the new test single-connection.
- If the forced-plan test cannot reproduce the failure, the diagnosis is unconfirmed, and a "fix" shipped on a guess would leave the bug in place.

## Open questions

- Severity: stay at S3, or raise to S2 because the batch bound is a production safeguard? That is for the planner and the owner.
- Fix all four sites in this ticket, or only the audit purge with the rest as follow-ups?
- Is the stats and autovacuum explanation for the order dependence worth documenting in the impl-log once the EXPLAIN is captured?

Files reviewed:
- `/home/user/org-admin-api/app/modules/audit/purge.py`
- `/home/user/org-admin-api/app/modules/auth/repository.py`
- `/home/user/org-admin-api/app/modules/join_requests/repository.py`
- `/home/user/org-admin-api/tests/test_cli_audit_purge.py`
- `/home/user/org-admin-api/work/tickets/BUG-001/ticket.md`

---
Orchestrator check (2026-11-18, local Postgres 17, temp table copy of the pattern, 5 rows, rolled back): with `enable_hashjoin/mergejoin/material/hashagg = off` (and also with `enable_indexscan/bitmapscan/sort = off`) the planner put the subquery on the OUTER side (`Nested Loop -> Unique -> Sort -> Subquery Scan -> Limit -> LockRows`), and exactly 2 rows were deleted. So the forced-plan recipe above did not reproduce the overrun; the implementer must find a plan that puts the limited subquery on the inner side of a semi join (or otherwise reproduce), and capture EXPLAIN before relying on the diagnosis.
