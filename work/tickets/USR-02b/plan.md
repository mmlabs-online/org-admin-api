Status: READY

# Plan — USR-02b (slice 02b-1): RBAC flag, APP_ENV rule, `UnprocessableError`, USR-02 carry-overs, apps CRUD + origins

> USR-02b is split (see **Split proposal**). This plan covers **slice 02b-1 only**: the `ADMIN_RBAC_API_ENABLED` exposure gate and the explicit-`APP_ENV` startup rule, `UnprocessableError`, the three 🟡 carry-overs from the USR-02 security review, and the staff **apps** endpoints (list/get/create/patch/delete) with origins normalization and the `admin`-app escalation guards. Permissions CRUD, roles CRUD, `PUT …/roles/{role_id}/permissions` (02b-2) and the role catalog with ETag (02b-3) have contracts in the Split proposal.

## Spec summary
- spec §5.3: "One model for every mmlabs app, the admin API included (app `admin`). Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles … Every admin `/api/v1` endpoint declares the `admin:*` permission it needs."
- spec §5.3 Membership: "each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin`, an `org_mode` (`none` | `required`; calechip = `required`) and its allowed browser `origins`. … Policy and role contents are data."
- spec §5.5: "The list of mmlabs products is the `apps` table of §5.3 (key, name, status)."
- identity-architecture.md §4: "Admin: `/api/v1/users`, `/api/v1/apps`, roles, assignments … | Bearer, `aud=admin` + `admin:*` | staff management | USR-01/02, ADM-002"; S4 (D12): "`Access-Control-Allow-Origin` echoes only origins in `apps.origins`, with `Allow-Credentials: true`."
- ADR-0003 §8: "The `admin` app has no member role. It is `closed`; `default_role_id` stays NULL and roles come only from grants." §3: "`mmlabs-auth` copies [`permits`] exactly."
- Ticket ACs (this slice): AC1 (flag + APP_ENV + mount), AC2 (`UnprocessableError`, `IntegrityError` → 409), AC3 (apps part: "apps CRUD by `key` (immutable) … `build_default_role` on app create"), AC4 (apps part: "key formats … (422) … origins normalized and restricted (https; http only for localhost); `admin` app … not deletable/re-keyable (409)"), AC6 (apps routes), AC7 (apps tests), and the Notes carry-overs (1)–(3) + "map `IntegrityError` races in `build_default_role` to 409".
- CLAUDE.md R3 class 1 (Bearer + declared `admin:…` permission, "until then new endpoints note the permission they will need"), R4 (marker until ADM-004), R6, R9, R11 (CORS echoes only `apps.origins`).

