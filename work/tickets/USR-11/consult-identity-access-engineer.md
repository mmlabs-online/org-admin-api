# USR-11 consult: identity-access-engineer

## Findings

**How `current_admin` works** (`/home/user/org-admin-api/app/modules/admin_auth/deps.py`)
1. It reads the token from the `HTTPBearer(scheme_name="bearerAuth", auto_error=False)` header only. It never reads the cookie or a query parameter. If no header is sent, it returns 401 `unauthorized` with `WWW-Authenticate: Bearer realm="admin"`.
2. It calls `verify_access_token(..., audience="admin", issuer=settings.jwt_issuer)`. This checks typ, ES256, kid, signature, a string-only `aud` (strict), `iss`, `exp`/`iat`, required claims and `email_verified`. Any failure gives 401 with `, error="invalid_token"`. A token with another `aud` therefore gets 401 automatically.
3. `sub` must parse as a UUID, otherwise 401.
4. `AdminAuthService.principal()` (`/home/user/org-admin-api/app/modules/admin_auth/service.py`) reloads the user on every request. If the user is missing, not `active` or has no `email_verified_at`, it raises `UnauthorizedError`, which becomes the same 401. It then calls `AuthzService.effective_permissions(user.id, "admin")` with no `org_id`, and returns `AdminPrincipal(user_id, frozenset(permissions), token_id)`. The principal carries no roles.

**How `require_permission(key)` works**
- It validates the key against `^admin:(<res>:<act>|\*)$`.
- It returns route kwargs: `dependencies=[Depends(RequirePermission(key))]`, `openapi_extra={"x-permission": key}` and `responses` 401/403, copied from `_ERROR_RESPONSES`.
- `RequirePermission` uses `permits()` (`/home/user/org-admin-api/app/modules/rbac/authz.py`): an exact key or `admin:*` passes. There is no global `*` and no `admin:<res>:*`. A failure gives 403 `permission_denied`, `details.required`.
- FastAPI caches dependencies per request, so `current_admin` runs once even though both the router and `RequirePermission` depend on it.

**How effective permissions are computed** (`RbacRepository.effective_permission_keys` / `effective_role_keys`)
- Both are DISTINCT and sorted, through `_active_assignments`, so expired assignments are excluded.
- With `org_id=None` only app-scope assignments count. Org-scoped `admin` assignments are ignored by enforcement.
- A second check drops keys that are not prefixed `admin:`.
- `admin:*` is returned unexpanded.
- Cost per admin request: one user lookup plus one permission query.

**Deny-by-default test** (`/home/user/org-admin-api/tests/test_route_auth_classes.py`)
- A route is classified only if it is in PUBLIC|COOKIE or has `current_admin` and exactly one `RequirePermission`.
- `guarded == set(BEARER)`.
- The OpenAPI test requires `security == bearerAuth` and both `401` and `403` for every BEARER route, and forbids `x-permission` and bearerAuth on every other route.
- The runtime sweeps (401 without a token, 401 with only a cookie) and the CORS matcher check iterate over BEARER only.
- **The new route will fail `test_every_route_is_classified` and `test_bearer_route_permission_map` today. That is intended: the exception has to be explicit.**

**Other code paths affected**
- **CORS and caching.** `BearerRouteMatcher` (`/home/user/org-admin-api/app/modules/admin_auth/route_classes.py`, used by the admin CORS scope in `app/main.py`) classifies a route by `current_admin` alone. So any route that depends on `current_admin` automatically gets admin non-credentialed CORS. `/home/user/org-admin-api/app/core/cors.py:116` adds `Cache-Control: no-store` when the response sets no cache header.
- **OpenAPI security.** `bearerAuth` is attached automatically through the `Security(bearer_scheme)` in `current_admin`. A route without `require_permission` will not get the 401 response entry, so it has to be added explicitly.
- **Behaviour today for the cases you asked about:**
  - Disabled, unverified or deleted user: 401 on every admin route (not 403).
  - Revoked or expired roles: the token stays valid until `exp` (`ACCESS_TOKEN_TTL_SECONDS`, default 900 s), the principal has empty permissions, and every permissioned route returns 403.
  - The token endpoint (`/home/user/org-admin-api/app/modules/tokens/service.py`) refuses a new `aud=admin` token with `not_member` when the user has no app-scope admin roles.

