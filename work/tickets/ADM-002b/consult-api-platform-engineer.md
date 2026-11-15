# consult-api-platform-engineer — ADM-002b

**Findings**
1. Flag surface (every place to touch): `app/core/config.py` fields `admin_users_api_enabled` / `admin_rbac_api_enabled` (l.106–108) and `_admin_apis_gate` (l.145–157); `app/main.py` l.91–95 conditional `include_router` plus the `/docs` comment (l.43–46); router docstrings in `users/router.py`, `rbac/router.py`, `rbac/assignments_router.py` ("still flag-gated") and `rbac/catalog_router.py` l.6 ("not behind `admin_rbac_api_enabled`"); `.env.example` l.68–69. `docs/deploy.md` has no row for either flag. `extra="ignore"` means a leftover env var is silently ignored, so removal does not break any deploy.
2. Tests that toggle the flags:
   - The flag is passed to `make_client(...)` in `test_rbac_roles_api`, `test_rbac_apps_api`, `test_rbac_permissions_api`, `test_rbac_assignments_api`, `test_rbac_locking`, `test_role_catalog_api:213`, `test_auth_cors:174,298`, `test_users_admin:30` and `test_admin_auth:51`.
   - `FLAGS` is defined in `test_route_auth_classes:72` (used in `_app()` and `flags_client`) and in `test_config:7–56` (5 gate tests).
   - Tests that prove the flag works: `test_users_admin:69–84`, `test_cli_grant_super_admin:298–299` and the four "not mounted by default" tests (`test_permissions_api_not_mounted_by_default`, `test_flag_off_404`, `test_users_api_not_mounted_by_default`, `test_apps_api_not_mounted_by_default`).
   - `make_client` passes `**settings_overrides` to `Settings`, so once the fields are gone a leftover kwarg is silently ignored rather than failing. Stale usages would linger unnoticed.
3. Principal plumbing: `AdminPrincipal(user_id, permissions: frozenset, token_id)` is already resolved per request. Routers use `dependencies=[Depends(current_admin)]` and never take the principal as a parameter. Services already accept `actor: uuid.UUID | None = None` (only an id, no permissions). The `# ADM-002 guard` sites need permissions too.
4. Test fixture `_override_admin` builds the principal with a **random** `uuid4()`. Self-grant / self-disable / `granted_by == actor` tests cannot work with it as is.
5. Error conventions: every 409 in `rbac/service.py` is a generic `ConflictError` with code `conflict`, distinguished only by message. The 403 precedent is `PermissionDeniedError` (`permission_denied`, `details={"required": key}`) plus domain subclasses with their own codes (`join_closed`, `audience_mismatch`). 422 refusals use `UnprocessableError` with `[{field, reason}]` details, e.g. `origin_taken`.
6. `/docs` and `/openapi.json` are public in every environment (spec §85: "temporary — revisit when ADM-002 mounts the admin routes"). `test_route_auth_classes.DOCS` asserts that exact path set.

**Recommendations**
1. **Flag removal (AC "Flags removed").**
   - Delete the fields and the validator, and mount the three routers unconditionally next to `api_router`.
   - Delete the 2 `.env.example` lines, the 5 `test_config` gate tests, `test_users_admin:69–84`, the flag asserts in `test_cli_grant_super_admin`, and the four not-mounted tests. Each not-mounted test becomes "401 without token", which the sweep already covers.
   - Drop the flag kwargs from all `make_client` calls. In `test_route_auth_classes`, replace `Settings(**FLAGS)` with `create_app()`.
   - Add a guard test that `Settings.model_fields` has no `admin_*_api_enabled`. Optionally make `make_client` reject unknown override keys, so stale kwargs fail loudly.
   - Update ADR-0008, identity-architecture §4 and the deploy.md "Admin access" section: users/RBAC are now live in production.
2. **Principal hand-off (R1).**
   - Routers declare `principal: AdminDep` (keep the router-level dependency so the enumeration test still sees `current_admin`; FastAPI caches it per request).
   - Pass a plain, HTTP-free value object to services, e.g. `Actor(user_id: UUID, permissions: frozenset[str])`, defined in `app/modules/rbac/authz.py` or `admin_auth/service.py` (no FastAPI imports). `AdminPrincipal` is already a frozen dataclass with no HTTP types, so passing it directly is acceptable. Leave `token_id` out of guard logic.
   - Make `actor` **required** on API-facing service methods. Keep a separate explicit CLI path (`system:cli`, actor None) so a forgotten argument cannot bypass a guard.
   - Guards call `permits(actor.permissions, "admin:*")`.
3. **Error codes (R6).** Keep the envelope and add stable codes, because the admin UI must tell these cases apart:
   - **403** (actor lacks authority), as `PermissionDeniedError` subclasses:
     - `admin_wildcard_required`, with `details={"required": "admin:*"}`, for admin-app / `is_admin` / `<app>:*` sites.
     - `self_grant_forbidden` for self-grant/revoke of an admin-app role.
     - `self_disable_forbidden`.
   - **409** (the data's state forbids it), as `ConflictError` subclasses:
     - `last_super_admin` for revoke and disable (also migrate the existing revoke message to this code).
     - `role_has_assignments` for an `is_admin` grant onto an assigned role.
     - `origin_taken` stays 422, as on the CLI path, for consistency.
   - `admin:*` only on `admin:super-admin`: 422 `unprocessable` with `{field:"permission_keys", reason:"wildcard_super_admin_only"}`, matching `_wildcard_must_be_admin`.
   - Check order: 404, then 403 guard, then 409/422 (so existence is not hidden from an authorized caller; acceptable because every caller is staff).
   - Add 409 to the `responses` of affected routes so OpenAPI documents them.
4. **Docs in production (owner decision).** Recommend adding `docs_enabled: bool` (default true locally/in tests; validator default false in production unless explicitly set), passing `docs_url`/`openapi_url=None` when off, and parametrising the `DOCS` allow-list. The whole admin surface (permission map in `x-permission`) becomes public reconnaissance once mounted; gating behind admin Bearer breaks Swagger's browser flow for little gain. Third option (keep public) is acceptable only with the owner's sign-off, since contract details are not secrets.
5. **Fixtures.**
   - `make_client(admin_permissions=..., admin_user=<User|None>)`: when a user is given, the principal uses `user.id`, enabling self-grant and `granted_by` asserts.
   - Add `actor_client` built on `make_staff` + `mint` for the real path, and a parametrised sweep: each guard site × {`admin:*`, `admin:roles:manage` only} → allowed / 403.

**Risks**
- Removing the flags and adding the guards in separate commits would expose the escalation paths (AC4). Land them in one commit and test the guard sites first.
- The silent `**settings_overrides` acceptance hides stale flag kwargs.
- Optional `actor=None` defaults would let a router that forgets to pass the actor skip the guards.
- Making `/docs` default-off in production changes what the owner sees today; communicate it in deploy.md.
- Renaming the existing revoke 409's code is a minor v1 contract change; acceptable only because the route was never mounted in production.

**Open questions**
1. `/docs`/`openapi.json` in production: keep public, disable by setting (recommended), or admin-only?
2. Should 403 guard codes be distinct (recommended) or plain `permission_denied` with `details.required`?
3. Is the `is_admin`-onto-assigned-role refusal 409 for every actor, `admin:*` holders included (as the ticket implies), or only for non-`admin:*` actors?
4. Should `make_client` reject unknown settings overrides (a small test-infra change, arguably outside R7)?
