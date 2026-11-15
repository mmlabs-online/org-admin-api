# consult-security-architect — ADM-004

**Findings**

1. The interim `LogAuditRecorder` (app/modules/audit/recorder.py) only logs. It accepts nothing but UUIDs, which is the right shape for R11. Its one real caller is `membership/service.py` (`app.join`). It has no ip, user agent, request id or `changes` field.
2. About 25 call sites in `rbac/service.py` and `users/service.py` are marked `# R4: … once ADM-004`. These cover app create/update/delete, register-app, set-admin-origins, grant-super-admin, permission/role CRUD, replace_permissions, `_build_for_app` (default role), assign/revoke, and user status changes.
3. Org create/update/delete in `organizations/service.py` has no R4 marker. Nothing is audited there today.
4. There is no request-id middleware anywhere in `app/`, but the acceptance criteria require a `request_id`.
5. Two transaction traps:
   - **Login failure.** The attempt counter is committed before Argon2 (`failures.commit()`). The 401 is then raised, and `get_session` rolls back. An audit row written "in the request transaction" would be lost.
   - **Device-session reuse.** `resolve()` revokes, commits (`commit_on_reuse`), then raises. The revocation persists, but a row written after that commit would be rolled back. In `start_for_login`, the reuse revocation lands only if the login itself succeeds.
6. Denied privileged attempts raise `AdminWildcardRequiredError` (`require_super`), `SelfAssignmentForbiddenError`, `SelfStatusChangeForbiddenError` or `LastSuperAdminError`, and every one rolls back.
7. CLI commands use `SYSTEM_CLI` (`actor_user_id` → None). `cleanup` commits per batch.
8. Other security-relevant events have no audit marker at all: user disable also calls `revoke_all_accounts_of_user`, and password reset completion, email verification and signup are not marked either.
9. The app and migrations appear to use one DB role, so a DB-level `REVOKE` does not hold against the table owner.
10. The permission `admin:audit:read` does not exist yet. Existing keys are `admin:{apps,organizations,users}:{read,write}`, `admin:roles:{assign,manage}`.

**Recommendations**

- **Schema** (acceptance criterion 1, R2): `audit_log` with these columns:
  - `id uuid`, `occurred_at timestamptz default now()`
  - `actor_type` enum `user|system|anonymous`, `actor_user_id uuid NULL` (no FK, so rows outlive deleted users), `actor_name` (`system:cli` / `system:join`)
  - `action` (dotted verb from a closed `StrEnum`), `outcome` (`success|failure|denied`)
  - `target_type`, `target_id`, `app_key`, `org_id`
  - `changes jsonb`, `ip inet`, `user_agent varchar(256)`, `request_id`
  - Indexes on `(occurred_at)`, `(actor_user_id, occurred_at)`, `(target_type, target_id)`, `(app_key, occurred_at)`, `(action, occurred_at)`
- **Request id** (acceptance criterion 1): add a small middleware. Accept an inbound `X-Request-ID` only if it is a valid UUID, otherwise generate one, and echo it in the response. Otherwise the column stays empty.
- **AuditLogger** (R1): a repository-backed service that takes the caller's `AsyncSession`, with a typed `record(action, actor, target, *, changes, outcome, ctx)`. `ctx` (ip, user agent, request id) comes from a router dependency. Services never read the request.
  - Build `changes` from an **allowlist** of fields per model (`{field: [old, new]}`), never by dumping the model.
  - Never include `password_hash`, `token_hash`, `prev_token_hash`, cookie values, Turnstile tokens, or emails of accounts that do not exist.
  - Show an account's own email masked (`m***@domain`) or not at all: `target_id` is enough.
- **Event catalogue, ADM-004:**
  - `organization.{created,updated,deleted}`
  - `user.{disabled,enabled}` (plus `sessions_revoked=count`)
  - `app.{created,updated,deleted,registered,admin_origins_set,default_role_built}`
  - `permission.{created,updated,deleted}`, `role.{created,updated,deleted,permissions_replaced}`
  - `role_assignment.{granted,revoked}`, `super_admin.bootstrapped`
  - `app.join` (and `app.join_decided` later, USR-03g)
  - CLI writes run in the same transaction with `actor_type=system`, `actor_name=system:cli`, plus the OS user from `getpass.getuser()` in `changes`.
  - `cleanup` writes one summary row (`retention.purged`, counts per table) after its batches. It is not a per-row event.
