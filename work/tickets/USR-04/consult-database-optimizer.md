# consult-database-optimizer — USR-04 (db)

**Findings**
- §3 lists only `email_tokens (id, user_id, purpose verify|reset, token_hash, expires_at, used_at)` and `email_send_log (id, email_hash, purpose, sent_at)`. It has no outbox, no `created_at`, no retention rules.
- There are working patterns to copy: hashes are stored as `LargeBinary` with an `octet_length = 32` CHECK (`device_sessions`, `login_failures`). Email keys use HMAC-SHA256 via `EmailHasher` (`app/modules/auth/throttle.py`). Concurrency uses an atomic upsert with a conditional `ON CONFLICT … WHERE` (`LoginFailureRepository.reserve`). Model CHECK texts must match the migration exactly.
- Head is `83936724f780` (create_sessions). The naming convention is applied through `op.f()`.
- Nothing in the repo runs scheduled jobs. Cleanup is already a follow-up for sessions and `login_failures` (ADR-0002, ADR-0004).

**Recommendations**
1. **`email_tokens`** (AC "tokens"): `user_id` FK → users `ON DELETE CASCADE`; `purpose String(10)` with CHECK `purpose IN ('verify','reset')`, following the `users.status` style rather than a PG enum, which is easier to downgrade; `token_hash` bytea UNIQUE with CHECK `octet_length = 32` (SHA-256 of a ≥256-bit random value; HMAC is not needed for high-entropy tokens); `expires_at`, `used_at NULL`, `created_at now()`. Add a partial index `(user_id, purpose) WHERE used_at IS NULL`.
   - **Redeem** in one statement: `UPDATE … SET used_at=now() WHERE token_hash=:h AND used_at IS NULL AND expires_at>now() RETURNING user_id, purpose`. This makes single use race-free.
   - **Issue**: in the same transaction, first `UPDATE … SET used_at=now() WHERE user_id=:u AND purpose=:p AND used_at IS NULL` (invalidates older links), then INSERT.
2. **Resend limits** (AC "1/min, 5/day"): use `email_send_log(id, email_hmac bytea CHECK len 32, purpose CHECK, sent_at now())` with index `(email_hmac, purpose, sent_at)`. There is no FK, so unknown emails on `forgot` are counted too, which avoids enumeration. Rename `email_hash` → `email_hmac` and record that in the plan.
   - **Atomicity**: `pg_advisory_xact_lock(hashtextextended(:purpose || encode(:hmac,'hex'), 0))`, then COUNT over the last 24 h and the last 60 s, then INSERT, all in one transaction.
   - Prefer this over a conditional `INSERT … SELECT WHERE (count) < 5`, which races under READ COMMITTED.
   - A counter-row upsert in the `reserve` style also works, but it needs two windows.
3. **Outbox** (AC "async, retry"): use a durable `email_outbox(id, user_id FK CASCADE NULL, purpose, attempts int, next_attempt_at, sent_at NULL, last_error_code NULL, created_at)`, written in the same transaction as the token.
   - **Do not store the raw token or link.** Either keep it encrypted, or keep the plaintext token only in process memory and treat a restart as "the user re-requests". Decide which (see open questions).
   - The worker loop in the lifespan uses `SELECT … FOR UPDATE SKIP LOCKED` on `WHERE sent_at IS NULL AND next_attempt_at<=now()`, with a partial index.
   - Exponential backoff and a cap on attempts.
   - A plain `BackgroundTasks` call loses mail on restart and has no retry state, so it is acceptable only if the owner accepts that loss.
4. **Retention**: delete `email_send_log` rows older than 24 h, used or expired `email_tokens` older than 7 d, and sent outbox rows older than 7 d. Run this opportunistically, either at the start of the worker loop or after an insert.
5. **Migration** (R2): one revision, `down_revision='83936724f780'`, tables only. `downgrade()` drops the indexes, then the tables in reverse order. Import the models in `app/db/models.py`. Partial indexes must be declared in the model, or `alembic check` will drift.

**Risks**
- A raw token kept in the outbox becomes a credential at rest, which breaks R11.
- Multiple uvicorn workers will double-send without `SKIP LOCKED`.
- Advisory-lock key collisions are harmless; at worst two emails serialize.
- Rotating `IDENTITY_HMAC_KEY` resets the resend windows, which is acceptable.
- An unbounded log grows if cleanup is skipped.

**Open questions**
- Is a durable outbox required, or is losing mail on restart acceptable? If durable, where does the token live while it waits to be sent: encrypted with a key from env, or not persisted at all?
- Token TTLs: is the plan verify 24 h and reset 30–60 min?
- Should a 429 on `forgot` stay hidden behind an always-202 response? Enumeration safety suggests yes, which conflicts with the AC "→ 429 beyond".
- Should a successful reset also mark outstanding `verify` tokens used and set `email_verified_at`?
