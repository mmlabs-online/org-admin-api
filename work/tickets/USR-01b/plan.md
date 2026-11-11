Status: READY

# Plan — USR-01b: Staff user management endpoints (list, get, enable/disable)

## Spec summary
- spec §5.8 Admin: "**Admin:** staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4)." This ticket covers **list, look up, disable/enable** only (roles → USR-02, session revoke → USR-03/03b, audit → ADM-004).
- spec §5.8 Users: "email (unique, case-insensitive), password, status (`active` / `disabled`), email verified flag, timestamps. Staff are users too; what they may do in the admin API is RBAC (§5.3)."
- spec §5.3: "Every admin `/api/v1` endpoint declares the `admin:*` permission it needs."
- identity-architecture.md §4: "Admin: `/api/v1/users`, … | Bearer, `aud=admin` + `admin:*` | staff management | USR-01/02, ADM-002".
- spec §6.1: "Accepted risk: until ADM-002 lands, anyone who can reach the hostname can call the admin endpoints." — accepted on 2026-11-10, before `users` held PII or a disable action existed (see A1).
- Origin: USR-01 plan "Split proposal", slice 2.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/users?email=&status=&limit=&offset=` → `Page[UserRead]`; `email` = case-insensitive contains with LIKE escaping; order `created_at desc, id` | `tests/test_users_admin.py`: `test_list_orders_by_created_at_desc_then_id`, `test_list_paginates_and_total_respects_filters`, `test_list_email_filter_is_case_insensitive_contains`, `test_list_email_filter_escapes_like_wildcards`, `test_list_status_filter`, `test_list_rejects_bad_query` |
| 2 | `GET /api/v1/users/{id}` → `UserRead` or 404; `PATCH /api/v1/users/{id} {status: active\|disabled}` (extra fields / explicit null → 422) | `test_get_user`, `test_get_missing_user_returns_404`, `test_get_bad_uuid_returns_422`, `test_patch_disables_and_reenables`, `test_patch_same_status_is_noop`, `test_patch_missing_user_returns_404`, `test_patch_rejects_invalid_bodies` (parametrized) |
| 3 | `UserRead` exact keys `{id, email, status, email_verified_at, display_name, created_at, updated_at}`, never `password_hash` | `test_user_read_has_exact_keys` (list item, get, PATCH bodies); also `"password_hash" not in resp.text` |
| 4 | Permissions declared per route: `admin:users:read` (list/get), `admin:users:write` (PATCH) (R3) | `test_users_routes_declare_permissions` enumerates `app.routes` and checks `route.openapi_extra == {"x-permission": …}` per (path, method) |
| 5 | Exposure before ADM-002: router mounted only when `ADMIN_USERS_API_ENABLED=true` (default false); refused in staging/production | `test_users_api_not_mounted_by_default` (404 on list/get/PATCH with default settings), `test_users_api_flag_refused_in_staging_and_production` (parametrized `Settings(...)` raises `ValidationError`), `test_users_api_flag_allowed_in_local_and_test`; `.env.example` contains `ADMIN_USERS_API_ENABLED=` |
| 6 | Tests: filters, pagination, 404, status change, 422 cases, key set | All of the above in `tests/test_users_admin.py` + `make check` green (R8). 401/403 tests are not possible until ADM-002 (no auth exists) |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/config.py` | Add `admin_users_api_enabled: bool = False` and a `model_validator` `_admin_users_api_not_public` | AC 5, R5 |
| `.env.example` | Add `ADMIN_USERS_API_ENABLED=` (no value) | R5 |
| `app/core/permissions.py` (new) | `permission(key: str) -> dict[str, Any]` returning `{"x-permission": key}` | AC 4 — one declaration mechanism ADM-002 can convert to `require_permission` |
| `app/modules/users/repository.py` | Add `get(user_id)`, `list(*, email, status, limit, offset)`, module-level `escape_like(term)` | AC 1, 2 (R1: SQLAlchemy only here) |
| `app/modules/users/schemas.py` (new) | `UserRead`, `UserStatusUpdate` | AC 2, 3 |
| `app/modules/users/service.py` (new) | `UserService.get/list/set_status` | AC 1, 2 (R1: business rules, `NotFoundError`) |
| `app/modules/users/router.py` (new) | `APIRouter(prefix="/users", tags=["users"])` with 3 routes | AC 1, 2, 4 |
| `app/main.py` | `if settings.admin_users_api_enabled: app.include_router(users_router, prefix=settings.api_prefix)` | AC 5. Do **not** add it to the module-level `api_router` in `app/api/router.py` (that file is unchanged) |
| `tests/test_users_admin.py` (new) | API tests + settings + route-permission tests | AC 6, R8 |

