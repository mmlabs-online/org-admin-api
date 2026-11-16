USR-11 consult (api-platform-engineer, read-only). I read the ticket, ADR-0008, `app/modules/admin_auth/*`, `app/modules/rbac/authz.py`, `app/api/router.py`, `app/core/{openapi,cors}.py`, `tests/test_route_auth_classes.py`, `tests/test_openapi_contract.py` and `docs/consumers.md`. I could not see `app/main.py` router order in full, `app/modules/rbac/schemas.py`, or `docs/spec-v1.md` §5.3 wording.

## Findings

1. **No `/me` or `/auth/*` precedent exists.** All admin routers hang off `/api/v1/<plural-noun>` with `dependencies=[Depends(current_admin)]`. They are mounted in `app/main.py` (lines 102-108), and `api/router.py` only aggregates the organizations, auth and catalog routers. `/api/v1/auth/*` is the cookie class and the identity-side CORS scope (credentialed, `X-MM-App`). Admin Bearer CORS is non-credentialed and is selected by `BearerRouteMatcher`, which tests for `current_admin` in the route's dependant tree. `tests/test_cors_matcher_agrees_with_bearer_map` asserts that no Bearer route is under `/auth/`, so the path must stay outside `/api/v1/auth/` (the ticket already says this).

2. **Everything needed already exists.**
   - `AdminAuthService.principal()` reloads the user on every request and calls `AuthzService.effective_permissions(user_id, "admin")`. That returns sorted keys with `admin:*` unexpanded, and `AdminPrincipal.permissions` is a `frozenset`, so the endpoint must `sorted()` it.
   - `principal` has no roles. `AuthzService.effective_roles(user_id, "admin")` returns sorted role keys such as `admin:superadmin`.
   - Enforcement and the endpoint would therefore share one permission code path. The only extra call is roles, which is a second query on the same session.
   - Gotcha: calling `effective_permissions` a second time would diverge from `current_admin`'s snapshot. Use `principal.permissions` (one snapshot per request, consistent with what `RequirePermission` just enforced) and fetch only roles.

3. **The auth-class deny-by-default test needs a real design change, not just an allow-list line.** `test_every_route_is_classified` requires Bearer routes to have exactly one `RequirePermission`. `test_bearer_route_permission_map` computes `guarded = routes - PUBLIC - COOKIE` and requires `guarded == set(BEARER)` with a permission string. `test_openapi_bearer_scheme` applies to `BEARER` only, and asserts that non-BEARER routes have no `bearerAuth` in `security`. `test_cors_matcher_agrees_with_bearer_map` iterates `BEARER`. So a fourth allow-list `SELF = {("GET", f"{V1}/me/permissions")}` must be threaded through all of these:
   - classified if `has_admin and (len(keys)==1 or route in SELF)`, and for SELF `keys == []`;
   - exempt from `guarded == set(BEARER)`;
   - OpenAPI: `security == [{"bearerAuth": []}]`, 401 present, no `x-permission`;
   - the runtime sweeps (`401 without token`, `401 with cookie only`) and the CORS matcher test should include it;
   - `test_public_and_cookie_routes_have_no_bearer_guard` must not include it.

   Also add a negative test: SELF entries must have no `RequirePermission`, so a later edit cannot silently drop the guard.

4. **Self-scoped is not unprotected.** `current_admin` still enforces `aud=admin`, active user, verified email, and the per-request reload. The endpoint discloses only the caller's own already-held grants, with no resource ids or other users' data. It does not widen the attack surface beyond what the token holder could learn by probing each endpoint for 403.

5. **`Cache-Control: no-store` is not guaranteed by the CORS middleware.** `DynamicCORSMiddleware` only adds it when the request is in scope and the header is absent. Set it explicitly in the route (`response.headers["Cache-Control"] = "no-store"`, as `auth/router.py` does), so it also holds for non-browser callers and any CORS path.

6. **Other consumers:** ADR-0008 §6 and R3 list the exception only implicitly. R3 text says "declared `require_permission`" and spec §5.3 says "every admin endpoint declares the permission it needs". The conflict is real; an exception is cleaner than inventing a placeholder permission such as `admin:me:read`.

## Recommendations