## Acceptance criteria → verification
| # | Criterion (02b-1) | How the reviewer verifies it |
|---|---|---|
| 1 | New `admin_rbac_api_enabled: bool = False` (`ADMIN_RBAC_API_ENABLED=` in `.env.example`, no value), separate from `admin_users_api_enabled`; refused when `app_env` ∈ {staging, production}; when **either** admin flag is true, startup is refused unless `APP_ENV` was set explicitly (`"app_env" in model_fields_set`; empty `APP_ENV=` counts as unset); the apps router is included in `create_app` only when the flag is on (never in `app/api/router.py`); default app → 404 on every new path (ticket AC1) | `tests/test_config.py`: `test_rbac_flag_refused_in_staging_and_production`, `test_admin_flag_requires_explicit_app_env` (parametrized over both flags), `test_admin_flag_empty_app_env_refused`, `test_no_flag_without_app_env_is_ok`, `test_flags_with_explicit_app_env_ok`; `tests/test_rbac_apps_api.py › test_apps_api_not_mounted_by_default`; existing `test_users_api_flag_refused_in_staging_and_production` still green; `grep -n rbac app/api/router.py` → nothing |
| 2 | `UnprocessableError(AppError)` → 422, code `unprocessable`, `details` a list of `{"field", "reason"}` (no submitted values); `IntegrityError` on racing inserts/updates/deletes → `ConflictError` 409, and the request session stays usable (savepoint) (ticket AC2) | `tests/test_errors.py › test_unprocessable_error_envelope`; `test_rbac_apps_api.py › test_create_app_integrity_race_is_409`; `test_rbac_default_role.py › test_build_role_insert_race_is_409` |
| 3 | Carry-over 1: `permits()` raises `TypeError` when `granted` is a `str`; edge cases pinned | `tests/test_rbac_authz.py › test_permits` (extended rows), `test_permits_rejects_str_granted` |
| 4 | Carry-over 2: `effective_roles` drops role keys whose prefix ≠ `f"{app_key}:"` | `test_rbac_authz.py › test_effective_roles_drop_foreign_prefix` |
| 5 | Carry-over 3: `build_default_role("admin")` → `ConflictError` (409), nothing written; role insert race → 409; `default_role_id` set by a conditional `UPDATE … WHERE default_role_id IS NULL` (no lost update); `role_permissions` insert idempotent | `test_rbac_default_role.py › test_build_refuses_admin_app`, `test_build_role_insert_race_is_409`, `test_build_does_not_overwrite_concurrent_default` ; existing builder tests still green |
| 6 | Apps endpoints exactly as in **API contract** (by immutable `key`, `Page[AppRead]` ordered by key, 201 create runs the member-role builder in the same transaction, `extra="forbid"` on every input schema so `key`/`id`/`is_system`/unknown fields → 422) (ticket AC3, apps part) | `test_rbac_apps_api.py`: `test_list_apps_paginated_and_sorted`, `test_get_app`, `test_get_app_unknown_404`, `test_create_app_builds_member_role`, `test_create_app_duplicate_key_409`, `test_create_app_validation_422`, `test_patch_app_fields`, `test_patch_app_rejects_key_and_unknown_fields`, `test_patch_app_null_only_for_default_role`, `test_patch_unknown_app_404`, `test_delete_app`, `test_delete_unknown_app_404`, `test_app_read_has_exact_keys` |
| 7 | `default_role_id` on PATCH: unknown role or a role of another app → 422 `unprocessable`; a role holding any `is_admin` permission → 422 (a default role never grants admin) | `test_patch_default_role_unknown_or_foreign_422`, `test_patch_default_role_with_admin_permission_422`, `test_patch_default_role_set_and_clear` |
| 8 | `admin` app protections (409 `conflict`): DELETE; PATCH `join_policy` ≠ `closed`; PATCH `default_role_id` non-null; PATCH `status` ≠ `active`. Same-value PATCH of those fields is a no-op 200. An app with role assignments cannot be deleted (409) (ticket AC4, apps part) | `test_admin_app_cannot_be_deleted`, `test_admin_app_locked_fields_409` (parametrized), `test_admin_app_same_value_patch_ok`, `test_delete_app_with_assignments_409` |
| 9 | Origins normalized and restricted per **Approach §4**: stored and returned normalized; `https` any LDH host (Q3 default), `http` only for `localhost`/`127.0.0.1`/`[::1]`; rejected forms → 422 `validation_error` whose message does not echo the value; ≤ 20 entries, each ≤ 255 chars; deduplicated (ticket AC4) | `tests/test_rbac_origins.py › test_normalize_origin_accepts` and `test_normalize_origin_rejects` (parametrized, no DB), `test_normalize_origins_dedupes_and_limits`; `test_rbac_apps_api.py › test_create_app_origins_normalized`, `test_patch_app_bad_origin_422` |
| 10 | Each apps route declares its permission via `openapi_extra=permission(...)`: GET → `admin:apps:read`, POST/PATCH/DELETE → `admin:apps:write`; mutations carry `# R4` markers; service methods take an unused `actor: uuid.UUID \| None = None` (ticket AC6, apps part) | `test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (exact 5-route map); `grep -n "# R4" app/modules/rbac/service.py` shows create, update, delete, builder |
| 11 | `make check` green; no migration (R2: no schema change) | `make check`; `uv run alembic check` clean |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/config.py` | add `admin_rbac_api_enabled: bool = False`; replace `_admin_users_api_not_public` with one `_admin_apis_gate` model validator (both flags; staging/production refused, message names the flag; any flag on + `"app_env" not in self.model_fields_set` → refused, message names `APP_ENV`) | AC 1 |
| `.env.example` | add `ADMIN_RBAC_API_ENABLED=` (no value) | R5, AC 1 |
| `app/core/errors.py` | add `UnprocessableError` (422, `unprocessable`) | AC 2, R6 |
| `app/main.py` | `if settings.admin_rbac_api_enabled: app.include_router(rbac_router, prefix=settings.api_prefix)` with the "unauthenticated until ADM-002" comment | AC 1 |
| `app/modules/rbac/authz.py` | `permits` signature `AbstractSet[str] \| Sequence[str]` + `str` guard (`TypeError`); `effective_roles` prefix filter | AC 3, 4 |
| `app/modules/rbac/models.py` | add constant `ADMIN_APP_KEY = "admin"` (no column/table change) | AC 5, 8 |
| `app/modules/rbac/origins.py` (new) | pure `normalize_origin(str) -> str` and `normalize_origins(list[str]) -> list[str]` (raise `ValueError` with a generic message) | AC 9 |
| `app/modules/rbac/schemas.py` (new) | `AppCreate`, `AppUpdate`, `AppRead` | AC 6, 7, 9 |
| `app/modules/rbac/repository.py` | add `list_apps`, `add_app` (savepoint), `save` (flush in savepoint), `delete_app` (savepoint), `get_role(app_id, role_id)`, `role_has_admin_permission(role_id)`, `app_has_assignments(app_id)`, `set_default_role_if_null(app_id, role_id)`; `add_role` in a savepoint; `add_role_permissions` via `pg_insert(...).on_conflict_do_nothing()` | R1, AC 2, 5–8 |
| `app/modules/rbac/service.py` | `RoleCatalogService`: split into public `build_default_role(app_key, actor=None)` and internal `_build_for_app(app)`; refuse `admin`; `IntegrityError` → `ConflictError`; conditional default set. New `AppService` (list/get/create/update/delete) | AC 2, 5–8, 10 |
| `app/modules/rbac/router.py` (new) | `router = APIRouter(prefix="/apps", tags=["apps"])`, 5 routes, HTTP only | AC 6, 10, R1, R3 |
| `docs/adr/0003-rbac-keys-and-matching.md` | amend: §1 prefix rule enforced in the service (422) + app keys immutable via API; §3 `str` guard + edge-case table; §4 `effective_roles` also drops foreign prefixes; §7 conditional default set, races → 409; §8 builder refuses `admin`, `admin` `join_policy`/`default_role_id`/`status` locked; new §9 "a default role never holds an `is_admin` permission" and the origins normal form | planner rule 7 (identity-access consult); `mmlabs-auth` copies §3 |
| `tests/test_config.py` (new) | config gate tests | AC 1 |
| `tests/test_errors.py` | `test_unprocessable_error_envelope` | AC 2 |
| `tests/test_rbac_authz.py` | extra `permits` rows, `str` test, foreign-prefix role test | AC 3, 4 |
| `tests/test_rbac_default_role.py` | admin refusal, race, conditional default | AC 5 |
| `tests/test_rbac_origins.py` (new) | pure unit tests for origins | AC 9 |
| `tests/test_rbac_apps_api.py` (new) | endpoint tests | AC 1, 2, 6–10 |

