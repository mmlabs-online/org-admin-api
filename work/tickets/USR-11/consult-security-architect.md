# USR-11 security-architect consult: `GET /api/v1/me/permissions`

The endpoint is low risk if it is built as an exact allow-list exception. It must take no inputs, use the same per-request reload that `current_admin` already does, and use the existing admin CORS and cache controls. I found no blocking issues. The real risks are erosion of the deny-by-default rule and the UI trusting stale data.

## Findings

**F1. Authentication path is already right.**
- `current_admin` (`/home/user/org-admin-api/app/modules/admin_auth/deps.py`) checks the signature, `typ`, `alg` and `kid`.
- It accepts only a string `aud=admin` (`strict_aud`) and requires `email_verified`.
- It then reloads the user on every request (`AdminAuthService.principal`, `/home/user/org-admin-api/app/modules/admin_auth/service.py`): the user must be active and verified, otherwise 401.
- So a disabled or unverified caller gets the same 401 as on every other admin route, which meets AC3 for free.
- `principal.permissions` is exactly the set `require_permission` checks in the same request: `AuthzService.effective_permissions(user, "admin")`, app scope only, expired rows ignored, `admin:*` unexpanded.

**F2. Information disclosure is minimal.**
- Role keys already travel in the caller's token `roles` claim (`/home/user/org-admin-api/app/modules/tokens/service.py:92`).
- The new data is the caller's own effective `admin:*` permission keys. That is a slice of the admin permission catalog, which D17 refuses to publish.
- Exposure is limited to authenticated staff, and only to what they already hold. It gives no escalation path because the API still enforces every permission.
- Acceptable, provided the response is exactly `{user_id, roles, permissions}`. No email, no `granted_by`, no assignment ids, no org ids, no `is_admin` flags, no role names or descriptions.

**F3. No enumeration surface.**
- There is no path parameter, query parameter or body, so there is no IDOR or BOLA vector as long as nothing is ever added.
- FastAPI ignores unknown query parameters, so `?user_id=<other>` is harmless. A test should prove it still returns the caller's own data.

**F4. Caching.**
- `DynamicCORSMiddleware` with `ADMIN_POLICY` (`/home/user/org-admin-api/app/core/cors.py`) adds `Vary: Origin`.
- It also adds `Cache-Control: no-store` only when the route set none, and only when `BearerRouteMatcher` classifies the route as Bearer. It will, because `current_admin` is in the dependant tree.
- AC2 still wants the route to set `no-store` itself. That is correct defence in depth: it does not depend on matcher behaviour and survives Cloudflare in front.
- No ETag on this route.

**F5. CORS under `ADMIN_POLICY` (ADR-0011) needs no change.**
- The path is outside `/api/v1/auth/`, so the two CORS scopes stay disjoint. `test_cors_matcher_agrees_with_bearer_map` asserts this, but only over the `BEARER` dict, so the new route must be added to that loop.
- Requests are non-credentialed and echo `apps[admin].origins` only. The cookie is ignored, so there is no CSRF surface.
- Other apps' SPAs cannot read the response cross-origin. A stolen `aud=admin` token can call the route from anywhere (curl), which is no worse than any other admin route.

**F6. Deny-by-default test interaction.**
- `/home/user/org-admin-api/tests/test_route_auth_classes.py` makes four assumptions the route breaks:
  - `test_every_route_is_classified` requires `has_admin and len(keys) == 1`.
  - `test_bearer_route_permission_map` requires the guarded routes to equal `BEARER`.
  - `test_openapi_bearer_scheme` asserts that any route not in `BEARER` has no `bearerAuth`.
  - The same test requires `{401, 403}` on routes in `BEARER`.
- All four must learn about a third set. If this is done carelessly it is where deny-by-default weakens.

**F7. Token vs. live state.**
- An admin token is only issued when the user holds at least one admin role (`tokens/service.py:73`).
- After a full revoke, the token stays valid up to its TTL (900 s by default). `current_admin` does not require a non-empty permission set.
- So this endpoint is the correct, fresh source for the UI. The token's `roles` claim can be stale for up to 900 s.

**F8. No admin Bearer route is rate-limited today.**
- `rate_limit()` is per-IP and only used on public and cookie routes.

## Recommendations

**R-a (AC1, ADR-0008 §3): use the request's principal.**
- Build `permissions` from `sorted(principal.permissions)` of this request's `AdminPrincipal`, not a second call to `effective_permissions`. That guarantees the response is literally the set enforcement used.
- Load `roles` with `AuthzService.effective_roles(principal.user_id, ADMIN_APP_KEY)`, app scope only (`org_id=None`), matching enforcement.
- Do not add roles to `AdminPrincipal`; that would add a query to every admin request.
- Keep R1 layering: router, then a thin service method, then the existing authz/repository.

**R-b (AC1): strict response schema.**
- Pydantic model with exactly `user_id: UUID`, `roles: list[str]`, `permissions: list[str]`; use `extra="forbid"` on the model.
- The route has no parameters at all.
- Test that `?user_id=<other staff id>` returns the caller's data.

