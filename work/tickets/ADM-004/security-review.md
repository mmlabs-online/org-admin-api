## Attempt 1

# security-review: ADM-004, attempt 1 (appsec-engineer)

Scope: `git diff origin/main...HEAD` at 990cfbd "ADM-004: audit log table, logger, admin and CLI events, read endpoint". I reviewed it by reading the code only. I did not run any tests, to avoid the shared test DB.

## Findings

No 🔴 blockers and no 🟡 should-fix findings. All findings are 💭 notes.

### R11: secrets leaking into `audit_log`: clean
- **`changes` uses allowlists only.**
  - The field lists are in `app/modules/audit/diff.py:12-16`: ORG, APP, PERMISSION, ROLE and USER(status).
  - No `model_dump()` reaches the audit log. The only `model_dump()` is in `Organization(**…)` at `organizations/service.py`, and it builds the ORM object, not the diff.
- **Assignment rows** carry user_id, role_key, role_id, org_id and expires_at. They have no email (`rbac/service.py:977-988`).
- **`grant-super-admin`** takes an email as input but records only the user_id (`rbac/service.py`, the `bootstrap_super_admin` → `_record_grant` call).
- **Join rows** hold an `EndUser` actor, a target of type role_assignment, `app_key` and `{"role_key"}` (`membership/service.py:102-109`). Nothing else is stored: no Origin, no `X-MM-App`, no cookie, no device-session id.
- **CLI rows** (register-app, set-admin-origins, cleanup):
  - register-app stores field names and values, origins, permission keys and member-permission keys.
  - set-admin-origins stores only the admin app's own origins.
  - Origins are public configuration, so none of this is a secret.
  - The cleanup summary stores row counts only (`auth/retention.py:80-86`).
- **Request id and user agent are never logged** (`core/request_id.py:4`, `audit/deps.py`).

### Forbidden-key guard
- 💭 **`audit/service.py:19-21,50-52`: the guard checks only top-level keys, never values or nested keys.** That is acceptable, because the allowlists are the real control and the guard is a backstop. Two hardening options:
  - Extend the regex with `secret|salt|credential|api_?key|private|jwk|signature`. `secret` is already in the list.
  - Tie the `_ALLOWED_KEYS` exemption (`email_send_log` / `email_tokens`) to `AuditAction.EMAIL_RETENTION_PURGED`, and require integer values. Today any action may use those two keys with any value.

### `X-Request-ID` and User-Agent: header and log injection
- **`X-Request-ID` is safe.** `core/request_id.py:14,27` checks it with a strict `fullmatch` against `[A-Za-z0-9._-]{1,64}` and replaces anything else with a UUID4. It cannot carry CR/LF into the echoed header. The DB CHECK `ck_audit_log_request_id_format` backs this up (`migration:83-86`).
- 💭 **`core/request_id.py:27`: the client controls the request id.** Anyone, including an end user on `/auth/{key}/join`, can set it to collide with another request's id. That makes it a correlation hint, not evidence. `docs/deploy.md` ("the same id is stored on the audit row") reads as if the value were trustworthy. Two options: accept an inbound id only from `trusted_proxy_cidrs` peers, or document that it is client-supplied.
- 💭 **`audit/service.py:57`: the user agent is cut to 512 characters, but control and high (`obs-text`) characters are stored as they arrive.** The JSON API output is safe. The admin web UI must render the value as text and never as HTML. A defensive option is to drop C0 control characters before storing.

### IP source
- **The IP comes only from trusted proxies.** `audit/deps.py:16` reuses `client_ip()` (`core/client_ip.py:8-24`): `CF-Connecting-IP` is read only when the peer is inside `trusted_proxy_cidrs`, and `X-Forwarded-For` is never read.
- The production `Dockerfile:18` uses `--no-proxy-headers`.
- `_valid_ip` drops values such as "unknown" (`audit/service.py:24-30`).

### Append-only enforcement
- **The DB enforces it.** A row trigger on UPDATE and DELETE plus a statement trigger on TRUNCATE (`migration:112-123`).
- **The repository has only `add` and `list`** (`audit/repository.py`).
- **No ORM path can delete audit rows:**
  - `audit_log` has no FKs, so no cascade can delete rows.
  - A loaded `AuditLog` that is dirtied and flushed hits the trigger and raises.
  - Org, app and user deletes cannot cascade into `audit_log`.
