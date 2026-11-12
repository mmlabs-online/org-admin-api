# consult-api-platform-engineer — USR-03e

**Findings**
- `app/main.py` uses a static `_CredentialedCORS(CORSMiddleware)` with `allow_methods/headers=["*"]` and `allow_credentials=True`, applied to every path except `/.well-known/`. It is only mounted when `CORS_ORIGINS` is set (`.env.example` sets it to `["http://localhost:3000"]`). In production the ricotdin SPA gets no CORS headers at all.
- `AppContextService.check` (`app/modules/auth/service.py`) already does the CSRF half of D12: it requires an active app, normalizes `Origin`, drops loopback in staging/production, and returns 403. The CORS registry must use exactly the same rules. `normalize_origin` and `_is_loopback` are the single source.
- Middleware added with `add_middleware` sits inside `ServerErrorMiddleware` and outside `ExceptionMiddleware`. So AppError responses (401/403/422/429) already pass through it and get CORS headers. Unhandled 500s do not, which is a Starlette limitation.
- Commit happens in `get_session` teardown, after `AppService.update/create/delete` returns. If the cache is invalidated inside the service, a concurrent refresh can read the old row and cache it for a full TTL.
- Dockerfile runs a single uvicorn process (no `--workers`), so an in-process cache is coherent today.
- JWKS sets `ACAO: *` itself (`app/modules/tokens/jwks_router.py`).

**Recommendations**
1. Put a pure ASGI `DynamicCORSMiddleware` in `app/core/cors.py`, behind `OriginRegistry.is_allowed(origin) -> Awaitable[bool]`. Implement it in rbac as a cache of the normalized origins of all active apps (one query, a set, a 60 s TTL, an asyncio lock against stampedes). Use `normalize_origin` and drop loopback when `app_env ∈ {staging, production}`. Covers AC1 and AC3.
2. Scope the middleware to `/api/v1/auth/*` only. The role catalog and health endpoints need no credentialed CORS. Admin Bearer endpoints get no CORS until the admin UI exists. Leave `/.well-known/` untouched.
3. Preflight is `OPTIONS` with `Access-Control-Request-Method`:
   - Allowed origin: return 204 with ACAO=<origin>, `ACA-Credentials: true`, `ACA-Methods: GET, POST`, `ACA-Headers: Content-Type, X-MM-App`, `Max-Age: 600`, and `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`.
   - Unknown origin: return 204 or 403 with no ACAO header.
   - Never forward a preflight to the router.
   - On simple and actual requests, wrap `send` to add ACAO, credentials and `Vary: Origin`. Always add `Vary: Origin` and merge it with any existing value. Expose `Retry-After` through `Access-Control-Expose-Headers`.
   - Covers AC2 and AC4.
4. Invalidate the cache after commit. Set a flag in `session.info` from the rbac service, then call the registry's `invalidate()` from a SQLAlchemy `after_commit` listener. The other option is to invalidate in the router after the dependency commits.
5. Drop `CORS_ORIGINS`, its validator and the tests that use it (`test_config` wildcard, the `test_jwks` cors_origins fixture). If the admin UI later needs CORS, source it from `apps[admin].origins` without credentials. Update `.env.example` and `docs/deploy.md` (AC5, AC6).
6. Tests. Use a fake registry for the middleware unit tests and the real DB for one integration test. Cover:
   - preflight allowed vs unknown origin
   - credentialed echo and `Vary`
   - 401/403/429 body readable cross-origin
   - disabled app's origin not echoed
   - loopback rejected when `app_env=production`
   - invalidation after a PATCH of an app
   - JWKS still `*`
   - a non-auth path gets no ACAO

   Estimated size: about 150 LOC of code and 200 LOC of tests.

**Risks**
- 500s carry no CORS headers, so the SPA sees an opaque network error. That is acceptable but should be documented. Don't add a catch-all that could leak details.
- The cache is per process. If `--workers` or replicas are added later, staleness is up to the TTL. Document this next to ADR-0002, which has the same caveat for the rate limiter.
- A DB outage during a refresh must fail closed (no ACAO) and must not crash every request. Keep the last good set for a short time.
- An origin can be echoed through CORS for app A while the request sends `X-MM-App: B`. This is fine because CSRF is enforced per app by `check`.

**Open questions**
- Owner: drop `CORS_ORIGINS` entirely (recommended) or keep it for the future admin UI?
- Should an unknown-origin preflight return 204 with no headers or 403? I lean towards 204 because it is simpler and leaks nothing.
- Is a TTL of 60 s with after-commit invalidation acceptable, or should it be 30 s?
