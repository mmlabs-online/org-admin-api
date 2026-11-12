# consult-security-architect — USR-03

### Findings
1. **SameSite=Lax does not separate apps.** Every `*.mmlabs.online` host is same-site with the API, so the cookie goes along on any credentialed request from any subdomain. The only real CSRF boundary is the `Origin` check. `X-MM-App` is chosen by the caller, so its only real job is to force a preflight.
2. **Existing CORS setup conflicts with D12.** `app/main.py` adds a static `CORSMiddleware` from `settings.cors_origins` with `allow_credentials=True` and `allow_methods/headers=["*"]`. If someone sets `"*"`, Starlette echoes any origin and sends credentials with it. It also ignores `apps.origins`.
3. **Cookie tossing / login fixation.** A subdomain that is compromised or taken over (dangling DNS) can set `mm_device` with `Domain=mmlabs.online; Path=/api/v1/auth`. If the victim then logs in, the attacker still holds the old value, which the 60 s grace accepts, so they can reach the victim's new account session. `__Host-` is not possible because it needs `Path=/`.
4. **Reuse detection only works for one step.** Only `prev_token_hash` is kept, so a value two rotations old looks the same as random garbage. With 24 h rotation, a stolen cookie can be used for up to about 24 h before anything is detected.
5. **Credential stuffing (one attempt per email, many IPs) never triggers the per-email backoff or Turnstile.** If counters exist only for known users, then "Turnstile required" or backoff responses tell an attacker which emails are registered.
6. **Apps may share an origin.** `apps.origins` is not unique across apps, so one origin could mint tokens for several audiences, including `admin`.

### Recommendations
- **CSRF / Origin (AC CORS):** on every cookie POST, and also on `login` (it sets the cookie, so login CSRF is possible), require all of these:
  - `Origin` is present, is not `null`, and after `normalize_origin` exactly matches an entry in `apps[X-MM-App].origins`.
  - the app is active.
  - otherwise return 403.
  - Optionally also reject `Sec-Fetch-Site: cross-site`.
- **Token audience (AC token):** `audience` must equal `X-MM-App`, and `Origin` must belong to that app. This makes the browser-set Origin the binding, so XSS on stoktok cannot mint an `aud=admin` token.
- **Origin rules:** enforce that origins are unique across apps (or at least that admin's origins are exclusive). Ignore loopback origins at runtime when `app_env` is staging or production, for both the CORS echo and the CSRF check.
- **CORS:** replace the static middleware with a dynamic check against `apps.origins` (cached, invalidated on app update), scoped to `/api/v1/auth/*`.
  - Always send `Vary: Origin`.
  - Preflight for an allowed origin: methods `POST` (GET for USR-03b), headers `Content-Type, X-MM-App`, `Max-Age` around 600.
  - Unknown origin: no `ACAO` headers.
  - JWKS: `ACAO: *` without credentials.
  - Drop `CORS_ORIGINS` or forbid `*` in it.
- **Cookie and rotation (AC cookie/rotation):**
  - Value from `secrets.token_urlsafe(32)`; store SHA-256 of it (Argon2 is not needed for 256-bit random values); unique index on `token_hash`, index on `prev_token_hash`.
  - Rotate with an atomic `UPDATE … WHERE token_hash=:cur RETURNING` so that tabs racing at rotation time do not cause false reuse revocations.
  - Keep `prev_token_hash` after the grace window ends so a later reuse can still be detected.
  - On reuse, revoke the device session and all its account sessions, and log a warning.
  - **Fixation fix:** a request authenticated with the grace (previous) value may only use account sessions created before `rotated_at`.
  - Reject a request whose Cookie header carries more than one `mm_device` (a sign of tossing).
  - Send `Cache-Control: no-store` on login, token and logout responses.
- **Brute force (D11):**
  - Order of checks: per-IP limiter (new `login` scope), then per-email backoff, then Turnstile, then Argon2.
  - Count unknown emails exactly like known ones, keyed by an HMAC of the normalized email.
  - Verify a dummy hash for unknown emails so timing matches.
  - Return the same `turnstile_required` and 429 + `Retry-After` responses for both cases.
  - Add a global failure-rate trigger that makes Turnstile mandatory for everyone, to cover stuffing.
  - Require `TURNSTILE_ALLOWED_HOSTNAMES` to be set in production.
  - Disabled users get the same 401.
  - Reset the per-email counter on success.
- **JWT:**
  - Ed25519 (EdDSA). Pin `algorithms=["EdDSA"]` on both sign and verify; never trust the header `alg`.
  - Resolve `kid` only from a static map of configured keys; ignore `jku`, `x5u` and `jwk`.
  - Header `typ: at+jwt`. Claims: `iss` exact, a single-string `aud`, and `jti`.
  - Re-check user status and membership every time a token is minted.
  - Private keys from env as `SecretStr`. Staging/production refuse to start without a signing key. Test that JWKS never contains `d`.
  - JWKS `Cache-Control: max-age=300`.
  - Rotation runbook: publish the next public key at least one cache TTL before switching the signer; keep the old public key at least 15 min plus skew after the switch.
  - Verifier leeway of 60 s or less.
- **Logout:** a token already issued stays valid until it expires (up to 15 min). Document this, and the client library must drop its in-memory token. `{all:true}` clears the cookie with the same attributes it was set with.
- **Logging (R5/R11):**
  - **Log:** login success or failure (user_id or HMAC'd email, reason code, ip_key, app), rotation, reuse detected (device_session_id), logout, token issued (user_id, aud, kid, jti), Origin/X-MM-App rejections (normalized, truncated origin).
  - **Never log:** cookie values or their hashes, JWTs, passwords, Turnstile tokens, full emails, key material.

### Risks
- Residual: a compromised *registered* app origin can mint tokens for its own audience for any account signed in on the device. This follows from the D5 design.
- The backoff and limiter are in-process (ADR-0002): they reset on restart and fail open under key flooding.
- Disabling a user or revoking access leaves a token valid for up to 15 min.
- A subdomain takeover anywhere under `mmlabs.online` turns into cookie tossing, so DNS hygiene matters.

### Open questions
1. Should `admin` tokens have a shorter TTL (for example 5 min) or an immediate revocation path?
2. Admin UI Bearer calls to `/api/v1/*` need CORS without credentials. Which origins, and from where (`apps[admin].origins`)?
3. Should origins be unique across apps globally, or only for `admin`?
4. Should the per-email backoff state live in Postgres so it survives restarts?
