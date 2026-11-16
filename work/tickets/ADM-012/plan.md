Status: READY

# Plan — ADM-012: Non-credentialed CORS for the Bearer admin endpoints

## Spec summary
- §2: "One admin API that the mmlabs admin web UI (and internal tools) use for cross-product administration."
- §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`."
- §5.8 (abuse protection): "CORS only for registered app origins; cookie-authenticated POSTs check Origin and `X-MM-App` (D12)."
- §5.9 prerequisite (owner, 2026-11-16): "`https://admin-portal.mmlabs.online` is registered as an `admin` app origin (CLI `set-admin-origins`), and admin routes answer CORS for those origins only, **without** credentials (Bearer in the `Authorization` header, never the cookie). The SPA gets its `aud=admin` token through the existing `/api/v1/auth/*` flow with `X-MM-App: admin`."
- CLAUDE.md R3 class (1) Bearer; ADR-0008 §1 "Bearer only … no device cookie"; ADR-0006 Consequences: "no `CORS_ORIGINS` for a future admin UI (its Bearer CORS would come from `apps[admin].origins`)".
- Ticket owner notes (2026-11-16): UI host is `admin-portal.mmlabs.online`; the admin UI sends no cookie to admin endpoints, only `Authorization: Bearer`.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Preflight on a Bearer admin route from an `apps[admin].origins` origin → 204 with `ACAO: <origin>`, `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`, `Allow-Methods: DELETE, GET, PATCH, POST, PUT`, `Allow-Headers: Authorization, Content-Type`, `Max-Age: 600`, **no** `Allow-Credentials`; never routed (no auth dependency runs) | `test_admin_cors.py › test_preflight_admin_origin_allowed`, `› test_preflight_never_reaches_auth` (no `Authorization`, 204 not 401), `test_cors.py › test_admin_policy_preflight_allowed` |
| 2 | Disallowed method or header (`x-mm-app`, `cookie`), header case variants accepted (`AUTHORIZATION`, `content-type`) | `test_admin_cors.py › test_preflight_disallowed_method_or_header` (204, no `Access-Control-Allow-*`), `test_cors.py › test_admin_policy_header_case_insensitive` |
| 3 | Actual requests with Bearer from the admin origin carry `ACAO`, `Access-Control-Expose-Headers: Retry-After, ETag, X-Request-ID`, no `ACAC` — on 200, 201/204 (PATCH), 401 (no token and bad token, with `WWW-Authenticate`), 403, 404, 422 | `test_admin_cors.py › test_actual_get_with_bearer`, `› test_actual_patch_with_bearer`, `› test_error_envelopes_readable` (parametrized) |
| 4 | Unregistered origin, another app's origin (stoktok), `null`, duplicate `Origin`, case variant → no CORS headers on admin routes; the stoktok origin still works on `/auth` | `test_admin_cors.py › test_unknown_origins_refused` (param.), `› test_other_app_origin_refused_on_admin_but_ok_on_auth` |
| 5 | Admin CORS reads only `apps[admin].origins` (active row; loopback dropped in staging/production) | `test_admin_cors.py › test_loopback_admin_origin_refused_in_production`, `› test_disabled_or_empty_admin_origins_refused`; `test_auth_cors.py › test_registry_per_app_view` |
| 6 | No `Origin` (server-to-server) → no ACAO; `Vary: Origin` and `Cache-Control: no-store` still present; body/status unchanged | `test_admin_cors.py › test_no_origin_only_vary_and_no_store` |
| 7 | Every admin response carries `Cache-Control: no-store` when the route set none (with or without Origin, 2xx and errors) | `test_admin_cors.py › test_actual_get_with_bearer`, `› test_error_envelopes_readable`, `› test_no_origin_only_vary_and_no_store`; `test_cors.py › test_admin_policy_existing_cache_control_kept` |
| 8 | No `Access-Control-Allow-Credentials` on any Bearer route, preflight or actual | `test_admin_cors.py › test_no_allow_credentials_on_any_bearer_route` (parametrized over `BEARER`, preflight + actual, admin origin + real token) |
| 9 | Classification by route, not prefix: `POST /apps/{key}/roles` preflight is admin; `GET /apps/{key}/roles` (catalog) gets no CORS; JWKS keeps its own `ACAO: *`; unmatched paths/methods untouched | `test_admin_cors.py › test_role_catalog_and_jwks_unchanged`, `› test_roles_post_preflight_is_admin`; `test_route_auth_classes.py › test_cors_matcher_agrees_with_bearer_map` |
| 10 | `/api/v1/auth/*` CORS unchanged (credentialed, union of apps' origins, `GET, POST`, `Content-Type, X-MM-App`); the admin origin still gets credentialed CORS there | `tests/test_cors.py` and `tests/test_auth_cors.py` green with assertions unchanged (helper change only, see R7 notes); `test_admin_cors.py › test_admin_origin_still_credentialed_on_auth` |
| 11 | Cookie-only request (no `Authorization`) to an admin route from the admin origin → 401 (readable, no ACAC) | `test_admin_cors.py › test_cookie_only_is_401` (plus existing `test_every_bearer_route_401_with_cookie_only`) |
| 12 | `set-admin-origins` changes apply: at once in-process (after commit), within the 60 s TTL from another process; `--clear` removes ACAO after the TTL | `test_admin_cors.py › test_set_admin_origins_applies_in_process`; `test_auth_cors.py › test_registry_per_app_view_ttl` (mocked `_Clock`, patched loader) |
| 13 | The two CORS scopes are disjoint (no Bearer route under `/api/v1/auth/`) | `test_route_auth_classes.py › test_cors_matcher_agrees_with_bearer_map` (asserts no `BEARER` path starts with `/api/v1/auth/`) |
| 14 | ADR written; `docs/consumers.md` admin-UI section (Bearer only, no cookies on admin endpoints); deploy operator step | Reviewer reads `docs/adr/0011-admin-cors.md`, the ADR-0006 amendment pointer, `docs/consumers.md` "Calling the admin API from the admin UI", `docs/deploy.md` "Admin UI CORS (ADM-012)" |
| 15 | `make check` green | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/cors.py` | Introduce frozen `CORSPolicy` dataclass and `IDENTITY_POLICY` (current values exactly) + `ADMIN_POLICY`; `RouteMatcher` type; `DynamicCORSMiddleware(app, *, registry, policy=IDENTITY_POLICY, path_prefix=None, matcher=None)` — exactly one of `path_prefix`/`matcher` (else `ValueError`); headers emitted from the policy | One class, two policies (AC1–3, 10); `test_cors.py` constructors keep working unchanged |
| `app/modules/rbac/origin_registry.py` | Cache also `key → frozenset(origins)` in the same refresh; add `is_allowed_for(key, origin)` and `for_app(key) -> AppOriginView` (implements `OriginRegistry`); failure clears both | Admin-only origins with the same TTL / single-flight / fail-closed / invalidation (AC5, 12) |
| `app/modules/rbac/repository.py` | Replace `active_app_origins()` with `active_app_origins_by_key() -> dict[str, list[str]]` (active apps; key → origins) | One query feeds both the union and the per-key view (its only caller is the registry) |
| `app/modules/admin_auth/route_classes.py` (new) | `dependant_calls(dependant)`, `is_bearer_dependant(dependant)` (`current_admin` in the tree), `BearerRouteMatcher(app)` (lazy, first-FULL-match) | Route-based classification shared by the middleware and `test_route_auth_classes.py` (AC9, 13) |
| `app/main.py` | Mount the admin instance (`registry=origin_registry.for_app("admin")`, `policy=ADMIN_POLICY`, `matcher=BearerRouteMatcher(app)`) next to the identity instance, both inside `RequestIdMiddleware`; update comment | Wire the feature |
| `tests/test_admin_cors.py` (new) | Real-app tests with real minted tokens (`admin_auth_factories.mint`/`bearer`), see Tests | AC1–12 |
| `tests/test_cors.py` | Add admin-policy unit tests with `FakeRegistry` + a fake matcher; existing tests untouched | Policy-level behaviour (AC1–3, 7) |
| `tests/test_auth_cors.py` | (a) `_patch_loader` / `broken` patch `active_app_origins_by_key` and wrap the given lists as `{f"app{i}": o}` (call sites and assertions unchanged); (b) update `test_role_catalog_and_admin_paths_have_no_cors` → `test_role_catalog_no_cors_admin_path_refuses_app_origin` (catalog: no ACAO, no `Vary: Origin` — unchanged; `/api/v1/apps` with the stoktok origin: no ACAO, no ACAC, but now `Vary: Origin` and `Cache-Control: no-store`); (c) add `test_registry_per_app_view`, `test_registry_per_app_view_ttl` | Repository rename; deliberate behaviour change on admin paths (R7, see Approach §8) |
| `tests/test_route_auth_classes.py` | Replace local `_calls`/`current_admin in calls` with `dependant_calls`/`is_bearer_dependant` from `route_classes`; add `test_cors_matcher_agrees_with_bearer_map` | One shared definition of "Bearer route" (AC9, 13) |
| `docs/adr/0011-admin-cors.md` (new) | Decision record (see Approach §9) | Decision outlives the ticket (planner rule 7) |
| `docs/adr/0006-dynamic-cors.md` | Append `## Amendment — admin CORS (ADM-012, 2026-11-16)`: §1 scope no longer says admin routes are untouched; points to ADR-0011; registry gains the per-app view | ADR-0006 §1 becomes stale otherwise |
| `docs/consumers.md` | New section "Calling the admin API from the admin UI" after "Calling the identity API from the browser" | AC14 |
| `docs/deploy.md` | "Browser origins (CORS)": one sentence on admin CORS; new subsection "Admin UI CORS (ADM-012)" with the operator steps | AC14, operator step from the ticket |

No other file. `work/` files are the orchestrator's.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_cors.py › test_admin_policy_preflight_allowed` | 204, ACAO, 3-part Vary, methods `DELETE, GET, PATCH, POST, PUT`, headers `Authorization, Content-Type`, Max-Age 600, no ACAC; inner app not called |
| `test_cors.py › test_admin_policy_header_case_insensitive` | `AUTHORIZATION`, `Content-Type`, `authorization, content-type` accepted; `x-mm-app` refused |
| `test_cors.py › test_admin_policy_actual_request` | ACAO + `Expose-Headers: Retry-After, ETag, X-Request-ID`, no ACAC, `no-store`, Vary merged |
| `test_cors.py › test_admin_policy_existing_cache_control_kept` | Route-set `Cache-Control` not overwritten |
| `test_cors.py › test_matcher_out_of_scope_untouched` | Matcher returns False → inner response byte-identical (no Vary, no Cache-Control) |
| `test_cors.py › test_matcher_receives_request_method_for_preflight` | Matcher is called with `Access-Control-Request-Method` on preflight, the request method otherwise |
| `test_cors.py › test_policy_requires_one_scope` | `ValueError` with neither / both of `path_prefix` and `matcher` |
| `test_admin_cors.py › test_preflight_admin_origin_allowed` | AC1 on `GET /api/v1/apps`, `PATCH /api/v1/apps/{key}`, `PUT …/roles/{id}/permissions`, `DELETE …` |
| `test_admin_cors.py › test_preflight_never_reaches_auth` | Preflight without token → 204 (not 401), no `WWW-Authenticate` |
| `test_admin_cors.py › test_preflight_disallowed_method_or_header` | Header `x-mm-app` / `cookie` / `if-match` → 204 without `Allow-*`; method not served by the route (e.g. `PUT /api/v1/apps`) → not admin-classified, no CORS headers |
| `test_admin_cors.py › test_unknown_origins_refused` | Unregistered, stoktok origin, `null`, duplicate `Origin`, case variant → no `Access-Control-*` on preflight and actual |
| `test_admin_cors.py › test_actual_get_with_bearer` | 200 with ACAO, Expose, `no-store`, `Vary: Origin`, no ACAC |
| `test_admin_cors.py › test_actual_patch_with_bearer` | PATCH on an admin resource (e.g. organization) with Bearer → 200, ACAO, no ACAC |
| `test_admin_cors.py › test_error_envelopes_readable` | 401 no token, 401 bad token (`WWW-Authenticate` present), 403 missing permission, 404, 422 → ACAO, Expose, `no-store`, no ACAC |
| `test_admin_cors.py › test_no_origin_only_vary_and_no_store` | Server-to-server: no ACAO/Expose, `Vary: Origin` + `no-store`, same status/body |
| `test_admin_cors.py › test_no_allow_credentials_on_any_bearer_route` | Parametrized over `BEARER` (`_fill`): preflight and actual from the admin origin with a real token → no ACAC; preflight has ACAO |
| `test_admin_cors.py › test_cookie_only_is_401` | Device cookie + admin Origin, no `Authorization` → 401 `unauthorized`, ACAO, no ACAC |
| `test_admin_cors.py › test_other_app_origin_refused_on_admin_but_ok_on_auth` | stoktok origin: no ACAO on `/api/v1/apps`; credentialed echo on `/api/v1/auth/token` |
| `test_admin_cors.py › test_admin_origin_still_credentialed_on_auth` | Admin origin on `/api/v1/auth/token` preflight → credentialed (`ACAC: true`, `GET, POST`, `Content-Type, X-MM-App`) |
| `test_admin_cors.py › test_role_catalog_and_jwks_unchanged` | `GET /apps/stoktok/roles` from admin origin: no ACAO, no `Vary: Origin`, keeps `ETag`/its own Cache-Control; JWKS: `ACAO: *`, no ACAC |
| `test_admin_cors.py › test_roles_post_preflight_is_admin` | Preflight `POST /apps/stoktok/roles` → admin policy; preflight `GET` same path → no CORS |
| `test_admin_cors.py › test_loopback_admin_origin_refused_in_production` | Admin row holding `http://localhost:5173` (direct UPDATE): echoed in test env, refused with `_PROD` settings |
| `test_admin_cors.py › test_disabled_or_empty_admin_origins_refused` | Empty origins (default seed) → no ACAO; non-active admin row (direct UPDATE in the test transaction) → no ACAO |
| `test_admin_cors.py › test_set_admin_origins_applies_in_process` | `make_strict_client`: prime cache; `AppService.set_admin_origins` (actor `SYSTEM_CLI`) + commit → new origin echoed, old not, before the TTL; `--clear` (`origins=[]`) → no ACAO |
| `test_auth_cors.py › test_registry_per_app_view` | Patched loader `{"admin": [A], "stoktok": [S]}`: `for_app("admin")` allows A only; union `is_allowed` allows both; unknown key → False; one load serves both |
| `test_auth_cors.py › test_registry_per_app_view_ttl` | `_Clock`: loader changes to `{"admin": []}` (out-of-process clear) → still allowed at +59 s, refused at +60 s; failing loader → view fails closed |
| `test_route_auth_classes.py › test_cors_matcher_agrees_with_bearer_map` | `BearerRouteMatcher` True for every `BEARER` (method, filled path); False for every `PUBLIC`/`COOKIE` route, `HEAD /healthz`, unknown path; no `BEARER` path under `/api/v1/auth/` |
| `test_auth_cors.py › test_role_catalog_no_cors_admin_path_refuses_app_origin` (updated) | Deliberate change of the old no-CORS test (R7) |

Fixtures: admin origin `ADMIN_ORIGIN = "https://admin-portal.mmlabs.online"`, set in the test transaction with `AppService.set_admin_origins` (or a direct `UPDATE apps SET origins` where the service refuses, e.g. loopback-in-test is allowed by the service, a disabled row is not). Staff via `make_staff(db_session, "admin:*")` / a narrower permission for 403; token via `mint(staff)` + `bearer(token)`. No `admin_permissions` override in `test_admin_cors.py`.

## Approach
1. **Policy object (`app/core/cors.py`).** `@dataclass(frozen=True, slots=True) class CORSPolicy`: `credentials: bool`, `allow_methods: frozenset[str]`, `allow_headers: frozenset[str]` (lowercase, for matching), `allow_headers_value: str` (as emitted), `expose_headers: str`, `max_age: int`.
   - `IDENTITY_POLICY = CORSPolicy(True, {"GET","POST"}, {"content-type","x-mm-app"}, "Content-Type, X-MM-App", "Retry-After", 600)` — the exact current values; emitted headers and their order unchanged (Allow-Methods stays `", ".join(sorted(...))` → `GET, POST`).
   - `ADMIN_POLICY = CORSPolicy(False, {"GET","POST","PATCH","PUT","DELETE"}, {"authorization","content-type"}, "Authorization, Content-Type", "Retry-After, ETag, X-Request-ID", 600)`. Allow-Methods emitted sorted: `DELETE, GET, PATCH, POST, PUT`.
   - `Access-Control-Allow-Credentials: true` is written only when `policy.credentials` — never for the admin policy, on preflight or actual.
   - `RouteMatcher = Callable[[Scope, str], bool]` (core imports no module, ADR-0006 §5).
2. **Middleware flow.** `__call__`: non-http → pass. `preflight = method == "OPTIONS" and "access-control-request-method" in headers`. Scope check: `path_prefix` → `scope["path"].startswith(prefix)` (identical to today); `matcher` → `matcher(scope, acrm if preflight else scope["method"])`. Out of scope → pass through untouched. In scope: Origin parsing (single, non-`null`, raw exact match) and registry lookup exactly as today; preflight answered with 204 and never routed (strict method + every requested header, case-insensitive); actual responses: merge `Vary: Origin`, ACAO (+ ACAC only if credentialed) + Expose for an allowed origin, `Cache-Control: no-store` when unset. Code paths are shared; only the policy values differ.
3. **Two instances, not one middleware with two policies** (justification): the identity instance stays byte-for-byte what it is (its unit suite constructs it with `path_prefix` and runs unchanged); each instance has exactly one policy and one scope selector, so no per-request policy choice can pick the wrong one; disjointness is proven by `test_cors_matcher_agrees_with_bearer_map` (no Bearer route under `/api/v1/auth/`), so order between them is irrelevant. Both inside `RequestIdMiddleware` (preflights still carry `X-Request-ID`).
4. **Per-app registry view.** `AppOriginRegistry._refresh` loads `active_app_origins_by_key()` once; stores `_origins` (union, unchanged semantics for `/auth`) and `_by_key: dict[str, frozenset[str]]`, both through `allowed_origins(row, app_env)` (loopback dropped in staging/production). Failure: both empty for 5 s, same warning. `is_allowed_for(key, origin)` shares the TTL check/refresh; `for_app(key)` returns a small `AppOriginView` with `async is_allowed(origin)` → `is_allowed_for(key, origin)`. The view holds no state, so the weakset `after_commit` invalidation and the test `session_factory` swap (`conftest._use_test_session`) apply to it automatically. Only active rows are loaded, so a non-active admin row yields no origins. The union set is never consulted by the admin policy.
5. **Route matcher (`app/modules/admin_auth/route_classes.py`).** `dependant_calls` = the existing `_calls` walk; `is_bearer_dependant(d) = current_admin in dependant_calls(d)`. `BearerRouteMatcher(app)`: on the first `__call__` (not in `create_app`, so every router is already included) builds `list[tuple[RouteContext, bool]]` from `fastapi.routing.iter_route_contexts(app.routes)` in router order (FastAPI 0.162 keeps included routers as `_IncludedRouter`; `RouteContext.matches` forwards to the effective context, which matches the full prefixed path). `__call__(scope, method)`: for each context, `match, _ = ctx.matches({**scope, "method": method})`; the **first `Match.FULL`** decides — admin iff that route is Bearer (mirrors the router's own selection). No FULL match → False (404/405 paths get no admin CORS). The scope copy is shallow and never mutated. ~30 routes per request, regex only.
6. **Unhandled 500s** keep the ADR-0006 caveat (produced by `ServerErrorMiddleware` outside user middleware; SPA sees a network error). Recorded in ADR-0011.
7. **Catalog and JWKS.** Catalog `GET /api/v1/apps/{key}/roles` is PUBLIC → not matched → no CORS (unchanged; it is meant for server-side consumers). JWKS keeps its own `ACAO: *`. Recorded in ADR-0011.
8. **R7 — deliberate test changes.** `test_role_catalog_and_admin_paths_have_no_cors` asserted admin paths send no `Vary: Origin`; that is now false by design (AC4 requires `no-store`, and `Vary: Origin` is always merged). It is updated (not deleted) to the new expectation, keeping the catalog half as is. `_patch_loader`/`broken` change only because the repository method is renamed; every registry assertion stays identical. `test_route_auth_classes.py` imports the shared walk instead of its own copy; its assertions are unchanged.
9. **ADR-0011 "Admin CORS"** (new rather than a large amendment, because ADR-0006 is titled and scoped "identity endpoints"; ADR-0006 gets a short pointer amendment). Content: context (§5.9 prerequisite, ADR-0008 Bearer-only); decision — scope by route (Bearer routes per the shared matcher, FULL match, request method or ACRM), origins only `apps[admin].origins` via the per-app registry view (active, loopback filtered in staging/production), the admin policy values, no credentials ever, `null`/missing/duplicate = unknown, preflight before routing/auth, `Max-Age 600` as an upper bound (after `--clear` a cached preflight can let a browser send requests ≤600 s, but each response loses ACAO within ≤60 s; side effects still need a valid token whose TTL is 900 s; do not raise above 600), `no-store` + `Vary: Origin` on every admin response; catalog no CORS, JWKS `*`; 500 caveat; consequences (admin origin still credentialed on `/auth` by design — the SPA logs in there; no CSRF surface because admin auth ignores the cookie; host takeover of `admin-portal` = admin compromise, DNS/tunnel ownership is the control; per-process cache as ADR-0002/0006).
10. **docs/consumers.md** "Calling the admin API from the admin UI": token from `POST /api/v1/auth/token` (`credentials: "include"`, `X-MM-App: admin`, `audience: "admin"`); admin calls `fetch(url, {credentials: "omit", headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"}})` — never `include`, no cookie, no `X-MM-App` (it is refused by the preflight); keep the token in memory only (not `localStorage`); re-call `/auth/token` ~60 s before `expires_in`; on a 401 from an admin endpoint re-call `/auth/token` once, then send to login (401 `session_invalid` from `/token` = log in again); re-call after a role change; readable 401/403/404/422 envelopes, exposed `Retry-After`, `ETag`, `X-Request-ID`; 500 unreadable; `admin-portal` cannot host sign-up/forgot/resend screens (ADM-002d).
11. **docs/deploy.md.** In "Browser origins (CORS)": admin routes get non-credentialed CORS from `apps[admin].origins` only (ADR-0011). New subsection "Admin UI CORS (ADM-012)": after the release, run `set-admin-origins --origin https://admin-portal.mmlabs.online` through `ops-org-admin-api` (dry-run first; during a host move keep the old origin as the second allowed one); wait 60 s (CLI is another process); add `admin-portal.mmlabs.online` to `TURNSTILE_ALLOWED_HOSTNAMES` in the deployment compose if that list is non-empty — **this is a `mmlabs-online/deployment` change for the owner/orchestrator, not in this repo**; verify with `curl -si -X OPTIONS -H 'Origin: https://admin-portal.mmlabs.online' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization' https://api.mmlabs.online/api/v1/apps` (ACAO, no ACAC) and the same from another origin (no ACAO). The existing `admin.mmlabs.online` references are left for ADM-014.
12. **Rules.** R1: no router/service change; middleware is HTTP-only plumbing. R2: no schema change. R3: classification reuses the R3 definition (`current_admin` in the dependant tree); no route changes class. R5: no new config. R6: envelopes unchanged, only headers added. R7: see §8 and Follow-ups. R8: tests above, `make check`. R9: no endpoint change. R11: CORS echoes only `apps.origins` (admin's own for admin routes); no credentials on admin routes; nothing logged beyond the existing truncated-origin debug line.

## Specialist input used
- **api-platform-engineer:** adopted rec. 1 (frozen policy, one class), 2 (admin-only origins — as a per-key view of the same registry rather than a second registry, per orchestrator/identity R1), 3 (route classification, lazy build, `route.matches` FULL only, shared walk), 4–5 (preflight before routing; `Vary`/`no-store` always on admin routes), 6 (500 caveat kept), 7 (Max-Age 600), 8 (catalog/JWKS unchanged), 9 (test list), 10 (docs). Refined 3: first FULL match across all routes decides, so the matcher mirrors router selection. Open Q1: `X-Request-ID` exposed (orchestrator); `WWW-Authenticate` not exposed (SPA uses the envelope code). Q2: caveat stands. Q3: follow-up. Q4: `If-Match`/`If-None-Match` not allowed until a route needs them.
- **security-architect:** adopted R-1 (`for_app("admin")`, active, loopback filtered), R-3, R-4, R-5 (600 s documented as upper bound), R-6, R-7, R-8 (catalog: no CORS), R-10 tests. R-2 "one middleware with two policies" rejected in favour of two instances of one class (Approach §3; disjointness tested). R-9 `nosniff` → follow-up. OQ1 → follow-up.
- **identity-access-engineer:** adopted R1 (same cached load, no second cache), R2, R3 (admin CORS never reads `X-MM-App`; `x-mm-app` not an allowed admin header), SPA pattern and operator steps for the docs. Open Qs: account picker is an admin-pan concern (out of scope); CLAUDE.md R11 cookie wording → follow-up.

## Assumptions
- Owner defaults recorded by the orchestrator (2026-11-16): origins only from `apps[admin].origins` via the per-key registry view; route-based scope; policy values and Expose list as above; catalog no CORS, JWKS `*`; ADR-0006 500 caveat stands; the listed follow-ups stay out of scope.
- No admin route sets `ETag` or `Retry-After` today; exposing them is forward-compatible and harmless.
- Browsers send `Origin` in lowercase default-port form, matching stored normalized origins (raw exact match, as ADR-0006).
- `HEAD` on a Bearer GET route is classified admin (Starlette adds `HEAD`), as an actual request only; `HEAD` is not in Allow-Methods.
- Routes are not added after the first request; the lazy matcher snapshot is valid for the process lifetime.
- Seeded `admin` row has empty origins in the test DB; tests set them inside the rolled-back test transaction.

## Blocking questions
None.

## Split proposal
None. Estimate: production code ~170 changed lines (`cors.py` ~+60/−20, `origin_registry.py` ~+35, `repository.py` ~+5/−5, `route_classes.py` ~+50 new, `main.py` ~+12); tests ~400 lines (mostly new `test_admin_cors.py` ~280, `test_cors.py` ~+70, `test_auth_cors.py` ~+40/−10, `test_route_auth_classes.py` ~+20/−10); docs ~110 lines (ADR-0011 ~60, ADR-0006 amendment ~8, consumers ~30, deploy ~20). No migration. One spec feature.

## Data model / migration
None.

## API contract
No endpoint added or changed; only response headers on existing routes.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| OPTIONS (preflight) | every Bearer route in `BEARER` (`/api/v1/organizations…`, `/users…`, `/apps…` except catalog GET, `/audit-logs`) | `Origin`, `Access-Control-Request-Method` ∈ {GET, POST, PATCH, PUT, DELETE} served by that route, `Access-Control-Request-Headers` ⊆ {authorization, content-type} | Allowed: `ACAO: <origin>`, `Access-Control-Allow-Methods: DELETE, GET, PATCH, POST, PUT`, `Access-Control-Allow-Headers: Authorization, Content-Type`, `Access-Control-Max-Age: 600`, `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`; never ACAC. Otherwise only the `Vary` | 204 (never routed, no auth) | none (preflight) |
| any (actual) | every Bearer route | `Authorization: Bearer <aud=admin>`; optional `Origin` | Always `Vary: Origin` (merged), `Cache-Control: no-store` if unset; allowed origin: `ACAO: <origin>`, `Access-Control-Expose-Headers: Retry-After, ETag, X-Request-ID`; never ACAC | unchanged (200/201/204/401/403/404/409/422) | unchanged (`require_permission`) |
| any | `/api/v1/auth/*` | unchanged | unchanged (credentialed, ADR-0006) | unchanged | unchanged |
| GET | `/api/v1/apps/{key}/roles`, `/.well-known/jwks.json` | unchanged | unchanged (catalog: no CORS; JWKS: `ACAO: *`) | unchanged | public |

## Follow-ups (not in this ticket)
- `X-Content-Type-Options: nosniff` on admin JSON responses (security R-9).
- A Bearer endpoint listing an app's roles for the admin UI (the catalog 404s for `admin` and has no CORS).
- `/auth/*` not echoing the admin origin when `X-MM-App` ≠ `admin` (security OQ1; conflicts with "`/auth` unchanged").
- CORS-readable 500 envelopes on admin routes (api rec. 6).
- CLAUDE.md R11 cookie wording (`__Host-mm_device; Path=/`, not `mm_device; Path=/api/v1/auth`).
- Deployment repo: add `admin-portal.mmlabs.online` to `TURNSTILE_ALLOWED_HOSTNAMES` (owner/orchestrator, `mmlabs-online/deployment`).
- `admin.mmlabs.online` → `admin-portal.mmlabs.online` in ADR-0008 / deploy.md (already ADM-014).