- **Event catalogue, identity:**
  - `auth.login.{succeeded,failed}`, `auth.logout`, `auth.logout_all`, `device_session.reuse_revoked`
  - Recommended additions: `auth.password_reset.completed`, `auth.email.verified`
- **Failed and denied attempts** (acceptance criterion 5 says "no entry when the action fails"; I suggest narrowing it):
  - Business failures (404/409/422) produce **no** row.
  - Security-relevant denials **do** produce a row with `outcome=denied`: privileged guards (403 from `require_super`, self-assignment, self-status), `LastSuperAdminError`, failed login, reuse revocation.
  - Write those through a **separate short session/transaction** (an `AuditLogger.record_detached`) committed before the exception propagates.
  - Do not audit plain `require_permission` 403s on reads; the log and rate limiter already cover those.
- **Failed login with unknown emails** (R11): store `email_hmac`, the same keyed HMAC as the throttle (`EmailHasher`), hex-encoded in `changes`, and no email at all. For a known user, also set `target_id=user.id`. The reason is internal only (`unknown|bad_password|disabled`), never returned by the API. Store nothing that lets the HMAC be reversed. Never store the password, its length or its hash.
- **Tamper resistance** (R4, spec §5.4):
  - No update or delete in the repository or the API.
  - A migration adds a `BEFORE UPDATE OR DELETE OR TRUNCATE` trigger that raises an exception, with a working `downgrade()`.
  - Follow-up for deploy: split the migration (owner) role from the runtime role, and grant the runtime role only `INSERT, SELECT` on `audit_log`. Until then the trigger only raises the bar.
- **Read access:** seed `admin:audit:read` (migration plus the admin catalog), with endpoint `GET /api/v1/audit-logs` returning `Page[T]`. Filters: `actor_user_id`, `action` (exact or prefix), `target_type`+`target_id`, `app_key`, `org_id`, `from`/`to` (half-open, max range enforced). Ordering is `occurred_at desc, id`. Grant `admin:audit:read` only to super-admin by default; a separate auditor role can hold it later. Reading the audit log is not itself audited in v1 (follow-up).
- **PII and retention:** store `ip` truncated to /24 (IPv4) and /48 (IPv6)? I recommend full IP but a retention policy. Truncate user agent to 256 characters. Propose 400-day retention, purged by `cleanup` via a security-definer function, which is the only delete path. Retention is the owner's call (spec Q6).
- **Split** (R7):
  - **ADM-004:** table, migration and trigger; request-id middleware; AuditLogger; all admin mutations and CLI operations; denied privileged attempts; `app.join` (replacing the interim recorder); read endpoint and tests.
  - **ADM-004b:** login success/failure, logout one/all, reuse revocation, password reset/verify, the detached-write path for unauthenticated flows, retention purge.

**Risks**

- A detached audit write that fails during login can turn a 401 into a 500, which becomes an error oracle. Catch the error, log `audit_write_failed` without PII, and keep the 401. Do the same for reuse.
- Failed-login rows are a write-amplification vector. Per-IP (20/min) and per-email throttles already bound them, but rate-limited 429s must not be audited.
- Diff builders that serialize whole models will leak hashes or secrets. Use allowlists and a test asserting no `*_hash`/`token` keys.
- The trigger can be dropped by the shared owner role, so the log is not tamper-proof against a compromised app or DB credential.
- An `org_id`/`actor_user_id` without FKs can refer to deleted rows. That is acceptable and intended.

**Open questions**

1. Should `admin:audit:read` be super-only, or held by a dedicated `auditor` admin role from the start?
2. Retention period, and should IPs be stored in full or truncated (spec Q6)?
3. Should the acceptance criterion change to "no entry when a business action fails; denied privileged attempts and failed logins are recorded with `outcome`"?
4. Will the owner provision a separate migration DB role in `mmlabs-online/deployment` so the runtime role can be restricted to INSERT/SELECT?
5. Should signup, password reset and email verification join the catalogue (ADM-004b), given they are not in the ticket?
