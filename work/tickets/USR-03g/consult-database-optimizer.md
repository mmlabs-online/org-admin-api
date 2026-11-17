**Findings**

- **Head revision.** The current head is `f3a8d51c9e27` (USR-05a), not `4d4b5d67dc44` as the USR-03b consult said. The chain is `…a7c3e5f19b42 → b4d82f6a1c93 → c5e91d3a7b24 → d6f03e8b2a15 → f3a8d51c9e27`. The new revision must use `down_revision="f3a8d51c9e27"`. Run `alembic heads` before writing it, because several tickets are landing on the same day.
- **No existing code.** There is no `join_requests` model, table or migration yet. `app/modules/membership/schemas.py` only has the pydantic `JoinRequest` body. Name the ORM class `JoinRequestRecord` or put it in a new module, so it does not clash with the schema class.
- **Conventions to copy.** `app/modules/invitations/models.py` and the `c5e91d3a7b24` migration show the house pattern:
  - **Constraint names.** The `NAMING_CONVENTION` in `app/db/base.py` turns a model `CheckConstraint(name="status_valid")` into `ck_join_requests_status_valid`. The migration spells this out with `op.f(...)`.
  - **Partial indexes.** They are declared with `postgresql_where=text(...)` in both the model and the migration.
  - **Foreign keys.** FK names are explicit.
  - **Primary key.** `UUIDPkMixin` has a Python-side default only.
  - **Audit.** `AuditLogger` lives in `app/modules/audit/service.py`. The ticket says `AuditRecorder`, so confirm which name USR-03b actually shipped.
- **Related tables.** `role_assignments` has `uq_role_assignments_user_role_org` (NULLS NOT DISTINCT) with `user_id` CASCADE and `granted_by` SET NULL. `apps` and `roles` cascade from apps. `users` is a hard-delete target, so the join_requests FKs behave the same way.
- **Retention.** There is an operator CLI in `app/cli.py` (`cleanup`, backed by `app/modules/auth/retention.py`). It deletes in batches and writes one audit summary row per batch. Audit purge has a 90-day floor.

**Recommendations**

1. **Columns.**
   - Keys: `id uuid` PK; `app_id uuid NOT NULL`; `user_id uuid NOT NULL`.
   - Status: `status varchar(20) NOT NULL`, with a StrEnum in the model. Set no server default, so the service always sets it explicitly (or set it in both model and migration).
   - Dates: `created_at timestamptz NOT NULL DEFAULT now()`; `decided_at timestamptz NULL`.
   - Decider: `decided_by uuid NULL`.
   - No `updated_at`, so do not use `TimestampMixin`.
2. **CHECKs.** The CHECK text is not compared by Alembic, so the model and migration strings must be byte-identical.
   - `ck_join_requests_status_valid`: `status IN ('pending', 'approved', 'rejected')`.
   - `ck_join_requests_decided_iff_not_pending`: `(status = 'pending') = (decided_at IS NULL)`. `status` is NOT NULL, so there is no NULL trap.
   - Do not add a "decided_by NOT NULL once decided" check. `ON DELETE SET NULL` on `decided_by` would break it when a staff user is deleted.
3. **Foreign keys.**
   - `app_id → apps.id ON DELETE CASCADE`.
   - `user_id → users.id ON DELETE CASCADE`.
   - `decided_by → users.id ON DELETE SET NULL`.
   - Name them `fk_join_requests_<col>_<table>`, which is what the convention generates.
   - A cross-app role FK is not needed. The role is read from the locked `apps` row at approval time and is not stored on the request.
4. **Indexes.**
   - `uq_join_requests_pending` is a unique index on `(app_id, user_id) WHERE status = 'pending'`. It also serves the `join_available` / pending lookup on `/token`.
   - `ix_join_requests_app_id_status_created_at_id` on `(app_id, status, created_at, id)` serves the staff list with a status filter, ordered by `created_at, id`. This is the default list view, which is most likely `status=pending`.
   - If the list can be unfiltered and ordered by date, a status-leading index cannot serve it without a sort. The table is small, so the sort is cheap. Add `(app_id, created_at, id)` only if that view is real.
   - **Replace the plain `user_id` index.** Use `ix_join_requests_user_id_app_id_created_at (user_id, app_id, created_at)` instead. It still serves the user cascade delete (leading column) and also serves a rejection cooldown lookup and per-user history, so there is no schema change if the owner picks the cooldown.
   - Skip an index on `decided_by`. The SET NULL scan only runs when a staff user is deleted, and the table is small.
