Status: READY

# Plan — USR-03e: Dynamic CORS from `apps.origins`; remove `CORS_ORIGINS`

## Spec summary
- spec-v1 §5.8: "CORS only for registered app origins; cookie-authenticated POSTs check Origin and
  `X-MM-App` (D12)."
- spec-v1 §5.3: each app has "its allowed browser `origins`" ... "app origins are limited to
  `mmlabs.online`, its subdomains and localhost (D19)."
- identity-architecture.md §5 S4 (D12): "`Access-Control-Allow-Origin` echoes only origins in
  `apps.origins`, with `Allow-Credentials: true`. Every cookie-authenticated POST checks `Origin`
  against the app's origins and requires `X-MM-App: <key>` (the custom header forces a CORS
  preflight)."
- D20: the cookie is `__Host-mm_device`, `Path=/`; "identity endpoints stay under `/api/v1/auth/*`".
- ADR-0004 §8: "Loopback origins are ignored in staging/production. ... The static `CORSMiddleware`
  stays until USR-03e". ADR-0005: JWKS is `Access-Control-Allow-Origin: *` without credentials.
- CLAUDE.md R3 (auth classes), R11 ("allow-origin echoes only `apps.origins` (with credentials)").

This ticket implements only the CORS half of D12. The CSRF half (`AppContextService.check`) already
exists and keeps its uncached per-request DB read; it is the security control. CORS only decides
whether a page can read the response.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A pure-ASGI `DynamicCORSMiddleware` (`app/core/cors.py`, behind an `OriginRegistry` protocol; `app/core` imports nothing from `app/modules`) replaces `_CredentialedCORS`/`CORSMiddleware`. | `grep -rn "CORSMiddleware\|_CredentialedCORS\|cors_origins" app tests` is empty; `grep -n "app.modules" app/core/cors.py` is empty; `tests/test_cors.py` passes. |
| 2 | Scope is exactly the path prefix `/api/v1/auth/` (`/api/v1/authx`, `/api/v1/apps/...`, `/healthz`, admin routes get no CORS headers at all; `/.well-known/jwks.json` keeps its own `ACAO: *` and no credentials). | `test_cors.py::test_out_of_scope_paths_untouched`, `::test_prefix_lookalike_untouched`; `test_jwks.py::test_jwks_wildcard_without_credentials_for_registered_origin`; `test_auth_cors.py::test_role_catalog_and_admin_paths_have_no_cors`. |
| 3 | Actual requests: when the raw `Origin` exactly equals an origin of some active app, the response carries `Access-Control-Allow-Origin: <raw Origin>`, `Access-Control-Allow-Credentials: true`, `Access-Control-Expose-Headers: Retry-After`; otherwise none of these. `Vary` includes `Origin` on every in-scope response (merged with an existing `Vary`). | `test_cors.py::test_actual_request_allowed_origin_echoed`, `::test_actual_request_unknown_origin_no_acao`, `::test_vary_origin_always_and_merged`, `::test_null_and_missing_origin_no_acao`; `test_auth_cors.py::test_login_cross_origin_credentialed_echo`. |
| 4 | Preflight (`OPTIONS` + `Access-Control-Request-Method`) is answered by the middleware, never routed: allowed → 204 with ACAO (raw origin), `ACA-Credentials: true`, `ACA-Methods: GET, POST`, `ACA-Headers: Content-Type, X-MM-App`, `ACA-Max-Age: 600`; unknown origin, method not in {GET, POST} or a requested header outside {content-type, x-mm-app} → 204 without any `Access-Control-Allow-*` header. Both carry `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`. | `test_cors.py::test_preflight_allowed`, `::test_preflight_unknown_origin`, `::test_preflight_bad_method_or_header`, `::test_preflight_never_reaches_app`; `test_auth_cors.py::test_preflight_token_from_registered_origin`. |
| 5 | Disabled apps' origins are not echoed; loopback origins are ignored in staging/production (same helper as `AppContextService`). | `test_auth_cors.py::test_disabled_app_origin_not_echoed`, `::test_loopback_origin_not_echoed_in_production`; `test_rbac_origins.py::test_allowed_origins_drops_loopback_in_staging_production`; existing `test_auth_app_context.py` stays green. |
| 6 | Error responses 401/403/422/429 carry the CORS headers, so a registered page can read the envelope (unhandled 500s do not; documented). | `test_auth_cors.py::test_error_envelopes_readable_cross_origin` (401 wrong password, 403 `origin_not_allowed` for a wrong `X-MM-App`, 422 bad body, 429 with `Retry-After` exposed). |
| 7 | Registry: per-process cached set, TTL 60 s, single-flight refresh, fail closed (load error → empty set for 5 s, no ACAO), invalidated after the commit of an app create/update/delete (and not on rollback). | `test_auth_cors.py::test_registry_ttl_and_single_flight`, `::test_registry_fails_closed`, `::test_registry_invalidation_during_refresh`, `::test_invalidated_after_commit_not_on_rollback`, `::test_app_patch_takes_effect_before_ttl`. |
| 8 | `Cache-Control: no-store` on every non-preflight `/api/v1/auth/*` response that does not already set `Cache-Control` (signup and error envelopes today lack it; Cloudflare ignores `Vary`). | `test_cors.py::test_no_store_added_when_absent`, `::test_existing_cache_control_kept`; `test_auth_cors.py::test_error_envelopes_readable_cross_origin` asserts `no-store`. |
| 9 | `CORS_ORIGINS` removed (setting, validator, `.env.example`, tests); a stale `CORS_ORIGINS` in an env file is ignored (`extra="ignore"`). | `test_config.py::test_cors_origins_env_ignored`; `grep -n CORS_ORIGINS .env.example app` empty. |
| 10 | Docs: `docs/deploy.md` (no CORS env var; origins come from apps), `docs/consumers.md` (browser integration for ricotdin), ADR-0006, pointers in identity-architecture S4, ADR-0004 §8, jwks docstring. | Reviewer reads the files listed below. |
| 11 | `make check` green. | `make check`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/cors.py` | **New.** `OriginRegistry` Protocol (`async def is_allowed(self, origin: str) -> bool`) and pure-ASGI `DynamicCORSMiddleware(app, *, registry, path_prefix)`; module constants for methods, headers, max-age, expose-headers. | AC1–4, 6, 8. |
| `app/modules/rbac/origins.py` | Add `is_loopback_origin(origin)` (moved from `auth/service.py`) and `allowed_origins(origins, app_env) -> list[str]` (drops loopback in staging/production). | One rule for CORS and CSRF (AC5). |
| `app/modules/rbac/origin_registry.py` | **New.** `AppOriginRegistry` (implements `OriginRegistry`), process-wide `WeakSet` of registries, `invalidate_all()`, SQLAlchemy `after_commit` / `after_soft_rollback` listeners keyed on `session.info`. | AC5, 7. |
| `app/modules/rbac/repository.py` | Add `APPS_CHANGED_KEY` constant, `active_app_origins() -> list[list[str]]` (origins of `status = active` apps) and `mark_apps_changed()` (sets `session.info[APPS_CHANGED_KEY] = True`). | R1: SQLAlchemy only in the repository. |
| `app/modules/rbac/service.py` | `AppService.create`, `update` (only when `changed`), `delete`: call `self.repo.mark_apps_changed()` after the write succeeded. | Invalidation (AC7). |
| `app/modules/auth/service.py` | `AppContextService.check` uses `allowed_origins(app.origins, self.app_env)`; delete `_is_loopback` and `_LOOPBACK_HOSTS`. | Shared helper (AC5); behaviour unchanged. |
| `app/main.py` | Remove `_CredentialedCORS` and the `CORSMiddleware` import; build `AppOriginRegistry(session_factory=SessionLocal, app_env=settings.app_env)`, store it on `app.state.origin_registry`, and `app.add_middleware(DynamicCORSMiddleware, registry=..., path_prefix=f"{settings.api_prefix}/auth/")` unconditionally. | AC1, AC2. |
| `app/core/config.py` | Remove `cors_origins` and `_no_wildcard_origin`. | AC9. |
| `tests/conftest.py` | In `make_client` and `make_strict_app`, point `app.state.origin_registry.session_factory` at an async context manager yielding `db_session` (so the registry sees the test transaction). | Integration tests read test data. |
| `tests/test_cors.py` | **New.** Middleware unit tests: fake registry + tiny Starlette app (records whether it was called). | AC1–4, 8. |
| `tests/test_auth_cors.py` | **New.** Real app + DB: registry, invalidation, auth endpoints cross-origin. | AC2, 3, 5–7. |
| `tests/test_rbac_origins.py` | Add tests for `is_loopback_origin` / `allowed_origins`. | AC5. |
| `tests/test_config.py` | Remove `test_cors_origins_wildcard_refused`; add `test_cors_origins_env_ignored`; fix the `# --- IDENTITY_HMAC_KEY and CORS_ORIGINS` heading. | AC9. |
| `tests/test_jwks.py` | Replace `test_jwks_no_credentials_with_cors_origin` (uses `cors_origins`) with `test_jwks_wildcard_without_credentials_for_registered_origin`. | AC2, AC9. |
| `.env.example` | Remove `CORS_ORIGINS=...`. | AC9, R5. |
| `docs/deploy.md` | New short section "Browser origins (CORS)". | AC10 (ticket AC6). |
| `docs/consumers.md` | New section "Calling the identity API from the browser" (before "Getting a token (SPA)"); update the prerequisites line. | AC10. |
| `docs/adr/0006-dynamic-cors.md` | **New ADR** (decision outlives the ticket): scope, preflight contract, registry/TTL/invalidation, fail-closed, per-process caveat, 500 limitation, `CORS_ORIGINS` removal. | Planner rule 7. |
| `docs/identity-architecture.md` | S4: one sentence pointing to ADR-0006 (scope `/api/v1/auth/*`, cache TTL 60 s). | AC10. |
| `docs/adr/0004-device-sessions-and-login.md` | §8 last sentence: "The static `CORSMiddleware` was replaced by dynamic CORS in USR-03e (ADR-0006); `CORS_ORIGINS` was removed." | Keeps the ADR true. |
| `app/modules/tokens/jwks_router.py` | Docstring: "the credentialed CORS middleware (`app.core.cors`) only covers `/api/v1/auth/`". | Docstring points at removed code. |

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_cors.py › test_preflight_allowed` | 204, raw origin echoed, credentials, methods `GET, POST`, headers `Content-Type, X-MM-App`, max-age `600`, preflight `Vary`; empty body. |
| `test_cors.py › test_preflight_unknown_origin` | 204, no `access-control-allow-*` headers, `Vary` present. |
| `test_cors.py › test_preflight_bad_method_or_header` | Param: `ACRM: PUT/DELETE`, `ACRH: authorization`, `x-mm-app, x-other` → 204 without ACAO; `ACRH: X-MM-App, content-type` (case/space) and absent ACRH → allowed. |
| `test_cors.py › test_preflight_never_reaches_app` | Inner app not called for allowed and denied preflights; `OPTIONS` without ACRM reaches the app. |
| `test_cors.py › test_actual_request_allowed_origin_echoed` | ACAO = raw origin, ACAC `true`, ACEH `Retry-After`. |
| `test_cors.py › test_actual_request_unknown_origin_no_acao` | No ACAO/ACAC/ACEH; inner response otherwise unchanged. |
| `test_cors.py › test_null_and_missing_origin_no_acao` | `Origin: null`, absent Origin, two `Origin` headers → no ACAO, registry not asked for `null`/absent. |
| `test_cors.py › test_case_variant_origin_not_echoed` | `https://RICOTDIN.mmlabs.online` or with `:443` → no ACAO (exact raw match). |
| `test_cors.py › test_vary_origin_always_and_merged` | Inner `Vary: Accept-Encoding` → `Accept-Encoding, Origin`; no duplicate when inner already has `Origin`. |
| `test_cors.py › test_no_store_added_when_absent` / `test_existing_cache_control_kept` | AC8. |
| `test_cors.py › test_out_of_scope_paths_untouched` | `/api/v1/apps/x/roles`, `/api/v1/organizations`, `/healthz`, `/.well-known/jwks.json`: headers byte-identical to the inner app's, preflight routed to the inner app. |
| `test_cors.py › test_prefix_lookalike_untouched` | `/api/v1/authx/login`, `/api/v1/auth` (no slash). |
| `test_cors.py › test_non_http_scope_passthrough` | `lifespan`/`websocket` scopes forwarded unchanged. |
| `test_auth_cors.py › test_preflight_token_from_registered_origin` | Real app: `OPTIONS /api/v1/auth/token` from `ORIGIN` → allowed; from `https://evil.mmlabs.online` → no ACAO. |
| `test_auth_cors.py › test_login_cross_origin_credentialed_echo` | `POST /login` with registered origin → ACAO/ACAC, `Vary: Origin`, `Cache-Control: no-store`. |
| `test_auth_cors.py › test_error_envelopes_readable_cross_origin` | 401 (wrong password), 403 (`X-MM-App` of another app; CORS still echoes because the origin belongs to *an* active app), 422 (bad body), 429 (exhaust `login_rate_limit=1`): each has ACAO + ACAC + envelope JSON + `no-store`; 429 has `Retry-After` and ACEH. |
| `test_auth_cors.py › test_disabled_app_origin_not_echoed` | App with `status=disabled` → its origin gets no ACAO. |
| `test_auth_cors.py › test_loopback_origin_not_echoed_in_production` | Pattern of `test_auth_app_context.py::test_loopback_origin_ignored_in_production`: `http://localhost:3000` echoed in `test`, not with `**_PROD`; the https origin still echoed. |
| `test_auth_cors.py › test_role_catalog_and_admin_paths_have_no_cors` | Registered Origin on `GET /api/v1/apps/stoktok/roles` and an admin route → no ACAO, no `Vary: Origin` added. |
| `test_auth_cors.py › test_registry_ttl_and_single_flight` | `AppOriginRegistry` with a fake clock and counting loader: one load for N concurrent `is_allowed` (`asyncio.gather`), reload only after 60 s. |
| `test_auth_cors.py › test_registry_fails_closed` | Loader raises → `False` for a previously allowed origin, no reload within 5 s, reload after; log line has no exception message/DSN. |
| `test_auth_cors.py › test_registry_invalidation_during_refresh` | Invalidate while a load is in flight → the next call reloads (generation check). |
| `test_auth_cors.py › test_invalidated_after_commit_not_on_rollback` | `mark_apps_changed()` + `rollback()` → no reload; + `commit()` → reload (uses `db_session`, savepoint mode). |
| `test_auth_cors.py › test_app_patch_takes_effect_before_ttl` | `make_strict_client(admin_rbac_api_enabled=True)`: origin A allowed (cache primed) → `PATCH /api/v1/apps/stoktok {"origins":[B]}` → A not echoed, B echoed, without advancing the clock. Also `PATCH {"status":"disabled"}` → not echoed. |
| `test_rbac_origins.py › test_is_loopback_origin` / `test_allowed_origins_drops_loopback_in_staging_production` | Helper. |
| `test_config.py › test_cors_origins_env_ignored` | `CORS_ORIGINS` env set → `Settings()` builds, no `cors_origins` attribute. |
| `test_jwks.py › test_jwks_wildcard_without_credentials_for_registered_origin` | JWKS with Origin of a registered app → `ACAO: *`, no ACAC, no `Vary: Origin` added. |

