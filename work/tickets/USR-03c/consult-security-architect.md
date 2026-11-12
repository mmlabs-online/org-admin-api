# consult-security-architect — USR-03c

### Findings
1. **No JWT library yet.** `pyproject.toml` has no PyJWT or cryptography. The safe design depends on pinning: `algorithms=["EdDSA"]` on both encode and decode. PyJWT never fetches `jku`/`x5u`/`jwk`, but nothing should be built on header-supplied keys, and `PyJWKClient` should not be used server-side.
2. **Error code conflict.** `AppContextService.check` (`app/modules/auth/service.py:89-104`) returns 403 `origin_not_allowed` for an unknown or disabled app. The AC asks for `app_disabled`. Because `AppContextDep` runs first, `app_disabled` can never be returned.
3. **Origins are not unique across apps.** `RbacService.create/update` does not check this. If any app's origin overlaps with `admin`'s origins, XSS on that app can mint `aud=admin` tokens. This was raised in USR-03 (Finding 6) and is still open.
4. **Static `CORSMiddleware` overrides JWKS headers.** For origins listed in `CORS_ORIGINS`, it would replace `ACAO: *` with an echoed origin plus credentials. The data is public, so this is harmless, but tests must expect it.
5. **Multiline PEM does not fit the env file.** The deploy writes `org-admin-api.env`, and Docker env files are single-line.

### Recommendations
- **Algorithms (AC1):** EdDSA/Ed25519 via `PyJWT[crypto]`.
  - Look up `kid` only in a static in-memory dict of configured keys. An unknown `kid` is rejected, and `kid` is never used in a path, SQL or URL.
  - Header is exactly `{alg, kid, typ:"at+jwt"}`.
  - `aud` is a single string. `jti` is a uuid4. `iss` comes from settings and must differ per environment.
  - The reference verifier in tests checks `typ`, `iss`, `aud` and `exp` with leeway ≤ 60 s. Add negative tests for `alg:none`, HS256 signed with the public key, an unknown `kid`, and an injected `jku`.
- **Keys (AC5):**
  - `JWT_SIGNING_KEY: SecretStr` holds base64 of an Ed25519 PKCS8 PEM (one line).
  - `JWT_PUBLIC_KEYS_EXTRA` is a JSON list of base64 public PEMs.
  - Validator rules:
    - The signing key must be Ed25519 private; extra keys must be public only.
    - `kid` = RFC 7638 thumbprint; no duplicates.
    - Staging/production refuse to start without a signing key; local/test generate one per process.
    - Re-raise parse errors `from None` with a generic message.
  - Never log or `repr` the key. At startup, log `kid`s only.
- **JWKS (AC6):**
  - Build the body once at startup; strong `ETag` from a body hash; return 304 on `If-None-Match`.
  - Test that each key has exactly the members `{kty,crv,x,kid,alg,use}` (an allowlist, not just "no `d`").
  - Use a generous per-IP limit, like the role catalog (app backends share an egress IP).
- **Audience (AC2):**
  - `audience` must equal `X-MM-App`; use one dedicated code, `audience_mismatch`.
  - Decide `app_disabled` vs `origin_not_allowed` in the plan and update the AC to match.
  - When `audience=="admin"`, also reject the request if the Origin appears in any other app's `origins`.
  - At mint time, re-read user status, `email_verified` and membership from the DB.
- **Rate limit:** add a `token` per-IP scope (about 60/min). It is not in the AC but is cheap.
- **Logging:** log `token_issued` with user_id, aud, kid, jti and account_session_id. Never log the JWT, cookie or email.
- **Rotation runbook (AC7):**
  1. Add the next public key to `JWT_PUBLIC_KEYS_EXTRA` and deploy. Wait at least the JWKS max-age (300 s) plus the `mmlabs-auth` cache lifetime (recommend 1 h).
  2. Make the new key the signer, move the old public key to the extra list, and deploy.
  3. After the 15-minute TTL plus 60 s leeway plus 300 s cache, remove the old key.
  4. **Compromise:** swap immediately and drop the old key; accept about 15 minutes of 401s.
  - `mmlabs-auth` must refetch JWKS on an unknown `kid`, rate-limited.
- **SPA (ricotdin):**
  - The token lives in memory only: never in localStorage, URLs or logs.
  - Send it only to the app's own backend. `/token` responses carry `Cache-Control: no-store`.
- **Deploy:**
  - Store `JWT_SIGNING_KEY` in the deployment repo's secrets and write it to the env file the same way as `IDENTITY_HMAC_KEY`. This needs a change to `deploy-org-admin-api.yml` in `mmlabs-online/deployment`.
  - Set it before the release: `migrate` loads the same Settings and will refuse to start without it.
  - Generate it with `openssl genpkey -algorithm ed25519 | base64 -w0`.
  - Add it to `.env.example` without a value. Use different keys for staging and production.

### Risks
- **XSS on a registered app's origin:** the attacker can mint tokens for that app, for any account signed in on the device, while the victim has the page open. This is inherent to D5.
- **Revocation lag:** a disabled user or a logout leaves an issued token valid for up to 15 minutes.
- **Leaked signing key:** with no revocation list, the only recovery is an emergency rotation.
- **Rotation depends on verifiers:** it is only safe if `mmlabs-auth` honours the cache TTL.

### Open questions
1. Should `admin` tokens get a shorter TTL (about 5 minutes)?
2. Should origin uniqueness be enforced on app create/update now, or only through the `/token` check for `admin`?
3. Which issuer should staging use (for example `https://api-staging.mmlabs.online`)?
4. Should `/token` refuse `audience=admin` until ADM-002 consumes these tokens?
5. Is there a single error code for a disabled/unknown app? It needs an AC fix.
