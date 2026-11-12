# consult-api-platform-engineer — USR-03c

**Findings**
- The ticket and the current code disagree on errors. `AppContextService.check` already returns 403 `origin_not_allowed` for an unknown or disabled `X-MM-App` (no oracle), and `audience` must equal `X-MM-App`. So `app_disabled` can never be reached through `/token`. Ticket AC4 conflicts with USR-03's no-oracle design.
- The global `CORSMiddleware` (`app/main.py`, `allow_credentials=True`) adds `Access-Control-Allow-Credentials: true` to every response for a request that has an `Origin`. For listed origins it also echoes the origin, which overwrites any `ACAO: *` set by a route. The JWKS AC ("`*` without credentials") therefore fails unless JWKS is excluded from the middleware. USR-03e will rewrite CORS, so the two tickets must agree on this.
- PyJWT and cryptography are not dependencies yet. `rate_limit.Scope` is a `Literal` and needs a new `"jwks"` scope plus settings.
- Reusable patterns already exist: the ETag pattern (`core/etag.py`, catalog router), the root mount (`health_router` included without a prefix), `DeviceCookieDoc`, the `no-store` header, `ResolvedDevice.accounts_created_before`, and `AuthzService.effective_roles`.
- D16: until USR-04 ships, every real user gets `email_unverified`. Tests must set `email_verified_at` through factories.

**Recommendations**
1. Schemas (AC1, R9):
   - `TokenRequest(extra="forbid")`: `audience` (APP_KEY_RE pattern), `account_id: UUID`, `org_id: UUID | None = None`.
   - `TokenResponse`: `access_token: str`, `token_type: Literal["Bearer"]`, `expires_in: int`, `audience: str`.
   - Give both explicit names so openapi-fetch generates clean types.
2. Error codes in the R6 envelope (AC2–4), each a subclass in `auth/errors.py`:

   | Status | Code | When |
   |---|---|---|
   | 401 | `session_invalid` | missing or invalid cookie, unknown or other-device `account_id`, account created after rotation, disabled user (one shared message, no oracle) |
   | 403 | `app_header_required`, `origin_not_allowed` | existing app-context checks; also covers unknown or disabled apps |
   | 403 | `audience_mismatch` | `audience` ≠ `X-MM-App` (new code; safe because the app is already proven) |
   | 403 | `email_unverified` | email not verified |
   | 403 | `not_member` | `details: {"join_available": bool}` |
   | 422 | `org_not_supported` | `org_id` sent (until USR-06), `details=[{"field":"org_id","reason":"not_supported"}]` |
   | 429 | `rate_limited` | rate limit hit |

   Drop `app_disabled` from AC4, or record it as reserved.
3. Check order: app context → cookie (DeviceDep) → audience → account session → user disabled → verified → membership → org. This leaks nothing to someone without a session.
4. Headers: `Cache-Control: no-store` and `Pragma: no-cache` on success and on error. `/token` should not add its own rotation step, but must forward `device.new_cookie` (same as logout); otherwise the rotation from `resolve` is lost. Commit before the response (USR-03f).
5. `/token` gets its own per-IP rate limit scope.
6. JWKS (AC6):
   - Separate `jwks_router`, mounted at the root like health, tag `identity`.
   - Body `{"keys":[{kty:"OKP",crv:"Ed25519",x,kid,alg:"EdDSA",use:"sig"}]}`, built once at startup into fixed bytes; the strong ETag is computed from those bytes.
   - 304 when it matches, `Cache-Control: public, max-age=300`, `Content-Type: application/jwk-set+json`, `ACAO: *`, no ACAC.
   - Exclude `/.well-known/` from the credentialed CORS middleware with a small ASGI wrapper (or within USR-03e).
7. OpenAPI: `DeviceCookieDoc` plus `X-MM-App` as a declared `Header`. `responses={401,403,422,429: ErrorEnvelope model}`; publish a real `ErrorEnvelope` schema rather than `_ERR`. JWKS uses `response_model=JwkSet` with `responses={304}`.
8. Tests:
   - Happy path: the PyJWT reference verifier checks `aud`, `iss` and `exp`, header `typ=at+jwt`, and that the `kid` is in JWKS.
   - Error codes: one test per code, plus missing cookie, missing headers, rotated cookie with a newer account, and `extra` field → 422.
   - Isolation: two accounts on one device.
   - Bad tokens: tampered, expired, wrong audience, and `alg=none`/HS256 confusion are all rejected.
   - JWKS: no `d`, 304 behaviour, headers, 429.
   - Config: staging refuses to start without a key or with a malformed one.

**Risks**
- The signed claims (`iss` string, `roles` format, `typ`) become a frozen contract once ricotdin ships. Write them into the ADR.
- In-process key loading: a bad PEM must fail at startup, not on the first request.
- The in-memory rate limit is per worker (ADR-0002).
- Size: M–L (about 600–900 LOC with tests and docs).

**Open questions**
- Should `/token` add `Vary: Cookie`? `no-store` probably makes it unnecessary.
- Does `iss` come from settings (`https://api.mmlabs.online`) or from the request? Recommendation: a settings value.
- Can the `admin` app get a token through `/token` now (needed by ADM-002), or is it blocked until then?
- Is a `jti` denylist out of scope? Recommendation: yes; logging `jti` is enough.