## Approach
1. **Shared helper (rbac/origins.py).** Move `_is_loopback` from `auth/service.py` as
   `is_loopback_origin(origin: str) -> bool` (input already normalized) and add
   `allowed_origins(origins: Iterable[str], app_env: str) -> list[str]` returning `origins` minus
   loopback when `app_env in {"staging", "production"}`. Pure functions, no imports. Rewrite
   `AppContextService.check` to `if normalized not in allowed_origins(app.origins, self.app_env)`.
   The registry builds its set from the same function, so status/loopback rules cannot drift.
2. **Middleware (app/core/cors.py)**, pure ASGI (not `BaseHTTPMiddleware`, no Starlette
   `CORSMiddleware`):
   - Pass through unchanged unless `scope["type"] == "http"` and `scope["path"].startswith(path_prefix)`
     where `path_prefix = "/api/v1/auth/"` (trailing slash: excludes `/api/v1/authx` and the bare
     `/api/v1/auth`). `/.well-known/` and every admin route are therefore untouched.
   - Origin: read with `starlette.datastructures.Headers(scope=scope).getlist("origin")`; exactly
     one value, not `"null"`, else "no origin". Allowed = `await registry.is_allowed(raw)`; the
     registry does a **raw, exact** set lookup (no normalization: browsers send the serialized
     lowercase origin without default port, and we never echo anything but the received bytes).
   - **Preflight** = method `OPTIONS` and header `access-control-request-method` present. Respond
     directly with 204, empty body, `Vary: Origin, Access-Control-Request-Method,
     Access-Control-Request-Headers`. Add the allow headers only if origin allowed **and** ACRM in
     `{"GET", "POST"}` **and** every comma-split, stripped, lowercased, non-empty ACRH item is in
     `{"content-type", "x-mm-app"}`. Allowed headers: `Access-Control-Allow-Origin: <raw>`,
     `Access-Control-Allow-Credentials: true`, `Access-Control-Allow-Methods: GET, POST`,
     `Access-Control-Allow-Headers: Content-Type, X-MM-App`, `Access-Control-Max-Age: 600`.
     Never call the inner app.
   - **Actual/simple requests**: compute `allowed` before calling the inner app, wrap `send`; on
     `http.response.start` use `MutableHeaders(scope=message)`: `add_vary_header("Origin")`;
     if allowed, set ACAO=<raw>, `Access-Control-Allow-Credentials: true`,
     `Access-Control-Expose-Headers: Retry-After`; if no `cache-control` header, set
     `Cache-Control: no-store`. Existing `no-store` + `Pragma` on `/token` stay.
   - Rejected origin: `logger.debug("cors_origin_rejected origin=%r", raw[:64])` only (no other
     headers, no cookies).
   - Placement: `add_middleware` puts it inside `ServerErrorMiddleware` and outside
     `ExceptionMiddleware`, so AppError/validation/429 responses pass through it (AC6). Unhandled
     500s are produced by `ServerErrorMiddleware` outside it and carry no CORS headers: the SPA sees
     a network `TypeError`. Accepted; documented in ADR-0006 and consumers.md. No catch-all.