No other files. No migration, no ADR (the flag is temporary; ADM-002 removes it).

## Tests to write
All in `tests/test_users_admin.py`. Fixture `users_client` = `make_client(admin_users_api_enabled=True)`. Helper `_user(db_session, email, *, status="active", created_at=None, updated_at=None)` inserts a `User(password_hash="h", …)` directly via `db_session` + `flush()` (explicit timestamps where order/`updated_at` matter — `now()` is constant inside the per-test transaction, see A7).

| Test (file › name) | Covers |
|---|---|
| `test_users_api_not_mounted_by_default` | Default `client`: `GET /api/v1/users`, `GET /api/v1/users/{uuid}`, `PATCH …` → 404 (AC 5) |
| `test_users_api_flag_refused_in_staging_and_production` | parametrize `app_env` ∈ {staging, production}: `Settings(app_env=…, admin_users_api_enabled=True)` raises `pydantic.ValidationError` matching "ADMIN_USERS_API_ENABLED" (AC 5) |
| `test_users_api_flag_allowed_in_local_and_test` | `Settings(app_env="local"/"test", admin_users_api_enabled=True)` constructs (AC 5) |
| `test_list_orders_by_created_at_desc_then_id` | 3 users with distinct `created_at` + 2 with the same `created_at` → order by `created_at` desc, ties by `id` asc (AC 1) |
| `test_list_paginates_and_total_respects_filters` | 3 users, `limit=2&offset=0` / `offset=2`; `total` = 3; with `status=disabled` filter `total` counts only matches; `limit`/`offset` echoed (AC 1, R9) |
| `test_list_email_filter_is_case_insensitive_contains` | `alice@example.com`, `bob@other.io`; `email=EXAMPLE` → only alice; `email=o` → both (AC 1) |
| `test_list_email_filter_escapes_like_wildcards` | users `a%b@x.io`, `a_b@x.io`, `a\b@x.io`, `axb@x.io`: `email=%` → only `a%b`; `email=_` → only `a_b`; `email=\` → only `a\b`; `email=a_b` → only `a_b` (not `axb`) (AC 1) |
| `test_list_status_filter` | `status=disabled` / `status=active` return only matching users (AC 1) |
| `test_list_rejects_bad_query` | parametrize: `status=banned`, `email=` (empty), `email=` 255 chars, `limit=0`, `limit=101`, `offset=-1` → 422 `validation_error` (AC 1, A3) |
| `test_get_user` | 200, body matches the inserted row (AC 2) |
| `test_get_missing_user_returns_404` | random UUID → 404 `not_found`; message does not contain the UUID (AC 2) |
| `test_get_bad_uuid_returns_422` | `/api/v1/users/not-a-uuid` → 422 (AC 2) |
| `test_patch_disables_and_reenables` | `{"status":"disabled"}` → 200, `status=disabled`, DB row updated; then `{"status":"active"}` → 200 active (AC 2) |
| `test_patch_same_status_is_noop` | user with `updated_at=2020-01-01Z`; PATCH `{"status":"active"}` → 200 and `updated_at` still 2020-01-01; PATCH `{"status":"disabled"}` → `updated_at` changed (A5) |
| `test_patch_missing_user_returns_404` | random UUID → 404 (AC 2) |
| `test_patch_rejects_invalid_bodies` | parametrize: `{}`, `{"status": null}`, `{"status": "banned"}`, `{"status":"active","email":"x@y.z"}`, `{"status":"active","password_hash":"x"}` → 422 `validation_error`; row unchanged (AC 2) |
| `test_user_read_has_exact_keys` | `set(body) == {"id","email","status","email_verified_at","display_name","created_at","updated_at"}` for a list item, GET and PATCH responses; `"password_hash"` and the hash value not in `resp.text` (AC 3) |
| `test_users_routes_declare_permissions` | build `create_app(Settings(admin_users_api_enabled=True))`; for every `APIRoute` whose path starts with `/api/v1/users`: expected `{("GET","/api/v1/users"): "admin:users:read", ("GET","/api/v1/users/{user_id}"): "admin:users:read", ("PATCH","/api/v1/users/{user_id}"): "admin:users:write"}`; assert the found set equals the expected keys exactly (AC 4) |

## Approach
1. **Config (R5).** In `Settings`: `admin_users_api_enabled: bool = False` with a comment "Temporary: exposes user PII and a disable action without auth. Removed by ADM-002." Add `@model_validator(mode="after") _admin_users_api_not_public`: if `self.admin_users_api_enabled and self.app_env in {"staging", "production"}` → `raise ValueError("ADMIN_USERS_API_ENABLED must not be set in staging/production before ADM-002")`. Reuses the `_argon2_floor` pattern. `.env.example`: `ADMIN_USERS_API_ENABLED=` (empty is ignored via `env_ignore_empty=True` → default false).
2. **Permission declaration (R3).** `app/core/permissions.py`: docstring "Declares the admin permission a route needs. ADM-002 turns each declaration into `require_permission(key)`." `PERMISSION_KEY = "x-permission"`; `def permission(key: str) -> dict[str, Any]: return {PERMISSION_KEY: key}`.
3. **Repository (R1).** In `app/modules/users/repository.py`:
   - `def escape_like(term: str) -> str`: replace `\` → `\\`, then `%` → `\%`, then `_` → `\_` (order matters).
   - `async def get(self, user_id: uuid.UUID) -> User | None: return await self.session.get(User, user_id)`.
   - `async def list(self, *, email: str | None, status: UserStatus | None, limit: int, offset: int) -> tuple[list[User], int]`: build `conditions` list — `User.email.ilike(f"%{escape_like(email)}%", escape="\\")` if email is not None; `User.status == status.value` if status is not None. `total = select(func.count()).select_from(User).where(*conditions)`; items `select(User).where(*conditions).order_by(User.created_at.desc(), User.id).limit(limit).offset(offset)`. Same shape as `OrganizationRepository.list`.
4. **Schemas.** `app/modules/users/schemas.py`:
   - `UserRead(BaseModel)`: `model_config = ConfigDict(from_attributes=True)`; fields exactly `id: uuid.UUID`, `email: str`, `status: UserStatus`, `email_verified_at: datetime | None`, `display_name: str | None`, `created_at: datetime`, `updated_at: datetime`. Built only with `UserRead.model_validate(user)`, never from `__dict__`.
   - `UserStatusUpdate(BaseModel)`: `model_config = ConfigDict(extra="forbid")`; `status: UserStatus` (required, not Optional). Docstring: "Unlike `OrganizationUpdate`, explicit null is rejected (422) — intentional, per USR-01b AC."
5. **Service (R1).** `app/modules/users/service.py`, `UserService(session)` with `self.repo = UserRepository(session)`:
   - `get(user_id)` → `NotFoundError("User not found")` if missing (no id in message).
   - `list(*, email, status, limit, offset)` → passthrough.
   - `set_status(user_id, status: UserStatus) -> User`: `user = await self.get(user_id)`; if `user.status == status.value: return user` (no write → `updated_at` unchanged); else `user.status = status.value`, `await self.repo.session.flush()`, `await self.repo.session.refresh(user)`, return. Comments: `# R4: audit entry in the same transaction once ADM-004 lands.` and `# USR-03/03b must reject login, revoke device sessions and refuse tokens for disabled users.` No logging added (A6).
