# ADR-0009: Audit log

Status: Accepted (ADM-004 plan, 2026-11-15)
Date: 2026-11-15

## Context
Spec §5.4 requires an append-only record of every mutating admin action (actor, action, target,
diff, timestamp, request id) with a read-only, filterable listing; spec §6 requires that 100% of
mutating endpoints write an entry. CLAUDE.md R4 puts the entry in the same transaction as the
change. Identity events (identity-architecture.md S8) are split: join is in ADM-004; login,
logout, reuse revocation, reset, verification and denied attempts are ADM-004b.

## Decision
1. **Append-only twice.** `AuditRepository` exposes only `add` and `list`. In the database a
   trigger function `audit_log_immutable()` raises `audit_log is append-only` on UPDATE or
   DELETE (row trigger) and TRUNCATE (statement trigger). Triggers bind the table owner too; only
   DDL defeats them. Splitting the runtime DB role (INSERT/SELECT only) from the migration owner
   is a deployment follow-up.
2. **Snapshots, no FKs.** `actor_user_id`, `target_id`, `app_key` and `org_id` have no foreign
   keys, so rows outlive deletes of users, apps and organizations. `app_key` is immutable (D18),
   so it identifies the app across time. `actor_kind` (`user`, `system`, `anonymous`) plus
   `actor_label` (`system:cli`) covers non-human actors.
3. **One write path.** `AuditLogger(session).record(...)` adds and flushes one row in the
   caller's transaction; it never commits. Each public mutator records exactly once, after its
   last guard and after its write has flushed, just before it returns. Internal helpers
   (`_grant_locked`, `_build_for_app`, `_set_role_permissions`, the private `_create`/`_update`)
   never record. No-ops record nothing.
4. **Allowlisted diffs.** `changes` is `{field: {"from", "to"}}` built only from the field lists
   in `app/modules/audit/diff.py`; never a model dump, never an email, hash, token or cookie. The
   logger rejects keys that look like secrets before any SQL.
5. **Context.** Routers pass an HTTP-free `AuditContext(ip, user_agent, request_id)`; the CLI
   passes `AuditContext.NONE`. The request id comes from a pure-ASGI middleware that keeps a
   valid inbound `X-Request-ID` (`^[A-Za-z0-9._-]{1,64}$`) or generates a UUID4, and echoes it.
6. **Failures write nothing in ADM-004.** A business failure (404/409/422/403 guard) rolls back
   with the request and leaves no row.
7. **ADM-004b failure path (decided here).** `AuditLogger.record_failure(...)` on the request's
   own session: `rollback()` (discards partial work), `add(row)`, `commit()`, then the caller
   raises the intended error. Wrapped in `try/except Exception` that logs
   `audit_write_failed action=<a>` without PII, so the intended 401/403/409 never becomes a 500.
   No separate engine or session. An additive `outcome` column (`success|failure|denied`) ships
   with it.
8. **Reads.** `GET /api/v1/audit-logs` needs `admin:audit:read` (seeded on app `admin`,
   `is_admin=true`, granted to no role, so only `admin:*` holders read it). Reading is not
   audited. Retention is indefinite in v1.

## Consequences
- A new mutating admin route fails `tests/test_audit_coverage.py` until it is mapped to an
  action with success and failure scenarios.
- A production downgrade past the migration destroys the log; `docs/deploy.md` forbids it.
- Rows of one transaction share `occurred_at` (`now()`); `id` breaks ties in the listing.
