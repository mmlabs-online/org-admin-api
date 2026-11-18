## USR-15 consult (database-optimizer): Findings, Recommendations, Risks, Open questions

### Findings
- The current schema is in `/home/user/org-admin-api/migrations/versions/20261117_a9d4c27e6b13_usr03g_join_requests.py`.
  - CHECK `status_valid`: `status IN ('pending','approved','rejected')`.
  - CHECK `decided_iff_not_pending`: `(status='pending') = (decided_at IS NULL)`.
  - Partial unique index `uq_join_requests_pending (app_id, user_id) WHERE status='pending'`.
  - `ix_join_requests_app_id_status_created_at_id (app_id, status, created_at, id)`.
  - `ix_join_requests_user_id_app_id_created_at (user_id, app_id, created_at)`.
  - All FKs cascade on app and user. `decided_by` is SET NULL.
- `/home/user/org-admin-api/app/modules/join_requests/models.py` repeats the CHECK text. Its docstring warns that Alembic does not compare CHECKs, so a model and migration mismatch is silent.
- `set_status` (`/home/user/org-admin-api/app/modules/users/service.py`) locks `lock_app(admin)`, then `get_for_update(user)`.
  - The disable branch already holds both locks and already revokes sessions and invalidates email tokens.
  - A cancel hook fits right after `invalidate_all`, before the audit row.
- The approve and reject paths lock app, then user (`FOR SHARE`), then request (`FOR UPDATE`).
  - The cookie join locks app, then reads the user with a plain `users.get` (no lock). This is the AC4 gap.
  - `set_status` locks `admin`, then user. It never takes the target app's lock.
- `rejected_within` is served by the `(user_id, app_id, created_at)` index only partially.
  - It filters on `app_id`, `user_id`, `status` and `decided_at`.
  - Per-(user, app) row counts are tiny, so this is fine.
- Retention (`/home/user/org-admin-api/app/modules/auth/retention.py`, `EmailRetentionRepository`) follows this pattern:
  - `set_lock_timeout`, then `DELETE ... WHERE id IN (SELECT ... ORDER BY ... LIMIT n FOR UPDATE SKIP LOCKED)`.
  - Commit per batch. An audit summary row is written in the same transaction as each deleting batch.
  - `AuditLogger._check_keys` rejects any change key matching `hash|token|password|cookie|email|secret`, unless the action is `EMAIL_RETENTION_PURGED`, the key is in `_ALLOWED_KEYS`, and the value is a count.
  - `app/cli.py::_cleanup` is hard-wired to `EmailRetentionService`.
- The audit purge (`app/modules/audit/purge.py`) is a separate CLI command with a latch. It is not relevant to join requests.

### Recommendations

**1. Status 'cancelled' (recommended)**
- Add a status value and widen the CHECK. Alternatives are worse:
  - Reusing `rejected` would trigger the cooldown on a disabled user, and `decided_by` would be wrong.
  - Deleting the rows loses history and needs its own audit.
  - A separate `cancelled_at` column breaks the `(status='pending') = (decided_at IS NULL)` invariant.
- `decided_at` is set at cancel time and `decided_by` is the actor (the staff user, via `actor_user_id`).
- `decided_by` and `decided_at` semantics are unchanged, so `decided_iff_not_pending` stays valid and needs no change.
- Migration (new revision, down_revision `a9d4c27e6b13`):
  - Upgrade: `op.drop_constraint("ck_join_requests_status_valid", "join_requests", type_="check")`, then `op.create_check_constraint("ck_join_requests_status_valid", "join_requests", "status IN ('pending','approved','rejected','cancelled')")`.
  - The constraint names in the migration are already `op.f("ck_join_requests_...")`. Take the exact drop name from the naming convention in `app/db/base.py`. Use `op.f()` or the literal full name so it is not double-prefixed.
  - Lock: dropping and re-adding a CHECK takes ACCESS EXCLUSIVE and scans the table. The table is small, so it is acceptable.
  - Safer on a larger table: `ADD CONSTRAINT ... NOT VALID` followed by `VALIDATE CONSTRAINT`. This is optional here.
- Downgrade must handle existing `cancelled` rows before it re-narrows the CHECK. Pick one:
  - (a) Rewrite them to `rejected`. This is lossy and would wrongly start a cooldown.
  - (b) Delete them. This is preferred: `DELETE FROM join_requests WHERE status='cancelled'`. They are inert history and the user can simply re-request.
  - Do (b), then drop and recreate the old CHECK. Write the downgrade docstring as "lossy for cancelled rows".
- Update the model CHECK text, `JoinRequestStatus` (add `CANCELLED`), and `JoinRequestStatusFilter`. Decide whether the filter allows `cancelled`. I suggest yes.
- Update `tests/test_join_requests_migration.py::test_constraints` to assert 'cancelled' is accepted and 'bogus' is rejected. Add a downgrade test that seeds a cancelled row.
- Check the `status` OpenAPI enum and `JoinRequestRead.status` for a Literal that needs widening.