5. **Idempotent request path.**
   - Take `lock_app` first, then run `INSERT … ON CONFLICT (app_id, user_id) WHERE status='pending' DO NOTHING RETURNING id`. On no row, `SELECT` the pending row.
   - Approve and reject also take `lock_app`, so the `SELECT` cannot race a decision. Without that lock, the pending row could be decided between the conflict and the `SELECT`.
   - The lock order everywhere is: `lock_app`, then `users … FOR KEY SHARE`, then the `join_requests` row `FOR UPDATE`.
6. **Approve.**
   - Read `default_role_id` from the locked app. If it is NULL, return 409.
   - Re-check the app is active, the user is active, and the email is verified.
   - Use the same grant upsert as join: `ON CONFLICT ON CONSTRAINT uq_role_assignments_user_role_org`.
   - If the user already holds the role (no row returned), still mark the request `approved`. Do not return 409 and do not leave it pending.
   - Set `status`, `decided_by` and `decided_at = now()` in the same transaction as the grant and the audit entry.
7. **Migration (new revision, `down_revision="f3a8d51c9e27"`).**
   - `upgrade()` is one `create_table` with the CHECKs (`op.f(...)`) and FKs, then `create_index` for each index. The partial index needs `postgresql_where=sa.text("status = 'pending'")`. This only creates a new table, so there are no lock concerns.
   - `downgrade()` drops the indexes (passing `postgresql_where` on the partial one, as c5e91d3a7b24 does) and then `drop_table`. Note in the docstring that it is lossy.
   - Import the model in `app/db/models.py`.
   - Verify by running `upgrade head`, `downgrade -1`, `upgrade head`, then `alembic check`.
8. **`alembic check` pitfalls.**
   - A column default must exist on both sides or neither. That includes `created_at`'s `server_default=func.now()` against `sa.text("now()")`.
   - Index names and column order must match exactly.
   - The `postgresql_where` text must match in model and migration.
   - Alembic ignores CHECK drift, so a typo will pass `alembic check` silently. Add a test that inserts a decided row with `decided_at NULL` and a pending row with `decided_at` set, and expects `IntegrityError` for both.
   - Add tests for the partial unique: a second pending insert fails, and after a decision a new pending insert succeeds.
9. **Retention.**
   - The table stores no email or token, only IDs, so it holds no PII beyond the user link. User deletion cascades.
   - Decided rows grow slowly, at most one per decision.
   - Follow-up, not in this diff: extend the `cleanup` CLI to delete rows with `status <> 'pending' AND decided_at < now() - interval '1 year'`, in batches with a summary audit row. Keep the newest decided row per `(app_id, user_id)` if a cooldown or deny-list reads it.
   - The audit log is the long-term history of decisions.

**Risks**

- **Request flooding.** After a rejection a user can file a new pending request at once, because the unique index only covers pending rows. This is spam against staff. The index in item 4 makes a cooldown a service-only change, so the owner's decision (see Open questions) needs no migration.
- **Pending requests after a policy change.** If the app switches from `approval` to `closed`, existing pending rows stay. If it switches to `open`, they are redundant. The approve path must define what happens (see Open questions).
- **Default role changing between request and approval.** The role is not stored, so approval grants whatever `default_role_id` is at that moment. The default role is not org-owner (CHECK `default_role_not_org_owner`), which limits what can be granted. The audit entry should record the role actually granted.
- **Wrong head.** Basing the migration on the old head creates a second head and `alembic upgrade head` fails.
- **Silent CHECK drift.** This is the one Alembic does not catch, hence the DB-level tests in item 8.

**Open questions**

- Rejection cooldown: yes or no, and how long? Item 4 covers the index either way.
- What does approve do if the app is now `open` (grant anyway) or `closed` (409 or reject)? I suggest: grant if `open`, 409 `join_closed` if `closed`.
- Should the staff list default to `status=pending`, and does anyone need an unfiltered date-ordered list (which would add the extra index)?
- Does the user's `/token` `join_available` need to show "pending" separately? If so, it reads the partial unique index.
- Is a retention window of 1 year for decided requests acceptable, or should it follow the audit purge setting?

Files consulted:
- `/home/user/org-admin-api/work/tickets/USR-03g/ticket.md`
- `/home/user/org-admin-api/work/tickets/USR-03b/consult-database-optimizer.md`
- `/home/user/org-admin-api/app/modules/invitations/models.py`
- `/home/user/org-admin-api/migrations/versions/20261117_c5e91d3a7b24_usr06b_org_invitations.py`
- `/home/user/org-admin-api/app/modules/rbac/models.py`
- `/home/user/org-admin-api/app/db/base.py`
- `/home/user/org-admin-api/app/modules/auth/retention.py`
