# consult-api-platform-engineer — ADM-012

**Findings**
- `DynamicCORSMiddleware` (app/core/cors.py) gates on one `path_prefix` (`/api/v1/auth/`). Its credentials flag, the methods `GET, POST`, the headers `Content-Type, X-MM-App`, `Max-Age 600` and `Expose: Retry-After` are hard-coded. It adds `Vary: Origin` and `Cache-Control: no-store` (if unset) to every in-scope response, with or without an `Origin`. It answers preflights itself with 204 and never routes them.
- `AppOriginRegistry` caches the union of the origins of **all** active apps, `admin` included. So `admin-portal` already gets credentialed CORS on `/auth/*`, which is intended: the admin UI logs in through `@mmlabs/auth-client`. Reusing this registry for admin paths would echo stoktok and calechip origins and break the ticket's "only `apps[admin].origins`" criterion. Invalidation uses a module weakset plus `after_commit`. `set_admin_origins` calls `mark_apps_changed`, but the CLI runs in another process, so the change reaches the API through the 60 s TTL.
- Middleware order: `RequestId` (outer) → CORS → `ExceptionMiddleware` → router. AppError and HTTPException envelopes (401/403/404/409/422/429) pass through the CORS send wrapper. Unhandled 500s come from Starlette's `ServerErrorMiddleware`, which sits outside all user middleware, so they carry no CORS headers. ADR-0006 already accepts this.
- No admin route sets `ETag` or `Retry-After` today. `ETag` is set only on JWKS and the catalog, and no admin route is rate-limited.
- `GET /api/v1/apps/{key}/roles` is public, but `POST /api/v1/apps/{key}/roles` is Bearer. Same path, different auth class, so a path prefix cannot classify it. The catalog returns 404 for `admin`, and there is no Bearer GET that lists roles.
- JWKS sets `ACAO: *` itself. The catalog has no CORS.
- `tests/test_auth_cors.py::test_role_catalog_and_admin_paths_have_no_cors` asserts that `/api/v1/apps` sends no `Vary: Origin`. It will have to change.

**Recommendations**
1. **One class, two instances.** Turn the hard-coded constants into a frozen `CORSPolicy` passed in at construction: `credentials`, `methods`, `allow_headers`, `expose`, `max_age`, `registry` and a `matcher(scope, method) -> bool`. Mount it twice:
   - `/auth/*`: the current values exactly, with `credentials=True` and the same prefix matcher (no behaviour change).
   - Admin: `credentials=False`, methods `GET, POST, PATCH, PUT, DELETE`, headers `Authorization, Content-Type`, expose `Retry-After, ETag`, max-age 600.

   A second copy of the class would drift from the first. `core` still imports no module (ADR-0006 §5).
2. **Admin registry.** Use a second `AppOriginRegistry` that only loads the `admin` row. Add a `RbacRepository.app_origins(key)` (status active) or a key filter on the existing loader, and apply the same `allowed_origins` loopback rule. It joins the weakset, so in-process invalidation works for free.
3. **Classify by route, not prefix (R3).** Build the admin matcher lazily on the first request from `app.router.routes`: the `APIRoute`s whose dependant tree contains `current_admin`. Move the `_calls` walk used in `test_route_auth_classes.py` into `app/modules/admin_auth/` so the test and the middleware share one definition. Match with `route.matches({**scope, "method": m})`, where `m` is the request method, or `Access-Control-Request-Method` for a preflight. Only `Match.FULL` counts.
   - This puts `POST /apps/{key}/roles` in the admin class and keeps `GET` of the same path public.
   - Unmatched paths and wrong methods (404/405) get no admin CORS, which is acceptable.
   - The scan is about 25 routes per request, which is cheap.