3. **Registry (rbac/origin_registry.py).** `AppOriginRegistry(session_factory, app_env,
   ttl_seconds=60.0, failure_backoff_seconds=5.0, load_timeout_seconds=5.0, clock=time.monotonic)`:
   - State: `_origins: frozenset[str]`, `_expires_at: float = 0`, `_generation: int`, `asyncio.Lock`.
   - `is_allowed(origin)`: if `clock() >= _expires_at`, `await _refresh()`; return `origin in _origins`.
   - `_refresh()` (single-flight): acquire the lock, re-check expiry (another waiter may have
     refreshed), remember `gen = _generation`, load inside `asyncio.timeout(load_timeout)`:
     `async with session_factory() as s: rows = await RbacRepository(s).active_app_origins()`;
     set = union of `allowed_origins(row, app_env)`. On success store it with
     `_expires_at = now + ttl` if `gen == _generation`, else `_expires_at = 0` (invalidated during
     the load → the result is not trusted as fresh). On any `Exception`: **fail closed** —
     `_origins = frozenset()`, `_expires_at = now + failure_backoff`, `logger.warning(
     "cors_origins_refresh_failed error=%s", type(exc).__name__)` (class name only, R5/R11).
   - `invalidate()` (sync, cheap): `_generation += 1; _expires_at = 0`. The old set is not served
     after an invalidation.
   - Every instance adds itself to a module-level `weakref.WeakSet`; `invalidate_all()` calls
     `invalidate()` on each (one per `create_app`; tests build many).
   - **Invalidation after commit (chosen mechanism: SQLAlchemy `after_commit` + `session.info`
     flag).** At import, `event.listen(sqlalchemy.orm.Session, "after_commit", _on_commit)`:
     `if session.info.pop(APPS_CHANGED_KEY, False): invalidate_all()`; and
     `event.listen(Session, "after_soft_rollback", _on_rollback)`: when
     `previous_transaction.parent is None`, `session.info.pop(APPS_CHANGED_KEY, None)`.
     `AsyncSession` wraps a sync `Session`, so the class-level listener covers request sessions,
     the CLI (USR-07, other process: TTL only) and the test `db_session`. Chosen over router-level
     invalidation because it fires exactly after the real commit in `get_session`'s teardown (a
     concurrent refresh cannot re-cache the pre-commit row), needs no plumbing through routers
     and also covers future non-HTTP writers. A spurious invalidation only costs one query; a
     lost one is bounded by the TTL.
   - **TTL = 60 s** (choice): staleness only matters for writers in another process (CLI, manual
     SQL) and for future replicas; in-process writes invalidate immediately. Stale entries only
     let a page *read* responses; CSRF stays exact per request.
