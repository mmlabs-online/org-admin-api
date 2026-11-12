# consult-api-platform-engineer — USR-03

**Findings**
- CORS today is static: `CORSMiddleware(allow_origins=settings.cors_origins, allow_headers=["*"])` in `app/main.py`. D12 needs origins taken from `apps.origins`, which Starlette's middleware cannot do. Its rejected preflight is a plain-text 400, not the R6 envelope.
- The ticket's `403 {error:"not_member", join_available}` breaks R6. It has to fit the envelope.
- R3 lists login as **public**, but §2 says login carries `X-MM-App` and an `Origin` check. Login reads and sets `mm_device`, so leaving it unchecked allows login CSRF (an attacker signs the victim into the attacker's account).
- We can reuse `RateLimitedError` (already sends `Retry-After`), `TurnstileFailedError`, `strong_etag`/`if_none_match` and the `make_client` fixtures.
- JWKS has to be mounted with `app.include_router(...)` and no `api_prefix`, the same way as `health_router`.

**Recommendations**
1. **Status codes** (R6):
   - Login → 200 (a session is a side effect, not a resource).
   - Logout → 204, with `Set-Cookie mm_device=; Max-Age=0` (same Path) on `{all:true}`.
   - Token → 200 `{access_token, token_type:"Bearer", expires_in, audience}`.
   - Login and token responses: `Cache-Control: no-store`.
2. **Error codes, all in the envelope:**
   - 401 `invalid_credentials`: one body for unknown email, wrong password and disabled user.
   - 403 `turnstile_required` (token missing after 3 failures) vs the existing `turnstile_failed`.
   - 429 `rate_limited` with `Retry-After`, for both per-email backoff and per-IP limits. Backoff is keyed by the normalized email's hash and applies whether or not the account exists.
   - 401 `session_invalid`: no cookie, unknown or revoked account, or reuse after grace. One code avoids an oracle. Clear the cookie when the device session is revoked.
   - 403 `origin_not_allowed` / `app_header_required`.
   - 403 `email_unverified`.
   - 403 `not_member` with `details:{"join_available": bool}`.
   - 403 `app_disabled`. 404 for an unknown audience.
   - 422 for `org_id` until USR-06.
3. **Audience must equal `X-MM-App`, and `Origin` must be in that app's origins.** Otherwise a stoktok page could mint `aud=admin` tokens for the signed-in account.
4. **CORS:** a small custom ASGI middleware (`app/core/` or the auth module):
   - Cache of all active apps' origins, refreshed with a TTL.
   - Echo `Origin` plus `Vary: Origin` and `Allow-Credentials`.
   - Preflight `Allow-Headers: Content-Type, X-MM-App`, methods `GET, POST`.
   - Wrap error responses too, so the browser can read 401/403 bodies.
   - Per-endpoint Origin/X-MM-App checks go in a dependency (`CookieAuthDep`) that raises `AppError`s (R1).
5. **Schemas** (`extra="forbid"`):
   - `LoginRequest{email, password: SecretStr, turnstile_token?}`.
   - `LogoutRequest`: exactly one of `account_id` or `all: Literal[True]`, else 422.
   - `TokenRequest{audience, account_id: UUID, org_id: UUID|None}`.
6. **JWKS:** 200 `{keys:[{kty, crv|n,e, kid, alg, use:"sig"}]}`, plus `ETag` and `Cache-Control: public, max-age=300`. Keep max-age short so key rotation spreads quickly. Rate-limit it per IP.
7. **OpenAPI:**
   - `APIKeyCookie(name="mm_device", auto_error=False)` as the security scheme.
   - `X-MM-App` declared as a header parameter.
   - `Set-Cookie` documented on login, token and logout.
   - A `responses=` map listing every error code above.
8. **Test plan** (on top of the ticket's list):
   - Exact key set of each response; no password or email echoed.
   - `Set-Cookie` attributes parsed one by one.
   - Preflight from an allowed vs an unknown origin.
   - 401 bodies carry CORS headers.
   - Audience ≠ `X-MM-App` → 403.
   - Logout `{}` or both fields → 422.
   - JWKS: `kid` matches the token header; 304 on `If-None-Match`; mounted at the root, not under `/api/v1`.
   - Backoff and rotation tests driven by an injected clock.
9. **Size and split:** this is XL (about 2 tables, 3 endpoints, middleware, crypto, brute force, ~40 tests). Proposed split:
   - **USR-03a:** sessions, cookie and rotation, login/logout, brute force, CORS/CSRF.
   - **USR-03c:** signing keys from env, token, JWKS, rotation doc.

**Risks**
- Removing `CORS_ORIGINS` could break the admin UI or local dev unless `admin` and localhost rows exist in `apps.origins`.
- Backoff and per-email state live in process (ADR-0002) and are lost on restart; that is acceptable only with a single worker.
- A token response that also rotates the cookie can race between parallel tabs. The 60 s grace must cover concurrent requests that present the old value.
- `ServerErrorMiddleware` 500s bypass the CORS middleware.

**Open questions**
1. Is login reclassified as public plus Origin/`X-MM-App` (recommended), and does R3 need rewording?
2. Must `audience` equal `X-MM-App`? Recommended: yes.
3. Unknown audience: 404, or fold it into 403 `app_disabled`?
4. Keep `CORS_ORIGINS` as a fallback for non-auth paths, or drop it?
5. EdDSA or RS256? The choice shows in JWKS `kty`/`alg` and in client library support.
6. JWKS `max-age` versus the key-rotation overlap window.