## Recommendations

1. **Auth class: Bearer self-scoped, as an explicit narrow exception (AC1, AC4, R3).** Keep `current_admin` as the only authenticator; do not invent a placeholder permission. Make the class detectable in code and also listed in the test:
   - Add `admin_self_scoped()` in `admin_auth/deps.py`, returning route kwargs:
     - a marker dependency `SelfScoped()`, a no-op callable class like `RequirePermission`, so the test can find it in the dependant tree;
     - `openapi_extra={"x-auth": "bearer-self"}`, or whatever extension name the planner picks;
     - `responses={401: dict(_ERROR_RESPONSES[401])}`, as a copy so `test_schema_generation_does_not_mutate_shared_dicts` keeps passing.
   - The helper should refuse non-GET use if it can. Otherwise the test enforces it.
2. **Test changes (AC4, AC6)** in `/home/user/org-admin-api/tests/test_route_auth_classes.py`:
   - Add `BEARER_SELF = {("GET", f"{V1}/me/permissions")}`.
   - Classification rule: a route is valid iff it is in PUBLIC|COOKIE, or it has `current_admin` and exactly one `RequirePermission` and no `SelfScoped`, or it is in `BEARER_SELF` and has `current_admin`, zero `RequirePermission` and exactly one `SelfScoped`.
   - Fail in two cases: a route has the `SelfScoped` marker but is missing from `BEARER_SELF`, or a `BEARER_SELF` entry lacks the marker. Either way an accidental permission-free admin route still fails.
   - Additional assertions:
     - every `BEARER_SELF` entry is GET and has no path parameters (so no subject can be chosen);
     - `guarded == set(BEARER) | BEARER_SELF`;
     - the OpenAPI operation has bearerAuth security and `401`, and has no `403` and no `x-permission`;
     - the runtime sweeps (401 without a token, 401 with only a cookie) and `test_cors_matcher_agrees_with_bearer_map` include `BEARER_SELF`;
     - the new route is not under `/api/v1/auth/`.
3. **Handler and service shape (R1, AC1)**
   - Use a router with `dependencies=[Depends(current_admin)]`, as ADR-0008 §5 requires for defence in depth.
   - The handler takes `principal: AdminDep` and calls a service method, for example `AdminAuthService.self_view(principal)`.
   - `permissions = sorted(principal.permissions)`: the exact set enforcement used on this request. Do not run a second permission query.
   - `roles = await AuthzService.effective_roles(principal.user_id, "admin")` with `org_id=None`, matching enforcement.
   - Do not add roles to `AdminPrincipal`: that would add a query to every admin request.
   - Response model `{user_id: UUID, roles: list[str], permissions: list[str]}`, with no email or name. It is a single document, not `Page[T]`.
   - Set `Cache-Control: no-store` explicitly on the response (AC2), even though the CORS middleware also adds it, and test the header.
   - No audit entry: the endpoint is read-only (R4). Log nothing beyond the existing denial logs.
4. **Disabled users, revoked roles, super-admins (AC3)**
   - A disabled or unverified caller gets 401 `unauthorized` (`invalid_token` challenge), identical to other admin routes. This comes free from `current_admin`; test it.
   - Revoked or expired roles: return **200 with `roles: []`, `permissions: []`**, not 403. It is truthful, matches the 403s the caller gets everywhere else, and lets the UI show "no access". The next `/auth/token` returns `not_member`.
   - A grant made after the token was issued shows up on the next call, because permissions are reloaded per request.
   - Super-admin: return `["admin:*"]` unexpanded with `roles` such as `["admin:super-admin"]` (AC1). Test this.
