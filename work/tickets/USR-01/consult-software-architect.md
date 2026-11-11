# consult-software-architect — USR-01

## Findings
- Layout is flat per-domain (`app/modules/organizations` reference); routers aggregated in `api_router`; `AppError` subclasses cover 400/403/404/409 and the handler can't set response headers (no `Retry-After`).
- R6 code list lacks 400 and 429, which this ticket needs (invalid Turnstile, rate limit) — small rule/ticket conflict.
- `httpx` is dev-only; no `argon2-cffi`, no citext usage yet.
- Runtime: single container, single uvicorn worker (Dockerfile `CMD`), behind Cloudflare Tunnel → `org-admin-api:8000`. `request.client.host` will be the cloudflared container, not the end user.
- D15 calls it the "Identity module", but the codebase has no nested module structure.
- Staff `/api/v1/users` endpoints would be public until ADM-002, which ships with/after USR-04 (D16) — they would list user emails (PII) on `api.mmlabs.online`.

## Recommendations
1. **Layout (R1, scope):** flat sibling modules, not a nested `identity/` package: `app/modules/users/` (model, repository, service, schemas, staff router `/users`) and `app/modules/auth/` (signup router `/auth`, `AuthService`, `passwords.py`, `turnstile.py`, `hooks.py`). Dependency direction auth → users only. "Identity module" = this family of modules (users, auth, apps, sessions, later organizations); note it in the plan so D15's "move" is a no-op rename later. Trade-off: nested package groups better but breaks the reference-module shape.
2. **Shared components (AC 2/4/5, R5):**
   - `PasswordHasher` in `auth/passwords.py`: Argon2id via `argon2-cffi`, params from `Settings`, methods `hash`, `verify`, `needs_rehash` (USR-03 rehash). Run hashing via `anyio.to_thread.run_sync` — single worker must not block the event loop. Policy (min length, common-password list) in the same file; ship the list as a packaged data file loaded once, no external lookup.
   - `TurnstileVerifier` Protocol (`async verify(token, remote_ip) -> bool`) + `CloudflareTurnstileVerifier` (needs `httpx` moved to runtime deps) + `FakeTurnstileVerifier` in tests; provided via a FastAPI dependency so tests use `dependency_overrides`. `TURNSTILE_SECRET_KEY: SecretStr`; fail closed — refuse to start in `staging/production` without it, never "skip if unset".
   - `RateLimiter` Protocol + `InMemoryRateLimiter` (fixed/sliding window keyed `(scope, ip)`, evict expired keys, cap size) in `app/core/rate_limit.py` (login/forgot reuse it), exposed as `Depends(rate_limit("signup"))`. Run it **before** the Turnstile call so abusive clients cost no outbound request. Limits from config. In-process is right for one container; a Postgres limiter adds a table, migration and write-per-request for no current benefit — the Protocol lets USR-03 swap it (per-email backoff does need persistence; that's USR-03's call).
   - `get_client_ip(request)` in `app/core/`, configured by `CLIENT_IP_HEADER` (e.g. `CF-Connecting-IP`, empty = `request.client.host`). The header is trusted only because port 8000 is reachable solely via the tunnel — document that assumption.
3. **Errors (R6):** add `RateLimitedError` (429, `rate_limited`) with `Retry-After` support (optional `headers` on `AppError`, passed through by the handler) and `TurnstileFailedError`; pick **one** status — suggest 400 `turnstile_failed`. Follow-up: amend R6 to list 400/429.
4. **`on_signup` hook (scope, USR-04):** Protocol `SignupHook.__call__(user, session) -> Awaitable[None]`, default no-op, injected into `AuthService` via dependency; runs after insert in the same transaction. Document that hooks must only enqueue/record, no slow I/O. No event bus.
5. **Routers (AC 3/6, R3, R9):** `POST /api/v1/auth/signup` → 201 minimal body (`id`, `email`, `email_verified: false`), never `UserRead`. Staff: `GET /users` (filters `email`, `status`, `Page[UserRead]`), `GET /users/{id}`, `PATCH /users/{id} {status}` for enable/disable (matches R9 and organizations). Note `admin:users:read` / `admin:users:update` per route (docstring or route `openapi_extra`) for R3. `UserRead` must not declare `password_hash`; test it's absent.
6. **Size (~400 lines):** estimate ~500 non-test lines (models+migration ~80, users ~180, auth ~120, core limiter/ip/errors/config ~90, plus data file). Either count only hand-written app code excluding migration/data file, or split into **USR-01a** (users table, hasher, staff endpoints) and **USR-01b** (signup, Turnstile, limiter, hook). I'd take the split if the planner can't keep it under ~400.

## Risks
- **PII exposure:** staff `/users` endpoints public on `api.mmlabs.online` until ADM-002. Mitigate: register the users router only when `app_env != "production"` (or behind a config flag) until ADM-002.
- **Rate-limit bucket collapse:** wrong client IP ⇒ every user shares the tunnel IP; signup becomes a global ~20/min bucket — trivially DoS-able. Header spoofing if the API is ever exposed outside the tunnel.
- **Limiter reset** on deploy/restart; inaccurate if scaled to >1 worker/replica.
- **Email enumeration:** 409 on duplicate email reveals account existence; Turnstile + IP limit mitigate, but it diverges from the login "identical error" principle.
- **CPU:** Argon2 cost × signup volume can starve the single worker; choose params with that in mind (e.g. ~64 MiB, t=3, measured).
- **CORS:** signup is browser-called but still uses static `CORS_ORIGINS` until USR-03 brings `apps.origins` — config must be kept aligned manually.
- **citext migration** needs `CREATE EXTENSION IF NOT EXISTS citext`; downgrade shouldn't drop the extension; confirm `alembic check` has no drift with `postgresql.CITEXT`.

## Open questions
1. Staff `/users` endpoints: hide in production until ADM-002, or defer them entirely to ADM-002?
2. Invalid Turnstile: 400 or 403? (suggest 400)
3. Keep 409 for duplicate email (as in the ticket), or a neutral 201/202 with USR-04 emailing the existing owner?
4. Split USR-01 into 01a/01b, or relax the 400-line budget for this ticket?
5. Is `CF-Connecting-IP` the agreed client-IP source, and is port 8000 guaranteed tunnel-only?
6. Which common-password list and size (top 10k vs 100k)? Licence attribution goes in `THIRD_PARTY_NOTICES.md`.