**2. Set-based cancel on disable**
- Add a repository method on `JoinRequestRepository`. Issue it inside `set_status` when `status == DISABLED`, after the user flush:
  - `UPDATE join_requests SET status='cancelled', decided_at=now(), decided_by=:actor WHERE user_id=:u AND status='pending' RETURNING id, app_id`.
  - It needs `synchronize_session=False` and `populate_existing`, or no ORM instances loaded.
- Locking:
  - The user row is already locked `FOR UPDATE`.
  - Request rows are locked implicitly by the UPDATE.
  - A concurrent approve or reject holds app, then `users FOR SHARE`, and blocks on the user `FOR UPDATE`. A concurrent approve takes the user row first and so serialises with `set_status`.
  - Reject does not take the user lock. It takes app, then request `FOR UPDATE`.
  - Possible deadlock: reject holds the request row and awaits nothing else, while `set_status` holds the user row and waits on that request row. `set_status` waits briefly and then proceeds, with no cycle, because reject never needs the user lock afterwards. It does read `get_user` without a lock, which is fine.
  - Approve after the disable sees `status != pending` and returns `not_pending`. Good.
  - Cancel does not take the app lock. A concurrent cookie join under `lock_app(app)` may insert a pending row for the user just as `set_status` runs.
    - The join reads the user without a lock and sees `active` if `set_status` has not committed.
    - Race: join inserts the pending row after the cancel UPDATE has run, but before `set_status` commits.
    - Mitigation: join's `users.get` becomes `get_for_share` (AC4). That blocks on the user `FOR UPDATE`, then rechecks `status` and raises `SessionInvalid`. This closes the race, so AC4 is load-bearing for AC1.
    - Lock order is app (join), then user, and `set_status` is admin, then user. There is no cycle unless the target app is `admin`, which join forbids.
- Audit (R4):
  - One audit row per cancelled request: `APP_JOIN_REQUEST_CANCELLED`, target `JOIN_REQUEST`, `app_key` from RETURNING, `changes={"status": change("pending","cancelled")}`, actor = staff actor.
  - Join `app_id` to `apps` to get the key, or batch-load keys.
  - The count per user is small, but cap with a loop over RETURNING rows.
  - Add the new action to the `AuditAction` enum and to `tests/test_audit_coverage.py`.
- Alternative: one summary row on the `user.disabled` entry (`changes["join_requests_cancelled"]: count`). That is cheaper, but per-request rows give consistency with approve and reject. I recommend per-row.
- On re-enable, nothing is restored. The user must re-request.

**3. Pending cap per app**
- Config: `join_max_pending_per_app: int = Field(default=..., ge=1)` in `app/core/config.py`, plus a key in `.env.example`.
  - Add a plumbing parameter to `JoinService`, as `reject_cooldown_seconds` is plumbed through the 4 call sites (tokens/service, auth/router, organizations/auth_router, auth_service).
  - Pass it from `settings`, same as cooldown, and be careful with these sites.
- Count query in `_request`, after the idempotent `get_pending` check, under `lock_app`:
  - `SELECT count(*) FROM join_requests WHERE app_id=:a AND status='pending'`.
  - It is served by `ix_join_requests_app_id_status_created_at_id` (prefix `app_id, status`), an index-only-ish range scan. No new index is needed.
  - The race is closed by the app lock. The join and approve/reject both hold `lock_app`, so count-then-insert is atomic per app. The partial unique index is only a backstop for per-user duplicates, not for the cap.
  - Do the check after the pending and cooldown checks, so an existing pending request stays idempotent (202), not 4xx.
  - Do it before `usable_default_role` only if that is cheaper. Keep the current order.
- Error: define `JoinQueueFullError(AppError)`, mapped to 409 or 429. Per R6, codes are 401, 403, 404, 409 and 422, so use 409, code like `join_queue_full`, with a generic message and no counts. Decide whether it is also reported by `join_available` (`not_member.details.join_available`). I suggest yes: it should return False when the cap is reached for a user without a pending request, which needs a count in `join_available`.
  - A count in `join_available` runs without the lock and is only advisory. Fine.

**4. Retention purge**
- Add `join_request_retention_days` (≥1, default e.g. 90) in config. Floor: it must be ≥ cooldown, otherwise the retention age is meaningless. Enforce `max(age, cooldown)` in code and validate at load.
- Purge only decided rows (`status IN ('approved','rejected','cancelled')`, `decided_at < now() - age`). Never purge pending.
- Keep the newest rejected row per (app, user) while the cooldown applies:
  - Only rows with `status='rejected'` and `decided_at > now() - cooldown` need protecting. Rows older than the retention age are normally past the cooldown, as long as retention is ≥ cooldown, so the simple rule `decided_at < now() - age` (age ≥ cooldown) already preserves every cooldown-relevant rejection.
  - The AC says "keeping the latest rejection per (app,user) while the cooldown applies". If age < cooldown is allowed, use a `NOT EXISTS (newer rejected row same app and user)` guard or `DISTINCT ON`. I recommend the clamp (age ≥ cooldown), with `NOT EXISTS` as belt and braces:
    ```sql
    DELETE FROM join_requests
    WHERE id IN (
      SELECT j.id FROM join_requests j
      WHERE j.status <> 'pending' AND j.decided_at < now() - :age
        AND NOT (j.status = 'rejected' AND j.decided_at > now() - :cooldown
                 AND NOT EXISTS (SELECT 1 FROM join_requests n
                                 WHERE n.app_id=j.app_id AND n.user_id=j.user_id
                                   AND n.status='rejected' AND n.decided_at > j.decided_at))
      ORDER BY j.decided_at LIMIT :batch FOR UPDATE SKIP LOCKED)
    ```
  - Simplest correct version: when age ≥ cooldown, no extra guard is needed.
  - Choose and document one. The ticket's wording favours an explicit guard.
