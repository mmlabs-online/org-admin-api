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

## Amendment — failure paths, identity events, outcome (ADM-004b, 2026-11-15)

Supersedes §6 and §7. A single rollback-first mechanism would undo the device-session reuse
revocation and cannot run for login before the password check, so each path has its own write:

| Path | Method | Transaction |
|---|---|---|
| Success (admin mutations, CLI, join, identity events) | `record` | Caller's transaction; flush only; an audit error fails the request (R4) |
| Failed login (after Argon2) | `record_failure` | `rollback()` (drops only the read; the attempt reservation is already committed) → add → `commit()`; errors logged as `audit_write_failed`, never raised |
| Denied privileged attempt | `record_denied` → `record_failure` | As above; only for an `AdminPrincipal` (system actors write nothing) |
| Device-session reuse on a cookie route | `record_in_savepoint` | Savepoint before the existing reuse commit; an audit error is logged and never undoes the revocation |
| Device-session reuse during login | `record` | Part of the login transaction (an audit error is a 500) |

- **`outcome`** (`success|failure|denied`, default `success`, CHECK + partial index on
  `failure|denied`) is always present in `GET /api/v1/audit-logs` and filterable.
- **`changes`**: admin mutations keep `{field: {from, to}}`; identity and failure rows use a flat
  object of allowlisted scalars. The `email_send_log`/`email_tokens` key exemption applies only to
  `email_retention.purged` with integer `{from: null, to: n}` values.
- **Denied attempts.** Each public mutator wraps its guard explicitly
  (`except DENIED_ERRORS: record_denied(...); raise`), never in a private helper or a global
  handler: `AppService.update`, `PermissionService.create/update/delete`,
  `RoleService.create/update/delete/replace_permissions`, `AssignmentService.assign/revoke`,
  `UserService.set_status`. `DENIED_ERRORS` = `admin_wildcard_required`,
  `self_assignment_forbidden`, `self_status_change_forbidden`, `last_super_admin`. The row carries
  the attempted action, `outcome=denied`, `changes={"reason": <code>, ...catalog keys}` (`role_key`,
  `permission_keys_added/removed`), server-resolved values only. Plain `require_permission` 403s
  are not recorded. Callers copy primitives before the call (the rollback expires ORM objects).
- **Identity events.**

  | Action | Actor | Target | `changes` |
  |---|---|---|---|
  | `auth.login_succeeded` | user | user | `account_session_id`, `device_session_id` |
  | `auth.login_failed` (credential-checked only) | anonymous | user, or none for an unknown email | `reason` (`unknown_account`, `invalid_credentials`, `user_disabled`), `failures` |
  | `auth.logged_out` | user | user | `account_session_id` |
  | `auth.logged_out_all` | anonymous | device_session | `accounts_revoked` |
  | `device_session.reuse_revoked` | anonymous | device_session | `accounts_revoked`, `during_login` |
  | `auth.password_reset_completed` | user | user | `account_sessions_revoked`, `verified` |
  | `auth.email_verified` | user | user | — |

  A 429, a Turnstile refusal, a no-op logout, an already revoked device or an idempotent verify
  writes nothing. No email, HMAC, password, token or cookie is ever stored.
- **Not audited:** sign-up and token issuance (follow-up), verification resend, password forgot.
- **`X-Request-ID`** is kept only from a peer in `TRUSTED_PROXY_CIDRS`, otherwise generated. It is
  a correlation id, not evidence (the trusted proxy forwards client headers).
- **Retention** stays indefinite until ADM-004c (purge; see the amendment below).

## Amendment — retention purge (ADM-004c, 2026-11-15)

Supersedes §8's "Retention is indefinite in v1" and the ADM-004b amendment's retention line.

- **Trigger.** `audit_log_immutable()` (migration `d7e4b2a91c3f`) still refuses every UPDATE and
  TRUNCATE. A DELETE passes only when the transaction set `SET LOCAL audit.allow_purge = 'on'`
  **and** `OLD.occurred_at < now() - interval '90 days'`. The floor is hard-coded in the function,
  never read from env or settings; `MIN_DAYS` in `app/modules/audit/purge.py` mirrors it (tested).
- **Latch vs. floor.** The setting is a latch against accidents, not access control: any session
  of the owner role can set it. The floor is what guarantees recent evidence cannot be erased
  without DDL, until the API runs as a role with INSERT/SELECT only (follow-up).
- **CLI only.** `python -m app.cli audit-purge --older-than-days N [--dry-run]` (90 ≤ N ≤ 3650) is
  the only caller; `app/modules/audit/purge.py` is the only `app/` module that sets the latch and
  only `app/cli.py` imports it. There is no HTTP route. Static tests enforce all three.
- **Summary rows.** Each batch transaction that deletes rows writes one `audit_log.purged` row
  (`system:cli`, `{cutoff, older_than_days, deleted}`), so no deletion exists without its row
  (R4). `audit_log.purged` rows are never purged. A run that deletes nothing writes no row (as
  §3: no-ops record nothing); the ops run summary is the trace. The email cleanup follows the same
  rule: one `email_retention.purged` row per deleting batch.
- **Backups** keep purged rows until backup retention expires. Retention period, per-class
  retention and backup erasure remain owner questions (spec Q6, Decree 13/2023); the default
  policy is 365 days.

## Amendment — DB guards (ADM-004d, 2026-11-15)

- **Purge trail.** `audit_log_immutable()` (migration `e91b6c3d58a2`) refuses DELETE of
  `audit_log.purged` rows unconditionally, latch and age notwithstanding.
- **Insert stamp.** A `BEFORE INSERT` trigger sets `occurred_at := now()` (transaction time, so
  rows of one transaction still share it); no backdated rows. Only owner DDL bypasses it; tests
  disable it inside a rolled-back transaction (`backdate_audit` marker).
- **Latch wording.** Any DB role can set `audit.allow_purge`, not only the owner (corrects the
  ADM-004c amendment).

## Amendment — generated request id (ADM-002d, 2026-11-15)

Supersedes §5's "keeps a valid inbound `X-Request-ID`" and the ADM-004b `X-Request-ID` bullet.

- **Always generated.** The middleware generates a UUID4 for every request, stores it in the
  request state (and so on the audit row) and echoes it. An inbound `X-Request-ID` is ignored
  from any peer, trusted proxies included: the stored id is unique and no client can choose it.
  Callers no longer get their own id echoed back.
- **Cloudflare correlation**, if ever wanted, logs a validated `cf-ray` next to the id in the app
  log (no schema change; not done here). `CF-Ray` is not used as the id: any container on the
  trusted network could forge it.
- **Older rows.** Rows written before this release may hold client-supplied ids (correlation only,
  not evidence).
- **2026-11-18 (ADM-022):** the CSV export (GET /api/v1/audit-logs/export) is the one audited read: one audit_log.exported row (filters, row_count, truncated, max_rows; no row data) in the request transaction. List and get stay unaudited.