4. **Repository / service.** `RbacRepository.active_app_origins()`:
   `select(App.origins).where(App.status == AppStatus.ACTIVE.value)`. `mark_apps_changed()` sets
   the flag. `AppService.create` (after `_build_for_app`), `update` (after the successful `save`,
   i.e. only when `changed`), `delete` (after `delete_app`) call it. R1 met: the service never
   touches the ORM directly; core never imports modules.
5. **Wiring (app/main.py).** Import `SessionLocal` from `app.db.session` and `AppOriginRegistry`
   from rbac (importing the module registers the listeners). Add the middleware unconditionally
   (all environments). Remove `_CredentialedCORS`.
6. **Config.** Delete `cors_origins` and its validator. `extra="ignore"` makes a leftover
   `CORS_ORIGINS` harmless.
7. **conftest.** Add an `@asynccontextmanager` `_registry_session()` yielding `db_session` and assign
   it to `app.state.origin_registry.session_factory` in `make_client` and `make_strict_app`. The
   loader finishes before the handler runs, so the shared session is never used concurrently.
8. **Docs** (content, concise):
   - `docs/deploy.md` "Browser origins (CORS)": no env var; the API echoes the origins of active
     apps (`apps.origins`, D19) for `/api/v1/auth/*` only; register/update them with the USR-07
     CLI (or the admin API later); changes through the API process apply at once, through the CLI
     within 60 s; loopback origins are ignored in staging/production; a stale `CORS_ORIGINS` in the
     deployment env file is ignored and can be deleted from `mmlabs-online/deployment`.
   - `docs/consumers.md` "Calling the identity API from the browser" (ricotdin):
     `fetch(..., {credentials: "include"})` on every `/api/v1/auth/*` call; headers
     `Content-Type: application/json` and `X-MM-App: ricotdin` (the custom header forces a
     preflight; that is expected); the page must run on a registered origin, exact scheme + host,
     no port, no path (`https://ricotdin.mmlabs.online`, `https://ricotdin-staging.mmlabs.online`
     once registered by USR-07); a CORS failure shows up as a `TypeError: Failed to fetch`, not a
     403 — check the page origin and the app's `origins`; 403 `origin_not_allowed` means the origin
     passed CORS (belongs to some app) but not this `X-MM-App`; read `Retry-After` on 429 (exposed);
     a 500 is not readable cross-origin; the API sends `Cache-Control: no-store`. Local development:
     run a local API (`make dev`, `http://localhost:8000`) with the SPA on
     `http://localhost:<port>` registered as an origin of the local app; use the same host name on
     both sides (`localhost` vs `127.0.0.1` is cross-site and the cookie is not sent); a local SPA
     cannot use the production API (loopback ignored there, and the request is cross-site); Safari
     may refuse `Secure`/`__Host-` cookies on `http://localhost` — use Chrome/Firefox or local https.
     Update the prerequisites line (USR-03e done).
   - ADR-0006 "Dynamic CORS for identity endpoints": context (D12, D19, D20), decision (items 2–3
     above, scope, `CORS_ORIGINS` removed, per-process cache like ADR-0002: before more workers or
     replicas, staleness is up to the TTL), consequences (500 not readable, origin echoed for app A
     while `X-MM-App: B` is fine because CSRF is per app), open owner confirmations (Assumptions
     A1, A3).
