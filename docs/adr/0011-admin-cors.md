# ADR-0011: Non-credentialed CORS for the Bearer admin endpoints

Status: Accepted (ADM-012 plan, 2026-11-16)
Date: 2026-11-16

## Context
The admin UI (`admin-pan`, an SPA at `https://admin-portal.mmlabs.online`) calls the Bearer admin
routes (R3 class 1) cross-origin. Spec §5.9 prerequisite (owner, 2026-11-16): the admin UI's
origin is registered on the `admin` app (`set-admin-origins`), and admin routes answer CORS for
those origins only, **without** credentials. ADR-0008 §1: admin auth is Bearer from the
`Authorization` header only, never the device cookie. ADR-0006 covers only `/api/v1/auth/*`.

## Decision
1. **One class, two instances.** `DynamicCORSMiddleware` takes a frozen `CORSPolicy`.
   `IDENTITY_POLICY` keeps ADR-0006 exactly (scope `path_prefix=/api/v1/auth/`).
   `ADMIN_POLICY` is a second instance scoped by a route matcher. Each instance has one policy and
   one scope, so no per-request choice can pick the wrong policy. The scopes are disjoint (no
   Bearer route under `/api/v1/auth/`, tested), so their order is irrelevant. Both sit inside
   `RequestIdMiddleware`.
2. **Scope by route, not prefix.** `BearerRouteMatcher` (`app/modules/admin_auth/route_classes.py`)
   walks the app's routes in router order and takes the first `Match.FULL` for the path and the
   method (the request method, or `Access-Control-Request-Method` on a preflight). The request is
   admin iff that route has `current_admin` in its dependant tree — the same definition
   `tests/test_route_auth_classes.py` uses. No full match (404/405) → no admin CORS. Built lazily
   on the first request, so every router is included.
3. **Origins: `apps[admin].origins` only.** `AppOriginRegistry.for_app("admin")` is a per-key view
   of the same cached load as the identity union (same TTL 60 s, single-flight, fail closed,
   after-commit invalidation). Only active rows; loopback dropped in staging/production. Other
   apps' origins are never echoed on admin routes. `null`, missing or repeated `Origin` = unknown;
   raw exact match as ADR-0006.
4. **Policy values.** Preflight (answered before routing and auth, 204): `Allow-Methods: DELETE,
   GET, PATCH, POST, PUT`, `Allow-Headers: Authorization, Content-Type`, `Max-Age: 600`, the
   3-part `Vary`. Actual: `ACAO: <origin>`, `Expose-Headers: Retry-After, ETag, X-Request-ID`.
   **Never** `Access-Control-Allow-Credentials`, on preflight or actual. `X-MM-App`, `Cookie` and
   `If-Match` are not allowed request headers.
5. **Every admin response** carries `Vary: Origin` (merged) and `Cache-Control: no-store` when the
   route set none, with or without `Origin`.
6. **`Max-Age: 600` is an upper bound.** After `set-admin-origins --clear`, a browser's cached
   preflight may let it send requests for up to 600 s, but each response loses `ACAO` within
   60 s, and side effects still need a valid token (TTL 900 s). Do not raise it.
7. **Catalog and JWKS unchanged.** `GET /api/v1/apps/{key}/roles` is public and server-side: no
   CORS (a preflight for `POST` on the same path is admin). `/.well-known/jwks.json` keeps its own
   `ACAO: *`.

## Consequences
- 401/403/404/409/422 envelopes are readable by the admin UI; unhandled 500s are produced by
  `ServerErrorMiddleware` outside user middleware and are not (ADR-0006 caveat).
- The admin origin is still credentialed on `/api/v1/auth/*` by design: the SPA signs in and gets
  its `aud=admin` token there with `X-MM-App: admin`.
- No CSRF surface on admin routes: admin auth ignores the cookie, and no credentials are allowed.
- Whoever serves pages on `admin-portal.mmlabs.online` can drive the admin UI flows: host takeover
  = admin compromise. DNS and tunnel ownership is the control.
- Per-process cache as ADR-0002/0006: CLI changes apply within 60 s.