5. **Consumer docs (AC5).** In `/home/user/org-admin-api/docs/consumers.md`, "Calling the admin API from the admin UI":
   - This endpoint is a UI hint only; the API still enforces everything and returns 403.
   - Client matching rule: `has(k) = perms.includes(k) || perms.includes("admin:*")`. There is no other wildcard.
   - Writes guarded by `admin:*` beyond the route permission (403 `admin_wildcard_required`) and the self-action refusals (`self_assignment_forbidden`, `self_status_change_forbidden`) cannot be inferred from the list. The UI must still handle 403.
   - Call it again after token refresh and after any 403.
6. **R9.** `/api/v1/me/permissions` is not `/api/v1/<plural-noun>` and is not a `Page[T]`. Record it as an R9 exception next to D18. Alternatively, `GET /api/v1/me` returning the same document would leave room for later fields.
7. **Draft wording for the amendments** (the owner confirms; the spec wins, so §5.3 must change too):
   - **ADR-0008, new amendment "Self-scoped admin reads (USR-11)":** "One explicit exception to §5/§6: `GET /api/v1/me/permissions` requires Bearer `aud=admin` through `current_admin` (all of §2–§4 apply: per-request reload, 401 for disabled or unverified users) and declares no `admin:*` permission. It returns only the authenticated principal's own `user_id`, app-scope admin role keys and effective admin permissions (`sorted(principal.permissions)`, `admin:*` unexpanded), with `Cache-Control: no-store`. A self-scoped route must be GET, take no path or query parameter that selects a subject, return only data about the caller, contain no PII beyond the caller's id, and carry the `admin_self_scoped()` marker. Each such route is listed in this ADR and in the `BEARER_SELF` allow-list; adding one needs an amendment. Self-scoped mutations (for example revoking one's own sessions, §5.10 OQ7) are out of this exception. Tokens still carry no permissions claim."
   - **R3 class (1):** "… + a declared `require_permission("admin:…")`, **except the self-scoped reads listed in ADR-0008 (USR-11), which declare no permission and return only the caller's own data**."
   - **Spec §5.3:** "Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, except self-scoped reads of the caller's own authorization (`GET /api/v1/me/permissions`, ADR-0008)."
   - **D14 / decision log:** add a row or a note in `/home/user/org-admin-api/docs/identity-architecture.md`.

## Risks
- **Precedent creep.** A "no permission" Bearer class becomes a convenient bypass. The marker, the explicit allow-list and the GET-with-no-subject-parameters test limit this; reviewers should reject any self-scoped route that takes a subject id.
- **Disclosure.** A token holder learns its own permission set. That is negligible: it could probe the routes and read 403 `details.required` anyway, and the response contains no PII.
- **The UI treating the list as authority, or caching it stale.** Mitigated by `no-store`, the documented 403 handling, and the fact that the server enforces everything regardless.
- **Roles vs permissions skew.** These are two reads (the principal's permissions, then roles), so a concurrent revoke between them could make the lists disagree for one response. Cosmetic and acceptable; it is the same class of risk ADR-0008 already accepts.
- **Org-scoped admin assignments.** They are ignored by enforcement and must be ignored here too. If they are shown, the UI over-promises.
- **CORS scope widens automatically.** Any route with `current_admin` is in the admin CORS scope, which is correct here but worth a line in the ADR.

## Open questions (owner)
1. Do you confirm the exception, with the ADR-0008, R3 and §5.3 wording above, or prefer an alternative? The only real alternative is a new permission such as `admin:self:read` granted to every admin role, which adds a migration and a data dependency, and fails when an operator forgets to grant it.
2. Path: `/api/v1/me/permissions` (as in the ticket), or `/api/v1/me` as an extensible document? Either way it is an R9 exception.
3. A caller with no remaining admin roles: 200 with empty lists (recommended) or 403?
4. Should `roles` return keys only (recommended; names would need the admin catalog, which D17 keeps closed) or also display names?