6. **Router (R1, R3, R9).** `app/modules/users/router.py`, `router = APIRouter(prefix="/users", tags=["users"])`; module docstring: "Admin (Bearer) endpoints — R3 class 1. Unauthenticated until ADM-002; mounted only when `admin_users_api_enabled` (see `create_app`)."
   - `GET ""` `response_model=Page[UserRead]`, `openapi_extra=permission("admin:users:read")`; params `session: SessionDep, page: PageDep, email: Annotated[str | None, Query(min_length=1, max_length=254)] = None, status: Annotated[UserStatus | None, Query()] = None`. (Name the status param so it does not shadow `fastapi.status`; e.g. import nothing from `fastapi.status` in this file.)
   - `GET "/{user_id}"` `response_model=UserRead`, `permission("admin:users:read")`.
   - `PATCH "/{user_id}"` body `UserStatusUpdate`, `response_model=UserRead`, `permission("admin:users:write")`, 200.
   - No HTTP logic beyond mapping; all lookups via `UserService`.
7. **App wiring.** In `create_app`, after `app.include_router(api_router, …)`: `if settings.admin_users_api_enabled: app.include_router(users_router, prefix=settings.api_prefix)`. Import `users_router` from `app.modules.users.router`. `app/api/router.py` stays unchanged (it is a module-level singleton; mutating it per app would leak between test apps).
8. **Tests** as listed; `make check` green.

