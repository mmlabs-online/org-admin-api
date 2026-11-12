# ADR-0006: Dynamic CORS for identity endpoints

Status: Accepted (USR-03e plan, 2026-11-12)
Date: 2026-11-12

## Context
D12 (S4): `Access-Control-Allow-Origin` echoes only origins in `apps.origins`, with credentials;
every cookie-authenticated POST checks `Origin` and requires `X-MM-App` (the CSRF half, already in
`AppContextService`). D19 limits app origins to `mmlabs.online`, its subdomains and loopback. D20
keeps the identity endpoints under `/api/v1/auth/*`. Until USR-03e a static `CORSMiddleware` read
`CORS_ORIGINS` from env, so a new app needed a redeploy and the list could drift from `apps`.

## Decision
1. **Scope.** A pure-ASGI `DynamicCORSMiddleware` (`app/core/cors.py`) covers paths starting with
   `/api/v1/auth/` (trailing slash) only. Admin routes, the role catalog, health and
   `/.well-known/jwks.json` (its own `ACAO: *`, no credentials, ADR-0005) are untouched.
2. **Matching.** The raw `Origin` must equal an origin of some active app exactly (no
   normalization) and is echoed as received. `null`, a missing or a repeated `Origin` is unknown.
3. **Actual requests.** Allowed origin: `Access-Control-Allow-Origin: <Origin>`,
   `Access-Control-Allow-Credentials: true`, `Access-Control-Expose-Headers: Retry-After`.
   Always `Vary: Origin` (merged) and `Cache-Control: no-store` when the route set none.
4. **Preflight** (`OPTIONS` + `Access-Control-Request-Method`) is answered by the middleware and
   never routed: 204, `Vary: Origin, Access-Control-Request-Method,
   Access-Control-Request-Headers`. Allow headers (`Methods: GET, POST`, `Headers: Content-Type,
   X-MM-App`, `Max-Age: 600`) only for an allowed origin, method and header list; otherwise 204
   without any `Access-Control-Allow-*`.
5. **Registry.** `AppOriginRegistry` (rbac, behind the `OriginRegistry` protocol; `app/core`
   imports no module) caches the origins of active apps per process: TTL 60 s, single-flight
   refresh, loopback dropped in staging/production (the same `allowed_origins` helper as the CSRF
   check). A load error fails closed: empty set for 5 s, warning with the exception class only.
6. **Invalidation.** App create/update/delete set a `session.info` flag; a SQLAlchemy
   `after_commit` listener invalidates every registry in the process, a root rollback drops the
   flag. Writers in other processes (CLI, SQL) rely on the TTL.
7. **`CORS_ORIGINS` removed.** A leftover value in an env file is ignored (`extra="ignore"`).

## Consequences
- New or changed app origins apply without a redeploy (at once in-process, within 60 s otherwise).
- 401/403/422/429 envelopes are readable cross-origin; unhandled 500s are produced outside the
  middleware and are not (the SPA sees a network error).
- An origin of app A is echoed for a request with `X-MM-App: B`; CSRF is still per app (403).
- Per-process cache like ADR-0002: before running more workers or replicas, staleness is up to the
  TTL unless a shared cache or pub/sub invalidation is added.
- Open owner confirmations: no `CORS_ORIGINS` for a future admin UI (its Bearer CORS would come
  from `apps[admin].origins`); whether ricotdin staging is a second origin or a separate app key.
