# Verifying mmlabs access tokens (for consuming apps)

How an mmlabs app gets and verifies its per-app access token (D7, ADR-0005). The worked example is
**ricotdin**; replace the app key for any other app.

Prerequisites for ricotdin, in order: USR-03c (this endpoint and JWKS), USR-03e (dynamic CORS,
done), USR-04 (verified email), USR-07 (ricotdin integration, registers its origins).

## Calling the identity API from the browser

CORS for `/api/v1/auth/*` echoes only the origins of active apps (ADR-0006).

- Every call: `fetch(url, {credentials: "include", headers: {"Content-Type":
  "application/json", "X-MM-App": "ricotdin"}})`. The custom header forces a preflight; that is
  expected.
- The page must run on a registered origin: exact scheme + host, no port, no path
  (`https://ricotdin.mmlabs.online`; a staging origin once USR-07 registers it).
- A CORS failure shows up as `TypeError: Failed to fetch`, not as a 403: check the page origin and
  the app's `origins`. A 403 `origin_not_allowed` means the origin passed CORS (it belongs to some
  app) but not this `X-MM-App`.
- 401/403/422/429 envelopes are readable; `Retry-After` is exposed on 429. A 500 is not readable
  cross-origin. Responses carry `Cache-Control: no-store`.
- Local development: run a local API (`make dev`, `http://localhost:8000`) with the SPA on
  `http://localhost:<port>` registered as an origin of the local app. Use the same host name on
  both sides (`localhost` vs `127.0.0.1` is cross-site and the cookie is not sent). A local SPA
  cannot use the production API (loopback origins are ignored there, and the request is
  cross-site). Safari may refuse `Secure`/`__Host-` cookies on `http://localhost`: use
  Chrome/Firefox or local https.

## Getting a token (SPA)

```js
const resp = await fetch("https://api.mmlabs.online/api/v1/auth/token", {
  method: "POST",
  credentials: "include",                       // sends the __Host-mm_device cookie
  headers: { "Content-Type": "application/json", "X-MM-App": "ricotdin" },
  body: JSON.stringify({ audience: "ricotdin", account_id }),
});
// 200 {access_token, token_type: "Bearer", expires_in, audience}
```

- Keep the token **in memory only**: never in `localStorage`, `sessionStorage`, URLs or logs.
- Refresh it before `exp` by calling `/token` again (the cookie still allows it). There is no
  refresh token.
- Send it only to ricotdin's own backend, as `Authorization: Bearer <token>`.
- Errors (`{"error": {"code", "message", "details"}}`):

| Status / code | What to do |
|---|---|
| 401 `session_invalid` | The account (or the whole device session) is gone: re-list the accounts (USR-03b) or sign in again. |
| 403 `not_member` | Not a member of ricotdin. If `details.join_available`, offer the join flow (USR-03b). |
| 403 `email_unverified` | Ask the user to verify their email (USR-04). |
| 403 `audience_mismatch` | Bug: `audience` must equal the `X-MM-App` key. |
| 403 `app_header_required` / `origin_not_allowed` | Bug or misconfiguration: wrong `X-MM-App`, or the page origin is not in the app's `origins`. |
| 422 `validation_error` / `org_not_supported` | Bug: bad body; `org_id` is not supported until USR-06. |
| 429 `rate_limited` | Back off for `Retry-After` seconds. |

## Verifying (backend) checklist

1. Pin `algorithms=["ES256"]`. Never read the algorithm from the token.
2. Get keys only from `https://api.mmlabs.online/.well-known/jwks.json`. Never from token headers
   (`jku`, `x5u`, `jwk`).
3. Select the key by `kid`. Cache the JWKS (at most 1 h; the server sends `max-age=300`). On an
   unknown `kid`, refetch at most once a minute.
4. Require `iss == "https://api.mmlabs.online"` exactly and `aud == "ricotdin"`.
5. Require `exp`, `iat`, `sub`, `jti`. Leeway at most 60 s (30 s recommended).
6. Require header `typ == "at+jwt"` and claim `email_verified is True`.
7. Map `roles` to permissions only through ricotdin's own role catalog
   (`GET /api/v1/apps/ricotdin/roles`).
8. Never log tokens.
9. Use `PyJWT[crypto]>=2.10.1` (2.10.0 has CVE-2024-53861, a partial `iss` match).
10. Set a `User-Agent` on the JWKS fetch: Cloudflare may block the urllib default.
11. Revocation lag: a logout, a disabled user or a removed role takes effect within 15 minutes
    (the token TTL).

`sub` is the mmlabs user id (stable across devices). Claims: `iss`, `aud`, `sub`, `email`,
`email_verified`, `roles`, `iat`, `exp`, `jti` (`org` arrives with USR-06).

## PyJWT sketch

```python
import jwt
from jwt import PyJWKClient

ISSUER = "https://api.mmlabs.online"
jwks = PyJWKClient(
    f"{ISSUER}/.well-known/jwks.json",
    cache_jwk_set=True,
    lifespan=300,
    headers={"User-Agent": "ricotdin-backend"},
)


def verify(token: str) -> dict:
    if jwt.get_unverified_header(token).get("typ") != "at+jwt":
        raise jwt.InvalidTokenError("typ")
    key = jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=["ES256"],
        audience="ricotdin",
        issuer=ISSUER,
        leeway=30,
        options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
    )
    if claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("email_verified")
    return claims
```

The server's own tests run the same checks (`tests/jwt_verifier.py`).
