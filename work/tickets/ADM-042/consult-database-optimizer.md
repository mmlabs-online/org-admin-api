# Consult — database-optimizer — ADM-042 (2026-11-22)

FINDINGS
- No counter/ledger tables yet; app/modules/usage/models.py has only UsageMetric and UsageLimitOverride. UsageMetric already has UNIQUE(id, app_id) (models.py:46; ADR-0024 §1) for composite same-app FKs.
- repository.effective_limit (repository.py:222-236) is one SELECT, ignores metric status, uses DB clock for expiry; usable as scalar/CTE in the consume transaction.
- ADR-0024 §4: a period change applies at once, so the counter key MUST include the period kind. A week starting on the 1st, or a Monday that is also a day start, yields equal period_start for different kinds and would share a bucket and corrupt `used`.
- Scope CHECK lives in the migration (migrations/versions/20261121_a4c81e6d27f3_usr29_org_members_read_scope.py:27-39, the pattern to copy) and in the model (app_credentials/models.py:38-40, TODO at :37). Also extend the enum (:20-22) and RUNTIME_SCOPES (:27), and tests/test_app_credential_scope_migration.py.
- ADR-0022 §5: idempotency scope is (app, idempotency_key), not key id; 128 printable ASCII; fingerprint (user, metric, operation, amount); state machine consume -> at most one settle -> at most one refund; retention >= 90 days.

RECOMMENDATIONS (one hand-reviewed migration; downgrade drops both tables and the new-scope keys)
1. usage_counters: (user_id, metric_id, period, period_start) unique; app_id; used bigint NOT NULL DEFAULT 0; CHECK used >= 0 and <= 2^53-1; composite FK (metric_id, app_id) -> usage_metrics(id, app_id); user FK ON DELETE CASCADE; updated_at. Never purged (OQ69). [AC1]
2. usage_events (ledger + idempotency record in one table, same retention): id, app_id, idempotency_key (CHECK ~ '^[\x20-\x7e]{1,128}$'), user_id, metric_id, charged (period, period_start) for credit-to-original-period, fingerprint bytea (sha256), amount > 0, settled_amount, refunded_amount, status consumed|settled|refunded, created_at/settled_at/refunded_at. UNIQUE (app_id, idempotency_key) serves the hot lookup. Indexes: (created_at) for ADM-045 purge; (user_id, metric_id, created_at DESC) for staff history. [AC2]
3. Consume in one transaction, no lock_app (ADR-0022 §3): insert event first with ON CONFLICT DO NOTHING RETURNING (on conflict SELECT, compare fingerprint: replay or 409 idempotency_key_reused); then INSERT counter row ON CONFLICT DO NOTHING; then UPDATE ... SET used = used + :a WHERE <key> AND used + :a <= :limit RETURNING used. Zero rows => 409 usage_limit_exceeded and roll back the event. The UPDATE row lock serialises per counter and READ COMMITTED re-checks the WHERE, so parallel consumes cannot overshoot; limit 0 refuses all. Compute period_start via periods.py from a DB-now parameter. [AC1 incl. concurrency test]
4. Settle: SELECT event FOR UPDATE, require status consumed, apply delta to the original counter with GREATEST(0, used+delta), no limit guard (overage accepted, never 409, ADR-0022 §5). Refund: credit the charged amount (never more) with GREATEST(used - x, 0) to the original period, mark refunded; second refund is a replay with no further credit. [AC3]
5. Scope CHECK: add all three (usage:read, usage:write, limits:write) now (ADR-0022 §1 defines the set; avoids a second CHECK migration in ADM-044). Downgrade: DELETE FROM app_credentials WHERE scope IN (...), then drop/recreate the CHECK as in a4c81e6d27f3:35-39. Update model CHECK text exactly, enum, RUNTIME_SCOPES; run alembic check. [AC4 groundwork]
6. Period-boundary contention: each period is its own counter row, so no reset job and no bulk UPDATE; the old row never blocks the new one.
7. Hot-path indexes: the two unique indexes suffice; verify the app-membership table has an index on (user, app) before joining it.

RISKS
- Counter keyed without period kind corrupts usage on period change.
- Per-user counter row is a serialisation point under bursts; keep the transaction short, no outbound calls while holding it.
- Rolling back refused events means refusals are not recorded (fits "no per-use audit rows").
- Ledger growth until ADM-045 purge ships.

OPEN QUESTIONS FOR MING (default)
1. Record refused consumes in the ledger? [No, roll back.]
2. Add limits:write to the CHECK now? [Yes, all three.]
3. Settle with actual < estimate credits the difference? [Yes, to the original period, never below 0.]
4. 90-day retention counted from? [created_at.]
5. Amount 0 allowed? [No, 422 for amount <= 0.]

Files: app/modules/usage/models.py, app/modules/usage/repository.py, app/modules/usage/periods.py, app/modules/app_credentials/models.py, migrations/versions/20261121_a4c81e6d27f3_usr29_org_members_read_scope.py
