**ADM-021 consult (api-platform-engineer, read-only)**

**Findings**
- The list route is `app/modules/audit/router.py:21-54`. Filters are an `AuditLogFilter` dataclass (`repository.py:18-29`), ANDed in `AuditRepository.list` (`repository.py:49-79`). `AuditLogService.list` (`service.py:260-271`) only validates from/to. The router is Bearer-protected by `current_admin` at `router.py:18`. The list route declares `require_permission("admin:audit:read")` at `router.py:21`.
- The repository comment at `repository.py:1-4` says "`add` and `list` are the only row operations". Adding `get` is a read, so it is compatible, but update the docstring (and any test that asserts this shape, e.g. `tests/test_audit_logger.py` or `tests/test_audit_schema.py`; I did not check).
- `AuditLogRead` (`schemas.py:14-31`) already carries `org_id`, `user_agent` and `ip`. `user_agent` is sanitized and truncated on write (`service.py:55-58`) and is returned as stored. The list does no read-side handling, so "same `user_agent` handling as the list" means reusing `AuditLogRead.model_validate(row)`. Do not add any transformation.
- **Index gap (AC3):** `app/modules/audit/models.py:46-76` has no index on `org_id`. The existing `ix_audit_log_target` is partial (`target_id IS NOT NULL`) and is keyed `(target_type, target_id, occurred_at, id)`.
  - The `org_id` filter ORs a target match with a column match: `(target_type='organization' AND target_id=:o) OR org_id=:o`.
  - Without an `org_id` index, the OR forces a seq scan on an append-only table that only grows.
  - Add a partial index `ix_audit_log_org_id (org_id, occurred_at, id) WHERE org_id IS NOT NULL`, following the pattern of `ix_audit_log_app_key` at `models.py:63-69`. Both OR branches can then use a BitmapOr, or the planner can run each branch ordered.
- The count query (`repository.py:71`) runs the same predicate as the page query, so the index serves both.
- `NotFoundError` exists (`app/core/errors.py:32`). The organizations service already uses it (`organizations/service.py:34`).
- Existing tests in `tests/test_audit_logs_api.py`: helper `_add` at line 40, `_ids` at 54, 401 at 203, 403 at 209. There is no single-read test yet.
- Latest migration I found is `migrations/versions/20261118_c7a3e91f5d28_usr15_join_requests_cancelled.py` (down_revision `e2b7a94d1c58`). I did not verify it is the sole head, so run `alembic heads` before setting `down_revision`.

**Recommendations**
1. (AC1) Add `org_id: uuid.UUID | None = None` to `AuditLogFilter` and to the router's `Query()` params.
   - In the repository, use `or_(and_(AuditLog.target_type == "organization", AuditLog.target_id == org_id), AuditLog.org_id == org_id)`.
   - Use `AuditTarget.ORGANIZATION.value`, not a literal.
   - Keep the `org_id` clause as its own element in `where`, so it ANDs with the others.
   - Combined with an explicit `target_type`/`target_id` it is a plain AND. For example, `org_id=X&target_type=user` returns only rows where org_id=X and the target is a user. Document this in the OpenAPI description.
2. (AC2) Add `GET /audit-logs/{id}` with `response_model=AuditLogRead` and `**require_permission("admin:audit:read")`.
   - Path param is `uuid.UUID`. A malformed id returns 422 under R6, and the 404 applies only to a well-formed unknown id.
   - Add `AuditRepository.get(id)` and `AuditLogService.get`, which raises `NotFoundError("Audit log entry not found")`.
   - Return the single document unwrapped, which is consistent with the other `GET /{id}` routes.
   - The route needs no new permission and is not itself audited (read-only, same as the list).
   - It inherits the R3 Bearer class from the router-level `current_admin`.
3. (AC3) Add the partial index to the model and to a hand-written migration with a working `downgrade()` (R2). Use `postgresql_where` text identical in model and migration so `alembic check` shows no drift.
   - Prove AC3 with an `EXPLAIN` on a seeded table (e.g. 100k+ rows, `SET enable_seqscan=off` if needed). Record the plan in impl-log.
   - On a large production table a plain `CREATE INDEX` takes a write lock that blocks audit inserts. Consider `CREATE INDEX CONCURRENTLY` via `autocommit_block`, or state that the table is small enough today.
   - `ix_audit_log_target` is partial (`target_id IS NOT NULL`), and the filter includes `target_id=:o`, so the planner can prove the predicate and use it.
4. (AC4) Tests to add (R8):
   - `org_id` alone, matching a target row and a column row.
   - `org_id` combined with `action`, `app_key` and `outcome`.
   - `org_id` that matches nothing returns an empty page.
   - Single read: assert full field equality with the list item, including `user_agent` and `ip`.
   - Single read: a random UUID returns 404 and a non-UUID returns 422.
   - 401 and 403 on both routes, reusing the pattern at lines 203-221.
   - A row where the org matches both via target and via column must appear once, not twice.
   - A migration/index existence test, alongside `tests/test_audit_migrations.py`.
5. Compatibility: both changes are additive (an optional query param and a new route), so they are safe within v1 (R9). Update the spec §5.4 line and any API docs listing filters.

**Risks**
- Without the index, the OR plus `count(*)` degrades as the log grows, and the spec's p95 < 200 ms target is at risk.
- Admin-pan needs rows for an organization. `record_in_savepoint` (`service.py:221-253`) hard-codes `org_id=None`. Identity-side rows, such as join requests and invitations, may only carry the org in `target` or `changes`, so those rows may not show up under the new filter. Check which audit actions populate `org_id` or `target=ORGANIZATION`. This is a data-completeness limit of the filter, not a bug, but note it for admin-pan. Backfilling is out of scope (R7), so list it in follow-ups.
- Deleted orgs: `organization.deleted` rows target the org id, so history stays reachable. This is good, but the filter must not validate that the org exists (no 404 for an unknown org; return an empty page, consistent with `test_unknown_app_key_empty_page`).
- Information exposure: `changes` and `ip` are already visible to `admin:audit:read`, so the single-read route adds no new exposure.

**Open questions**
- Should `org_id` filter on the target match only when `target_type='organization'`? The AC says so. I assume yes, and that other target types with that UUID cannot collide (UUIDs are unique).
- Is a plain `CREATE INDEX` acceptable on the production table, or is a CONCURRENTLY build wanted?
- Should the single read 404 or 403 for entries a narrower future permission might hide? Not needed now (one permission), so only 404 is expected.