- 💭 **The table owner can still `DISABLE TRIGGER`, `DROP` it, or set `session_replication_role=replica`.** The runtime role is the owner, and anyone holding the app DB credential (or with SQL injection elsewhere) can do this. This is already recorded as a deployment follow-up (ADR-0009 §1, `docs/deploy.md`): split roles and grant only INSERT/SELECT on `audit_log`. Keep that follow-up tracked.
- 💭 **`migration:133-148`: `downgrade()` destroys the log.** This is documented ("never downgrade production past c3f1a9d27b60"), but nothing enforces it. Option: make `downgrade()` refuse when `audit_log` has rows unless a guard env var is set. That would need R2 sign-off.

### Read endpoint `GET /api/v1/audit-logs`
- **Auth class 1.** `current_admin` plus `require_permission("admin:audit:read")` (`audit/router.py:18,21`).
- **The permission is seeded with `is_admin=true` and granted to no role** (`migration:125-130`). Only `admin:*` holders (super-admins) can read the log, through `permits()`. Granting it to another admin role goes through the admin-app `require_super` guards (`rbac/service.py:690,722`).
- **Filters are validated and SQL injection is not possible.**
  - `actor_kind`, `action` and `target_type` are enums.
  - The ids are UUIDs.
  - `app_key` must match `APP_KEY_RE`.
  - `from` and `to` must be timezone-aware datetimes, and `from` > `to` gives 422.
  - All queries are parameterised SQLAlchemy (`audit/repository.py:38-62`).
  - `limit` is at most 100 through `PageDep`.
- 💭 **No limit on `offset`, and `count(*)` scans the whole filtered set on every call.** This is acceptable for a super-admin-only endpoint. Revisit when the table grows (retention is indefinite).
- 💭 **Information disclosure is acceptable.** Super-admins see other admins' and end users' IPs, user agents and actions. Join rows put the IP and user agent of end users of every app into an indefinitely retained table. That is personal data. It is acceptable for `admin:*`, but the retention and IP-truncation decision (spec Q6, deferred to ADM-004b) should be made before any non-super "auditor" role gets this permission.

### Forging and integrity
- **The client controls no audit fields except User-Agent, request id and (through a trusted proxy only) the IP.**
  - The actor comes from the server-reloaded `AdminPrincipal`, the `EndUser(user.id)` of the resolved device session, or `SYSTEM_CLI`.
  - The action and target types are enums, and the DB CHECKs back them up.
  - Reading the log does not write rows, so there is no mass-assignment surface.

### Mutation without its audit row, or an audit row without its mutation
- **Every admin mutator records after its write is flushed, in the request session.** `get_session` commits or rolls back both together.
- **No-ops write no row, and business failures roll back** (ADR-0009 §3, §6).
- **CLI dry-runs roll back through `_Rollback` / `_AdminOriginsRollback`, so their audit rows are discarded too** (`app/cli.py:346,446`).
- 💭 **`auth/retention.py:68-87`: cleanup commits each batch before it writes the summary row.** A crash or failure mid-run leaves deleted rows with no audit row. This is accepted by design (per-batch commits, USR-04d), and the operator workflow run is the fallback trail. Option: write a summary row per table after that table's loop, or say this in ADR-0009.
- 💭 **`app.deleted` records no child rows.** The app delete cascades to its permissions, roles and role_permissions (`rbac/models.py:110,130,148,154`), and only the `app.deleted` row is written. Assignments block the delete, so no grant is lost silently. Acceptable.

## Follow-ups (out of scope)
- Deployment: a runtime DB role with only INSERT/SELECT on `audit_log`, separate from the migration owner. Already noted in `docs/deploy.md`.
- ADM-004b: decide audit retention and IP truncation (spec Q6) before widening `admin:audit:read`.
- Optional: audit reads of the audit log.

**Verdict:** PASS (security). There are no blockers. R4, R5 and R11 hold. The 💭 items are hardening and documentation only.