9. `make check`.

Rules: R1 (layering above; core/ has no module import), R3 (no new endpoint; CORS limited to
class 2/3 identity endpoints, none on admin class 1, `/.well-known/` keeps `*`), R5 (one env key
removed from `.env.example`; no secrets logged), R6 (envelopes unchanged, now readable), R7 (no
other changes; follow-ups below), R8 (tests above), R9 (no API change), R11 (CORS echoes only
`apps.origins` with credentials; `Origin`/`X-MM-App` check unchanged).

Size: ~280 lines of code + docs, ~350 lines of tests, no migration, one feature. Not split.

## Specialist input used
- api-platform-engineer: adopted pure-ASGI middleware + protocol, 60 s TTL, asyncio lock,
  `/api/v1/auth/*` scope, preflight never routed, `Vary` merge, `Retry-After` exposed, after-commit
  invalidation via `session.info` + `after_commit`, drop `CORS_ORIGINS`, fake-registry unit tests +
  DB integration, 500 limitation documented. Rejected "keep the last good set for a short time" on
  DB failure in favour of the security architect's fail-closed empty set (refresh only happens
  once the set is already stale or invalidated).
- identity-access-engineer: adopted the shared `allowed_origins` helper with `AppContextService`
  keeping its uncached read, 204 without ACAO for unknown preflights, `null` = unknown, the full
  consumers.md local-dev guidance, the staging-key risk and the R11 drift (as owner items).
  Rejected keeping `CORS_ORIGINS` for admin Bearer calls (no admin UI exists; see A1). Rejected
  normalizing the request Origin before lookup in favour of the security architect's raw exact
  match.
