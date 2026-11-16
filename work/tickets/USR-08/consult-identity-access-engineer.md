# consult-identity-access-engineer — USR-08

## Findings

1. **The contract is stable and complete enough to wrap.** All `/api/v1/auth/*` routes are in `app/modules/auth/router.py`. Signup, forgot and every cookie-class route need `X-MM-App` plus a registered `Origin`; `verify-email` and `password/reset` are public and need neither. CORS exposes `Retry-After` on `/auth/*` and `Retry-After, ETag, X-Request-ID` on admin routes (`app/core/cors.py`). A 500 is not readable cross-origin, and a CORS failure shows up as `TypeError`.
2. **Every route and its result:**
   - `POST signup` → 201 `{id, email, email_verified, created_at}`
   - `POST login` → `{account_id, user{id, email, email_verified}}`
   - `GET accounts` → `{items:[{account_id, email, email_verified, display_name}]}`
   - `POST token {audience, account_id}` → `{access_token, token_type, expires_in, audience}`
   - `POST apps/{key}/join {account_id}` → `{status:"member"}`
   - `POST logout {account_id}|{all:true}` → 204
   - `POST verify-email {token}` → 204
   - `POST verify-email/resend {account_id}` → 202
   - `POST password/forgot {email, turnstile_token}` → 202
   - `POST password/reset {token, new_password}` → 204
3. **Error codes in the server today:**
   - 401: `session_invalid`, `invalid_credentials`
   - 403: `not_member` (`details.join_available`), `email_unverified`, `audience_mismatch`, `app_header_required`, `origin_not_allowed`, `turnstile_required`, `turnstile_failed`, `join_closed`, `join_approval_unavailable`, `join_unavailable`, `app_mismatch`, `permission_denied`
   - 409: `email_taken`, `email_already_verified`
   - 422: `validation_error` (details `[{type, loc, msg}]`), `weak_password` (`details.reasons`), `token_invalid`, `org_not_supported`
   - 429: `rate_limited` (`details.retry_after` and the `Retry-After` header)
   - Framework errors: `not_found`, `method_not_allowed`, `payload_too_large`, `unsupported_media_type`
4. **Cookie.** It is `__Host-mm_device` (`Path=/`, HttpOnly, host-only) per D20/ADR-0004. The browser sends it and the library never touches it. It rotates at most every ~24 h with a 60 s grace for the previous value. `logout {all:true}` clears it.
5. **Verifier.** `app/core/access_token.py` is pure and written to be copied: ES256 only, `typ` must be `at+jwt`, `kid` must be in the key set, `strict_aud`, `iss`, leeway 30, required claims `exp, iat, sub, jti, iss, aud`, then `email_verified is True`. Failure reasons are `malformed, typ, alg, kid, signature, expired, audience, issuer, claims, email_verified`. `permits()` in `app/modules/rbac/authz.py` is also pure: an exact match or `<app>:*`, no global `*`, and it raises `TypeError` if `granted` is a `str`.
6. **JWKS and role catalog.** JWKS sends a strong `ETag` and `max-age=300`, and supports `If-None-Match` → 304. The role catalog `{app, roles:[{key, name, is_system, permissions}]}` sends a strong `ETag` with `Cache-Control: no-cache` and returns 404 for unknown, disabled or `admin` apps.

## Recommendations

**TS core: `createAuthClient(opts)`**
- Options: `{ baseUrl, appKey, fetch?, storage?: Storage|null, now?: () => number, refreshSkewSec = 60 }`.
- Methods, each mapping 1:1 to one endpoint (AC: TS API surface):
  - `signup({email, password, turnstileToken}): Promise<SignupResult>`
  - `login({email, password, turnstileToken?}): Promise<LoginResult>`. It sets `mm_account_id` and clears the token cache.
  - `listAccounts(): Promise<Account[]>`
  - `getToken(audience = appKey, {accountId?, forceRefresh?}): Promise<string>`
  - `join({accountId?}): Promise<void>`. The path key is always `appKey`.
  - `logout({accountId} | {all: true}): Promise<void>`
  - `resendVerification({accountId?}): Promise<void>`
  - `verifyEmail(token)`, `forgotPassword({email, turnstileToken})`, `resetPassword({token, newPassword})`. These cover the owner's optional ask.
  - Account choice: `getAccountId(): string|null`, `selectAccount(id)`, `clearAccount()`
  - `onChange(listener)`: events `account-changed`, `signed-out`.