**R1. Path and naming (R9).**
- Use `GET /api/v1/me/permissions`.
- `me` is a singular, non-plural noun, so it needs a recorded R9 exception, like D18 (`apps/{key}`). Record it in the same ADR-0008 amendment and in R9's exceptions sentence.
- Alternatives considered:
  - `/api/v1/users/me/permissions`: it fits the plural-noun rule, but `users` routers are guarded per-route with `admin:users:*`, and `{user_id}` is a UUID path param. FastAPI would match `/users/{user_id}` first and 422 on "me" unless the route is registered before it. It also tempts a future reader to treat it as a users-permission endpoint. Reject.
  - `/api/v1/auth/me`: this is the cookie scope and breaks the disjoint CORS scopes. Reject.
  - `/api/v1/admin/me`: a new top-level noun with no precedent. Reject.
  - Keep `/me/permissions`. It reads as "my effective permissions" and leaves room for `/me` later. Do NOT add a bare `GET /me` now (R7); it would invite PII (email) creep.

**R2. Response schema.** `MePermissions` (snake_case, `extra="forbid"` style like the other schemas):
- `user_id: UUID`
- `roles: list[str]` (admin-app role keys, sorted, e.g. `admin:superadmin`)
- `permissions: list[str]` (sorted, `admin:*` unexpanded, e.g. `["admin:*"]`)
- It is a single document, not `Page[T]`. That needs a one-line R9 exception beside D18's, since the lists are bounded by the role catalog.
- No `email`, `display_name`, `status`, or `expires_at`.
- Document in the schema description that the client must treat `permissions` as a hint, evaluate `admin:*` as matching every `admin:...` key (same rule as `permits()`), and tolerate unknown keys (new permissions are additive).
- Return 200 with empty `roles` and `permissions` for a valid staff-less admin token holder; do not 403. A principal with no permissions is still authenticated. Note that only users with an admin role can obtain an `aud=admin` token, so this is an edge case after revocation (token valid up to 15 min). It is the correct "revocation visible on next request" behaviour and should be tested (AC 3).
- Disabled or unverified caller: `current_admin` already gives 401 (identical to other endpoints). Do not special-case.

**R3. Module placement.**
- Create `app/modules/admin_auth/me_router.py` (router `prefix="/me"`, `tags=["me"]`, `dependencies=[Depends(current_admin)]`) with schema `MePermissions` in `admin_auth/schemas.py`. `admin_auth` already owns `current_admin`, `AdminPrincipal` and `AdminAuthService`; this is exactly "who am I as an admin".
- Add `AdminAuthService.me(principal)` (or a thin method) that calls `AuthzService.effective_roles` and returns sorted data. Keep SQL in the existing `RbacRepository` (R1). The router is HTTP only.
- Do not use `users` (it is `admin:users:*`-guarded) or `rbac` (catalog and assignments).
- Mount it in `app/main.py` next to the other `app.include_router(..., prefix=settings.api_prefix)` calls.
- Be careful with import cycles: `route_classes` imports `deps`, `deps` imports `service`, so `me_router` importing from `deps` and `service` is fine.

**R4. Auth class / exception record.**
- Add an ADR-0008 amendment (dated 2026-11-16, USR-11).
  - Admin routes declare `require_permission`, except explicitly listed self-scoped routes in the test's `SELF` allow-list.
  - The class is "Bearer, self-scoped, no permission".
  - Criteria: returns only data about the authenticated principal, no PII, and no other-user ids or resource data.
  - Adding to `SELF` requires owner approval, and the test file docstring should say so.
- Update the R3 wording in `CLAUDE.md` and spec §5.3 only as a short pointer ("plus the self-scoped routes listed in ADR-0008"). The planner flags this for the owner (the ticket says the owner confirms).
- Alternative (rejected): `admin:me:read` or `admin:*`-free placeholder. A placeholder permission that all staff must be granted would make the endpoint 403 for staff without it, which defeats the purpose, and would add a permission row and migration.

**R5. Tests.**
- Cover the ticket list: 401 missing token, 401 wrong `aud` (token minted for another app), own data only (two staff users, assert no leakage), role grant and revoke visible on the next request, `admin:*` returned unexpanded and sorted, disabled user 401, and a cookie-only 401 via the shared sweep.
- Also assert `Cache-Control: no-store` on 200 and on 401, and the exact response keys (no extra fields).
- Add a unit assertion that `SELF` routes have no `RequirePermission`.
- Per R8 there is no 404 or validation case for a no-input GET. Note that explicitly (a query param `?user_id=` must be ignored; add a test that it cannot read another user).