- security-architect: adopted raw exact match + raw echo, credentials only with an echoed origin,
  no wildcard path, single-flight, set lookups (unknown origins cost no query), invalidation after
  commit, fail closed, `Vary: Origin` everywhere in scope, `Cache-Control: no-store`, ACRM/ACRH
  validation, `/api/v1/auth/` with trailing slash, no CORS on admin routes before ADM-002,
  debug-level truncated rejected-origin log. Chose 204 (not 400) for every denied preflight.
  Origin uniqueness/admin exclusivity and admin-UI CORS → follow-ups.

## Assumptions
- **A1 (owner to confirm, non-blocking):** `CORS_ORIGINS` is removed entirely. No admin UI exists;
  admin routes are unauthenticated and off in production until ADM-002. When an admin UI arrives,
  its non-credentialed Bearer CORS should come from `apps[admin].origins` in a separate ticket
  (follow-up F2), not from an env var. Reverting means re-adding one setting.
- **A2:** TTL 60 s, failure backoff 5 s, load timeout 5 s, preflight `Max-Age` 600 s (browsers cap
  Chrome at 7200, Firefox 86400; 600 keeps origin removals effective within 10 min of preflight
  cache). Constants, not env settings (no new config surface).
- **A3 (owner question, affects USR-07 not this ticket):** is `https://ricotdin-staging.mmlabs.online`
  a second origin of `ricotdin` (staging and production share accounts and the `aud=ricotdin`
  token is valid for both backends) or a separate app key `ricotdin-staging` with its own audience
  (recommended by the identity consult)? Is there a staging API host, or will staging call
  `api.mmlabs.online`? This ticket works either way.
