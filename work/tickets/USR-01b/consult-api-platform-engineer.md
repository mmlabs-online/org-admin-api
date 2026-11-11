# consult-api-platform-engineer — USR-01b

**Findings**
- USR-01 already merged `app/modules/users/{models,repository}.py` (`User`, `UserStatus` StrEnum, `UserRepository.add` only). Auth module has signup only: `auth/schemas.py` (`SignupRequest` with `extra="forbid"`, `SignupResponse`) and `auth/router.py` (prefix `/auth`). `app/core/pagination.py` has `PageParams`/`page_params` (`limit` 1–100, default 20) and `Page[T]`; `app/api/deps.py` has `SessionDep`/`PageDep`. `core/errors.py` has `NotFoundError` and a 422 handler that strips `input`/`ctx`. No permission declaration exists yet (no `require_permission`, no `openapi_extra` anywhere).
- Reference-module divergence: `OrganizationUpdate` treats explicit `null` as "no change"; the USR-01b AC requires `null` → 422. That is intentional — the plan should call it out so the reviewer doesn't flag it as a departure from the reference module.
- Permission naming conflict: ADR-0001 §3 uses `admin:users:disable` as an example; ADM-002's ticket and the USR-01b AC use `admin:users:read|write`; the USR-01 architect consult said `admin:users:update`. ADM-002's catalog should win (ADR-0001's string is only an example).
- `TimestampMixin` uses `server_default=func.now()`. In Postgres `now()` is the transaction start time, and tests roll back one transaction per test, so every user inserted in a test gets the same `created_at` → ordering falls back to `id` (random UUID). Ordering tests must set `created_at` explicitly.
- `create_app(settings)` and the `make_client(**settings_overrides)` fixture already exist, so a router gated by a settings flag is easy to test.

**Recommendations**
1. **Layout (R1):** `app/modules/users/{schemas,service,router}.py` with `APIRouter(prefix="/users", tags=["users"])` — these are admin (Bearer) endpoints, not cookie/auth, so they don't belong in `auth/`. Extend `UserRepository` with `get(id)` and `list(*, email, status, limit, offset)` and reuse its session; no second repository. Include the router in `app/api/router.py` only when the flag is on (next point).
2. **Exposure (gating AC):** `admin_users_api_enabled: bool = False` in `config.py`, `ADMIN_USERS_API_ENABLED=` in `.env.example` (R5). `create_app` includes the users router under `api_prefix` only when true (making `api_router` a function is fine). Tests: flag off → 404 on `/api/v1/users`; flag on via `make_client(admin_users_api_enabled=True)` for the full suite.
3. **List (list AC, R9):** query `email: str | None = Query(None, min_length=1, max_length=320)`, `status: UserStatus | None` (unknown value → 422 automatically), `PageDep`. Filter `User.email.ilike(f"%{escaped}%", escape="\\")`, escaping `\`, `%`, `_` in that order (`ilike` on citext is fine). One helper, tested with `%`, `_`, `\` literally. Order `created_at.desc(), id`. `total` must apply the same filters. Return `Page[UserRead]`.
4. **Get (get AC):** `GET /users/{user_id}` with `user_id: uuid.UUID` (bad UUID → 422). Missing → `NotFoundError("User not found")`; don't echo the id into a log.
5. **PATCH (PATCH AC, R6/R9):** `UserStatusUpdate(BaseModel)` with `model_config = ConfigDict(extra="forbid")` and `status: UserStatus` required, not Optional → `{}`, `{"status": null}`, `{"status": "banned"}` and extra keys all 422. Return 200 `UserRead`. Idempotent: if the status is unchanged, return the row without writing, so `updated_at` stays put (test it). Unknown id → 404. Leave a `# R4: audit (ADM-004)` marker in the service, no audit code.
6. **UserRead (key-set AC):** `from_attributes=True`, exactly `id, email, status: UserStatus, email_verified_at: datetime | None, display_name: str | None, created_at, updated_at`. One test asserts `set(body) == {...}` on list items, get and PATCH.
7. **Permissions (permissions AC, R3):** a tiny `app/core/permissions.py` with `permission(key: str) -> dict` returning `{"x-permission": key}`, used as `openapi_extra=permission("admin:users:read")` on each route; ADM-002 can mechanically turn it into `dependencies=[require_permission(key)]`. A test enumerates `app.routes` and asserts every `/api/v1/users*` route carries a key, with the expected value per method; ADM-002 can extend it to all routes.

**Risks**
- Disable has no effect yet (no sessions/login until USR-03). The plan must state that USR-03 must check `status`, or the follow-up gets lost.
- Once enabled, the flag exposes emails and a disable action without auth. A production guard is prudent: fail at startup if `app_env=production` and the flag is on (pre-ADM-002) — security should confirm.
- `%term%` on citext can't use the unique index → sequential scan. Fine at v1 scale; trigram index as a follow-up.

**Open questions**
1. Owner: may these endpoints run in production before ADM-002? (Default: flag off.)
2. Confirm `admin:users:write` and drop ADR-0001's `admin:users:disable` example — or amend the ADR in ADM-002?
3. Should `email` have a minimum filter length (e.g. 3) so staff can't list everyone with a one-character match? An unfiltered list already does that, so I'd suggest 1.
