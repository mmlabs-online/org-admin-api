# consult-api-platform-engineer — USR-02b

## Findings
1. **Reusable:** `RbacRepository` already has `get_app_by_key`, `get_role_by_key`, `add_role`, `list_permissions(app_id, is_admin=)` (not paginated), `role_permission_keys`, `add_role_permissions`, `remove_role_permissions`. `RoleCatalogService.build_default_role` already computes a target/current diff — the PUT replace can reuse the same logic.
2. **Missing:** paginated list + count for apps and permissions; get permission/role by `(app_id, id)`; insert app/permission; delete app/permission/role; `permissions_by_keys(app_id, keys)`; assignment counts per app and per role; "is this permission held by any role"; a batch query of roles with their permission keys for an app (RoleRead + catalog).
3. **Failed inserts break the transaction:** `add_role` only flushes; only `UserRepository.add` uses `begin_nested()`. Without a savepoint, a unique violation leaves the request (and test) session unusable, so mapping `IntegrityError` → 409 needs a savepoint around every insert/delete.
4. **Flag pattern:** the users router is mounted in `create_app` under `if settings.admin_users_api_enabled`, with `openapi_extra=permission(...)`; the route→permission test (`tests/test_users_admin.py:303`) enumerates `app.openapi()` — copy as is.
5. **DB guards already exist:** cross-app `default_role_id`, cross-app role permissions (composite FKs) and deleting an assigned role (NO ACTION) fail as `IntegrityError`; the API still needs pre-checks so the caller gets a clear 422/409.

## Recommendations
- **Errors (AC2, R6):** `UnprocessableError` code `unprocessable`, `details` a list of `{field|key, reason}`. Body-only rules (key regex, origins, `extra="forbid"`) stay Pydantic `validation_error`; rules needing the path or DB (prefix ≠ app key, `*` with `is_admin=false`, unknown/other-app `default_role_id`, unknown keys in PUT) raise `UnprocessableError`. Tests check status and code.
- **Schemas (AC3, AC4):** `AppCreate{key,name,join_policy?,org_mode?,origins?}`; `AppUpdate` `extra="forbid"`, applied via `model_fields_set`, explicit `null` only for `default_role_id` (others → 422 via a validator); `AppRead` as in the plan; `PermissionCreate{key,description?,is_admin?}`, `PermissionUpdate{description?(nullable), is_admin?(non-null)}`, `PermissionRead{id,key,description,is_admin}`; `RoleCreate{key,name}`, `RoleUpdate{name}`, `RoleRead{id,key,name,is_system,permissions:list[str] sorted}`; `RolePermissionsReplace{permission_keys: list[str], max_length 200}`; `is_system` never an input. Origins: a pure `normalize_origin()` in `app/modules/rbac/origins.py`, called from a field validator, with its own unit tests.
- **PUT replace (AC3):** dedupe, look up all keys, 422 with `details` listing every unknown/other-app key; then apply the diff via an extracted `_set_role_permissions` shared with `build_default_role`; return `RoleRead`. Empty list allowed. Removing `admin:*` from `admin:super-admin` → 409. Idempotent.
- **Deletes (AC4):** pre-check → 409 when: app is `admin` or has assignments; role is `is_system`, is the app's default role, or has assignments; permission is `admin:*` or held by any role. Keep `IntegrityError` → 409 as fallback.
- **Catalog (AC5, R1):** the service returns a Pydantic `RoleCatalog{app, roles:[RoleRead]}`, everything sorted; the router serializes once (`model_dump_json()`), hashes those exact bytes for the ETag, and returns `Response(content=..., media_type="application/json")` (keep `response_model=RoleCatalog` for OpenAPI). A small `if_none_match_matches(header, etag)` helper in the router handles lists, `W/`, `*`; 304 has an empty body and repeats `ETag` and `Cache-Control: no-cache`. Put it in its own `catalog_router` (still flag-gated) so the Q1 answer changes one line in `main.py`.
- **Permissions (AC6, R3):** declare `admin:apps:read` on the catalog too (behind the admin flag; R3 needs one class per endpoint) — drop if Q1 makes it public. `test_rbac_routes_declare_permissions` with the exact 13-route map. Services take `actor: uuid.UUID | None = None`; mutations carry `# R4` markers.
- **Config (AC1):** `"app_env" in self.model_fields_set` works with env-sourced values; `env_ignore_empty` makes `APP_ENV=` count as unset. Note in `.env.example`: local `.env` with `ADMIN_USERS_API_ENABLED=true` and no `APP_ENV` will now refuse to start.
- **Carry-overs:** `permits` raises `TypeError` for a `str` `granted`; `effective_roles` filters by prefix; `build_default_role("admin")` → 409, and its `IntegrityError` race → 409.

## Diff-size estimate
Production: config/errors/main ~35, schemas + origins ~130, repository ~120, services ~220, routers ~170, carry-overs ~15 → **~690 lines**; tests ~600. **Does not fit ~400.** Proposed split: **02b-1** flag, `UnprocessableError`, `APP_ENV` rule, carry-overs, apps CRUD + origins (~300 prod lines + tests); **02b-2** permissions + roles CRUD, PUT, catalog + ETag (~390 + tests) — or the catalog alone as **02b-3** (~90), which fits well because Q1 may change how it is mounted.

## Risks
- `PATCH /apps/admin` can change `status`, `join_policy`, `origins` → disabling `admin` locks staff out once USR-03/ADM-002 check app status. Recommend 409 for `status` changes on `admin`.
- Staff can create `admin:*`-prefixed permissions while the flag is on; those rows survive until ADM-002 (FU2 review).
- An ETag not computed from the exact response bytes won't match the body.
- The 409 on deleting a permission held by a role changes my earlier advice (cascade): safer, but staff must PUT it off every role first.

## Open questions
1. 409 for `admin` app `status`/`join_policy` changes (recommended)?
2. Include `is_admin` per permission in the catalog (backward-compatible to add later)?
3. Accept a 3-way (or 2-way) split, or keep one ticket with a larger review?
4. Ticket Q1–Q3 still open.
