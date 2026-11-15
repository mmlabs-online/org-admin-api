# consult-database-optimizer — ADM-004

**Findings**
- Head revision is `5e2a9c7d1f04` (seed admin permissions), so the new migration's `down_revision = "5e2a9c7d1f04"`. `Base` uses a naming convention. `UUIDPkMixin` generates the id on the client (`uuid4`). Migrations follow the `_now_column`/`op.f()` style and add a comment when a CHECK text must match the model.
- `get_session` gives one session per request and commits before the response. Services already commit in the middle of a request in three places:
  - `LoginService.login` commits the failure reservation before Argon2 (`failures.commit()`). The `InvalidCredentialsError` raised after that rolls back anything added later.
  - `DeviceSessionService` commits on token reuse (`commit_on_reuse`).
  - The retention CLI commits once per batch.
- Tests bind the session with `join_transaction_mode="create_savepoint"`, so `session.commit()` inside a test only releases a savepoint. A separate engine or `SessionLocal()` connection would escape the per-test rollback and could not see uncommitted fixtures.
- These are hard deletes: organizations, apps (which cascade to roles, permissions and assignments), roles, permissions and role assignments. Nothing deletes users today. `role_assignments.granted_by` already uses `ON DELETE SET NULL`.
- The interim `AuditRecorder.record(event, actor_user_id, app_id, **ids)` only accepts UUID kwargs. It has no target type, org, diff, ip, user agent or request id, and no slot for an anonymous actor. It is only used by `membership.service` (`app.join`) and the auth router.
- **There is no request-id middleware anywhere in `app/`.** The ticket's request-id column has no source yet.
- `SystemActor` exists (`system:join`). The actor is not always a user.

**Recommendations**
1. **Schema** (AC1, R2, R9):
   - `id uuid PK` (`UUIDPkMixin`). Use uuid7 if it can be generated cheaply; if not, uuid4 plus a time-ordered index is enough.
   - `occurred_at timestamptz NOT NULL DEFAULT now()`. Use the DB clock, and do not use `TimestampMixin`, because `updated_at` contradicts append-only.
   - `actor_user_id uuid NULL` with **no FK**. Also `actor_kind text NOT NULL` with `CHECK IN ('user','system','anonymous')` and a nullable `actor_label text` (e.g. `system:join`). With no FK, the row survives any future user hard-delete or anonymization unchanged. Under `SET NULL` the row would silently lose its actor, and that update would itself break append-only and hit the trigger in 3.
   - `action text NOT NULL` with `CHECK (action ~ '^[a-z_]+(\.[a-z_]+)+$')`.
   - `target_type text NULL`, `target_id uuid NULL`, with `CHECK ((target_type IS NULL) = (target_id IS NULL))`.
   - `app_key text NULL`, plus `app_id uuid NULL` if useful. Apps are hard-deleted with cascade, so store the key as a snapshot with no FK. The key is immutable (D18), which makes it a stable filter value.
   - `org_id uuid NULL`, no FK (orgs are hard-deleted).
   - `changes jsonb NULL` with `CHECK (jsonb_typeof(changes)='object')`. Write it from an allowlisted diff builder (field-level before/after) and never from `model_dump()` (R11).
   - `ip inet NULL`; reuse `_valid_ip`.
   - `user_agent varchar(512) NULL`, truncated in code as `device_sessions` does.
   - `request_id text NULL` with `CHECK (length(request_id) <= 64)`.
2. **Indexes** (AC4, p95 < 200 ms):
   - `(occurred_at DESC, id DESC)`.
   - `(actor_user_id, occurred_at DESC)` partial `WHERE actor_user_id IS NOT NULL`.
   - `(action, occurred_at DESC)`.
   - `(target_type, target_id, occurred_at DESC)` partial.
   - `(app_key, occurred_at DESC)` partial.
   - `org_id` only if it becomes a filter (it is not one in AC4).
   - No GIN index on `changes`.
   - Order every query by `occurred_at DESC, id DESC`, with a half-open time range `[from, to)`.
   - R9 requires `Page[T]` with offset, so keep offset with `limit ≤ 100`. `total` uses `count(*)` under the same filters, and that is the expensive part on a large table. Leave a keyset cursor as a later, additive v1 change.
3. **Append-only**:
   - In code: the repository exposes only `add()` and the list method.
   - In the DB: a `BEFORE UPDATE OR DELETE` row trigger and a `BEFORE TRUNCATE` statement trigger on `audit_log` that call `audit_log_immutable()` and `RAISE EXCEPTION`.
   - `REVOKE UPDATE, DELETE, TRUNCATE` alone does nothing, because the app role `org_admin` probably owns the tables (it runs migrations). The trigger binds the owner too, unless it runs `ALTER TABLE … DISABLE TRIGGER` or drops the table.
   - Test that an UPDATE raises.
   - `downgrade()` drops the triggers, the function, the indexes and then the table, in reverse order.
4. **Same transaction** (AC2, AC5): rewrite the recorder as `AuditLogger(session)` and call `session.add(AuditLog(...))` inside the service before the service returns. A failed action rolls back its entry.
   - **Login failure:** use commit-then-raise on the request session, as the reservation already does. Before `raise InvalidCredentialsError`, add a `login.failed` entry and commit, then raise.
   - Blocked or Turnstile-refused attempts (`RateLimitedError`, `TurnstileRequiredError`, `TurnstileFailedError`): either do not audit them or use the same pattern. An attacker can flood these, so see Risks.
   - For an unknown email: `actor_user_id NULL`, no email at all (R11). If correlation is needed, the existing HMAC is the only option, and I advise against it.
   - **Reuse revocation:** add the entry before the existing `commit_on_reuse` commit.
   - Do not use a separate connection: it breaks atomicity and the test savepoint model.
5. **Request id:** this needs a small middleware that reads `X-Request-ID` (validated to `[A-Za-z0-9-]{1,64}`) or generates one, and echoes it back. If the planner thinks that is outside the ticket, leave the column nullable.
6. **Retention:** v1 should keep entries indefinitely. Admin traffic is low, and login events dominate: about 1 KB per row, so even 100k rows a month is around 100 MB a year. Future purging becomes a follow-up, either through a trigger bypass flag (`SET LOCAL audit.purge='on'`, operator CLI only) or through monthly partitions with `DETACH`/`DROP`.

**Risks**
- Login-failure events are attacker-controlled volume. Per-IP limits apply, but distributed attacks still grow the table and slow `count(*)`. Write rows only for credential-checked failures, not for blocked or Turnstile-refused attempts.
- Commit-then-raise means a commit failure on the failure path gives a 500 instead of a 401, which changes error behaviour. Wrap that commit so the 401 always comes back and an audit write failure is only logged.
- If the `changes` diff builder passes through `password_hash`, emails or token fields, R11 is violated. It needs an explicit allowlist per entity and a test for it.
- Writing the audit row adds an INSERT to every mutation, which is a small cost.
- `alembic check` does not see triggers or functions, so drift there goes unnoticed. Write that DDL by hand and test it.

**Open questions**
1. Should turnstile-required, rate-limited and backoff-blocked login attempts be audited too, or only credential failures?
2. Can users ever be hard-deleted or anonymized? This confirms the no-FK choice for the actor.
3. Is a request-id middleware in scope for ADM-004, or does `request_id` stay null until a follow-up?
4. Retention: keep indefinitely in v1, as recommended?
5. Is keyset pagination allowed here as an exception to R9 offset pagination, or should it stay offset for now?
