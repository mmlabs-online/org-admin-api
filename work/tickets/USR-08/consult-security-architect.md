# consult-security-architect — USR-08

**Findings**

1. The server contract is already strict, and the libraries must not loosen it. `app/core/access_token.py` checks, in order: `typ == at+jwt`, `alg == ES256`, `kid` among the published keys, signature, string-only `aud` (`strict_aud`), `iss`, required claims `exp iat sub jti iss aud`, 30 s leeway, then `email_verified is True`. Each failure raises a fixed reason code and never carries the token. `permits()` (`app/modules/rbac/authz.py`) is pure and refuses a `str` argument. Both are written to be copied verbatim into `python/`.
2. The cookie is now `__Host-mm_device` with `Path=/` (D20). It therefore reaches **every** path on `api.mmlabs.online`, not only `/auth`. CLAUDE.md R11 still says `mm_device; Path=/api/v1/auth`; that is stale and should be flagged to the owner. Consequence: a Bearer call to the API host made with `credentials:"include"` would attach the device cookie. Admin routes ignore it (ADR-0008) and the admin CORS refuses credentials (ADR-0011), but the client must still never send it.
3. Cookie rotation uses compare-and-swap with a 60 s grace for the previous value. Reuse after the grace revokes the whole device (ADR-0004). Several tabs calling `/token` at the same moment are safe inside the grace. A tab that replays requests long after (for example a retry queue holding stale state) cannot replay the cookie itself, because the browser owns it. The library must not build any cookie handling of its own.
4. `/auth/token` answers 429 before every other check (ADR-0005 §9), and `Retry-After` is exposed. A naive "refresh on 401, retry" loop, repeated across N tabs and M audiences, multiplies load on per-IP limits that all apps on one NAT share.
5. `/accounts` returns emails. Error envelopes may carry `details` (for example `join_available`). A wrapper that copies a response body into an error's `message` could leak emails into logs or telemetry.
6. `/home/user/mmlabs-auth` is empty apart from README and .gitignore, so no CI or supply-chain baseline exists yet.

**Recommendations**

*TS, `@mmlabs/auth-client`*
- **T1 (AC getToken).** Keep tokens in a module-private `Map<audience, {token, exp}>` inside a closure or class with `#private` fields.
  - Never write them to `localStorage`, `sessionStorage`, IndexedDB, cookies, `window`/`globalThis`, React context *values* (expose `getToken()` instead), URLs or query strings.
  - Run a test that spies on all three storage APIs and asserts that no token-shaped value is ever written.
- **T2.** Persist only `mm_account_id` in `localStorage`. Never persist email, display name or the account list. Validate the stored value as a UUID on read and drop it otherwise.
- **T3 (AC credentials).** Use one internal `authFetch` for `/api/v1/auth/*`. It sends `credentials:"include"`, `X-MM-App`, `Content-Type: application/json` and `cache:"no-store"`.
  - The public endpoints `verify-email` and `password/reset` use `credentials:"omit"` and no `X-MM-App`.
  - `bearerFetch` (or `authHeader()`) uses `credentials:"omit"` and never sets `X-MM-App`.
  - Refuse a `baseUrl` that is not `https:`, except `http://localhost` when `dev: true`.
- **T4 (refresh).** Refresh when `exp − now ≤ 60 s`, or ≤ 20 % of TTL for short TTLs.
  - Use one in-flight promise per audience (single-flight). A failure clears the promise and is never cached as success.
  - On a 401 from a Bearer call, refresh once per request, and no more than once per 30 s per audience. Then raise `session_invalid`.
- **T5 (429).** Throw `RateLimitedError{retryAfter}`.
  - Set a per-audience "blocked until" timestamp. Every call before it rejects locally with no network request.
  - No automatic retry. Background refresh backs off exponentially with jitter, starting at `max(Retry-After, 5 s)` and capped at 15 min.
  - Never refresh in a tight loop, from a timer while `document.hidden`, or more than once per `visibilitychange`.
- **T6 (multi-tab).** Use a `BroadcastChannel("mmlabs-auth:<appKey>")` that carries only `{type: "logout" | "logout_all" | "account_changed", accountId?}`.
  - A receiving tab clears its in-memory tokens and re-reads `mm_account_id`.
  - Tokens are never broadcast. Ignore any unknown or malformed message shape.
  - Fall back to the `storage` event on the `mm_account_id` key.
- **T7 (errors).** Use a typed hierarchy: `NotMemberError{joinAvailable}`, `EmailUnverifiedError`, `SessionInvalidError`, `RateLimitedError{retryAfter}`, `AuthError{code, status, requestId}`.
  - Copy only `code`, `status`, an allow-listed `details` subset and `X-Request-ID`. Never copy the request body, password, token, email or the raw response.
  - Write `toJSON` and `toString` safely.
  - The library does no `console.*` logging. A debug hook gets codes only.