**R6. OpenAPI after ADM-013.**
- Declaring the route with `dependencies=[Depends(current_admin)]` and `responses={401: {"description": "Missing or invalid Bearer token"}}` gives the ADM-013 envelope automatically (`_postprocess` rewrites every >=400 response to `ErrorEnvelope`, and adds `X-Request-ID`).
- Add `response_model=MePermissions`.
- Do not add 403 (nothing can return it), since `test_every_error_response_references_envelope` only checks what is documented.
- Add the explicit, accurate 401 only. Include `operationId` via the existing naming convention (e.g. `get_my_permissions`), and a description pointing at the ADR.
- The security scheme appears automatically (`bearerAuth`), because `current_admin` uses `HTTPBearer`.
- Add `"/api/v1/me/permissions"` to `test_openapi_contract`'s `checked` assertions.
- Do not add an `x-permission` extension. If a consumer tool relies on `x-permission`, optionally add `x-self-scoped: true` via `openapi_extra`. This is not required; the test would then forbid unknown `x-permission` only, so it is safe, but skip it unless admin-pan asks (R7).

**R7. docs/consumers.md.**
- Add a bullet in "Calling the admin API from the admin UI" (lines 63-81), after the 401 bullet, e.g. "To learn the signed-in staff member's own user id, roles and permissions, call `GET /api/v1/me/permissions` with the same Bearer token."
- Spell out the contract:
  - fields and example response;
  - `admin:*` is a wildcard meaning all `admin:` permissions (clients must expand it themselves, the same as `permits()`);
  - it is a UI hint only, the API enforces every call (403 `permission_denied`);
  - not cached (`no-store`);
  - re-call after `/auth/token` refresh or a role change; a change shows on the next request, the token keeps no permissions claim;
  - 401 behaviour is the same as every admin endpoint.
- Also mention that the user must not decode the token for `sub`.
- No new top-level section.
- Also correct line 74-75 ("after a role change"): the permissions endpoint makes this unnecessary for permissions, and the token's role claim may lag (up to 15 min). Say that this endpoint is the source of truth for UI gating, not the token's `roles`.

**R8. Compatibility policy.** The response is additive-only within v1: new fields allowed, none removed or renamed, `permissions` stays a sorted string array. State in the schema docstring that adding fields is non-breaking.

## Risks
- UI/server drift: if admin-pan expands `admin:*` differently from `permits()`, it will show wrong controls. Mitigate in docs (consumers.md) and give the exact rule; do not expand server-side (the ticket requires unexpanded).
- A self-scoped allow-list tends to grow. Gate additions behind ADR/owner approval (R4), with a test that checks SELF contains exactly this one route.
- Roles and permissions come from two queries; they are read in the same transaction (`SessionDep`), but a concurrent grant between them could give an inconsistent pair. The ticket's AC wants permissions from enforcement logic, so accept this and note it. `principal.permissions` plus a separate `effective_roles` query is acceptable; optionally compute roles first and permissions after if consistency matters. Low impact.
- Information disclosure to a stale token: a token for a user whose roles were just revoked returns empty `roles`/`permissions` (200), not 401. This is intended, but UIs should treat empty permissions as "no access" and show a re-login prompt.
- Rate limiting: no limiter is mentioned for admin routes. Not needed here (cheap), but it is a per-request DB hit that the UI may call frequently; advise the UI to call it on session start and after role change, not on every navigation.
- R3 and spec wording will disagree with the code until the amendment lands; flag it in the plan and PR for owner approval.

## Open questions (for the planner / owner)
1. Owner approval of the exception (ADR-0008 amendment plus R3/§5.3/R9 wording): is "Bearer, self-scoped, no permission" accepted as a named class, and is `me` (singular) acceptable under R9?
2. Should `roles` carry only admin-app role keys, or also scope info (org-scoped assignments)? Recommend keys only. Admin assignments are app-scope (admin-app roles have no org scope, to be confirmed by the planner in `rbac/models.py`).
3. Should the response include `expires_at` of the earliest expiring role assignment so the UI can warn? Recommend no for v1 (R7).
4. Where is `docs/spec-v1.md` §5.3 to be updated, and does the owner want a spec edit now or only the ADR plus a follow-up? (I did not read §5.3.)
5. Does admin-pan need the endpoint to also work with `Origin`-less non-browser calls? Behaviour is already the same as other admin routes, so none is expected.

Relevant files:
- /home/user/org-admin-api/work/tickets/USR-11/ticket.md
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/app/modules/admin_auth/deps.py
- /home/user/org-admin-api/app/modules/admin_auth/service.py
- /home/user/org-admin-api/app/modules/admin_auth/route_classes.py
- /home/user/org-admin-api/app/modules/rbac/authz.py
- /home/user/org-admin-api/app/main.py
- /home/user/org-admin-api/app/core/openapi.py
- /home/user/org-admin-api/tests/test_route_auth_classes.py
- /home/user/org-admin-api/tests/test_openapi_contract.py