4. **Preflight.** Short-circuit in the middleware before routing, so auth dependencies never run on `OPTIONS`. Check the origin, the method and every requested header strictly, as `/auth` does. Return 204 with the 3-part `Vary` and no `Allow-Credentials` ever.
5. **Actual responses on admin-matched routes.**
   - Always merge `Vary: Origin`.
   - Always set `Cache-Control: no-store` when unset, with or without an `Origin` (the criterion is "admin responses").
   - For an allowed origin, add `ACAO: <origin>` and `Expose-Headers`, and never `Allow-Credentials`.
   - Use the middleware rather than a router dependency, because a dependency cannot reach exception-handler envelopes.
6. **Order.** Keep both CORS instances inside `RequestIdMiddleware`. The two scopes are disjoint, so their relative order does not matter. Leave 500 handling as ADR-0006 has it. If the owner wants readable 500s, the CORS wrapper can catch the exception before `http.response.start`, send a 500 envelope with CORS headers, then re-raise so `ServerErrorMiddleware` still logs it. That is a scope decision.
7. **Max-Age 600.** It matches `/auth`. With the 60 s TTL, revoking an origin takes effect for browsers within about 11 minutes. Chromium caps the value at 7200 anyway.
8. **Catalog and JWKS.** Leave both unchanged and record the decision in the ADR. JWKS keeps `*`. The catalog is meant for server-side consumers, and its `GET` stays public with no CORS.
9. **Tests**, in a new `tests/test_admin_cors.py` that uses real minted tokens (`admin_auth_factories.mint`), not `admin_permissions` overrides:
   - Preflight from an admin origin returns 204 with ACAO, the three-part `Vary`, the methods, the headers, Max-Age, and no ACAC.
   - Preflight from an unknown origin, an origin of another app (stoktok), and requests with a disallowed method or a disallowed header (`x-mm-app`, `cookie`) get none of the `Allow-*` headers.
   - A real `GET /api/v1/apps` with `Bearer` from the admin origin returns 200 with ACAO, `Expose-Headers` and `no-store`. The same holds for 401 (no token, bad token), 403 (missing permission), 404 and 422.
   - No ACAC appears on any `BEARER` route (parametrized over the route map).
   - No `Origin` means no ACAO, but `no-store` is still present.
   - `POST /apps/{key}/roles` preflight is treated as admin; the catalog `GET` gets no CORS.
   - `/auth/*` is unchanged, with the existing suite green, and an admin origin still gets credentialed CORS there.
   - Registry: `set_admin_origins` in-process applies at once. An out-of-process change (a monkeypatched loader plus a `_Clock`) applies after the TTL.
   - Amend the old no-CORS test: no ACAO, but `Vary` is now present.
   - Add a unit test that the matcher and `test_route_auth_classes` agree on the `BEARER` set.
10. **Docs.** Amend ADR-0006 (scope §1, two policies, 500 caveat, catalog/JWKS decision) or add ADR-0011. Add an admin-UI section to `docs/consumers.md`: Bearer only, `credentials: "omit"`, no cookies on admin paths.

**Risks**
- If the admin CORS reused the union registry, every app's origin could read admin responses. A token is still needed, but it widens the XSS blast radius.
- Path-prefix classification would misclassify the catalog and `POST /roles`, and would silently cover future non-admin `/api/v1/*` routes.
- A lazy matcher built before all routers are included would miss routes. Build it on the first `__call__`, not in `create_app`.
- Unhandled 500s are unreadable cross-origin, so the SPA sees "Failed to fetch".
- The admin UI cannot list roles: the catalog returns 404 for `admin` and gives other apps no CORS. Role-management screens may be blocked.
- Changing the existing test needs an explicit note in the plan (R7).

**Open questions**
1. Should `X-Request-ID` (for support correlation) and `WWW-Authenticate` be in `Expose-Headers` beyond `Retry-After, ETag`?
2. Should 500s be CORS-readable on admin paths (recommendation 6), or should the ADR-0006 caveat stand?
3. The admin UI needs to read roles. Should that be a follow-up Bearer list endpoint, or admin-origin CORS on the catalog `GET`?
4. Should `If-Match`/`If-None-Match` be pre-allowed in `Allow-Headers` for future ETag concurrency, or added when needed?