Rules: R1 (router → service → repository; service raises `NotFoundError` only), R2 (no schema change → no migration; `alembic check` unaffected), R3 (class 1 Bearer; permissions declared now, enforced by ADM-002; exposure gated by A1), R4 (no audit infra yet — marker comment, follow-up FU1), R5 (flag from env, `.env.example` key without value; no filter values or emails logged), R6 (envelope via existing handlers; 200/404/422), R7 (only the files above; extras → Follow-ups), R8 (tests above; 401/403 impossible until ADM-002 — FU1), R9 (`/api/v1/users`, `PATCH`, `Page[T]`, limit ≤ 100, UUID, timestamptz), R10 (n/a), R11 (no `password_hash` in any schema; 404/422 bodies echo no ids or values).

Size estimate: ≈ 150 lines app code + ≈ 190 lines tests ≈ 340.

## Specialist input used
**api-platform-engineer**
- Adopted: layout `app/modules/users/{schemas,service,router}.py`, `prefix="/users"` (admin, not `auth/`); extend `UserRepository`, no second repo.
- Adopted: flag in `config.py` + `.env.example`; mounted in `create_app` only when true; 404 test when off.
- Adopted: `email` Query `min_length=1`; `status: UserStatus | None` (unknown → 422); `ilike` with `\`,`%`,`_` escaping in that order; `total` with the same filters; order `created_at desc, id`.
- Modified: `email` `max_length` 254 (security consult; email-validator's limit) instead of 320.
- Adopted: `UserStatusUpdate` `extra="forbid"`, required `status` → `{}`/null/unknown/extra all 422; reference-module divergence called out in the docstring (step 4).
- Adopted: idempotent PATCH without a write; tested via a past `updated_at`.
- Adopted: `NotFoundError("User not found")` without the id; `# R4` marker, no audit code.
- Adopted: `app/core/permissions.py` `permission(key)` → `openapi_extra={"x-permission": key}` + a route-enumeration test.
- Adopted: explicit `created_at` in ordering tests (`now()` is constant per test transaction).
- Adopted (risk): USR-03 must enforce `status` → FU2. Trigram index → not needed at v1 scale (FU5).
- Open Q2 (permission naming) → decided A4. Open Q3 (min length) → `min_length=1`, A3.

