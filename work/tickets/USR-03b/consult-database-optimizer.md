# consult-database-optimizer — USR-03b

**Findings**
- Head migration is `20261113_4d4b5d67dc44`. `join_requests` exists only as a sketch in §3 (`id, app_id, user_id, status, created_at, decided_by`), with no `decided_at`, constraints or indexes.
- `lock_app` takes `FOR NO KEY UPDATE`, and every RBAC writer calls it, including the writer of `default_role_id`. So reading `default_role_id` after `lock_app` meets the USR-02d note: no extra `FOR SHARE` is needed.
- USR-02c does not use `ON CONFLICT … WHERE expired`. `_grant_locked` finds the row, replaces an expired one in place, and raises 409 on an active one. Join must be idempotent, so it cannot reuse `_grant_locked` as is.
- The accounts list is already covered by the partial unique index `uq_account_sessions_active (device_session_id, user_id) WHERE revoked_at IS NULL`. There are only a few rows per device.

**Recommendations**
- **Join write path (open policy):** call `lock_app(key)` first, then check status and `join_policy` and read `default_role_id` from the locked row. If it is NULL, return 409 `join_unavailable` (see the USR-07 S1 note). Then run one statement: `INSERT … ON CONFLICT ON CONSTRAINT uq_role_assignments_user_role_org DO UPDATE SET expires_at=NULL, granted_by=NULL WHERE role_assignments.expires_at <= now() RETURNING id, (xmax=0) AS created`.
  - Name the constraint explicitly: inferring a NULLS NOT DISTINCT constraint from columns is fragile.
  - No row returned means the role is already held, so return 200 with no audit entry.
  - Check `email_verified_at` and user status from a `users` read with `FOR KEY SHARE`.
- **`join_requests` table:**
  - Columns: `id uuid PK`, `app_id → apps ON DELETE CASCADE`, `user_id → users ON DELETE CASCADE`, `status varchar(20) CHECK IN ('pending','approved','rejected')`, `decided_by → users ON DELETE SET NULL`, `decided_at timestamptz`, `created_at timestamptz default now()`.
  - CHECK `(status = 'pending') = (decided_at IS NULL)`.
  - Partial unique index `uq_join_requests_pending (app_id, user_id) WHERE status = 'pending'`. Insert with `ON CONFLICT (app_id, user_id) WHERE status = 'pending' DO NOTHING` and return the existing row, so a repeated request gives 202 idempotently.
  - Staff list index `ix_join_requests_app_status_created (app_id, status, created_at, id)`, plus `ix_join_requests_user_id` for the cascade delete and lookups by user.
  - Approve/reject: `lock_app`, then `SELECT … FOR UPDATE WHERE id=? AND app_id=? AND status='pending'` (404 or 409 otherwise), then the same grant upsert, then set `status`, `decided_by` and `decided_at=now()`.
- **Migration:** a new revision with `down_revision='4d4b5d67dc44'`. The CHECK text must match the model exactly. `downgrade()` is `drop_table` (indexes drop with it). Import the model in `app/db/models.py`, and `alembic check` must stay clean (R2).
- **Accounts list:** `account_sessions JOIN users` filtered on `device_session_id = :d AND revoked_at IS NULL [AND created_at < :accounts_created_before]`, `ORDER BY created_at, id`. Add the cutoff whenever the previous cookie value was used (N1). No new index is needed.

**Risks**
- Without `lock_app`, a concurrent role or default switch could grant admin permissions through join (USR-02c F4).
- A user who is rejected can create pending requests without limit, because the unique index covers pending rows only.

**Open questions**
- Should join be a no-op when the user already holds another active role in the app?
- Does an active default role with a future `expires_at` stay as it is? I'd say yes.
- Does the accounts list hide disabled users, or show them flagged?
- Should there be a cooldown after a rejected request?
