# consult-api-platform-engineer — ADM-004

**Findings**
- `Page[T]` / `page_params` (app/core/pagination.py) already enforce `limit` 1..100 (default 20) and `offset ≥ 0`, so it can be reused as is.
- `require_permission` checks keys against `admin:<res>:<action>`. `admin:audit:read` passes. The seed migration `20261115_5e2a9c7d1f04` only covers the 8 existing keys, so the new permission needs its own data migration in the same style: app `admin` (`00000000-…a1`), `is_admin=true`, `ON CONFLICT DO NOTHING`, and a `downgrade()` that deletes it.
- `Actor = AdminPrincipal | SystemActor` (rbac/actor.py) is HTTP-free. `actor_user_id()` returns None for `system:cli` and `system:join`, so the system actor's name would be lost unless it is stored.
- The interim recorder (audit/recorder.py) is a `Protocol` with `actor_user_id: uuid.UUID` and `app_id` both required. That cannot express anonymous events (failed login), system actors or org-only events. Only `JoinService` uses it, injected through `get_audit_recorder` in auth/router.py:66-71. About 20 `# R4 … ADM-004` markers sit in rbac/service.py and users/service.py.
- The organizations router never takes `AdminDep`, so it has no actor yet. Users and rbac routers already pass `actor=principal`.
- There is no request-id middleware and nothing reads `X-Request-Id`. `client_ip(request, trusted_proxy_cidrs)` exists (CF-Connecting-IP only from trusted proxies).
- Transaction boundary: `get_session` commits on success and rolls back on any exception. Login failure commits the failure counter itself (`auth/service.py:159`) before raising. Device-session reuse revocation commits itself (`sessions/service.py:92`) before raising `SessionInvalidError`. Any audit row written in the request transaction for these "failure" events is rolled back when the error propagates.
- `tests/test_route_auth_classes.py` already lists every route and has a `BEARER` map of (method, path) → permission. It is the natural base for an audit-coverage test.

**Recommendations**
1. **Contract** `GET /api/v1/audit-logs`, `**require_permission("admin:audit:read")`, `Page[AuditLogRead]`. Query params, all optional and combined with AND:
   - `actor_id: UUID`
   - `actor: str` (system actor name such as `system:cli`; optional)
   - `action: str` (exact match, pattern `^[a-z_]+(\.[a-z_]+)+$`, max 64)
   - `target_type: str` (enum of known types)
   - `target_id: UUID`
   - `app_key: str` (app key pattern; an unknown key returns an empty page, not 404, because the log outlives the app)
   - `org_id: UUID`
   - `from` / `to`: aware datetimes. Use `alias="from"`; reject naive datetimes with 422; `from` is inclusive, `to` is exclusive; `from > to` → 422 `validation_error`.
   - `limit`, `offset`.
   - Sort `occurred_at DESC, id DESC` (stable for offset paging), backed by an index on `(occurred_at DESC, id)` plus filter indexes; that is DB-optimizer territory.
2. **Item schema**: `id`, `occurred_at` (timestamptz), `action`, `actor_user_id` (nullable), `actor` (`"user"` or the system name `"system:cli"` / `"system:join"`; `"anonymous"` for failed login), `target_type`, `target_id` (nullable), `app_key` (nullable, stored as a denormalized text snapshot so deleted apps stay readable), `org_id`, `changes` (`dict[str, {"from": …, "to": …}]`, allow-listed fields only), `ip`, `user_agent` (truncate to 512), `request_id`. Add no GET-by-id now; it can be added later without breaking anything.
3. **R1 context.** Add a frozen dataclass in `app/modules/audit/context.py`: `AuditContext(ip: str | None, user_agent: str | None, request_id: str | None)` with no FastAPI import. Build it in a dependency, `AuditContextDep = Annotated[AuditContext, Depends(audit_context)]`, using `client_ip` and `settings.trusted_proxy_cidrs`. Routers pass `ctx=` next to `actor=`; services hand both to `AuditLogger(session).record(action, actor=…, ctx=…, target=…, app_key=…, org_id=…, changes=…)`. For the CLI, use `AuditContext.system()` (all None), with `SYSTEM_CLI` as actor and the name stored in the `actor` column.
4. **Request id.** Add a small pure-ASGI middleware that accepts an inbound `X-Request-Id` only if it matches `^[A-Za-z0-9._-]{1,64}$` and otherwise generates a UUID4. It echoes the header and puts the value in `request.state`. This is a new contract surface, so it must be additive.
5. **Action names**: `<resource>.<verb>`, lowercase, past tense, from one `AuditAction` StrEnum. Examples: `organization.created|updated|deleted`, `user.disabled|enabled`, `app.created|updated|deleted`, `permission.*`, `role.created|updated|deleted|permissions_replaced`, `role_assignment.granted|revoked`, `app.joined`, `auth.login_succeeded|login_failed`, `auth.logout`, `auth.logout_all`, `device_session.revoked_on_reuse`. Use the same names for the `action` filter enum and for the docs.
6. **Enforceability test.** Use a fixture that overrides the recorder dependency with a spy, or count rows in `audit_log`. Add `AUDITED: dict[(method, path), action]` in the test and assert `AUDITED.keys() == {r ∈ BEARER if method ≠ GET}`, so a new mutating route fails until it is mapped. Parametrize over it: the happy-path call must produce exactly one row with that action, and a 404/409 call must produce none. Do the same for the COOKIE/PUBLIC identity POSTs (login, logout, join).
7. **Failure-path events** (login_failed, reuse revocation) must commit alongside the code's existing commits (`failures.commit()`, `repo.commit()`) before the error is raised. Assert this in tests.
8. Replace the `AuditRecorder` Protocol rather than widening it, and keep `LogAuditRecorder` deleted (R7: wire, don't duplicate).

**Risks**
- `changes` leaking secrets or PII (R11). Use per-resource allow-lists, never dump models, never store an email for an unknown account, and record user-agent and IP only. Tests must assert that no `password`, `token` or `@` appears for failed logins.
- Login-failure rows give unauthenticated users a write amplification path. They are already rate-limited, but the table size needs a retention decision (spec Q6).
- Offset paging over a growing append-only table drifts. This is acceptable in v1; keyset paging can be added later as an additive param.
- `from` is a Python keyword, so the alias handling must show correctly in OpenAPI.
- Append-only is only "no code path". A DB-level `REVOKE UPDATE, DELETE` or a trigger is stronger; that is for the security-architect to weigh.
- Org endpoints gaining `AdminDep` is an internal change only, so no break.

**Open questions**
1. Should `app_key` be stored as a snapshot string or as an FK `app_id`? I recommend the snapshot, because app deletion must not cascade into the log or block it.
2. Is a `request_id` echo header (`X-Request-Id`) in scope for ADM-004, or should it be a follow-up?
3. Retention period and whether IP counts as PII (spec Q6), which is the owner's call.
4. Should failed-login rows be visible to every `admin:audit:read` holder, or need a separate permission?