- **T8 (join).** `join()` is only callable with an explicit `accountId` argument, never automatically after `not_member` (D8 consent).
- **T9 (XSS posture, documented).** The library cannot defeat XSS: injected script can call `getToken()`. It can stop *passive* exfiltration: no token in storage, in globals, or in URL/Referer, and no logging.
  - Recommend a strict CSP, `Referrer-Policy: no-referrer` on `/verify-email` and `/reset-password`, and Trusted Types.
  - Passwords pass straight through and are never retained after the call.

*Python, `mmlabs-auth`*
- **P1.** Copy `verify_access_token` and `permits()` as they are.
  - Pin `ES256`, use `strict_aud=True`, compare `iss` exactly, require `typ`, require `exp iat sub jti iss aud`, require `email_verified is True`.
  - Leeway defaults to 30 s and raises `ValueError` if above 30.
  - Never read `jku`, `x5u` or `jwk` from the header. `kid` must be a string of at most 128 characters.
- **P2 (JWKS).**
  - Accept only an `https://` URL (loopback only with an explicit test flag).
  - TLS verification is always on, with no `verify` parameter exposed. Do not follow redirects.
  - Timeouts: connect 3 s, total 5 s. Response body limit 64 KB.
  - Set a `User-Agent`. Cache keyed by `kid`, max TTL 1 h, default 300 s.
  - Unknown `kid`: refetch at most once per 60 s, single-flight under a lock. Apply the cooldown even when the fetch fails.
  - On fetch failure keep the last good set, then fail closed after the max TTL.
  - Provide a `flush()` for emergency rotation (USR-03c S1/S2).
  - Accept only `kty=EC`, `crv=P-256`, and `alg=ES256` or absent, `use=sig`.
- **P3 (catalog).**
  - ETag/304 with the same timeouts and HTTPS rule.
  - Keep the last good catalog on errors.
  - Validate the shape. An unknown role maps to no permissions (deny).
- **P4 (FastAPI).** `require_permission()` reads only `Authorization: Bearer`. It returns a 401 that carries `WWW-Authenticate` and gives no reason, and a 403 `permission_denied`. Reason codes are logged with `jti`, never the token or email.
- **P5 (deps).** Runtime deps are `PyJWT[crypto]>=2.10.1,<3` and `httpx` (or stdlib), with `fastapi` as an optional extra.

*Supply chain*
- **S1.** Commit lockfiles (`pnpm-lock.yaml`, `uv.lock`). CI installs with `--frozen-lockfile` / `uv sync --locked`.
- **S2.** TS has zero runtime dependencies. React is a `peerDependency`. No `postinstall`/`prepare` scripts are published, and CI runs with `ignore-scripts=true`.
- **S3.** Pin GitHub Actions to commit SHAs. Set `permissions: contents: read`. No `pull_request_target`. Publish via OIDC/provenance, not long-lived tokens.
- **S4.** Run Dependabot/Renovate and `pip-audit` / `pnpm audit` in CI.

*Acceptance tests*
- **TS:**
  - no storage writes and no `window` exposure;
  - single-flight under 10 concurrent calls;
  - refresh before expiry with a fake clock;
  - 429 blocks calls until `Retry-After` with no network request;
  - a 401 retries once only;
  - `credentials` and headers are correct per endpoint class, and Bearer calls have no cookie or `X-MM-App`;
  - every error code maps to its type, and `JSON.stringify(err)` contains no token, email or password;
  - the BroadcastChannel payload never contains a token, and logout in one tab clears the others;
  - per-origin `mm_account_id` is validated;
  - `join` is never called automatically.
- **Python:**
  - valid token passes;
  - rejected: expired, wrong `aud`, list `aud`, wrong `iss` (including a prefix), `alg` none/HS256/ES384, wrong `typ`, missing each required claim, `email_verified` false or missing, future `iat`;
  - unknown `kid` fetches once and a second fetch within 60 s is suppressed;
  - `flush()` works; `jku` is ignored; an `http://` URL is refused;
  - timeout fails closed;
  - catalog 304/ETag;
  - `permits()` wildcard rules;
  - the exception `repr` contains no token.

**Risks**
- XSS on any app origin can mint that app's tokens through the cookie (`/auth/token`). This is inherent; CSP is the control. For `admin-portal` it means a full admin compromise.
- Tokens stay valid for up to 900 s after logout. No revocation exists, by design (D7).
- A JWKS refetch without a cooldown, or a shared NAT with many tabs hitting 429, causes self-inflicted DoS. T5 and P2 address both.
- The library repo is private, and a git-install consumer may pin a moving branch. Pin tags or SHAs.

**Open questions**
1. Owner: should CLAUDE.md R11 be updated to the D20 cookie (`__Host-mm_device`, `Path=/`)?
2. Should forgot, reset and verify-email go into the client now, or stay a follow-up?
3. Publishing: private npm/PyPI, or git tags? This decides how provenance is done.
4. Should the Python verifier offer an explicit opt-in to `email_verified=False` for any app? I recommend not: keep it hard-required.