- **Headers by route.** Cookie-class routes plus signup and forgot send `credentials:"include"` and `X-MM-App`. `verify-email` and `reset` send `credentials:"omit"` and no `X-MM-App`. Bearer helpers send `credentials:"omit"` and never `X-MM-App`. Build these headers inside one internal `authFetch(path)` that asserts `path.startsWith("/api/v1/auth/")`. Unit-test that admin calls carry no cookie or header.
- **Account choice.** Store only the UUID, as `localStorage["mm_account_id"]`; localStorage is already per origin. Validate it against the UUID pattern on read, and wrap every access in try/catch. Fall back to memory when storage is unavailable. Listen to `storage` events so other tabs see an account switch.
- **Token cache.**
  - Use a `Map` keyed by `${accountId}|${audience}`, holding `{token, expAtMs}`, in memory only.
  - Take `expAtMs` from `now() + expires_in*1000`, not from decoding `exp`, so clock skew does not matter.
  - The token counts as fresh while `now < expAtMs - skew`, where skew = `min(60 s, expires_in/4)`.
  - Single-flight: one in-flight promise per key. Every caller of that key shares the result or the error, and the entry is deleted in `finally`.
  - Clear the cache on account switch, login, logout (one account → that account's entries, all → everything), any `session_invalid`, and `invalidate(audience?)`.
  - On a 401 from the app's own backend, the helper `fetchWithToken` calls `getToken(aud, {forceRefresh:true})` once and retries once.
- **Typed errors.** `class MmAuthError extends Error {status, code, details, requestId}` with subclasses:
  - `SessionInvalidError`: also clears the cached account entries. Do not auto-clear `mm_account_id`; let the app re-list accounts.
  - `NotMemberError {joinAvailable}`
  - `EmailUnverifiedError`
  - `RateLimitedError {retryAfter}`: from the `Retry-After` header, falling back to `details.retry_after`, both in seconds.
  - `InvalidCredentialsError`
  - `TurnstileRequiredError`, `TurnstileFailedError`
  - `ValidationError {details}`, `WeakPasswordError {reasons}`, `TokenInvalidError`
  - `EmailTakenError`, `EmailAlreadyVerifiedError`
  - `JoinUnavailableError {code}`: covers `join_closed`, `join_approval_unavailable`, `join_unavailable`
  - `ConfigurationError {code}`: covers `app_header_required`, `origin_not_allowed`, `audience_mismatch`, `app_mismatch`, `org_not_supported`
  - `NetworkError`: a `TypeError` from fetch or an unreadable 5xx.
  - Fallback: `MmAuthError` with the raw code.
  - Error messages must never contain the password, token or email. Unit-test the table.
- **Flows.**
  - `not_member` with `joinAvailable` → the app shows "Continue as <email>". Only the user's click calls `join()` and then `getToken` (D8). The library never joins automatically.
  - `email_unverified` → `resendVerification()`. Treat `email_already_verified` as success. After the user verifies, call `getToken(aud, {forceRefresh:true})`.
- **React.** `<MmAuthProvider client>` plus hooks:
  - `useAuth()` → `{status: 'loading'|'signed-out'|'needs-account'|'ready', accounts, accountId, selectAccount, login, logout}`
  - `useAccessToken(audience)` → `{token, error, refresh}`
  - `useJoin()`
  
  Errors surface as typed errors, so admin-pan can branch on `instanceof`.

**Python: `mmlabs_auth`**
- `verify_access_token(token, key_source, *, audience, issuer, leeway=30)`, copied verbatim from `app/core/access_token.py`, with the same reasons and error class name. This gives parity tests for valid, expired, wrong-audience, list-audience, `typ`, `alg`, unknown `kid` and `email_verified`.
- **`JwksCache`.** Write it on httpx; do not use `PyJWKClient`, which has no cooldown or flush.
  - Fetch with a `User-Agent` and `If-None-Match`.
  - Cache TTL = the server's `max-age`, at most 1 h.
  - Accept only JWKs with `kty=EC, crv=P-256, alg=ES256, use=sig`.
  - On an unknown `kid`, refetch at most once per `cooldown=60` s, guarded by a lock (single-flight).
  - `flush()` drops the keys and resets the cooldown, for emergency rotation (S1/S2).
  - On fetch failure, serve stale keys up to the max age, then fail closed.
- **`RoleCatalogCache(app_key)`.** Use `If-None-Match` revalidation, because the server sends `no-cache`. Keep a short local TTL (e.g. 60 s) between conditional GETs. A 304 keeps the cached body. Treat a 404 as a configuration error and fail closed. `permissions_for(roles) -> frozenset[str]` takes the union over the catalog, and roles missing from the catalog are ignored.
- **`require_permission("<app>:<res>:<act>")`.** A FastAPI dependency mirroring org-admin-api:
  - Bearer from the header only.
  - 401 `unauthorized` with `WWW-Authenticate: Bearer error="invalid_token"`, in the R6 envelope.
  - `permits(perms, key)`, copied verbatim with the `str` guard.
  - 403 `permission_denied` with `details.required`.
  - Validate at definition time that the key has prefix `<audience>:`.
  - Expose a `Principal {sub, email, roles, permissions, jti}`.
  - Log the reason code and `jti` only.

## Risks

- **Silent drift.** The verifier and `permits()` exist in two repos. Add a shared test-vector file (tokens plus expected reasons), or pin the copy to a commit in a test.
- **Cookie rotation race.** Several tabs calling `/token` together. The 60 s grace covers it, and single-flight cuts it down, but reuse after the grace revokes the whole device. Avoid background refresh in hidden tabs: refresh lazily, on demand.
- **Logout leaks.** A token cached before logout stays valid server-side for up to 15 min, so the library must clear its memory cache synchronously before it awaits the request.
- **Persisted tokens.** A future "persist for UX" change would break R11. Add a test asserting nothing except `mm_account_id` is written to storage.
- **Unverified local catalog.** Apps that authorize from `roles` without the catalog bypass role → permission mapping. Document "catalog only".

## Open questions

1. Should `SessionInvalidError` on `getToken` auto-clear `mm_account_id`, or only the cache? Recommendation: cache only, then `listAccounts()`.
2. Should `JwksCache` and `RoleCatalogCache` be async-only (httpx.AsyncClient), or also offer a sync API?
3. Publishing: GitHub Packages for npm plus a git-tag install for Python, or a private PyPI?
4. Catalog local TTL, and should a stale catalog be served if org-admin-api is down? Recommendation: stale up to 10 min, then fail closed.
5. Angular is deferred. Confirm that a React peer dependency is acceptable in a `@mmlabs/auth-client/react` subpath export.