No migration, no change to `app/api/router.py`, `app/db/models.py`, `tests/rbac_factories.py` (reuse as is; if a helper is truly needed, add it there and record why in `impl-log.md`).

## Tests to write
Notes: API tests use a `rbac_client` fixture = `make_client(admin_rbac_api_enabled=True)` (same pattern as `users_client`). Use app keys other than `admin` except in the admin-protection tests. Rows are created through `tests/rbac_factories.py`.

| Test (file › name) | Covers |
|---|---|
| `test_config.py › test_rbac_flag_refused_in_staging_and_production` | parametrized staging/production, `admin_rbac_api_enabled=True` (Argon2 floor satisfied) → `ValidationError` matching `ADMIN_RBAC_API_ENABLED` |
| `› test_admin_flag_requires_explicit_app_env` | parametrized over `admin_users_api_enabled` / `admin_rbac_api_enabled`: `monkeypatch.delenv("APP_ENV")`, `Settings(_env_file=None, <flag>=True)` → `ValidationError` matching `APP_ENV` (`_env_file=None` so a developer `.env` cannot make it pass) |
| `› test_admin_flag_empty_app_env_refused` | `monkeypatch.setenv("APP_ENV", "")` + flag → refused |
| `› test_no_flag_without_app_env_is_ok` | no `APP_ENV`, no flag, `_env_file=None` → `app_env == "local"` (unchanged default) |
| `› test_flags_with_explicit_app_env_ok` | `APP_ENV=test` (env) and `Settings(app_env="local", ...)` (kwarg) + both flags → OK |
| `test_errors.py › test_unprocessable_error_envelope` | a throwaway app route raising `UnprocessableError("x", details=[{"field": "f", "reason": "r"}])` → 422 `{"error": {"code": "unprocessable", …}}` |
| `test_rbac_authz.py › test_permits` (new rows) | `({"adm:*"}, "admin:x:y") → False`, `({"admin:*"}, "adm:x:y") → False`, `({"a:*"}, "a:") → False`, `({"a:*"}, ":x:y") → False`, `({"A:*"}, "a:x:y") → False`, `(["a:x:read"], "a:x:read") → True` (list accepted) |
| `› test_permits_rejects_str_granted` | `permits("a:x:read-all", "a:x:read")` → `TypeError` |
| `› test_effective_roles_drop_foreign_prefix` | a `stoktok` role keyed `admin:x` assigned to a user → absent from `effective_roles(user, "stoktok")` and `effective_roles(user, "admin")`; a normal `stoktok:viewer` role still returned |
| `test_rbac_default_role.py › test_build_refuses_admin_app` | `build_default_role("admin")` → `ConflictError`; no `admin:member` row; `admin.default_role_id` still NULL |
| `› test_build_role_insert_race_is_409` | create `stoktok:member` directly; `monkeypatch` `RbacRepository.get_role_by_key` → `None`; build → `ConflictError`; then a plain `SELECT` on `db_session` still works (savepoint) |
| `› test_build_does_not_overwrite_concurrent_default` | app with `default_role_id` NULL loaded; then set it to another `stoktok` role via a Core `UPDATE` (simulates a concurrent PATCH; the loaded object is stale); build → `default_role_id` after `refresh` is the other role, not member |
| `test_rbac_origins.py › test_normalize_origin_accepts` | `https://App.Example.com` → `https://app.example.com`; `https://example.com/` → `https://example.com`; `https://example.com:443` → `https://example.com`; `https://example.com:8443` kept; `http://localhost:3000`, `http://127.0.0.1:5173`, `http://[::1]:8080` kept; `http://localhost:80` → `http://localhost`; `https://localhost:3000` kept; `https://xn--bcher-kva.example` kept (already-punycode ASCII) |
| `› test_normalize_origin_rejects` | `http://example.com`, `ftp://x.io`, `https://`, `https://example.com.`, `https://example.com/path`, `https://example.com?q`, `https://example.com#f`, `https://user@example.com`, `https://*.example.com`, `null`, `*`, `https://bücher.example`, `https://10.0.0.1`, `https://[2001:db8::1]`, `https://exa mple.com`, ` https://example.com`, `https://example.com:0`, `https://example.com:65536`, `https://example.com:080`, `https://ex%61mple.com`, `https://example.com\\@evil.com`, `https://-bad.example`, `https://a..b` |
| `› test_normalize_origins_dedupes_and_limits` | `["https://A.io", "https://a.io/"]` → `["https://a.io"]`; 21 entries → `ValueError`; a 256-char entry → `ValueError`; `[]` → `[]` |
| `test_rbac_apps_api.py › test_apps_api_not_mounted_by_default` | default `client`: GET/POST `/api/v1/apps`, GET/PATCH/DELETE `/api/v1/apps/stoktok` → 404 |
| `› test_list_apps_paginated_and_sorted` | seed `admin` + `calechip` + `stoktok` → `items` keys sorted, `total`, `limit=1&offset=1` returns the second; `limit=101` → 422 |
| `› test_get_app` / `› test_get_app_unknown_404` | 200 `AppRead`; `/apps/nope` → 404 `not_found` |
| `› test_app_read_has_exact_keys` | key set = `{id,key,name,status,join_policy,org_mode,origins,default_role_id,created_at,updated_at}` on list, get, create, patch |
| `› test_create_app_builds_member_role` | POST `{key:"stoktok", name:"Stoktok"}` → 201; defaults `join_policy=open`, `org_mode=none`, `status=active`, `origins=[]`; `default_role_id` = id of `stoktok:member` (`is_system`, no permissions) |
| `› test_create_app_duplicate_key_409` | POST twice → 409 `conflict`; POST `key:"admin"` → 409 |
| `› test_create_app_integrity_race_is_409` | `monkeypatch` `RbacRepository.get_app_by_key` → `None` for the pre-check; POST an existing key → 409 (not 500) |
| `› test_create_app_validation_422` | parametrized → 422 `validation_error`: key `Bad`, `a`, `st:ok`, `1app`, 33 chars; missing `name`; `name: ""`; `join_policy:"x"`; `org_mode:"x"`; extra fields `status`, `default_role_id`, `is_system`, `id` |
| `› test_create_app_origins_normalized` | POST with `["https://Stoktok.MMLabs.online/", "https://stoktok.mmlabs.online:443"]` → `origins == ["https://stoktok.mmlabs.online"]` |
| `› test_patch_app_fields` | PATCH `name`, `status=disabled`, `join_policy=approval`, `org_mode=required`, `origins` → each applied; `{}` → 200 unchanged |
| `› test_patch_app_rejects_key_and_unknown_fields` | `{"key": "x"}`, `{"id": …}`, `{"foo": 1}` → 422; key unchanged |
| `› test_patch_app_null_only_for_default_role` | `{"name": null}`, `{"status": null}`, `{"join_policy": null}`, `{"org_mode": null}`, `{"origins": null}` → 422 |
| `› test_patch_app_bad_origin_422` | `{"origins": ["http://example.com"]}` → 422; response body does not contain `example.com` |
| `› test_patch_unknown_app_404` | PATCH `/apps/nope` → 404 |
| `› test_patch_default_role_set_and_clear` | role `stoktok:viewer` (non-admin permission only) → set → 200 with its id; `null` → 200 `default_role_id: null` |
| `› test_patch_default_role_unknown_or_foreign_422` | random UUID → 422 `unprocessable`; a `calechip` role id → 422 |
| `› test_patch_default_role_with_admin_permission_422` | role holding a `stoktok:roles:manage` (`is_admin`) permission → 422 `unprocessable`; `default_role_id` unchanged |
| `› test_admin_app_cannot_be_deleted` | DELETE `/apps/admin` → 409; app still present |
| `› test_admin_app_locked_fields_409` | parametrized PATCH `/apps/admin`: `join_policy` open/approval, `default_role_id` = `admin:super-admin` id, `status=disabled` → 409; row unchanged |
| `› test_admin_app_same_value_patch_ok` | PATCH `/apps/admin` `{join_policy:"closed", status:"active", default_role_id:null, name:"mmlabs Admin"}` → 200 |
| `› test_delete_app` | DELETE created app → 204; GET → 404; its member role and permissions gone |
| `› test_delete_unknown_app_404` | → 404 |
| `› test_delete_app_with_assignments_409` | app + role + assignment (`make_user`, `assign`) → 409; app still present |
| `› test_rbac_routes_declare_permissions` | `create_app(Settings(admin_rbac_api_enabled=True)).openapi()` paths under `/api/v1/apps` == `{("GET","/api/v1/apps"): "admin:apps:read", ("POST","/api/v1/apps"): "admin:apps:write", ("GET","/api/v1/apps/{key}"): "admin:apps:read", ("PATCH","/api/v1/apps/{key}"): "admin:apps:write", ("DELETE","/api/v1/apps/{key}"): "admin:apps:write"}` |

