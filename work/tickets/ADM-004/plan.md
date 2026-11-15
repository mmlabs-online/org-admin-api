Status: READY

# Plan — ADM-004: Audit log (admin actions, join, CLI) + read endpoint

This plan covers **slice 1 (ADM-004)** of the split below. The identity events (login, logout,
reuse revocation), the failure-path writer and denied privileged attempts move to **ADM-004b**.

## Spec summary
- spec §5.4: "Append-only record of every mutating admin action: actor, action, target type/id,
  before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors."
- spec §6: "100% of mutating endpoints write an audit entry (once §5.4 lands)."
- spec §5.8 Admin: "staff can list, look up, disable/enable users, manage their roles per app and
  revoke their sessions (audited, §5.4)."
- identity-architecture.md §5 S8: "login (success/failure), logout, join, role/assignment changes,
  org membership changes are audit events (ADM-004)." In this slice: join and role/assignment
  changes. Login and logout go to ADM-004b. Org membership goes to USR-06 (ticket Dependencies:
  "USR-06 events are added there").
- identity-architecture.md D8: join "assigns the default role at app scope, writes audit."
- Ticket AC1, AC3, AC4 and AC5 in full. AC2 covers every admin mutation, every CLI write and
  `app.join`. The rest of AC2 maps as follows. Login and logout go to ADM-004b. Approval
  decisions go to USR-03g, which does not exist yet. Org membership goes to USR-06.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `audit_log` table + migration with: actor (`actor_kind`, nullable `actor_user_id`, `actor_label`), action, target type/id, app key, org id, `changes`, ip, user agent, request id, `occurred_at`. Working `downgrade()`. `alembic check` reports no drift | `make check` (CI also runs `alembic check`); `tests/test_audit_schema.py` (all CHECKs); the session fixture runs downgrade to base and then upgrade |
