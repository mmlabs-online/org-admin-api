# Consult — database-optimizer — ADM-040 (2026-11-20)

**Findings**
- ADR-0024 point 4 (docs/adr/0024-usage-metrics.md:26-27) already says "ADM-040 stores overrides only", read live. Spec OQ62 (docs/spec-v1.md:278) allows a limit below the default, including 0, with optional "until a date". OQ67 (:283) requires a reason of 1-500 chars.
- ADR point 5 (:31-33) stores the reason only in the audit `changes.reason`. It is "never returned by the API". The ticket scope lists "required reason" as an override column. Following the ADR means no reason column.
- Precedent to copy: `RoleAssignment` (app/modules/rbac/models.py:195-229) has a unique key on the natural key, `user_id` CASCADE, `granted_by` SET NULL, `expires_at` timestamptz and a `server_default=func.now()` created_at. `UsageMetric` (app/modules/usage/models.py:30-61) shows the house style: a named FK with a `fk_<table>_<col>_<ref>` name, CHECKs that must be mirrored textually in the migration, and a `MAX_LIMIT` constant (:16).
- `UsageMetric` has `UniqueConstraint("id","app_id")` (:36) for a composite FK. It is not needed here, because the override only needs `metric_id`, and the app is derived via the metric.
- ADR point 4 says a period change makes ADM-042 use new buckets. Overrides are period-agnostic (a limit per period), so there is no interaction.
- Migration head is d7a3f5b2e801 (migrations/versions/20261120_d7a3f5b2e801_adm030_bug_report_attachments.py). `periods.py` is not needed for ADM-040: expiry is compared with the DB clock only, not period bounds.

**Recommendations**
1. **One row per (user, metric), with upsert (AC "set, replace, remove").**
   - Table `usage_limit_overrides`: `id` (UUIDPk), `user_id`, `metric_id`, `limit_value` (BigInteger), `expires_at timestamptz NULL`, `created_by`, plus TimestampMixin (`created_at` and `updated_at`).
   - Unique constraint `uq_usage_limit_overrides_user_id_metric_id`. It doubles as the lookup index for ADM-042 (user_id first).
   - Name the column `limit_value`, not `limit`, because `limit` is a SQL reserved word.
   - History comes from the audit log (`usage_limit_override.set`, `.removed`, each with from/to and reason). Do not add history rows, so "current override" needs no "latest row" query.
   - "Remove" is a hard DELETE, and it is audited.
2. **FKs.**
   - `user_id` → users.id ON DELETE CASCADE. Overrides are personal data and should go with the user (same as role_assignments).
   - `metric_id` → usage_metrics.id ON DELETE RESTRICT. Metrics are never deleted (:31). Archiving keeps the row. Archived-metric behaviour is an open question (see below).
   - `created_by` → users.id ON DELETE SET NULL, nullable (as app_credentials:38).
   - Index `metric_id` (`ix_usage_limit_overrides_metric_id`) for the RESTRICT check and per-metric admin lists. Index `expires_at` only if a purge job is added. Not needed now.
3. **CHECKs (named, texts mirrored in the migration).**
   - `limit_value BETWEEN 0 AND 9007199254740991`, built from `MAX_LIMIT`.
   - `expires_at IS NULL OR expires_at > created_at`. This is satisfiable because `created_at` defaults to `now()` in the same statement. But on upsert-replace, `created_at` stays at the first set. Either (a) compare with `updated_at` and bump it on every replace (the TimestampMixin may do that via onupdate, so verify), or (b) drop the CHECK and enforce "expires_at must be in the future" in the service against the DB clock `now()`. I recommend (b) plus no CHECK. A CHECK against a stale `created_at` is misleading, and an override that later expires is legitimate.
4. **Effective-limit query (DB clock).** Use a single read, `SELECT COALESCE(o.limit_value, m.default_limit) FROM usage_metrics m LEFT JOIN usage_limit_overrides o ON o.metric_id = m.id AND o.user_id = :u AND (o.expires_at IS NULL OR o.expires_at > now()) WHERE m.id = :m`.
   - The expiry test goes inside the JOIN's ON clause. Putting it in WHERE would drop the row and hide the metric.
   - Use `func.now()`, never Python `datetime.now()`. `now()` is the transaction start time, which is acceptable. Use `clock_timestamp()` only if you want mid-transaction precision.
   - Expired rows are never deleted on read. Leave them as dead rows and show them to staff as "expired". A purge job is a follow-up.
   - Put this query in the repository as `effective_limit(user_id, metric_id)`. ADM-042 reuses it inside its consume transaction.
5. **Concurrency.** Use `INSERT ... ON CONFLICT (user_id, metric_id) DO UPDATE SET limit_value, expires_at, created_by, updated_at = now() RETURNING (xmax = 0) AS inserted`. This is race-free, and two concurrent sets produce last-writer-wins with no 500 from a unique violation. For audit "from" values, run `SELECT ... FOR UPDATE` first, or read the old row in the same transaction. Without the lock the from/to diff can be wrong under a race. DELETE with `RETURNING` gives the old values for the audit entry atomically. Optional `If-Match` is not required here, because ADR-0024 point 8 only specifies it for PATCH on metrics.
6. **Migration.** `down_revision = "d7a3f5b2e801"`. `upgrade()` runs `create_table` with every named constraint, the FKs and the index. `downgrade()` drops the index and the table. Import the model in app/db/models.py, run `alembic check`, and test upgrade → downgrade → upgrade. It is a new table with no backfill, so there is no lock risk. It is a High path, so it goes through the plan gate.
7. **Tests (R8).** Cover set, replace (the second row is not duplicated), remove, 404 for a missing user or metric, 422 for a missing reason and for negative or too-large limits or a past `expires_at`, and 401/403. For expiry, insert an override row with a past `expires_at` directly, then assert the effective limit falls back to the default. Do not mock the clock.

**Risks**
- If the reason is stored as a column, it contradicts ADR-0024 point 5. Keep it audit-only.
- The CHECK `expires_at > created_at` becomes wrong on replace (see 3).
- Audit "from" values are inaccurate without row locking.
- If a metric is archived, ADM-042 must decide whether to honour its overrides. ADM-040 should not block setting them.

**Open questions for Ming (suggested defaults)**
1. Reason as an audit-only field (ADR) or also a column? Default: audit-only. Staff then cannot see "why" in the override list.
2. Can an override be set or replaced on an archived metric? Default: 409 (`metric_archived`). Removing an override stays allowed.
3. Can an override equal the default? Default: allowed, and no special case.
4. User deleted: the CASCADE default is fine. Confirm that deleting the user also removes the audit trail's meaning (audit keeps ids only).
5. Should the set response return `expires_at`? Default: yes. Should an expired override stay visible to staff? Default: yes, flagged `expired: true`, and removable.