## Approach
1. **Config (AC 1; R5).** In `Settings`: `admin_rbac_api_enabled: bool = False` with the comment "Temporary: privilege-changing RBAC endpoints without auth. Removed by ADM-002." Replace `_admin_users_api_not_public` by one `@model_validator(mode="after") _admin_apis_gate`:
   ```python
   flags = {
       "ADMIN_USERS_API_ENABLED": self.admin_users_api_enabled,
       "ADMIN_RBAC_API_ENABLED": self.admin_rbac_api_enabled,
   }
   for name, on in flags.items():
       if on and self.app_env in {"staging", "production"}:
           raise ValueError(f"{name} must not be set in staging/production before ADM-002")
   if any(flags.values()) and "app_env" not in self.model_fields_set:
       raise ValueError("APP_ENV must be set explicitly when an admin API flag is on")
   ```
   (`env_ignore_empty=True` already makes `APP_ENV=` count as unset — security-architect finding 1.) The existing staging/production test keeps matching `ADMIN_USERS_API_ENABLED`. `tests/conftest.py` sets `APP_ENV=test`, so existing flagged tests still pass. Do not build a flagged `Settings` with `model_copy(update=…)` (skips validators).
2. **Errors (AC 2; R6).** `class UnprocessableError(AppError): status_code = 422; code = "unprocessable"`. Services pass `details=[{"field": …, "reason": …}]`; never the submitted value. Body-only rules stay Pydantic (`validation_error`); rules needing the DB or the path raise `UnprocessableError`.
3. **Carry-overs (AC 3–5).**
   - `permits(granted: AbstractSet[str] | Sequence[str], required: str)`: first line `if isinstance(granted, str): raise TypeError("granted must be a collection of keys, not a str")`; rest unchanged.
   - `effective_roles`: `prefix = f"{app_key}:"; return [k for k in keys if k.startswith(prefix)]` with the same "second check" comment as `effective_permissions`.
   - `RoleCatalogService.build_default_role(app_key, actor=None)`: look up (404), then `_build_for_app(app)`. `_build_for_app`: `if app.key == ADMIN_APP_KEY: raise ConflictError("The admin app has no member role")`; get-or-create the role, where `repo.add_role` inserts inside `session.begin_nested()` and the service maps `IntegrityError` → `ConflictError("Member role was created concurrently")`; diff as today; `add_role_permissions` uses `sqlalchemy.dialects.postgresql.insert(RolePermission).values(rows).on_conflict_do_nothing()`; replace `if app.default_role_id is None: app.default_role_id = role.id` with `await repo.set_default_role_if_null(app.id, role.id)` (`UPDATE apps SET default_role_id = :r WHERE id = :a AND default_role_id IS NULL`), then `await session.refresh(app)`. Keep `# R4` marker. Existing tests stay green.
