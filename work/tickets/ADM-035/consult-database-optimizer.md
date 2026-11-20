# Consult — database-optimizer — ADM-035 (2026-11-20)

**ADM-035 consult (database-optimizer)**

**Findings**
- The app_credentials module is a good template: codec.py (regex + 128-char cap before matching, sha256, new_credential), models.py (CHECKs for key_id and 32-byte hash, partial index on `revoked_at IS NULL`, unique key_id), a repository whose touch_last_used and revoke are guarded `UPDATE ... WHERE revoked_at IS NULL RETURNING`, and a service that does lock, count_active, then insert.
- The "max 2 active" limit in app credentials is serialized by `rbac.lock_app(key)`. deployer_credentials has no parent row, so that lock does not exist. Without a replacement, two concurrent POSTs can both see count=1 and create a 3rd key.
- The repo already uses `pg_advisory_xact_lock(hashtextextended(...))` (auth/repository.py:216, 254; bug_reports/repository.py:31). That is the right tool.
- ADR-0018 §2 says "every request reads the database". The `_reject` pattern gives one 401 with a challenge header; the deployer one needs `realm="deployer"`.
- Audit needs these new enum values: `AuditTarget.DEPLOYER_CREDENTIAL`, `AuditAction.DEPLOYER_CREDENTIAL_ISSUED` and `DEPLOYER_CREDENTIAL_REVOKED`. These are plain enum additions. Check whether any DB CHECK or enum on audit columns needs a migration step.
- `app_key` is optional on audit.record, so pass none for deployer actions.

**Recommendations**
1. **Table** `deployer_credentials`: use the same columns and constraints as app_credentials minus app_id.
   - Keep `key_id` unique, with its 16-hex CHECK.
   - Keep the `octet_length(secret_hash)=32` CHECK.
   - Set `created_by` to FK users ON DELETE SET NULL.
   - Add a partial index on `revoked_at IS NULL`. It is tiny, so it is optional. A plain index on `created_at DESC` is enough for list.
   - Name constraints explicitly, so the naming convention and `alembic check` stay clean.
   - Keep the model's CHECK and `postgresql_where` texts identical to the migration, since Alembic does not compare CHECKs.
   - Write a real `downgrade()` that drops the table. The migration is a High path, so it goes to Ming.
2. **Limit serialization**: take `pg_advisory_xact_lock(hashtextextended('deployer_credentials', 0))` first in both issue and revoke. Then run count_active, then insert, in the same transaction.
   - Take the same lock in authenticate before touch, so the lock order is always lock, then row (this mirrors the lock_app order).
   - For authenticate, consider skipping the global lock. A guarded `UPDATE ... WHERE id=:id AND revoked_at IS NULL RETURNING` already gives "revoked is refused immediately". That avoids serializing every deploy report behind staff writes.
   - Alternative that needs no lock: a fixed `slot smallint CHECK (slot IN (1,2))` with a partial UNIQUE on `slot WHERE revoked_at IS NULL`. The DB then enforces the limit and a conflict maps to 409. It adds a column and slot-picking logic, so the advisory lock is simpler. Pick one at PLAN.
3. **Auth order** in `authenticate(raw)`: missing, then parse (rejects wrong prefix with no DB read), then lookup by key_id, then `hmac.compare_digest`, then revoked, then guarded touch. Every failure returns the same 401 with `WWW-Authenticate: Bearer realm="deployer"`. Log only the reason and key_id, never the raw value.
   - Return `AuthenticatedDeployer(credential_id, key_id)` with `actor = SystemActor(f"deployer:{key_id}", privileged=False)`.
   - Use `HTTPBearer(auto_error=False)` with a separate scheme name, `deployerCredential`.
   - Because the parser is prefix-aware and anchored with `\Z`, `mmak_` keys and JWTs fail at parse. The reverse holds too: the mmak codec rejects `mmdk_`. The admin `current_admin` should already reject non-JWTs, but add a test that proves `mmdk_` gets 401 on admin, cookie, app-credential and public-with-token routes.
   - Reuse the codec by parameterizing the prefix, or copy it with its own PREFIX constant. Do not loosen the existing mmak regex.
4. **last_used_at write amplification**: each deploy report writes one row, which is negligible at this volume. No throttling is needed.
5. **Staff routes** (`/deployer-credentials`, router-level `current_admin`, `require_permission("admin:*", requires_super="always")` or whatever the existing helper uses for `admin:*`):
   - POST returns 201 and sets `Cache-Control: no-store`. A 409 carries `details[0].reason="credential_limit"`.
   - GET returns `Page[...]` with no hash. The model `__repr__` must not include the hash. Order by created_at desc, then id.
   - DELETE returns 204 and is idempotent: revoking twice returns 204 with no second audit row. Unknown id gives 404.
   - Decide whether revoke needs `admin:*` too. The ticket says all three do, which is stricter than app revoke; follow the ticket. A denied attempt on issue should be audited via `record_denied`, as in app credentials.
   - Audit changes hold only `key_id`, never the secret, hash or raw value.
6. **Tests**:
   - Issue, list, revoke, double revoke, 3rd key gives 409, and revoke-then-issue works.
   - 401/403 for each route, plus `GET` and `DELETE` with a non-super admin.
   - A concurrent-issue test: two sessions, one wins and one gets 409. If the test DB is transactional per test, this test needs real separate connections.
   - Raw value absent from the audit rows, from caplog, and from a second GET.
   - Revoked key gives 401 immediately; `last_used_at` is set after use.
   - Wrong-prefix test with the DB session patched to assert zero queries.
   - Migration round-trip. Add the new routes to `tests/test_route_auth_classes.py` (as class 1), and add a test-only route using `current_deployer`.
7. **Docs**: in `docs/deploy.md`, cover the rotation procedure (issue the 2nd key, update `MMLABS_DEPLOYER_CREDENTIAL`, verify `last_used_at` moves, revoke the old key) and the bootstrap via admin API. Update R3 wording in CLAUDE.md only if ADM-033 has not already done so.

**Risks**
- Missing lock leads to a race on the limit (see 2). This is the main DB risk.
- The `AuditAction` and `AuditTarget` enums may be mirrored in a DB CHECK or a docs list; verify.
- A leaked `mmdk_` key could mark deploys. Impact is low per ADR-0018, and immediate revoke is the mitigation.
- The ticket is High (migration, auth, tokens), so it stops at PLAN for Ming's approval.

**Open questions**
- Advisory lock or slot column for the limit? I recommend the advisory lock.
- Does authenticate take the advisory lock, or only the guarded UPDATE? I recommend only the guarded UPDATE.
- Does revoke use `admin:*` like issue (per the ticket), or a lighter permission as app revoke does? The ticket says `admin:*`.
- Should the per-IP rate limit and the 429 handling live in ADM-036? The ADR puts them on the report route, so this ticket only needs the dependency. The rate limit must run before `current_deployer`, as a route dependency.