- **A4 (owner edit):** CLAUDE.md R11 still says cookie `mm_device` with `Path=/api/v1/auth`; D20
  made it `__Host-mm_device`, `Path=/`. Agents do not edit CLAUDE.md; the owner should update R11
  (and may add "CORS scope `/api/v1/auth/*`, ADR-0006").
- **A5:** One uvicorn process (Dockerfile, ADR-0002), so after-commit invalidation reaches every
  request in production today; other processes (CLI) rely on the TTL.
- **A6:** Raw exact matching means an Origin that only matches after normalization (uppercase,
  `:443`) gets no ACAO while the CSRF check (normalizing) would pass it. Browsers never send those
  forms; the result is fail-closed.
- **A7:** The role catalog (`GET /api/v1/apps/{key}/roles`, D17) gets no CORS: it is for app
  backends. A browser need would be a new ticket.

Follow-ups (not in this diff):
- F1: Origin uniqueness across apps / `admin` app origins exclusive, before ADM-002 enables
  `aud=admin` (security consult Q1; USR-03 Q3, USR-03c F3).
- F2: Admin UI CORS (non-credentialed, Bearer) from `apps[admin].origins`, with ADM-002 / the admin
  UI ticket.
- F3: Remove a leftover `CORS_ORIGINS` from `mmlabs-online/deployment` if present.
- F4: Shared cache (or pub/sub invalidation) before running more than one worker/replica
  (together with the ADR-0002 rate limiter).

## Blocking questions
None.

## Split proposal
None.

## Data model / migration
None (reads `apps.origins` and `apps.status`; no schema change).

## API contract
No endpoint added or changed. Header contract for `/api/v1/auth/*` only:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| OPTIONS (preflight) | `/api/v1/auth/*` | `Origin`, `Access-Control-Request-Method`, optional `Access-Control-Request-Headers` | Allowed: `ACAO: <Origin>`, `ACAC: true`, `ACAM: GET, POST`, `ACAH: Content-Type, X-MM-App`, `ACMA: 600`. Always `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`. | 204 (allowed or denied; denied has no `Access-Control-Allow-*`) | none (middleware) |
| any (actual) | `/api/v1/auth/*` | `Origin` | Registered origin: `ACAO: <Origin>`, `ACAC: true`, `ACEH: Retry-After`. Always `Vary: ...Origin`; `Cache-Control: no-store` unless set. | unchanged (incl. 401/403/422/429 envelopes; 500 without CORS) | unchanged |