**R-c (AC3): empty grants return 200, not 403.**
- A caller with a valid token whose roles were all revoked gets `200 {"roles": [], "permissions": []}`.
- This is self data and grants nothing, and it lets the UI show "no access" instead of misreading a 403. Add a test.

**R-d (AC2, ADR-0011 §5): explicit no-store and no ETag.**
- Set `Cache-Control: no-store` explicitly in the route.
- Assert `Vary` contains `Origin` and that there is no `ETag`.
- Test the 401s (missing token, wrong `aud`, list `aud`) for the R6 envelope and `WWW-Authenticate: Bearer realm="admin"`.

**R-e (AC4, R3): exact allow-list exception.**
- Add `SELF_SCOPED = {("GET", f"{V1}/me/permissions")}` to `test_route_auth_classes.py`. Do not use a decorator or marker that the classifier trusts.
- Update the tests:
  - `test_every_route_is_classified`: accept a route in `SELF_SCOPED` only if `_guard(...) == (True, [])`, meaning Bearer-guarded with zero permissions. A self-scoped route that loses `current_admin` must fail.
  - `test_bearer_route_permission_map`: guarded routes == `set(BEARER) | SELF_SCOPED`.
  - `test_openapi_bearer_scheme`: self-scoped routes have `security == [{"bearerAuth": []}]`, a `401` response, no `x-permission` and no `403`.
  - `test_cors_matcher_agrees_with_bearer_map`: iterate `SELF_SCOPED` too.
  - The runtime 401 sweeps (`..._401_without_token`, `..._401_with_cookie_only`): parametrize over `BEARER` and `SELF_SCOPED`.
- No placeholder permission such as `admin:self:read`, as the ticket says.

**R-f (R3 / spec §5.3 wording; owner confirms).**
- Add an ADR-0008 amendment, "Self-scoped admin reads", that defines the exception class narrowly. An endpoint qualifies only if all of these hold:
  - `GET` only;
  - no path, query or body inputs;
  - the subject is always `principal.user_id`;
  - it returns only the caller's own authorization state, with no PII;
  - no side effects;
  - Bearer `aud=admin` through `current_admin`;
  - listed by exact (method, path) in `SELF_SCOPED`.
- Any future `/me/*` endpoint that does not meet all of these must declare a permission.
- Mirror the wording in CLAUDE.md R3 (class 1: "… + `require_permission`, except the self-scoped reads listed in ADR-0008") and in spec §5.3 line 45 ("Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, except self-scoped reads of the caller's own authorization (ADR-0008)").
- This is a spec edit, so flag it for the owner.

**R-g (R4): no audit entry.**
- Confirmed: R4 covers mutating actions only, and existing read routes (including `GET /audit-logs`) write none.
- Logging: no per-call info log needed. Never log the token. Do not log the permission list.

**R-h: no rate limit in this ticket.**
- Every call is authenticated and costs about three indexed queries, the same as any admin GET.
- A per-IP limit would penalise staff behind one NAT, and no other Bearer route has one.
- Record a follow-up for a general per-`sub` admin limit if wanted.
- The consumer guide should tell `admin-pan` to fetch once per token and re-fetch after any 403 or token refresh, not on every navigation.

**R-i (AC5): `docs/consumers.md` content.**
- The response is a UI hint only; the API is authoritative.
- `admin:*` arrives unexpanded, so the UI must implement the same matcher as `permits()`: exact key or `admin:*`, no `<app>:<resource>:*` and no global `*`.
- Prefer this endpoint over the token's `roles` claim, which can be stale for up to the token TTL.
- Do not persist the response (for example in `localStorage`).
- In OpenAPI, the route shows only the ADM-013 envelope on 401, with no 403.

## Risks

1. **Precedent creep (medium, governance).** The "self-scoped" exception becomes an escape hatch for permission-less routes. Mitigation: exact-tuple allow-list, the `(True, [])` assertion, and the ADR criteria in R-f.
2. **Stale UI state (low).** The UI hides or shows controls from a snapshot. This is purely cosmetic because the API enforces every permission. R-h and R-i cover re-fetching.
3. **Roles and permissions can disagree within one response (low, accept).** They come from separate statements under READ COMMITTED, so a grant committed between them can show up in one list and not the other. The response is advisory and the next request corrects it. Document it in the ADR "accepted risks" rather than adding a transaction-isolation change.
4. **Permission-key disclosure to holders (informational).** A compromised staff token learns which admin keys it holds, which it could learn anyway by probing 403s. Accept.
5. **Host takeover of `admin-portal.mmlabs.online` (existing, ADR-0011).** Unchanged by this ticket.

## Open questions

1. Owner: confirm the exception wording for CLAUDE.md R3 and spec §5.3 (R-f), or prefer a different home for the rule.
2. Planner or owner: is 200 with empty lists (R-c) acceptable for a caller with no remaining admin roles, rather than 403? I recommend 200.
3. Should the response include per-assignment `expires_at` so the UI can warn before access lapses? I would leave it out of v1 to keep the response minimal; add it later as a non-breaking field if needed.
4. Module placement: a new `app/modules/me/` module or `app/modules/admin_auth/router.py`. Security-neutral, but the router must be mounted through `create_app` so `BearerRouteMatcher` sees it.
