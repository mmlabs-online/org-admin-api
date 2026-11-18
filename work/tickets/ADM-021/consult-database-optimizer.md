Findings

- **Table and indexes.** The table is `audit_log`. The model is `app/modules/audit/models.py:21`, created in `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py`, with `outcome` added later in `af3eb84d2d5e`.
- **No index on `org_id`.** The column is at `models.py:90` and migration line 51, and none of the indexes at `models.py:46-76` covers it. The `occurred_at` index is `ix_audit_log_occurred_at_id` (`models.py:46`).
- **Target arm is already covered.** `ix_audit_log_target` is `(target_type, target_id, occurred_at, id) WHERE target_id IS NOT NULL` (`models.py:55-62`). `target_type='organization' AND target_id=:org` implies the partial predicate, so Postgres can use that index for the target arm.
- **The OR cannot be index-backed today.** BitmapOr needs an index for every arm. Without an `org_id` index, Postgres falls back to a backward scan of `ix_audit_log_occurred_at_id` with a filter, or a seq scan. That degrades with table size, and the count query is a full scan.
- **Current list query.** `app/modules/audit/repository.py:49-79` builds an AND list of filters, runs `count(*)` with the same WHERE clause, then runs `ORDER BY occurred_at DESC, id DESC LIMIT/OFFSET`. The `org_id` filter needs a new field in `AuditLogFilter` (`repository.py:18-29`) and one more entry in `where`.
- **No `get` by id exists.** The primary key `pk_audit_log` already covers the single-entry read, so no index work is needed there.

Recommendations

1. **Use one `or_()`, not UNION.** Append `or_(and_(AuditLog.target_type == "organization", AuditLog.target_id == org), AuditLog.org_id == org)` to `where` (AC1).
   - A single OR cannot return a row twice, so there is nothing to de-duplicate. UNION would only add a dedupe step, and it would split `count`, `ORDER BY` and `OFFSET` across two branches.
   - The one OR expression works for both the count and the page query, and combines with the other filters by AND.
2. **Add one migration.** Create `ix_audit_log_org_id` on `(org_id, occurred_at, id) WHERE org_id IS NOT NULL` (R2, AC3).
   - It mirrors the existing partial-index style (`models.py:47-53`).
   - Declare it in the model's `__table_args__` with the same `postgresql_where`, so `alembic check` reports no drift.
   - `downgrade()` drops the index. This is safe: only DDL touches the table, and the append-only trigger covers UPDATE, DELETE and TRUNCATE (migration lines 118-125).
   - Plain `CREATE INDEX` takes a write lock while it builds. Use `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()` if audit_log may already be large in production. Copy whichever approach `af3eb84d2d5e` took for its outcome index.
3. **Expected plan.** A BitmapOr over `ix_audit_log_target` and `ix_audit_log_org_id`, then a top-N sort of the matching rows. A single organization's history is small, so p95 should be far below 200 ms. The count query gets the same BitmapOr.
4. **Add `AuditRepository.get(id)`.** Use `session.get(AuditLog, id)`, which is a primary-key lookup, and have the service raise `NotFoundError` on `None`. Reuse the list route's `user_agent` handling (AC2).
5. **Evidence for AC3.** Run `EXPLAIN (ANALYZE, BUFFERS)` on a seeded table of about 100k or more rows and put the output in `impl-log.md`.
   - I would not assert plan shape in a unit test, because it is fragile. An optional test can set `enable_seqscan=off` and check that the plan mentions both index names.
6. **Tests (R8).** Cover both match paths:
   - a target-only row (`target_type='organization'`, `org_id` NULL);
   - a column-only row (an entitlement with `org_id` set and a different target);
   - a row matching both, which must appear once, so check `total`;
   - `org_id` combined with `action`/`outcome`, and an unknown `org_id` returning an empty page.

Risks

- **Non-organization targets.** A row with `target_type='user'` and `target_id` equal to the org UUID cannot match the target arm. That is correct, since the pair is checked together.
- **Planner choice on a small table.** On small tables or stale stats Postgres may pick a seq scan, which is harmless there. Run `ANALYZE` in the evidence run.
- **Offset paging.** Deep offsets stay linear in the matched rows, which is fine per organization.
- **Extra write cost.** There is one more index on an append-only, write-heavy table, but the partial predicate skips rows with NULL `org_id`.
- **Unrelated filter selectivity.** If a caller combines `org_id` with a broad filter such as `outcome`, the planner picks whichever index is cheapest. That is acceptable.

Open questions

- Does `af3eb84d2d5e` use `CONCURRENTLY` plus an autocommit block? If yes, copy the pattern; if not, a plain `CREATE INDEX` is probably fine at v1 volume.
- What is the expected production `audit_log` row count? Retention is still open (ADR-0009 amendment, spec Q6). It only affects whether `CONCURRENTLY` is necessary.
- Should `org_id` be validated as an existing organization, giving a 404 for an unknown one? There are no FKs by design (ADR-0009 §2), so I recommend returning an empty page for an unknown org instead.
