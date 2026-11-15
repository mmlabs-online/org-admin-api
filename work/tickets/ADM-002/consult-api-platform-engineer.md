# consult-api-platform-engineer — ADM-002

## Findings

- **Current state.** Organizations routes are always mounted (`app/api/router.py`), have no `openapi_extra=permission(...)` and no auth, so they are open in production today. The users, rbac and assignments routers are mounted only behind `admin_users_api_enabled` / `admin_rbac_api_enabled` (`app/main.py`). `Settings._admin_apis_gate` (`app/core/config.py:146`) refuses those flags in staging and production. `app/core/permissions.py` only writes `x-permission` into OpenAPI and does not enforce anything.
- **Blocker.** `TokenService.issue` (`app/modules/tokens/service.py:58`) raises `AudienceNotSupportedError` for `app.key == "admin"`. Until ADM-002 removes that, no staff member can get an `aud=admin` token.
- **Seeds.** Only `admin:*` and `admin:super-admin` are seeded (`migrations/versions/20261111_b638a7c84c31_create_rbac.py`). The granular `admin:<resource>:<action>` permissions are not seeded yet.
- **Error mapping.** `PermissionDeniedError` (403, `permission_denied`) exists. There is no 401 `AppError` subclass. `_HTTP_CODES` maps 401 to `unauthorized`, so a 401 already comes out in the error envelope. `AppError.headers` can carry `WWW-Authenticate`.
- **Permission matcher.** `permits()` in `app/modules/rbac/authz.py` already handles `admin:*` (wildcard per app, no global `*`). `AuthzService.effective_permissions(user_id, "admin")` returns app-scope keys.
- **Existing route test.** `tests/test_rbac_apps_api.py::test_rbac_routes_declare_permissions` reads permissions from `app.openapi()`, which is a good base. Separately, `tests/jwt_verifier.py` is a reference token verifier (ES256, `typ=at+jwt`, `kid`, required claims, `email_verified`).
- **Join requests.** These endpoints (USR-03g) do not exist yet, so they are out of scope here.

**Endpoint inventory**

| Method | Path | Current | Proposed |
|---|---|---|---|
| GET | /api/v1/organizations | none | admin:organizations:read |
| POST | /api/v1/organizations | none | admin:organizations:write |
| GET | /api/v1/organizations/{org_id} | none | admin:organizations:read |
| PATCH | /api/v1/organizations/{org_id} | none | admin:organizations:write |
| DELETE | /api/v1/organizations/{org_id} | none | admin:organizations:write |
| GET | /api/v1/users | admin:users:read | same |
| GET | /api/v1/users/{user_id} | admin:users:read | same |
| PATCH | /api/v1/users/{user_id} | admin:users:write | same (plus F2 last-super-admin guard) |
| GET | /api/v1/users/{user_id}/role-assignments | admin:users:read | same |
| POST | /api/v1/users/{user_id}/role-assignments | admin:roles:assign | same (plus `admin:*` guard for admin-app or is_admin roles) |
| DELETE | /api/v1/users/{user_id}/role-assignments/{assignment_id} | admin:roles:assign | same (plus guard) |
| GET | /api/v1/apps, /apps/{key} | admin:apps:read | same |
| POST/PATCH/DELETE | /api/v1/apps, /apps/{key} | admin:apps:write | same (plus `admin:*` for admin origins, `org_mode` pinned) |
| GET | /api/v1/apps/{key}/permissions | admin:apps:read | same |
| POST/PATCH/DELETE | /apps/{key}/permissions[/{id}] | admin:roles:manage | same (plus `admin:*` for is_admin / wildcard) |
| POST/PATCH/DELETE | /apps/{key}/roles[/{id}] | admin:roles:manage | same (plus `admin:*` for admin-app roles) |
| PUT | /apps/{key}/roles/{role_id}/permissions | admin:roles:manage | same (plus `admin:*` guard) |
| GET | /api/v1/apps/{key}/roles | none (public, D17) | public allow-list |

The ticket also mentions `admin:apps:*`. Keep `read` and `write` instead, because `admin:apps:*` would look like a resource wildcard and `permits()` does not support those.

## Recommendations

