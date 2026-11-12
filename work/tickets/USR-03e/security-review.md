# Security review — USR-03e

## Attempt 1

**Verdict: no blockers.** I found nothing that must be fixed before merge. There are two small should-fix items and several notes. I ran only `uv run pytest tests/test_cors.py -q`, and all 28 tests passed. I wrote no files.

### Findings

🟡 **S1: The cache lock is tied to one event loop.** `app/modules/rbac/origin_registry.py:50` creates `asyncio.Lock()` when the app is built. Python 3.12 ties the lock to the first event loop that has to wait on it. If the same app object is later used from another loop (a test harness, or a future multi-loop runner), every auth request that carries an `Origin` gets a `RuntimeError`. That error is raised outside the `try` in `_refresh` (`:61-75`), so the result is a 500, not a clean fail-closed response. Production is safe today: uvicorn runs one loop per process.
- Fix: create the lock lazily inside `_refresh` (`if self._lock is None: self._lock = asyncio.Lock()`), or note the one-loop assumption in the docstring.

🟡 **S2: Preflight responses carry no `Cache-Control`.** `app/core/cors.py:68-87` sets `Vary` on preflights but no `Cache-Control`, unlike normal responses (`:61-62`). Cloudflare does not cache `OPTIONS` by default, and browsers key their preflight cache per origin, so there is no exploit today. But if a cache rule ever turns on caching for `OPTIONS`, a cached "allowed" preflight could be served to other origins.
- Fix: add `out["Cache-Control"] = "no-store"` in `_preflight`. Defence in depth, one line.

🟢 **Notes (checked, no change needed)**
- **Exact-match echo.** `cors.py:41-43` compares the raw `Origin` against the cached set. Both checks pass on the expected cases:
  - Accepted: only an origin that is exactly in the set, with one `Origin` header (not `null`).
  - Rejected: `null`, a missing `Origin`, multiple `Origin` headers, and case variants (`test_case_variant_origin_not_echoed`).
  - The value echoed back is the value received, which by then equals a stored, normalized value. Nothing is normalized and then reflected.
- **Credentials only with an echo.** `Access-Control-Allow-Credentials` is set only together with `Access-Control-Allow-Origin`: `cors.py:57-60` for normal responses, `:76-85` for preflights.
- **Path scope.** `startswith("/api/v1/auth/")` (`main.py:42`) excludes `/api/v1/authx` (`test_prefix_lookalike_untouched`).
  - CORS and routing both read `scope["path"]`, which is already percent-decoded, so an encoded path such as `%2F` gets the same treatment from both.
  - Starlette does not resolve `..`, so a path like `/api/v1/auth/../x` matches no route and returns 404.
  - No `--root-path` is used (`Dockerfile:18`).
  - The bare `/api/v1/auth` has no route, so leaving it uncovered is harmless.
- **Preflights are never routed** (`cors.py:47-49`, `test_preflight_never_reaches_app`). Allowed methods are `GET, POST`; allowed headers are `Content-Type, X-MM-App`; `Max-Age` is 600. For an unknown origin or a method/header outside those lists, the reply is a 204 with no `Access-Control-Allow-*` headers.
- **`Vary` and `Cache-Control`.** `Vary: Origin` is merged in, never duplicated, and added even for unknown origins (`:54-56`). `Cache-Control: no-store` is added only when the route set none (`:61-62`).
- **Error responses.** The CORS middleware sits inside Starlette's `ServerErrorMiddleware` and outside `ExceptionMiddleware`. So `AppError` responses (401/403/422/429, `RateLimitedError`) get the CORS headers, and `Retry-After` is exposed (`cors.py:21`). A 500 is not readable cross-origin; this is documented in `docs/consumers.md` and acceptable.
- **Fail closed.** A load error or a 5 s timeout leaves an empty set for 5 s (`origin_registry.py:70-74`). The log line records only the exception class name. If the request is cancelled mid-refresh, the lock is released and the next request reloads. Only one refresh runs at a time, with a recheck inside the lock (`:62-64`). An invalidation that arrives during a load is respected through the `generation` counter (`:76-78`).
- **Listeners** (`origin_registry.py:89-98`).
  - The flag lives in `session.info`, which belongs to one session, and `SessionLocal` creates a session per request (`app/db/session.py:30`). So a flag cannot leak between sessions.
  - `after_commit` fires only on the outermost commit.
  - An outermost rollback drops the flag, and `get_session` rolls back when a commit fails.
  - The services set the flag only after the write and its flush succeed (`rbac/service.py:207, 250, 265`).
  - The worst failure mode is invalidating more often than needed, which is harmless.
  - The cookie CSRF check (`AppContextService`, `auth/service.py:89-93`) reads `app.origins` fresh from the DB. A stale cache can therefore only make a response readable for up to 60 s per worker; it never lets a request through.
  - In production the RBAC admin router is gated off, so origin changes come from the CLI and take effect within the 60 s cache lifetime, as `docs/deploy.md` says.
- **Loopback in production.** `allowed_origins` drops loopback origins when `app_env` is staging or production (`rbac/origins.py:255-259`), and `app_env` can only be one of four fixed values (`config.py:28`).
  - A stored non-normalized loopback value (only possible via manual SQL) can never match, because browsers send lowercase origins with no trailing slash.
  - Covered by `test_loopback_origin_not_echoed_in_production`.
- **Logging.** Rejected origins are logged at debug level only, shown with `%r` and cut to 64 characters (`cors.py:44-45`), so log injection is not possible. An origin is not a secret.
- **Old middleware fully removed.** No `CORSMiddleware` or other credentialed CORS remains anywhere in `app/`.
  - JWKS still sets its own `Access-Control-Allow-Origin: *` without credentials (`tokens/jwks_router.py:32`).
  - The role catalog and the admin paths now have no CORS at all (`test_role_catalog_and_admin_paths_have_no_cors`).
  - A leftover `CORS_ORIGINS` env var is ignored because settings use `extra="ignore"` (`config.py:22`, `test_cors_origins_env_ignored`).
- **Scope.** `origin/main..HEAD` also contains `568303a solo: deploy runbook…`, a separate logged `/solo` commit that changes `docs/deploy.md`. The reviewer should not count it against R7.

### Follow-ups (out of scope)
- When an admin web UI needs cross-origin Bearer calls, add a separate non-credentialed CORS policy for `/api/v1/*`, sourced from `apps[admin].origins` (the owner decides). Today those calls have no CORS.
- Consider a DB `CHECK` constraint, or a CLI-side `normalize_origins` check, so that origins inserted by manual SQL are always normalized.
- Cache invalidation does not reach other worker processes; the 60 s cache lifetime is the bound. If urgent revocation of an origin is ever needed, add Postgres `LISTEN/NOTIFY`.