**security-architect**
- Adopted: default-off flag + `model_validator` refusing it in staging **and** production (fail closed); 404-by-default test; validator test.
- Adopted: rejected alternatives recorded — "mount when `app_env != production`" (fail-open with default `local`) and deferring the ticket.
- Adopted: `UserRead` explicit fields, `from_attributes`, never `__dict__`; exact-key test.
- Adopted: escaping `\`,`%`,`_`; `max_length=254`; `min_length=1` so `email=` → 422 (not "match all").
- Modified: PATCH `status` typed as the existing `UserStatus` StrEnum instead of `Literal["active","disabled"]` — same values, one source of truth with the model and check constraint.
- Adopted: idempotent no-op; 404 does not echo the id.
- Adopted: no logging of filters/results — this slice adds no log statements at all (A6).
- Adopted: permissions declared per route and enumerable in one place.
- Adopted as follow-ups (not in this diff): ADM-002 obligations (remove flag + validator, mount unconditionally behind Bearer + `require_permission`, 401/403 tests, self-disable / last-admin guards); ADM-004 audit of status changes; USR-03/03b disabled-user enforcement; owner confirmation of §6.1; `/openapi.json` in production; audit of PII reads.

## Assumptions
- **A1 — Exposure (ticket AC 5, owner question open).** Applied the ticket's stated fallback: the users router is mounted only when `ADMIN_USERS_API_ENABLED=true` (default false), and `Settings` refuses the flag when `APP_ENV` is `staging` or `production`. This fails closed: production cannot expose user emails or the disable action before ADM-002, whatever the owner later answers; if the owner wants them live earlier, that is a one-line validator change in a later ticket. Not blocking. Treated as: user PII and the disable action are **outside** §6.1's 2026-11-10 accepted risk until the owner says otherwise (FU4).
- **A2 — Residual risk.** A `local`/`test` instance with the flag on and exposed through a tunnel would still be open; out of scope (security consult "Risks").
- **A3 — Email filter.** `min_length=1`, `max_length=254`; `email=` (empty) → 422, not treated as absent; no trimming; no minimum of 3 (an unfiltered list already returns everyone, so a higher minimum protects nothing).
- **A4 — Permission names.** `admin:users:read` and `admin:users:write`, as in the ticket AC and ADM-002's catalog. ADR-0001's `admin:users:disable` is labelled "e.g." and is not a binding catalog; no ADR change here (ADM-002 may note it — FU1).
- **A5 — Idempotent PATCH.** Same status → 200 with the unchanged row and no UPDATE (`updated_at` unchanged). Changes always return 200 `UserRead`.
- **A6 — Logging.** No log statements are added (none exist in the reference module either). Nothing about filters, emails or result sets is logged.
- **A7 — Test timestamps.** Postgres `now()` is the transaction start, and tests run in one rolled-back transaction, so tests that depend on order or `updated_at` set timestamps explicitly when inserting users.
- **A8 — Declaration mechanism.** `openapi_extra={"x-permission": …}` makes the permission visible in OpenAPI (only when mounted) and machine-checkable. ADM-002 decides whether to keep it alongside `require_permission`.
- **A9 — Disable has no effect yet.** Nothing reads `status` until USR-03; this ticket only stores it (FU2).

## Blocking questions
None.

## Split proposal
None (≈ 340 lines, one spec feature, no migration).

## Data model / migration
None. Uses the existing `users` table (USR-01). No new index: `ILIKE '%term%'` cannot use `uq_users_email` and does a sequential scan, acceptable at v1 user counts (FU5).

## API contract
All mounted under `/api/v1` only when `admin_users_api_enabled` is true; otherwise 404. Auth class 1 (Bearer, `aud=admin`) — **not enforced until ADM-002**.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users` | Query: `email` (str, 1–254, case-insensitive contains, `%`/`_`/`\` literal), `status` (`active`\|`disabled`), `limit` (1–100, default 20), `offset` (≥ 0, default 0). Order `created_at desc, id asc` | 200 `Page[UserRead]` `{items, total, limit, offset}`; `total` counts filtered rows | 200, 422 | `admin:users:read` |
| GET | `/api/v1/users/{user_id}` | Path `user_id: UUID` | 200 `UserRead` | 200, 404 `not_found`, 422 | `admin:users:read` |
| PATCH | `/api/v1/users/{user_id}` | Body `{"status": "active" \| "disabled"}`; required; `extra="forbid"`; `null` → 422 | 200 `UserRead` (unchanged row and no write if status is the same) | 200, 404, 422 | `admin:users:write` |

`UserRead` = `{id: uuid, email: str, status: "active"|"disabled", email_verified_at: datetime|null, display_name: str|null, created_at: datetime, updated_at: datetime}` — exactly these keys.

## Follow-ups (not in this diff)
- **FU1 — ADM-002:** remove `admin_users_api_enabled` and its validator; mount the users router unconditionally behind Bearer `aud=admin` + `require_permission` (from the `x-permission` declarations); add 401/403 tests (R8); guard against a staff user disabling themselves or the last `admin:*` holder; optionally reconcile ADR-0001's `admin:users:disable` example.
- **FU2 — USR-03/USR-03b:** reject login for `disabled` users, revoke their device/account sessions on disable, refuse token issuance.
- **FU3 — ADM-004:** audit every user status change in the same transaction (R4); decide whether staff reads of user PII (list/get) are audited too.
- **FU4 — Owner:** confirm user PII and the disable action are excluded from the §6.1 accepted risk until ADM-002, and update the spec note; confirm whether a public staging exists.
- **FU5 — Later:** `pg_trgm` index on `users.email` if the contains-search gets slow.
- **FU6 — Later:** disable `/openapi.json` in production (currently public).
