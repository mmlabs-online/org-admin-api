## Attempt 1

# Security review: ADM-012, attempt 1 (appsec-engineer)

I read `app/core/cors.py`, `app/main.py`, `app/modules/admin_auth/route_classes.py`, `app/modules/rbac/origin_registry.py`, `app/modules/rbac/repository.py`, the docs diff, the plan, the FastAPI 0.162.2 routing internals (`iter_route_contexts`, `_IncludedRouter._match`) and `app/modules/rbac/origins.py`. I tried to run `tests/test_cors.py` DB-free as asked: all 39 tests error at setup because a conftest fixture needs the database. So I verified nothing by running tests; the reviewer's full suite covers them.

## Findings

No 🔴 and no 🟡.

### 💭 Notes

1. **Only the admin app's own origins are echoed on admin routes. Confirmed.**
   - `app/modules/rbac/origin_registry.py:62-65,89-93`: `for_app("admin")` reads only `_by_key["admin"]`. The union of all apps' origins is never read by the admin policy.
   - Origins come from active rows only and go through `allowed_origins` (`origins.py:70-74`). That function drops loopback when `app_env` is `staging` or `production`. `app_env` is a validated `Literal` (`config.py:29`), so a typo in `APP_ENV` cannot switch the filter off.
   - A registry load failure clears both views (`:83-88`), so CORS fails closed.
   - `cors.py:93-95`: a missing `Origin`, two `Origin` headers, or `null` counts as unknown. Matching is raw and exact against stored origins, which are already lowercase with the default port removed. Uppercase variants, a trailing `/`, an explicit `:443` and any other port are all refused.
   - Impact is limited even if an origin were wrongly allowed: CORS here carries no credentials and the token is never sent automatically. A wrong origin would only gain the ability to read responses for a token it already holds.

2. **`Access-Control-Allow-Credentials` cannot appear on an admin path.**
   - It is written only when `policy.credentials` is true (`cors.py:113,137`), and `ADMIN_POLICY.credentials=False` (`:43`).
   - The identity instance acts only on paths starting `/api/v1/auth/`. No Bearer route lives under that prefix: router prefixes are `/apps`, `/users`, `/organizations`, `/audit-logs`, and no route at the `/api/v1/{param}` level could match an `/auth/…` path. So no response gets headers from both instances.
   - Error envelopes (401 with `WWW-Authenticate`, 403, 404, 422) are produced by the exception handlers inside the user middleware, so they also pass through the admin middleware and get its headers.
   - Unhandled 500s get no CORS headers at all. This is the documented ADR-0006 caveat and it fails closed.

3. **Route classification matches the router. No bypass found.**
   - `route_classes.py:41-55` flattens `app.routes` with FastAPI's own `iter_route_contexts`. That gives the same depth-first order and the same `matches()` that the router uses, and the first `Match.FULL` decides in both. Router and matcher therefore always agree on which route a request hits.
   - Trailing `/`, `//`, uppercase paths and `%2F` all fail the regex in both. Those requests get a 404 or 307 with no CORS headers.
   - `HEAD` is not added to FastAPI `APIRoute` methods, so a `HEAD` request gets a 405 with no CORS headers. The plan's assumption that "HEAD is classified admin" is wrong, but the effect is harmless.
   - The role catalog `GET /apps/{key}/roles` is public, so it is not matched and gets no CORS. A preflight for `POST` on the same path is classified admin.
   - The FastAPI docs and OpenAPI routes are not `APIRoute`s, so they get no CORS.
   - The probe scope is a shallow copy. FastAPI's `setdefault("fastapi")` writes only into the copy and restores its own keys, so the real scope is not changed.
   - The route list is built lazily on the first request (`:48-49`). A router included after that would be treated as not admin, which also fails closed.

4. **The `/auth` instance is unchanged.**
   - Every line removed from `cors.py` maps one-to-one onto `IDENTITY_POLICY` (`:34-41`): same methods, headers, Max-Age, Expose and credentials. The scope check is still `startswith(prefix)` (`:79`).
   - Nothing still references the removed `ALLOW_*`, `MAX_AGE_SECONDS`, `EXPOSE_HEADERS` constants or the old `active_app_origins` method.
   - The union is built the same way as before. It still includes the admin origin, which is intended (ADR-0011, Consequences).
   - Separately, `/auth/token` still checks per request that the `Origin` is registered for the `X-MM-App` app (`auth/service.py:95`). A stoktok page therefore cannot mint an `aud=admin` token.

5. **Preflights are answered before routing and auth.**
   - An in-scope preflight is answered with a 204 and is never routed (`cors.py:99-101`), so no auth dependency runs.
   - The method must be one of the five listed, in uppercase. Requested headers are compared case-insensitively against `authorization` and `content-type`, so `x-mm-app`, `if-match` and `cookie` are refused.
   - A preflight whose requested method the route does not serve is not classified admin. It falls through to the router, which answers 405 without running the handler or any dependency. Nothing slips through.
   - A lowercase `patch` (fetch does not uppercase PATCH) gets a 405, which is a usability matter only.

6. **Caching headers are correct.**
   - Preflights carry the three-part `Vary`.
   - Every in-scope actual response gets `Vary: Origin` (merged with any existing value) and `Cache-Control: no-store` when the route set none. No Bearer route sets `Cache-Control`.
   - `Max-Age: 600` is documented as an upper bound (ADR-0011 §6).
   - Small hardening idea: the 405 that a wrong-method preflight gets carries no `Vary`. It does not depend on `Origin`, so this is harmless.

7. **Expose-Headers leaks nothing sensitive.** `Retry-After, ETag, X-Request-ID` are not sensitive. `WWW-Authenticate` is not exposed, which is fine because the UI uses the error code in the envelope.

8. **The docs advice is sound.**
   - `docs/consumers.md` says: `credentials: "omit"`, never send `X-MM-App` to admin endpoints, keep the token in memory only, refresh about 60 s before `expires_in`, retry once on a 401 and then go to login.
   - `docs/deploy.md` gives the operator steps: dry-run first, wait for the 60 s TTL, set `TURNSTILE_ALLOWED_HOSTNAMES` in the deployment repo, then a curl check.
   - Optional addition: say that no admin call should put the token in a URL or query string. The server already ignores it there (ADR-0008).

9. **Per-request cost.** The admin matcher runs about 40 regex matches on every HTTP request, including `/auth` and health checks, because it is the outer instance. This is negligible.

## Follow-ups (out of scope)
- `X-Content-Type-Options: nosniff` on admin JSON responses (already in the plan's follow-ups).
- Make `tests/test_cors.py` runnable without the database (its autouse DB fixture). The unit tests are pure-ASGI, and right now they cannot be run on their own as a quick check.

**Verdict: PASS. No exploitable path found for an origin other than the admin app's, for `Access-Control-Allow-Credentials` on admin routes, or for a classification or preflight bypass. The `/auth` behaviour is unchanged.**