- Index: purge scans `status <> 'pending' AND decided_at < cutoff`. The table is small, so the ordering is not critical. If it grows, add a partial index `(decided_at) WHERE status <> 'pending'` (expand-only migration, CREATE INDEX CONCURRENTLY outside the transaction, `postgresql_concurrently` with autocommit_block). I do not recommend it now (R7).
- Do not use `FOR UPDATE SKIP LOCKED` with a `NOT EXISTS` subquery across self-join rows without care. Lock only the outer row.
- Batching and commit per batch, with `set_lock_timeout` as in `EmailRetentionRepository`. Order by `decided_at, id`.
- Audit summary: reuse the pattern.
  - New action `JOIN_REQUEST_RETENTION_PURGED`, or reuse `EMAIL_RETENTION_PURGED` (wrong semantics). Prefer a new action.
  - `_check_keys` is only a problem for forbidden names. `join_requests` does not match the regex, so no `_ALLOWED_KEYS` change is needed. `changes={"join_requests": change(None, deleted)}`.
  - Write one row per deleting batch in the same transaction as the delete, as the email retention does.
- CLI: `_cleanup` is hard-wired to `EmailRetentionService`. Extend it to run a second service (call it in sequence inside the same scope) and print the table rows with the existing format. Keep `--dry-run` as count-only. Dry-run must not write audit rows. Consider whether `cleanup` exit code or message needs updating, plus the `CLEANUP_USAGE` docstring in `app/cli.py` lines 12-13.
- The retention does not need the app lock (like email). The purge only touches decided rows.

**5. alembic check pitfalls**
- CHECK constraints are not compared by `alembic check`. The model and migration texts can drift silently, so a hand-written test is required (as in `test_constraints`).
- Naming: model `name="status_valid"` resolves to `ck_join_requests_status_valid` via the naming convention. In the migration, use `op.f(...)` on create. On drop, pass the full name.
- Do not touch the partial index text. The `postgresql_where` text must still match, or `alembic check` reports index drift.
- New action enum values: check whether `AuditAction` is stored in a Postgres enum or a CHECK, or just a string. If a CHECK on `audit_log.action` exists, the migration must widen it too. I did not find it in this review, so verify in `app/modules/audit/models.py`.

### Risks
- Downgrade of the cancelled rows is lossy (deleted). State it in the docstring.
- If the join keeps reading the user without a lock, a pending row can appear for a user being disabled, which defeats AC1. AC4 must ship with AC1.
- Retention age below cooldown could remove a cooldown-relevant rejection. Clamp it.
- The cap check without the app lock (for example in `join_available`) is advisory only, so the authoritative check must stay in `_request`.
- A per-row audit write on mass-cancel is bounded by one user's pending requests (at most one per app), so it is small.
- Widening the status filter changes the OpenAPI schema (non-breaking in v1, additive).

### Open questions
1. Should `cancelled` be listable via `GET .../join-requests?status=cancelled`? (I suggest yes.)
2. Cap-reached error: 409 with which code, and should `join_available` report False for it?
3. Should the cap count apply to idempotent re-requests? (I suggest no: existing pending returns 202 first.)
4. Retention default age and whether it is clamped to the cooldown or rejected at config load.
5. Does disable cancel requests for every app (including disabled apps)? (I suggest yes: all pending of that user.)
6. Per-request audit rows or one summary on `user.disabled`?
7. Confirm no CHECK or enum constrains `audit_log.action` or `target_type`.

Files read: `/home/user/org-admin-api/work/tickets/USR-15/ticket.md`, `/home/user/org-admin-api/migrations/versions/20261117_a9d4c27e6b13_usr03g_join_requests.py`, `/home/user/org-admin-api/app/modules/join_requests/{models,repository,service}.py`, `/home/user/org-admin-api/app/modules/users/service.py`, `/home/user/org-admin-api/app/modules/membership/service.py`, `/home/user/org-admin-api/app/modules/auth/retention.py`, `/home/user/org-admin-api/app/modules/auth/repository.py` (EmailRetentionRepository), `/home/user/org-admin-api/app/modules/audit/service.py`, `/home/user/org-admin-api/app/cli.py`.