4. **Origins (AC 9; R11/S4).** `app/modules/rbac/origins.py`, no dependencies, no IDNA conversion (non-ASCII → reject; security-architect):
   - Reject if the raw value is > 255 chars or contains any char outside printable ASCII, or whitespace.
   - Lowercase, then full-match `^(https?)://(\[::1\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?/?$` (this alone rejects userinfo, `%`, `\`, path, query, fragment, `*`, `null`).
   - Host: `[::1]`, or dot-separated labels each `[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`, total ≤ 253 (rejects empty labels and a trailing dot). Reject when every label is numeric (IPv4 literal forms) unless the host is exactly `127.0.0.1`.
   - Scheme: `https` → any valid host (Q3 default: any https); `http` → host must be `localhost`, `127.0.0.1` or `[::1]`.
   - Port: no leading zero, 1–65535; drop `:443` for https and `:80` for http.
   - Return `f"{scheme}://{host}{':' + port if port else ''}"`.
   - `normalize_origins(values)`: ≤ 20 entries (checked on input), normalize each, dedupe keeping first-seen order.
   - `ValueError` messages are generic (e.g. "origin must be https://host[:port], or http on localhost") so the 422 never echoes input.
5. **Schemas** (`app/modules/rbac/schemas.py`; R9). All inputs `ConfigDict(extra="forbid")`.
   - `AppCreate`: `key: str = Field(pattern=APP_KEY_RE, max_length=32)`, `name: str = Field(min_length=1, max_length=255)`, `join_policy: JoinPolicy = JoinPolicy.OPEN`, `org_mode: OrgMode = OrgMode.NONE`, `origins: list[str] = Field(default_factory=list, max_length=20)` + `field_validator` → `normalize_origins`.
   - `AppUpdate`: `name`, `status: AppStatus`, `join_policy`, `org_mode`, `origins` (same validator), `default_role_id: uuid.UUID | None`; all optional with `None` default; a `model_validator` rejects explicit `None` for every field except `default_role_id` (fields in `model_fields_set` whose value is None). The service applies only `model_fields_set`.
   - `AppRead` (`from_attributes`): `id, key, name, status: AppStatus, join_policy: JoinPolicy, org_mode: OrgMode, origins: list[str], default_role_id: uuid.UUID | None, created_at, updated_at`.
6. **Repository** (R1; SQLAlchemy only). New/changed methods listed in Files. `add_app`, `delete_app`, `save` and `add_role` wrap the flush in `async with self.session.begin_nested():` (the `UserRepository.add` pattern — api consult finding 3), so an `IntegrityError` leaves the request session usable. `list_apps(limit, offset)` → `(items ordered by key, total)`. `role_has_admin_permission(role_id)` → `EXISTS(role_permissions ⋈ permissions WHERE role_id = :r AND is_admin)`. `app_has_assignments(app_id)` → `EXISTS(role_assignments ⋈ roles WHERE roles.app_id = :a)`.
7. **AppService** (`service.py`; R1, R4). Constructor builds `RbacRepository` and a `RoleCatalogService` on the same session. All public methods take `actor: uuid.UUID | None = None` (unused until ADM-002).
   - `list(limit, offset)`, `get(key)` → `NotFoundError("App not found")` (message does not echo the key).
   - `create(data, actor=None)`: pre-check `get_app_by_key` → `ConflictError`; `add_app` (IntegrityError → `ConflictError`); `await catalog._build_for_app(app)` in the same transaction (a failure rolls the app back with the request); refresh; `# R4`.
   - `update(key, data, actor=None)`: get (404). For `key == ADMIN_APP_KEY`: changing `join_policy` to ≠ `closed`, `default_role_id` to non-null, or `status` to ≠ `active` → `ConflictError` (checked before any 422). Add `# ADM-002 guard: changing admin origins requires admin:*` at the origins assignment. For `default_role_id` set non-null: `repo.get_role(app.id, role_id)` None → `UnprocessableError(details=[{"field": "default_role_id", "reason": "unknown_role"}])` (covers other-app roles); `role_has_admin_permission` → `UnprocessableError(... "reason": "role_has_admin_permissions")`. Apply fields from `model_fields_set` (`StrEnum` → `.value`); if nothing changed, return without writing; else `repo.save()` (IntegrityError → `ConflictError`) and refresh. `# R4`.
   - `delete(key, actor=None)`: get (404); `admin` → `ConflictError("The admin app cannot be deleted")`; `app_has_assignments` → `ConflictError("App has role assignments; disable it instead")`; `delete_app` (IntegrityError → `ConflictError`). `# R4`.
8. **Router** (`router.py`; R1, R3, R9). Module docstring as in `users/router.py` (class 1 Bearer, unauthenticated until ADM-002, mounted only when `admin_rbac_api_enabled`). Routes with `openapi_extra=permission(...)` per the API contract; path param `key: str` (no pattern: unknown or malformed → 404). Responses via `AppRead.model_validate`. DELETE `status_code=204`, POST `201`.
9. **main.py**: import `router as rbac_router` from `app.modules.rbac.router`; conditional include after the users one.
10. **ADR-0003 amendments** as in Files (add an "Amended 2026-11-11 (USR-02b)" line under Status). Only slice-1 decisions; 02b-2/02b-3 add theirs.
11. `make check` green.

Rules: **R1** router → `AppService` → `RbacRepository`; services raise `AppError` subclasses only. **R2** no schema change (constant only); `alembic check` clean. **R3** every new route is class 1 Bearer with a declared `admin:apps:read|write`; the flag gate stands in until ADM-002. **R4** `# R4` markers on create/update/delete/builder. **R5** new key in `.env.example` without value. **R6** 404/409/422 via `AppError`; 201/204. **R7** only the listed files. **R8** happy/404/409/422 tests per endpoint; 401/403 not applicable before ADM-002. **R9** `/api/v1/apps`, `PATCH`, `Page[T]`; `{key}` instead of a UUID is owner question Q2 (non-blocking, already in the USR-02 contract). **R10** no billing/OIDC. **R11** origins normalized for the future CORS echo; no logging added; errors never echo input.

Size estimate: ~430 production lines (config 20, errors 5, main 4, origins 60, schemas 70, repository 70, services 130, router 60, authz 10) + ADR ~25 + ~450 test lines. Slightly above the ~400 guide but one cohesive slice: the gate, errors and carry-overs are prerequisites for 02b-2/02b-3.

## Specialist input used
**api-platform-engineer**
- Adopted: `UnprocessableError` code `unprocessable` with `{field, reason}` details; Pydantic for body-only rules, `UnprocessableError` for DB/path rules; `AppUpdate` `extra="forbid"`, applied via `model_fields_set`, `null` only for `default_role_id`; savepoint around every insert/delete (finding 3); route→permission test copied from `test_users_admin.py`; `.env.example` key; pure `normalize_origin` in `origins.py` with its own unit tests; 409 for `status` changes on `admin` (risk 1, open Q1).
- Adopted (split): 02b-1 = flag + `APP_ENV` + `UnprocessableError` + carry-overs + apps CRUD + origins; catalog as its own 02b-3 because Q1 may change how it is mounted.
- Deferred to 02b-2/02b-3 with contract: permission/role schemas, PUT replace via a shared `_set_role_permissions`, delete pre-checks for roles/permissions, catalog serialization/ETag helper/`catalog_router`, `is_admin` in the catalog (their open Q2 → 02b-3, default: not included).

**identity-access-engineer**
- Adopted: `permits` `str` guard + the edge-case rows; `effective_roles` prefix filter; builder refuses `admin` via `ADMIN_APP_KEY`, role insert in a savepoint → 409, idempotent `role_permissions` insert with `on_conflict_do_nothing`, conditional `default_role_id` update, public/internal builder split; app create + build in one transaction; `admin` locks (`join_policy` closed, `default_role_id` NULL, `status` active); `default_role_id` may not point to a role holding `is_admin` permissions (422); `extra="forbid"` on PATCH schemas; ADR-0003 amendments.
- Deferred to 02b-2: prefix == app key on POST permission/role (422), `<app>:*` forced `is_admin`, system-row delete rules, "super-admin always holds `admin:*`", PUT adding an `is_admin` permission to the default role → 409 (their open Q3: in 02b, not ADM-002 — agreed).
- Deferred to follow-up: DB-level `app_key` column + composite FK (schema change, R7). Their Q1 (catalog 404 for `admin`) → adopted in the 02b-3 contract.

**security-architect**
- Adopted: one validator covering both flags; `model_fields_set` test with `monkeypatch.delenv` + `_env_file=None`, empty `APP_ENV=` refused; mount only in `create_app`; strict origins pattern with non-ASCII rejection (replaces the USR-02 plan's IDNA → punycode step: no new dependency, no IDNA2003/2008 mismatch), loopback-only `http`, default ports dropped, ≤ 20 × 255; `admin` app locks; default role never holds `is_admin` permissions; `# ADM-002 guard` comments; `ADMIN_RBAC_API_ENABLED=` without a value.
- Deferred to 02b-2: "`PATCH permission is_admin=true` on a permission the default role holds → 409" and the PUT guard. To 02b-3: catalog stays flag-gated, `admin` → 404, expose only key/name/`is_system`/permission keys.
- Not adopted now: write-time env check for `http://localhost` (adds nothing: the flag is refused where it would matter — their finding 3); the real control is read-time in USR-03 (follow-up). Q3 suffix allowlist: owner question, default "any https" kept (ticket).
- Informational (`ValidationError` prints a truncated `input_value`): follow-up, not in this diff.

## Assumptions
- **A1 — Split.** ~690 production + ~600 test lines for the whole ticket (api consult) → three slices; this plan is 02b-1. This ticket ID (USR-02b) carries 02b-1; the orchestrator creates tickets for 02b-2 and 02b-3.
- **A2 — `{key}` path param** (Q2 default): kept as in the USR-02 contract. Reversible only by a v1-breaking change, but the flag keeps the routes unreachable in staging/production, so the owner can still change it before ADM-002 removes the flag.
- **A3 — Origins** (Q3 default): any https LDH host; adding a suffix allowlist later only narrows input and is non-breaking for stored data (re-validate at USR-03 read time).
- **A4 — No status locking for non-admin apps**: disabling `stoktok` is a legitimate staff action. Only `admin` is locked.
- **A5 — `org_mode` on `admin` is not locked** (no consult asked for it; ADM-002 release checklist covers it — follow-up).
- **A6 — PATCH explicit `null`** is rejected (422) for non-nullable fields, like `UserStatusUpdate` (USR-01b), not ignored like `OrganizationUpdate`.
- **A7 — Deleting an app** with no assignments is allowed and cascades its catalog (DB, A9 of the USR-02 plan); with assignments → 409 ("disable instead").
- **A8 — `AppCreate` does not accept `status` or `default_role_id`**: the new app is `active` and its default is the built member role.
- **A9 — `.env` impact:** a local `.env` with an admin flag on and no `APP_ENV` will now refuse to start; `.env.example` already has `APP_ENV=local`, so a fresh copy works (note in `impl-log.md`).

## Blocking questions
None. Q1–Q3 keep their ticket defaults; every route here stays behind a default-off flag that is refused in staging/production, so each answer can still change the contract before ADM-002.

## Split proposal
Order: **02b-1 (this plan) → 02b-2 → 02b-3**. USR-02c (assignments + CLI) ← 02b-2. USR-05 ← 02b-3. ADM-002 ← all three + 02c. 02b-3 may be merged into 02b-2 if the owner answers Q1 first and the combined diff stays reviewable. All later slices use the same `ADMIN_RBAC_API_ENABLED` gate, `UnprocessableError`, savepoint pattern, `actor` parameter, `# R4` markers, and extend `test_rbac_routes_declare_permissions`.

### 02b-1 — Gate, errors, carry-overs, apps CRUD + origins (this plan)
Covers ticket AC1, AC2, AC3 (apps), AC4 (apps + origins + `admin` app), AC6 (apps routes), AC7 (apps tests), carry-overs (1)–(3).

### 02b-2 — Permissions + roles CRUD and role-permission PUT (areas: api, auth, security; ~300 prod + ~350 test lines)
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/permissions` | `limit`, `offset` | `Page[PermissionRead]` `{id,key,description,is_admin}` order key | 200, 404 | `admin:apps:read` |
| POST | `/api/v1/apps/{key}/permissions` | `{key, description?, is_admin?}` | 201 `PermissionRead` | 201, 404, 409 dup, 422 | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/permissions/{permission_id}` | `{description? (nullable), is_admin? (non-null)}` | `PermissionRead` | 200, 404, 409, 422 | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/permissions/{permission_id}` | — | — | 204, 404, 409 | `admin:roles:manage` |
| POST | `/api/v1/apps/{key}/roles` | `{key, name}` | 201 `RoleRead` `{id,key,name,is_system,permissions:[sorted keys]}` | 201, 404, 409, 422 | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/roles/{role_id}` | `{name}` | `RoleRead` | 200, 404, 422 | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/roles/{role_id}` | — | — | 204, 404, 409 | `admin:roles:manage` |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | `{permission_keys: list[str] (≤ 200)}` replaces the set | `RoleRead` | 200, 404, 409, 422 | `admin:roles:manage` |

No single-role GET (as in the USR-02 contract): roles are read through the 02b-3 catalog and the `RoleRead` responses. Rules: path ids of another app → 404. Keys: Pydantic pattern (`PERMISSION_KEY_RE`/`ROLE_KEY_RE`) → `validation_error`; prefix ≠ app key → `UnprocessableError` (field `key`). `<app>:*`: `is_admin` absent → forced true; `is_admin=false` → 422; PATCH `is_admin=false` on `*` → 422. `is_system`, `key` never accepted (`extra="forbid"`). PUT: dedupe; every unknown/other-app key listed in 422 `details`; empty list allowed; idempotent; diff applied through a `_set_role_permissions` shared with the builder. 409 guards: DELETE permission `admin:*` or held by any role; DELETE role `is_system`, the app's default role, or with assignments; PUT on `admin:super-admin` without `admin:*`; PUT adding an `is_admin` permission to the app's current default role; PATCH permission `is_admin=true` while the default role holds it. `IntegrityError` → 409. `# ADM-002 guard` comments where granting `<app>:*`/`is_admin` permissions will need `admin:*`. Tests: happy, 404 (unknown app, foreign ids), 409 dups and each guard, 422 prefix/format/wildcard/unknown keys, route map (13 routes with 02b-1; 14 with the 02b-3 catalog).

### 02b-3 — Role catalog with ETag (areas: api, security; ~90 prod + ~150 test lines)
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/roles` | `If-None-Match` | `RoleCatalog {app: key, roles: [{key, name, is_system, permissions: [sorted keys]}]}` roles sorted by key; strong `ETag` `"<first 32 hex of sha256(body bytes)>"`; `Cache-Control: no-cache` | 200, 304 (lists, `W/`, `*`; empty body, repeats `ETag` + `Cache-Control`), 404 (unknown, disabled, or `admin`) | `admin:apps:read` while flag-gated; public mount only if the owner answers Q1 yes |

Service builds the Pydantic document (R1); router serializes once (`model_dump_json()`), hashes those exact bytes, returns `Response(media_type="application/json")` with `response_model=RoleCatalog` for OpenAPI. Separate `catalog_router` (still behind `ADMIN_RBAC_API_ENABLED`) so a Q1 answer moves one line in `main.py`. `admin` → 404 now so a later public mount is non-breaking. `is_admin` per permission not included (additive later). Tests: stable ETag, ETag changes after a permission/role change, 304 variants, 404 cases, flag off → 404.

### Owner questions (non-blocking, defaults in place)
- **Q1** public catalog (R3 class 3)? Default: flag-gated (02b-3).
- **Q2** R9 exception for `{key}` paths + non-`Page` catalog document? Default: as contracted.
- **Q3** origins limited to a configured suffix (e.g. `.mmlabs.online`)? Default: any https.
- New, for ADM-002: should changing `admin` app origins require `admin:*` rather than `admin:apps:write`? (marked `# ADM-002 guard`).

### Follow-ups (not in any 02b slice)
- **USR-03:** drop `http://` origins when `app_env` ∈ {staging, production}; bind `Origin` to the app named by `X-MM-App`/`aud`, not the union of all apps' origins; re-validate stored origins at read time.
- **ADM-002 release checklist:** review `apps.origins`, `default_role_id`, `join_policy`, `org_mode`, `is_admin` on every app and any `admin:`-prefixed rows created while the flag was on.
- **USR-05:** the manifest sync must call the same services (guards live in the service only).
- **Later ticket:** DB-level `app_key` + composite FK + `CHECK (split_part(key, ':', 1) = app_key)`; raise a plain `RuntimeError` instead of a `ValidationError` with `input_value` for refused flags.

## Data model / migration
None. Only a Python constant (`ADMIN_APP_KEY`) is added to `app/modules/rbac/models.py`; `alembic check` must stay clean.

## API contract
All R3 class 1 (Bearer, `aud=admin` once ADM-002 lands); unauthenticated until then and mounted only when `ADMIN_RBAC_API_ENABLED=true` (refused in staging/production). Errors use the R6 envelope.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps` | `limit` (1–100, default 20), `offset` (≥ 0) | `Page[AppRead]` ordered by `key` | 200, 422 | `admin:apps:read` |
| POST | `/api/v1/apps` | `AppCreate {key, name, join_policy? = open, org_mode? = none, origins? = []}`, `extra="forbid"` | 201 `AppRead`; `<key>:member` built, `default_role_id` set | 201, 409 (duplicate key, incl. `admin`; race), 422 | `admin:apps:write` |
| GET | `/api/v1/apps/{key}` | — | `AppRead {id, key, name, status, join_policy, org_mode, origins, default_role_id, created_at, updated_at}` | 200, 404 | `admin:apps:read` |
| PATCH | `/api/v1/apps/{key}` | `AppUpdate` any of `{name, status, join_policy, org_mode, origins, default_role_id}`; `null` only for `default_role_id`; `extra="forbid"` (so `key` is immutable) | `AppRead` | 200, 404, 409 (`admin`: `join_policy` ≠ closed, `default_role_id` ≠ null, `status` ≠ active; race), 422 (`validation_error`: bad value/origin/null/extra; `unprocessable`: unknown or other-app role, role holding `is_admin` permissions) | `admin:apps:write` |
| DELETE | `/api/v1/apps/{key}` | — | — | 204, 404, 409 (`admin`; app has role assignments; race) | `admin:apps:write` |
