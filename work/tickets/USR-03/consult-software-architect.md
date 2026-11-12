# consult-software-architect — USR-03

### Findings
1. **The per-request DB session undoes cookie-reuse revocation.** `get_session` (app/db/session.py) rolls back on any exception. If the service revokes a device session when an old cookie is reused (S2) and then raises a 401 `AppError`, the revoke is rolled back. Failed-login counters stored in the DB would be lost the same way.
2. **CORS is static today.** `main.py` sets up `CORSMiddleware` from `settings.cors_origins`. D12 needs the allowed origins to come from the DB (`apps.origins`). A preflight request carries only header *names*, not the `X-MM-App` value, so CORS can only check the union of all active apps' origins. The per-app match can only happen in the actual POST.
3. **Reusable pieces already exist:**
   - `rate_limit()` and `InMemoryRateLimiter` (ADR-0002; new scopes needed).
   - `client_ip` / `ip_key`, `PasswordHasher.verify` / `needs_rehash`, `TurnstileVerifier`.
   - `AuthzService.effective_roles` (it does not check user status, email verification or app status, as its docstring says).
   - `normalize_origin`.
4. **No JWT library is installed** (only `argon2-cffi`, `httpx` and the base stack). Adding one is a new dependency; check that this counts as a dependency, not a stack change.
5. **Nothing in the ticket stops a cross-app token request.** It does not say that `audience` must equal `X-MM-App`. Without that, any origin of app A could get a token for app B.

### Recommendations
- **Module boundaries (R1):**
  - New `app/modules/sessions/` holds the `DeviceSession` and `AccountSession` models, a repository, and a `DeviceSessionService` with `resolve`, `rotate`, `add_account` and `revoke`. It has no HTTP code; it takes and returns raw token strings.
  - New `app/modules/tokens/` holds `keys.py` (the key set loaded once into `app.state`), `issuer.py` (pure: claims in, JWT out), a `TokenService` (sessions + users + apps + `AuthzService`) and a `jwks_router`.
  - `app/modules/auth/` keeps the HTTP surface: login, logout and token routes; cookie set/clear helpers; the `login_throttle.py` backoff state; and a `require_app_context` dependency that checks Origin and `X-MM-App` and returns the `App`.
  - Register the new models in `app/db/models.py`.
- **Order of each cookie request:**
  1. A dependency reads `mm_device` and calls `resolve()`, which locks the device row with `SELECT … FOR UPDATE`.
  2. If the cookie matches `token_hash`: when `rotated_at` is more than 24 h old, move the current hash to `prev` with `prev_valid_until = now + 60 s`, store the new hash, and return the new raw value.
  3. If it matches `prev` within the grace window: accept it and do not rotate again.
  4. If it matches `prev` after the grace window: revoke the device session, **commit, then raise 401** (Finding 1). Do this with an explicit commit in the service before raising, or with a small "commit-then-raise" error type that `get_session` honours. The plan must pick one and test it.
  5. On success the route sets `Set-Cookie` on the injected `Response`.
- **Brute force (D11):** keep the per-email backoff and failure counts in process memory, behind a protocol like `RateLimiter`. Key it by a hash of the normalised email, not the plain email (R11). Amend ADR-0002 to record this. Ask for Turnstile when either the email or the IP has 3 or more failures. Add a per-IP scope `login` at about 20 per minute. On an unknown email, run a dummy Argon2 verify so timing matches a wrong password.
- **JWT signing:** use **EdDSA (Ed25519)** with `PyJWT[crypto]`. Keys are small, signatures deterministic, and our consumers (`mmlabs-auth` and Node `jose`) support it. The alternative is ES256: wider support, a bit more friction. Avoid RS256 because the keys are large.
  - Config: `JWT_SIGNING_KEYS` as a `SecretStr` JSON list of `{kid, private_pem_b64}`, plus `JWT_ACTIVE_KID`.
  - Check at startup that keys parse and `kid`s are unique. Staging and production should refuse to start without keys, like `TRUSTED_PROXY_CIDRS`.
  - JWKS publishes every key with `Cache-Control: max-age=300`.
  - Rotation runbook: add the new key unpublished as active-for-verify only → wait for the JWKS cache TTL → switch `ACTIVE_KID` → remove the old key after token TTL + cache TTL. Put it in `docs/deploy.md`.
  - Tokens carry `iss`, `aud`, `sub`, `email`, `email_verified`, `roles`, `iat`, `exp` and a `jti` (optional).
- **CORS/CSRF:** replace `CORSMiddleware` with a small pure-ASGI middleware in `app/core/cors.py`. It depends on an `OriginRegistry` protocol, implemented in rbac as a cache of active apps' origins with a 30–60 s TTL that rbac service writes invalidate, and is wired in `main.py`. This keeps core from importing modules. The per-app CSRF check is the `require_app_context` dependency; also require `audience == X-MM-App` on `/token`. JWKS gets `Access-Control-Allow-Origin: *` without credentials.
- **Split (estimated production lines):**
  - **03a, about 400 lines:** migration and models; sessions module; cookie; login with backoff, Turnstile after 3 failures and per-IP limit; logout one or all; `require_app_context`, which can initially check against a direct DB lookup.
  - **03b, about 300 lines, depends on 03a:** key set, settings, issuer, `/token`, JWKS, rotation runbook.
  - **03c, about 200 lines, depends on 03a and can run in parallel with 03b:** dynamic CORS middleware, origin registry cache, removal of `CORS_ORIGINS`.
  - The existing USR-03b is renamed **USR-03d** to avoid a name clash. Update `work/backlog.md` and the dependencies for ADM-002 and USR-04.

### Risks
- **Loopback origins (D19) and SameSite=Lax:** a local dev app at `localhost` calling `api.mmlabs.online` is cross-site, so the cookie is not sent. Local development needs a local API.
- **Single-process memory:** backoff state resets when the process restarts, and it breaks once there is more than one worker (same as ADR-0002).
- **Login CSRF and cookie planting:** login is listed as public. Without an Origin and `X-MM-App` check, an attacker could plant a device cookie in the victim's browser.
- **Origin-cache staleness:** a removed origin keeps CORS access until the cache TTL runs out.

### Open questions
1. Should `/login` also require Origin and `X-MM-App` (recommended), even though R3 lists it as public?
2. Should `CORS_ORIGINS` be removed, or kept as a static extra list for the admin UI before its app row exists?
3. EdDSA or ES256? Does any planned consumer lack Ed25519 support?
4. Is it acceptable to add `PyJWT[crypto]` as a dependency?
5. When `org_id` is given, should the token request return 422 always, or only for `org_mode = required` apps, until USR-06?