1. **One source of truth per route.** Replace `permission(key)` with `require_permission(key)`. It returns a `Depends` that enforces the check and also sets the `x-permission` value in `openapi_extra`, so the declaration and the enforcement cannot drift apart. Apply it per route through `dependencies=[...]`. Also add a router-level `dependencies=[Depends(current_admin)]` on each admin router as defence in depth. `current_admin` caches per request, so it runs once.
2. **`current_admin` dependency.** It reads only `Authorization: Bearer` through `HTTPBearer(auto_error=False)` and never looks at cookies (security note N-d). It verifies the same way as `tests/jwt_verifier.py` (`typ`, `kid` from `app.state.key_set`, ES256, `aud=admin`, `iss`, `exp` with leeway, `email_verified`). It then reloads the user and requires `status=active`. Permissions come from the database via `AuthzService.effective_permissions(sub, "admin")`, not from token claims, so a revoked permission takes effect immediately. This is a choice for the planner to confirm.
3. **401 shape.** Add `UnauthorizedError(AppError)` with status 401 and code `unauthorized`, always sending the header `WWW-Authenticate: Bearer realm="admin"`. Use one generic message for missing, malformed, expired and wrong-audience tokens. Put the RFC 6750 `error="invalid_token"` only in the header and leave `details=None` (R5/R11: say nothing about the reason). A wrong audience gets 401, as the ticket's acceptance criteria say, not 403.
4. **403 shape.** Raise `PermissionDeniedError("Missing permission")` with `details={"required": "admin:users:read"}`. The key is not secret, and this helps debugging.
5. **Deny by default plus route-enumeration test.** Walk `app.openapi()["paths"]` (and `app.routes` for routes with `include_in_schema=False`). Classify each operation:
   - public: an explicit allow-list (`/healthz`, `/readyz`, `/.well-known/jwks.json`, `GET /api/v1/apps/{key}/roles`, and the five public `/auth` POSTs);
   - cookie: the `/api/v1/auth/*` routes in a second explicit list;
   - bearer: everything else.

   Bearer routes must have an `x-permission` value that matches `^admin:[a-z-]+:[a-z]+$` or equals `admin:*`, and must declare the `security` requirement. The test fails on any route that is not classified, so a new route with no declaration fails CI. Also do a runtime check: call every bearer route with no token and assert 401 plus `WWW-Authenticate`.
6. **OpenAPI.** Register a `bearerAuth` scheme (`type: http`, `scheme: bearer`, `bearerFormat: JWT`) through `HTTPBearer(scheme_name="bearerAuth")`. Add `401`/`403` to the `responses` of every bearer route.
7. **Flags.** Remove both flags and the `_admin_apis_gate`, and mount users, rbac and assignments unconditionally (Settings comments say "Removed by ADM-002"). Update `test_apps_api_not_mounted_by_default`. Do not ship until all `# ADM-002 guard` sites (8 in `rbac/service.py`) enforce `admin:*`. The guard needs the caller's permissions, so pass an `Actor`/`granted` value from the router into the service (R1: no HTTP objects in services).
8. **Migration.** Seed the `admin:organizations:read|write`, `admin:users:read|write`, `admin:apps:read|write`, `admin:roles:manage` and `admin:roles:assign` permissions (`is_admin=true`), with a working `downgrade()`. Allow admin-aud tokens in `TokenService`.
9. **Test fixtures.**
   - `admin_token(perms=[...])`: creates a verified active user, an admin-app role with those permissions and an assignment through `rbac_factories`, then mints a token with `app.state.key_set` (shared with the TokenService signing code).
   - `make_token(**claim_overrides)` and a raw-key signer for the expired, wrong-`aud`, wrong-`iss`, unknown-`kid`, HS256/`alg=none` and `typ` cases.
   - `admin_client` with a default `admin:*` header, so existing API tests change only their client fixture.
   - A cookie-only request to an admin route must return 401.
10. **CORS.** Add non-credentialed CORS from `apps[admin].origins` only if the admin UI is cross-origin (owner note A1). Otherwise leave it out.

## Risks

- Organizations are public in production right now. Locking them is a breaking change for anyone calling them, but that is acceptable: there are no documented consumers and the endpoints are not yet authenticated.
- If `require_permission` and the OpenAPI declaration are kept separate, they will drift. A single helper prevents that.
- Locked out after deploy: the release must include the token issuance change and the seed migration, and needs a bootstrapped super-admin (USR-02f CLI) whose email is verified (D16).
- Permission checks add one database read per admin request. This is acceptable at admin traffic levels.
- `admin:` rows created while the flags were on (F3) need review before the flags are removed.

## Open questions

1. Should permissions be read from the database on every request (recommended) or from a `permissions` claim in the token?
2. Should `admin:*` be granted only to `admin:super-admin` (USR-02d note)?
3. Should an `is_admin` grant on a role that already has assignments return 409?
4. Is the admin UI same-origin, or does it need Bearer CORS?
5. Swagger `/docs` is public. Should the OpenAPI schema stay public once admin routes are always mounted?
