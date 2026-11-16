# consult-security-architect — ADM-012

**Findings**

1. **Cookie is never honoured on admin paths (confirmed).** `app/modules/admin_auth/deps.py` reads only `HTTPBearer` (header). No admin router, `app/api/deps.py` or middleware reads `__Host-mm_device`; cookie code lives only in `auth`/`sessions`/`tokens`. Admin requests carry no ambient authority, so CORS adds no CSRF surface: a cross-site page cannot set `Authorization` without a preflight we approve, and a cookie the browser attaches anyway is ignored.
2. **What CORS adds.** It lets JS on a registered admin origin send `Authorization` and read responses. It does not protect a stolen token, which works from curl. An XSS on admin-portal already has full admin power through the SPA's in-memory token. So the real risks are mis-scoping (credentials, `*`, reflecting any origin, using the origins of the wrong app), not the feature itself.
3. **The registry is a union, not per app.** `AppOriginRegistry.is_allowed` checks one frozenset of every active app's origins. Reusing it would let any app's origin call admin routes. This breaks AC3.
4. **Disjointness is enforced on every app writer, with gaps.** `_create`, `update` (via `_validate_admin_origins`), `register` and `set_admin_origins` all call `app_keys_holding_origins` (any status, `admin` included). Gaps: (a) loopback is excluded from the claim check, so in local/test the admin loopback origins can overlap other apps (they are dropped in staging and production); (b) the ADM-002b A8 race and manual SQL, with no DB constraint.
5. **The catalog shares the admin prefix.** The role catalog `GET /api/v1/apps/{key}/roles` sits under the admin prefix. `tests/test_auth_cors.py::test_role_catalog_and_admin_paths_have_no_cors` asserts that admin paths have no CORS, so it must be split or updated.
6. **The admin origin already appears in `/auth` CORS.** It must, because admin-portal gets its token from `/auth/token` with `X-MM-App: admin`, credentialed. A preflight cannot see the `X-MM-App` value, only header names. Per-app CSRF still returns 403.
7. **The host policy is stale.** ADR-0008 and deploy.md reserve `admin.mmlabs.online`. The takeover and "never routed elsewhere" policy now has to cover `admin-portal.mmlabs.online` (ADM-014).

**Recommendations**

- **R-1 (AC3).** Add `origins_for(app_key)` (or a key→origins map) to the registry, built in the same refresh with the same TTL, fail-closed and invalidation. Admin CORS uses `origins_for("admin")` only, and only when the admin row is active, with loopback filtered in staging/production.
- **R-2 (AC1/2).** Give each path exactly one policy: `/api/v1/auth/` is credentialed; the rest of `/api/v1/` is the admin policy. Prefer one middleware with two policies over two stacked middlewares, and decide on the same `scope["path"]` the router sees.
- **R-3 (AC1, Allow-Credentials).** Never emit `Access-Control-Allow-Credentials` on admin paths, never `*`, and only echo an exact raw match. Treat `null`, a missing or a repeated `Origin` as unknown, which covers file://, sandboxed iframes and data: URLs.
- **R-4 (AC1, preflight).** Answer the preflight in the middleware, before routing and auth. Browsers send no `Authorization` on a preflight, so `current_admin` must never run on it.
  - Allow-Methods: `GET, POST, PATCH, PUT, DELETE`. Allow-Headers: `Authorization, Content-Type`. Requested headers are matched case-insensitively; any other header, or a method outside the list, gets 204 with no `Allow-*`.
  - Set `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`.
- **R-5 (Max-Age).** Use 600 s, and record it as the upper bound in the ADR. After `--clear`, a cached preflight still lets the browser send requests for up to 600 s, but each actual response loses ACAO within ≤60 s (registry TTL), so the SPA cannot read it. Side effects still need a valid token, and issued tokens last 900 s anyway, so Max-Age adds no window beyond token TTL. Do not raise it above 600 s.
- **R-6 (AC2, Expose-Headers).** Expose `Retry-After, ETag`. No admin route sets `ETag` today (only the catalog and jwks do). Do not allow `If-Match`/`If-None-Match` request headers until a route needs them. Exposing `X-Request-Id` is optional.
- **R-7 (AC4, Cache-Control).** Set `Cache-Control: no-store` on every admin response (2xx, 4xx and 401/403) when the route set none, plus `Vary: Origin` always, even without an `Origin` header. Cloudflare largely ignores `Vary`, so `no-store` is the real guard against cross-origin cache poisoning. The catalog keeps its `no-cache` and `ETag`.
- **R-8 (catalog CORS, AC5 decision).** Either exclude the catalog exactly from the admin policy and keep the current no-CORS behaviour, or include it, which is harmless (public GET, no credentials). Pick one, record it in the ADR and test it. jwks keeps its own `ACAO: *`.
- **R-9 (`X-Content-Type-Options`).** `nosniff` on admin JSON is cheap and useful. It is not in the AC, so add it as a follow-up per R7, or include it if the planner widens scope.
- **R-10 (tests).**
  - Preflight from a valid admin origin; from an invalid origin, another app's origin, `null`, duplicate `Origin`, and a case variant; with a disallowed method or header.
  - A real GET and PATCH with Bearer.
  - ACAO and Expose headers on 200, 401 (with `WWW-Authenticate`), 403 and 422.
  - No `Allow-Credentials` on any admin path, preflight included. A route-walk test is best.
  - Cookie-only request to an admin route → 401.
  - No `Origin` → no ACAO, but `no-store` is present.
  - Another app's origin on an admin path → no ACAO, while it still works on `/auth`.
  - `/auth` suite unchanged.
  - A `set_admin_origins` commit invalidates in-process at once; clearing removes ACAO after the TTL (mocked clock).
  - Loopback admin origin refused in production.

**Risks**

- If R-1 is skipped, every mmlabs app origin can drive the admin API with a token in hand. This is the main design risk.
- Origin disjointness has no DB guard (A8, SQL). R-1 keeps admin CORS admin-only even if an overlap appears, but `/auth` would still echo the overlapping origin.
- Unhandled 500s are built outside the middleware and carry no ACAO, so the SPA sees a network error. This was accepted in ADR-0006.
- The preflight cache lingers up to 600 s after `--clear`. This is bounded and dominated by token TTL.
- Takeover of `admin-portal.mmlabs.online` equals a full admin compromise. DNS and tunnel ownership is the real control.

**Open questions**

1. Should `/auth` responses stop echoing an admin origin when `X-MM-App` ≠ `admin`? This can only be done on actual responses, not preflights. Value is low (it only makes a 403 readable), and it conflicts with "`/auth` unchanged". I suggest a follow-up rather than this ticket. Owner?
2. Role catalog: admin CORS or none (R-8)?
3. Is `X-Content-Type-Options: nosniff` in this ticket or a follow-up?
4. Should `X-Request-Id` be exposed to the SPA?