| 2 | Append-only. The code has no update or delete path: the repository exposes only `add` and `list`. The DB refuses UPDATE, DELETE and TRUNCATE | `test_audit_schema.py › test_update_refused`, `test_delete_refused`, `test_truncate_refused`; review of `audit/repository.py` |
| 3 | Every mutating Bearer route maps to an `AuditAction`. A successful, state-changing call writes **exactly one** row, in the request transaction. A business failure (404/409/422/403 guard) writes **none**. A no-op writes none | `tests/test_audit_coverage.py › test_every_mutating_bearer_route_is_mapped`, `test_success_writes_one_row[*]`, `test_failure_writes_no_row[*]`, `test_noop_writes_no_row`, `test_strict_rollback_leaves_no_row` |
| 4 | `app.join` writes one row in the join transaction. This replaces `AuditRecorder` and `LogAuditRecorder`, which are deleted. "Already a member" and refusals write none | `tests/test_auth_join.py › test_join_writes_audit_row`, `test_join_already_member_no_audit_row`, `test_join_closed_no_audit_row`; `grep -r AuditRecorder app tests` finds nothing |
| 5 | The CLI writes one `system:cli` row per state-changing run: register-app (created/updated), set-admin-origins (updated), grant-super-admin (granted), cleanup (summary). Unchanged runs, dry runs and refusals write none | `test_cli_register_app.py`, `test_cli_set_admin_origins.py`, `test_cli_grant_super_admin.py`, `test_cli_cleanup.py` (new `*_audit_*` tests, listed below) |
| 6 | Each row carries `ip` (client IP, the same resolution as the rate limiter), `user_agent` (≤ 512) and `request_id` from the request-id middleware | `test_audit_coverage.py › test_row_carries_request_context`; `tests/test_request_id.py` |
| 7 | Request id: a valid inbound `X-Request-ID` (`^[A-Za-z0-9._-]{1,64}$`) is kept. A missing or invalid one is replaced by a generated UUID4. It is echoed on every response, error responses included | `tests/test_request_id.py` (5 tests) |
| 8 | `changes` holds only allowlisted fields as `{field: {"from", "to"}}`, never model dumps. No `*_hash`, `token`, `password`, cookie or email (no `@`) ever appears. The logger rejects forbidden keys | `test_audit_coverage.py › test_no_secret_or_email_in_any_row` (scans every row the coverage run writes); `tests/test_audit_logger.py › test_forbidden_change_key_rejected` |
| 9 | `GET /api/v1/audit-logs`: `Page[AuditLogRead]` ordered `occurred_at DESC, id DESC`. Filters: `actor_user_id`, `actor_kind`, `action`, `target_type`, `target_id`, `app_key`, and `from`/`to` as the half-open range `[from, to)` (aware datetimes). Bearer + `admin:audit:read` | `tests/test_audit_logs_api.py` (list below); `test_route_auth_classes.py` BEARER map gains the route |
| 10 | `admin:audit:read` is seeded on app `admin` (`is_admin=true`) and granted to no role, so only `admin:*` holders can read the log | `test_audit_logs_api.py › test_permission_seeded_not_granted`, `test_403_without_permission` |
| 11 | `make check` is green | CI / `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py` | new: table, CHECKs, indexes, `audit_log_immutable()` function + 2 triggers, seed of `admin:audit:read`; downgrade in reverse order | AC1, AC2, AC10, R2 |
| `app/modules/audit/models.py` | new: `AuditLog` model (no `TimestampMixin`), CHECKs and indexes that mirror the migration | AC1, R2 (`alembic check`) |
| `app/modules/audit/actions.py` | new: `AuditAction`, `AuditTarget`, `ActorKind` StrEnums | single action catalogue |
| `app/modules/audit/context.py` | new: HTTP-free `AuditContext(ip, user_agent, request_id)` frozen dataclass + `AuditContext.NONE` (CLI) | R1 |
| `app/modules/audit/deps.py` | new: `audit_context(request)` dependency + `AuditContextDep` | R1 (only routers read the request) |
| `app/modules/audit/diff.py` | new: `snapshot(obj, fields)`, `diff(before, after)`, JSON coercion (UUID/datetime/enum/list); per-entity field allowlists | AC8, R11 |
| `app/modules/audit/repository.py` | new: `AuditRepository.add(row)` + `list(filters, limit, offset)` with `count(*)`. Nothing else | AC2, AC9, R1 |
| `app/modules/audit/service.py` | new: `AuditLogger(session).record(...)` (forbidden-key guard, flush); `AuditLogService.list(...)` (`from > to` → 422) | AC3, AC8, AC9 |
| `app/modules/audit/schemas.py` | new: `AuditLogRead` | AC9 |
| `app/modules/audit/router.py` | new: `GET /audit-logs` (Bearer router, `current_admin` + `require_permission("admin:audit:read")`) | AC9, R3 |
| `app/modules/audit/recorder.py` | **delete** | replaced (ticket scope: "replacing the interim interface") |
| `app/core/request_id.py` | new: pure-ASGI `RequestIdMiddleware` (sets `scope["state"]["request_id"]`, echoes `X-Request-ID`) | AC7 |
| `app/main.py` | add `RequestIdMiddleware` **after** `DynamicCORSMiddleware`, so it is outermost; include `audit_router` like `users_router` | AC7, AC9 |
| `app/db/models.py` | import and export `AuditLog` | R2 |
| `app/modules/organizations/router.py` | mutators take `principal: AdminDep`, `ctx: AuditContextDep`, and pass `actor=` / `ctx=` | AC3 (orgs had no actor) |
| `app/modules/organizations/service.py` | `create/update/delete(..., actor, ctx)` record `organization.*`; update/delete snapshot before the change | AC3 |
| `app/modules/users/router.py`, `users/service.py` | `set_status(..., ctx)` records `user.disabled` / `user.enabled` after the write; no row on a same-status no-op | AC3 |
| `app/modules/rbac/router.py`, `rbac/assignments_router.py` | mutators take `ctx: AuditContextDep` and pass `ctx=` | AC3 |
| `app/modules/rbac/service.py` | record in each public mutator after its last write (see Approach §4). Split `AppService.create`/`update` and `PermissionService.create` into private `_create`/`_update` bodies plus auditing public wrappers. `register` and `set_admin_origins` write one summary row. `bootstrap_super_admin` and `assign` write `role_assignment.granted`, `revoke` writes `role_assignment.revoked`. Replace every `# R4 … ADM-004` marker | AC3, AC5 |
| `app/modules/membership/service.py` | `JoinService(session)` (no recorder); `join(..., ctx)` records `app.joined` via `AuditLogger` | AC4 |
| `app/modules/auth/router.py` | drop `LogAuditRecorder` / `get_audit_recorder`; `join_app` takes `ctx: AuditContextDep` | AC4 |
| `app/modules/auth/retention.py` | after the batches of a non-dry run: one `email_retention.purged` row (counts per table) + commit | AC5 |
| `tests/test_route_auth_classes.py` | add `("GET", "/api/v1/audit-logs"): "admin:audit:read"` to `BEARER` | R3 deny-by-default test |
| `tests/test_auth_join.py` | remove `FakeRecorder`/`recorder` fixture and the two recorder tests; add the row tests | AC4 |
| `tests/test_audit_schema.py`, `tests/test_audit_logger.py`, `tests/test_audit_coverage.py`, `tests/test_audit_logs_api.py`, `tests/test_request_id.py` | new | AC1–AC10 |
| `tests/test_cli_register_app.py`, `test_cli_set_admin_origins.py`, `test_cli_grant_super_admin.py`, `test_cli_cleanup.py` | add audit-row tests | AC5 |
| `docs/adr/0009-audit-log.md` | new ADR (see Approach §8) | rule 7: append-only trigger, no-FK snapshot, single write path, failure-path decision |
| `docs/identity-architecture.md` | S8 rewritten (what is audited, where, ADM-004b remainder); §4 row "Admin: `GET /api/v1/audit-logs`" | docs |
| `docs/deploy.md` | new "Audit log (ADM-004)" section; replace "Until ADM-004 the workflow run … is the audit trail" (2 places) with "the CLI writes a `system:cli` audit row; the workflow run stays the operator trail" | docs |

The implementer touches no other file without recording why in `impl-log.md`. Tests in other
files that break because a router now needs `principal`/`ctx` are fixed in place and listed in
the log.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_audit_schema.py › test_update_refused` / `test_delete_refused` / `test_truncate_refused` | AC2: each runs inside `db_session.begin_nested()` and expects `DBAPIError` with the message `audit_log is append-only` |
| `test_audit_schema.py › test_check_action_format`, `test_check_target_pair`, `test_check_changes_object`, `test_check_actor_consistency`, `test_check_request_id_format` | AC1 CHECKs (raw INSERTs in savepoints) |
| `test_audit_logger.py › test_forbidden_change_key_rejected` | AC8: keys matching `hash\|token\|password\|cookie\|email\|secret` → `ValueError` before any SQL |
| `test_audit_logger.py › test_values_json_coerced`, `test_user_agent_truncated`, `test_invalid_ip_dropped` | AC6/AC8 |
| `test_audit_coverage.py › test_every_mutating_bearer_route_is_mapped` | AC3: `AUDITED.keys() == {r for r in BEARER if r[0] != "GET"}` (imports `BEARER`), so a new mutating route fails until it is mapped |
| `test_audit_coverage.py › test_success_writes_one_row[route]` (16 params) | AC3: per-route scenario; +1 row, action ∈ `AUDITED[route]`, `actor_kind="user"`, `actor_user_id` = principal, target set |
| `test_audit_coverage.py › test_failure_writes_no_row[route]` (16 params) | AC3/AC5: 404 (unknown id/key) or 409 (duplicate slug/key) with **`make_client`** (no rollback): the row is written only after every guard and write succeeds |
| `test_audit_coverage.py › test_denied_guard_writes_no_row` | 403 `admin_wildcard_required` and 409 `last_super_admin` → 0 rows (in this slice; ADM-004b adds `outcome=denied`) |
| `test_audit_coverage.py › test_noop_writes_no_row` | same-value PATCH on app/user/role/permission, unchanged PUT of role permissions → 0 rows |
| `test_audit_coverage.py › test_strict_rollback_leaves_no_row` | `make_strict_client`: success commits 1 row; a 409 commits 0 |
| `test_audit_coverage.py › test_row_carries_request_context` | AC6: sent `X-Request-ID` + `User-Agent` stored; `ip == "127.0.0.1"` |
| `test_audit_coverage.py › test_no_secret_or_email_in_any_row` | AC8: after all scenarios, `changes::text`, `user_agent` and `actor_label` have no `_hash`, `token`, `password`, `cookie` or `@`, and no fixture user's email |
| `test_auth_join.py › test_join_writes_audit_row` | AC4: `app.joined`, actor `user`=joiner, target `role_assignment`=ra.id, `app_key`, `changes.role_key`, request ctx |
| `test_auth_join.py › test_join_already_member_no_audit_row`, `test_join_closed_no_audit_row` | AC4/AC5 |
| `test_cli_register_app.py › test_register_audit_created`, `test_register_audit_unchanged_none`, `test_register_audit_dry_run_none` | AC5: one `app.registered` row, `actor_kind=system`, `actor_label=system:cli`, no ip/ua/request id |
| `test_cli_set_admin_origins.py › test_set_admin_origins_audit_updated`, `test_set_admin_origins_audit_unchanged_none`, `..._dry_run_none` | AC5 |
| `test_cli_grant_super_admin.py › test_grant_audit_row`, `test_already_held_no_audit_row` | AC5: `role_assignment.granted`, target the assignment; no email in the row |
| `test_cli_cleanup.py › test_cleanup_audit_summary`, `test_cleanup_dry_run_no_audit_row` | AC5: `email_retention.purged` with `{"email_send_log": {"from": null, "to": n}, …}` |
| `test_audit_logs_api.py › test_list_newest_first_paged` | AC9: order, `total`, `limit`/`offset` |
| `test_audit_logs_api.py › test_filter_actor_user_id`, `test_filter_actor_kind`, `test_filter_action`, `test_filter_target`, `test_filter_app_key`, `test_time_range_half_open` | AC9 (`from` inclusive, `to` exclusive) |
| `test_audit_logs_api.py › test_unknown_app_key_empty_page` | AC9: the log outlives the app, so an unknown key gives an empty page, not a 404 |
| `test_audit_logs_api.py › test_422_naive_datetime`, `test_422_from_after_to`, `test_422_unknown_action`, `test_422_limit_over_100` | R8 validation |
| `test_audit_logs_api.py › test_401_without_token`, `test_403_without_permission` (`admin:users:read` only) | R8, AC10 |
| `test_audit_logs_api.py › test_permission_seeded_not_granted` | AC10: the permissions row exists with `is_admin=true` and has no `role_permissions` row |
| `test_request_id.py › test_generated_when_absent`, `test_valid_inbound_kept`, `test_invalid_inbound_replaced` (65 chars, space, `<`), `test_echoed_on_error_response` (404 envelope), `test_echoed_on_health` | AC7 |

A 404 test for the read endpoint does not apply: there is no by-id route (R8).

## Approach
1. **Migration + model (R2).** See *Data model*. Write the trigger and function DDL by hand,
   because `alembic check` cannot see them; the schema tests cover them instead. `downgrade()`
   deletes the seed permission, then drops the triggers, the function, the indexes and the
   table.
2. **Catalogue.** `AuditAction(StrEnum)` uses past tense, in the form `<resource>.<verb>`:
   `organization.created|updated|deleted`, `user.disabled|enabled`,
   `app.created|updated|deleted|registered|admin_origins_set|joined`,
   `permission.created|updated|deleted`,
   `role.created|updated|deleted|permissions_replaced`, `role_assignment.granted|revoked`,
   `email_retention.purged`. `AuditTarget(StrEnum)`: `organization`, `user`, `app`, `permission`,
   `role`, `role_assignment`. `ActorKind(StrEnum)`: `user`, `system`, `anonymous` (the last is
   reserved for ADM-004b).
3. **Logger (R1, R11).**
   ```python
   class AuditLogger:
       def __init__(self, session: AsyncSession) -> None: ...
       async def record(self, action: AuditAction, *, actor: Actor | EndUser, ctx: AuditContext,
                        target: tuple[AuditTarget, uuid.UUID] | None = None,
                        app_key: str | None = None, org_id: uuid.UUID | None = None,
                        changes: Mapping[str, Change] | None = None) -> None
   ```
   - `EndUser(user_id)` (in `audit/context.py`) is the joiner. An `AdminPrincipal` maps to
     `user` + `user_id`. A `SystemActor` maps to `system` + `actor_label=name`.
   - `record` rejects forbidden keys, coerces values to JSON, and stores an empty `changes` as
     NULL. It then runs `session.add` + `flush`, so a constraint error surfaces at the call
     site. It **never commits**: the request's `get_session`, or the CLI scope, owns the
     transaction.
   - Services build the logger in `__init__` (`self.audit = AuditLogger(session)`), so
     constructors keep their signatures.
4. **Call-site rule (AC3, AC5).** Record exactly once per public mutator. The call goes **after
   the last guard and after the write has flushed successfully** (after the
   `IntegrityError → ConflictError` block), immediately before `return`. No-op branches
   (`if not changes: return`, same-status) return before recording.
   - Internal helpers never record: `_grant_locked`, `_build_for_app`, `_set_role_permissions`.
     So `POST /apps` writes one `app.created` row, and the default role appears in its
     `default_role_id` change.
   - `register` uses the new private `AppService._create` / `_update` and
     `PermissionService._create`. It writes one `app.registered` row when the outcome is not
     `UNCHANGED`. The changes are: name/join_policy diffs, `origins {added, removed}`,
     `permissions_added`, and `member_permissions {added, removed}`. Set-valued entries use
     `{"from": [...removed], "to": [...added]}`, so every entry keeps the `{from, to}` shape.
   - `set_admin_origins` records only when `UPDATED`.
   - `bootstrap_super_admin` records `role_assignment.granted` only when `GRANTED`.
   - The CLI methods take no `ctx`: they use `AuditContext.NONE` and `SYSTEM_CLI`.
   - `EmailRetentionService.run` (not a dry run) records `email_retention.purged` after its
     loop, then `await repo.commit()`.

   Targets, `app_key` and `org_id` per action:
   - Org events: target `organization`, `org_id`=org.id.
   - Assignment events: target `role_assignment`, `app_key`, `org_id`=ra.org_id; changes
     `role_key`, `role_id`, `org_id`, `expires_at`; the target user id goes in
     `changes.user_id`.
   - User events: target `user`, changes `status`.
   - App, permission and role events: target the row, `app_key`=app.key.
   - `app.joined`: actor `EndUser(user.id)`, target `role_assignment`, changes `role_key`.
5. **Diff allowlists (AC8).** These field lists in `audit/diff.py` are the only source of
   `changes`:
   - `ORG`: slug, name, description, status
   - `APP`: key, name, status, join_policy, org_mode, origins, default_role_id
   - `PERMISSION`: key, description, is_admin
   - `ROLE`: key, name, is_system
   - `USER`: status

   Create = `{f: {from: null, to: v}}`, delete = `{f: {from: v, to: null}}`, update = changed
   fields only. Never call `model_dump()` and never take an email. In update services,
   `snapshot()` runs **before** `setattr`.
6. **Context (R1).** `audit_context(request)` builds the context from three sources:
   - `ip`: `client_ip(request, settings.trusted_proxy_cidrs)`, validated with `ipaddress`, so
     an invalid value becomes `None`.
   - `user_agent`: `request.headers.get("user-agent")`, cut to 512 characters.
   - `request_id`: `request.state.request_id`.

   Routers pass `ctx=ctx` next to `actor=principal`. `ctx` is a **required** keyword on every
   public mutator, so nothing fails open by forgetting it (the same pattern as `actor`).
7. **Request id.** A pure ASGI middleware handles `http` scopes only. It reads the first
   `x-request-id` header, keeps it if it matches the regex, and otherwise sets
   `str(uuid.uuid4())`. It stores the value in `scope.setdefault("state", {})["request_id"]` and
   appends `x-request-id` to the `http.response.start` headers. Registered last, it is the
   outermost user middleware, so CORS preflights and error envelopes carry it. Unhandled 500s
   produced by Starlette's `ServerErrorMiddleware` do not carry it (accepted). The inbound value
   is never logged.
8. **ADR-0009 (audit log).** The ADR records these decisions:
   - Append-only by repository shape plus a DB trigger. The trigger binds the owner role too;
     only DDL defeats it.
   - No FKs: the actor, app and org are snapshots that outlive deletes. `app_key` is immutable
     (D18).
   - One write path: in-transaction `session.add` before the service returns.
   - Business failures and denials write nothing in ADM-004.
   - The ADM-004b failure path is decided here (Split proposal), so 004b only implements it.
   - The DB-role split is a deployment follow-up.
9. **Read API.**
   - The router builds query params with these types: `AwareDatetime` for `from`
     (`alias="from"`) and `to`; `AuditAction`/`AuditTarget`/`ActorKind` enums; `app_key` with
     the `APP_KEY_RE` pattern; UUIDs for ids. Wrong types give 422 `validation_error`.
   - `AuditLogService.list` raises `UnprocessableError` (`details=[{"field":"from","reason":"after_to"}]`)
     when `from > to`.
   - The repository applies the filters with AND, `occurred_at >= from AND occurred_at < to`,
     orders by `occurred_at DESC, id DESC`, and uses offset pagination with `count(*)` (R9).
   - Reading is not audited.
10. **Rules.**
    - R1: routers handle HTTP and ctx only. `AuditContext` is HTTP-free.
    - R2: a single migration, with downgrade.
    - R3: the new route is Bearer and added to `BEARER`.
    - R4: this ticket implements it.
    - R5: no new config.
    - R6: `UnprocessableError`/validation give 422.
    - R7: no login/logout/denied-attempt writes, and no `org_id` filter, in this slice.
    - R8: tests as listed.
    - R9: `/api/v1/audit-logs`, `Page[T]`, `limit` ≤ 100, UUIDs, `timestamptz`.
    - R11: allowlists plus the forbidden-key guard; no email ever, the target id is enough.

## Specialist input used
- **database-optimizer.** Adopted:
  - the schema (no FKs, `actor_kind` + label, action regex, target pair, jsonb object CHECK,
    `inet`, `varchar(512)`, request-id length CHECK);
  - `occurred_at` with `server_default now()` and no `TimestampMixin`;
  - repository `add`/`list` only, triggers for UPDATE/DELETE (row) and TRUNCATE (statement),
    and an UPDATE test;
  - in-transaction add, no separate connection;
  - the request-id middleware, offset `Page[T]`, `[from, to)`, indefinite retention;
  - no `org_id` index (it is not a filter).

  Changed: indexes are ascending composites ending in `(occurred_at, id)` rather than declared
  `DESC`. Postgres scans them backward for `ORDER BY occurred_at DESC, id DESC`, and this avoids
  `alembic check` drift on DESC expression indexes. Login-failure commit-then-raise is deferred
  to ADM-004b.
- **api-platform-engineer.** Adopted:
  - `Page[T]` reuse and the seed data migration style (`ON CONFLICT DO NOTHING`);
  - the HTTP-free `AuditContext` + `AuditContextDep`, `AuditContext.NONE` for the CLI;
  - one `AuditAction` StrEnum, past tense;
  - the route-enumeration test built on `BEARER`;
  - deleting the `AuditRecorder` Protocol, and `AdminDep` on the org router;
  - `AwareDatetime`, `alias="from"`, `from > to` → 422;
  - unknown `app_key` → empty page.

  Changed:
  - the `actor` text filter becomes `actor_kind` (an enum, which covers "all CLI writes");
  - `org_id` filter dropped (not in AC4; it is a follow-up);
  - `action` filter is enum-validated instead of a regex;
  - request-id regex adopted as `^[A-Za-z0-9._-]{1,64}$`.
- **security-architect.** Adopted:
  - allowlisted diffs (no hashes, tokens, cookies or emails);
  - the trigger, with the DB-role split as a deploy follow-up;
  - `admin:audit:read` held by super-admins only;
  - reading the log is not audited;
  - CLI writes as `system:cli` in the same transaction;
  - the cleanup summary row;
  - the split (identity events, failure path and retention to ADM-004b).

  Rejected:
  - the `getpass` OS user in CLI rows: it is always the container user, and the ops workflow
    run is the operator trail;
  - accepting a UUID-only request id, in favour of the wider safe charset (UUIDs still pass);
  - IP truncation (full IP, owner default);
  - 400-day retention (indefinite in v1).

  Deferred to ADM-004b:
  - **denied privileged attempts** (`outcome=denied`): they need the same failure-path writer
    as failed logins, so both ship together with one mechanism;
  - `email_hmac` for unknown emails: ADM-004b decides; the recommendation is no HMAC.

## Assumptions
Owner defaults, recorded rather than blocking:
- `admin:audit:read` is granted only through `admin:*` (super-admin) for now. A dedicated
  auditor role can be added later as data.
- Retention is indefinite in v1. A purge comes in ADM-004b or later (spec Q6 stays open).
- The full client IP is stored, and is visible only to `admin:audit:read` holders.
- Reading the audit log is not itself audited.
- Splitting the runtime DB role (INSERT/SELECT only on `audit_log`) from the migration owner is
  a deployment follow-up in `mmlabs-online/deployment`. Until then the trigger raises the bar
  but does not stop the owner role from running DDL.

Other assumptions:
- Users are never hard-deleted today. With no FK, rows survive if that changes.
- `occurred_at = now()` is the transaction start. Rows from one transaction share it, and `id`
  breaks the tie. That is fine, because each operation writes one row.
- Production never downgrades past this revision: a downgrade destroys the audit log.
  `deploy.md` states this.
- No CORS `Expose-Headers` for `X-Request-ID`: the admin routes are not under the CORS prefix.
  This is a follow-up if the admin UI needs to read the header.
- Size: about **820 production lines** (see the split proposal), mostly mechanical call-site
  wiring. It is above the ~400-line guide, but the orchestrator prescribed the split to keep
  the coverage test whole (every mutating route at once).

## Blocking questions
None.

## Split proposal
- **ADM-004 (this plan).** It contains:
  - the `audit_log` table, triggers and seed;
  - `AuditLogger` + `AuditContext` + the request-id middleware;
  - every admin mutation (organizations, user status, apps/permissions/roles/role-permissions,
    assignments) and every CLI write (register-app, set-admin-origins, grant-super-admin,
    cleanup summary);
  - `app.join`, replacing the interim recorder;
  - `GET /api/v1/audit-logs` + `admin:audit:read`;
  - the enforcement test.

  Business failures **and denied attempts** write nothing in this slice.
- **ADM-004b (proposed ticket; the orchestrator creates it).** It contains:
  - Identity events: `auth.login_succeeded`, `auth.login_failed` (credential-checked failures
    only; never 429, Turnstile-required or Turnstile-failed), `auth.logout`, `auth.logout_all`,
    `device_session.reuse_revoked`, `auth.password_reset_completed`, `auth.email_verified`.
  - Denied privileged attempts: 403 `admin_wildcard_required` / `self_assignment_forbidden` /
    `self_status_change_forbidden` from the ADM-002b guards, and 409 `last_super_admin`.
    Plain `require_permission` 403s are excluded.
  - An additive migration: `outcome text NOT NULL DEFAULT 'success'`,
    `CHECK IN ('success','failure','denied')`, and an `outcome` filter.
  - **One failure-path write mechanism** (decided now): `AuditLogger.record_failure(...)` on the
    **request's own session**. It runs `rollback()`, which discards any partial work of the
    failed action; then `add(row)` and `commit()`. Then the caller raises the intended error.
    The whole sequence is wrapped in `try/except Exception`, which logs
    `audit_write_failed action=<a>` with no PII and swallows the error. So the intended
    401/403/409 is always returned and never becomes a 500.
    - It works with the test harness: on the `create_savepoint` session, rollback and commit
      act on the savepoint, like the existing `failures.commit()` / `commit_on_reuse`. Tests
      use `make_strict_client` and commit their setup first.
    - There is no separate engine or `SessionLocal()`.
    - Where the code already commits before raising (login failure reservation, reuse
      revocation), the row is added before that existing commit instead.
  - For an unknown email: `actor_kind=anonymous`, no email and no HMAC. For a known user:
    target `user`.
  - Retention purge (period per owner, spec Q6) through a trigger bypass (`SET LOCAL
    audit.purge = 'on'` checked in `audit_log_immutable()`, CLI only) or monthly partitions,
    decided in ADM-004b.
- **Later follow-ups (not tickets yet):**
  - an `org_id` filter on `GET /audit-logs`;
  - additive keyset pagination;
  - `Expose-Headers: X-Request-ID` if the admin UI needs it;
  - the runtime/migration DB-role split (deployment repo);
  - auditing reads of the log.

**Estimated production lines (ADM-004):**

| Part | Lines |
|---|---|
| Migration | ~110 |
| `audit/` module (models 55, actions 40, context 25, deps 25, diff 50, repository 60, service 80, schemas 30, router 50) | ~415 |
| `core/request_id.py` | ~35 |
| `main.py` / `db/models.py` | ~6 |
| `rbac/service.py` wiring and private splits | ~140 |
| rbac routers | ~25 |
| organizations | ~35 |
| users | ~12 |
| join + auth router | ~0 net (+10 / −10) |
| retention | ~10 |
| Deleted `recorder.py` | −25 |
| **Total** | **≈ 820** |

Tests add about 650 lines, docs about 120.

## Data model / migration
Revision `c3f1a9d27b60`, `down_revision = "5e2a9c7d1f04"`, file
`20261115_c3f1a9d27b60_create_audit_log.py`.

Table `audit_log`. The model uses `UUIDPkMixin` and `Base` with no `TimestampMixin`:

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | uuid PK (`pk_audit_log`) | no | client `uuid4` |
| `occurred_at` | timestamptz | no | `now()` |
| `actor_kind` | text | no | — |
| `actor_user_id` | uuid (**no FK**) | yes | — |
| `actor_label` | text | yes | — |
| `action` | text | no | — |
| `target_type` | text | yes | — |
| `target_id` | uuid (no FK) | yes | — |
| `app_key` | text (snapshot, no FK) | yes | — |
| `org_id` | uuid (no FK) | yes | — |
| `changes` | jsonb | yes | — |
| `ip` | inet | yes | — |
| `user_agent` | varchar(512) | yes | — |
| `request_id` | text | yes | — |

CHECKs. Names follow `ck_audit_log_<name>`, and the migration comment notes that the texts must
match the model:
- `actor_kind_valid`: `actor_kind IN ('user','system','anonymous')`
- `actor_consistent`: `(actor_kind = 'user') = (actor_user_id IS NOT NULL)` AND
  `(actor_kind <> 'system' OR actor_label IS NOT NULL)`
- `actor_label_length`: `actor_label IS NULL OR length(actor_label) <= 64`
- `action_format`: `action ~ '^[a-z_]+(\.[a-z_]+)+$' AND length(action) <= 64`
- `target_pair`: `(target_type IS NULL) = (target_id IS NULL)`
- `app_key_format`: `app_key IS NULL OR app_key ~ '<APP_KEY_RE>'`
- `changes_object`: `changes IS NULL OR jsonb_typeof(changes) = 'object'`
- `request_id_format`: `request_id IS NULL OR request_id ~ '^[A-Za-z0-9._-]{1,64}$'`

Indexes. All are ascending; a backward scan serves `ORDER BY occurred_at DESC, id DESC`. The
model declares them too, with `postgresql_where`:
- `ix_audit_log_occurred_at_id (occurred_at, id)`
- `ix_audit_log_actor_user_id (actor_user_id, occurred_at, id) WHERE actor_user_id IS NOT NULL`
- `ix_audit_log_action (action, occurred_at, id)`
- `ix_audit_log_target (target_type, target_id, occurred_at, id) WHERE target_id IS NOT NULL`
- `ix_audit_log_app_key (app_key, occurred_at, id) WHERE app_key IS NOT NULL`
- No index on `actor_kind` (low cardinality; it combines with the time index), on `org_id`, or
  GIN on `changes`.

Append-only DDL, written by hand with `op.execute`:
```sql
CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
```

Seed: the `INSERT INTO permissions` statement from `5e2a9c7d1f04`, for
`('admin:audit:read', 'Read the audit log')` on app `00000000-0000-4000-8000-0000000000a1`,
with `is_admin=true` and `ON CONFLICT (app_id, key) DO NOTHING`. It is granted to no role.

**Downgrade**, in this order:
1. `DELETE` the seed permission.
2. `DROP TRIGGER audit_log_no_truncate`, then `audit_log_no_update_delete`.
3. `DROP FUNCTION audit_log_immutable()`.
4. Drop the indexes, then the table.

The table drop destroys the log; `deploy.md` warns about this.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/audit-logs` | query, all optional and combined with AND: `actor_user_id` uuid, `actor_kind` `user\|system\|anonymous`, `action` (`AuditAction`), `target_type` (`AuditTarget`), `target_id` uuid, `app_key` (`APP_KEY_RE`), `from` (aware datetime, inclusive), `to` (aware datetime, exclusive), `limit` 1–100 (default 20), `offset` ≥ 0 | `Page[AuditLogRead]`: `{id, occurred_at, actor_kind, actor_user_id, actor_label, action, target_type, target_id, app_key, org_id, changes, ip, user_agent, request_id}`, ordered `occurred_at DESC, id DESC` | 200; 401 `unauthorized`; 403 `permission_denied`; 422 `validation_error` (types, naive datetime, unknown enum, limit) / `unprocessable` (`from > to`) | `admin:audit:read` (Bearer `aud=admin`) |
| * | every response | optional `X-Request-ID` (`^[A-Za-z0-9._-]{1,64}$`) | `X-Request-ID` echoed (inbound if valid, otherwise a generated UUID4) | unchanged | — (additive) |

The existing mutating admin routes (16) and `POST /api/v1/auth/apps/{key}/join` keep their
contracts: same request/response/codes, plus the `X-Request-ID` header and an audit row on
success.
