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
| 403 `email_unverified` | Ask the user to verify their email; offer "resend" (see "Email verification"). |
| 403 `audience_mismatch` | Bug: `audience` must equal the `X-MM-App` key. |
| 403 `app_header_required` / `origin_not_allowed` | Bug or misconfiguration: wrong `X-MM-App`, or the page origin is not in the app's `origins`. |
| 422 `validation_error` / `org_not_supported` | Bug: bad body; `org_id` is not supported until USR-06. |
| 429 `rate_limited` | Back off for `Retry-After` seconds. |

## Email verification

A new account is unverified; `/token` (and join, USR-03b) answer 403 `email_unverified` until the
user opens the link from the verification email (D2, ADR-0007).

**Sign-up.** `POST /api/v1/auth/signup` **requires** `X-MM-App` and a browser `Origin` registered
for that app. Without them it answers 403 `app_header_required` (missing or malformed key) or
`origin_not_allowed` (unknown or disabled app, missing or unregistered origin) before any account
is created or any email is queued. Call it from the browser with `credentials: "include"`, like the
other `/auth/*` calls, so CORS answers the preflight that the custom header triggers. The link
points at the page origin that made the request; there is no API-wide fallback origin.

> **v1 contract change (USR-04c, 2026-11-13).** Sign-up without `X-MM-App` used to succeed (the
> link then used a fixed fallback origin); it now answers 403. This change is made inside v1 (R9)
> because no consumer used sign-up yet.

**Link format (fixed for every app):** `{origin}/verify-email#token=<43 chars>`. **Every registered
origin of an app must serve `/verify-email` and `/reset-password`** (see "Password reset"). The
token is in the fragment, so it never reaches a server log. The page:

1. reads `location.hash`, then removes it at once (`history.replaceState(null, "", "/verify-email")`);
2. is served with `Referrer-Policy: no-referrer`;
3. calls

   ```js
   await fetch("https://api.mmlabs.online/api/v1/auth/verify-email", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({ token }),
   });
   ```

   (public endpoint: no cookie or `X-MM-App` needed; never send the token in a query string);
4. on 204 shows "email verified" and, if the user is signed in, fetches a new `/token`.

| Status / code | What to do |
|---|---|
| 204 | Verified (also when the email was already verified, e.g. a double click). |
| 422 `token_invalid` | Link unknown, expired (24 h), already used or replaced by a newer one: offer "resend". |
| 422 `validation_error` | Bug or truncated link. |
| 429 `rate_limited` | Back off for `Retry-After` seconds. |

**Resend** (cookie class, like `/token`): `POST /api/v1/auth/verify-email/resend` with
`{account_id}`, `credentials: "include"` and `X-MM-App`. The new link points at the calling page's
origin and invalidates earlier links.

| Status / code | What to do |
|---|---|
| 202 | Sent (asynchronously; delivery is not confirmed). |
| 401 `session_invalid` | Sign in again. |
| 409 `email_already_verified` | Nothing to do; fetch a new `/token`. |
| 429 `rate_limited` | At most 1 per minute and 5 per day per email (sign-up counts as the first), plus a per-IP limit; show `Retry-After`. |

## Password reset

**Forgot.** `POST /api/v1/auth/password/forgot` `{email, turnstile_token}` **requires** `X-MM-App`
and a browser `Origin` registered for that app, like sign-up (403 otherwise, before anything is
sent). Call it from the browser with `credentials: "include"` so CORS answers the preflight. A
Turnstile token is always required. The answer is **202 for every email**, registered or not:
per-email limits (1 per minute, 5 per day) and the daily send cap apply silently, so the page must
always say "if an account exists for this address, we sent a link".

| Status / code | What to do |
|---|---|
| 202 | Show "check your email" (whether or not an email was sent). |
| 403 `app_header_required` / `origin_not_allowed` | Bug: send `X-MM-App` from a registered origin. |
| 403 `turnstile_failed` | Re-run the Turnstile challenge. |
| 422 `validation_error` | Bad email or body. |
| 429 `rate_limited` | Per-IP limit; back off for `Retry-After` seconds. |

**Link format (fixed for every app):** `{origin}/reset-password#token=<43 chars>`, where `origin` is
the page origin that called forgot. The link works once and expires after **30 minutes**; a newer
link replaces older ones. The `/reset-password` page:

1. reads `location.hash`, then removes it at once (`history.replaceState(null, "", "/reset-password")`);
2. is served with `Referrer-Policy: no-referrer`;
3. asks for the new password and calls

   ```js
   await fetch("https://api.mmlabs.online/api/v1/auth/password/reset", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({ token, new_password }),
   });
   ```

   (public endpoint: no cookie or `X-MM-App` needed; never send the token in a query string);
4. on 204 shows "password changed, sign in with your new password". Reset signs nobody in and
   sets no cookie.

| Status / code | What to do |
|---|---|
| 204 | Done: send the user to sign in. |
| 422 `weak_password` | Show `details.reasons` (`too_short`, `too_common`); the link still works, let the user retry. |
| 422 `token_invalid` | Link unknown, expired (30 min), already used or replaced: offer "forgot password" again. |
| 422 `validation_error` | Bug or truncated link. |
| 429 `rate_limited` | Back off for `Retry-After` seconds. |

**Effects.** A reset ends **every signed-in session of the account** on every device (other
accounts signed in on the same device stay); the next `/token` for it answers 401
`session_invalid`, so sign in again. Access tokens already issued stay valid until they expire (at
most 15 min, `ACCESS_TOKEN_TTL_SECONDS`). A reset also marks the email verified (the user proved
they receive mail there) and clears the login backoff for the email.

## Accounts and joining an app

### Listing the accounts on this browser

```js
const resp = await fetch("https://api.mmlabs.online/api/v1/auth/accounts", {
  credentials: "include",
  headers: { "X-MM-App": "ricotdin" },
});
// 200 {"items": [{"account_id", "email", "email_verified", "display_name"}]}
```

- Lists the accounts signed in on **this browser** (oldest first): every app on the device sees
  the same list. `account_id` is what `/token` and join take; it is not the user id.
- Use it for an account switcher or a "Continue as <account>" screen. Switching only changes the
  app's own `localStorage.mm_account_id`.
- Same errors as `/token` for the cookie and app context (401 `session_invalid`, 403
  `app_header_required` / `origin_not_allowed`, 429 `rate_limited`).

### Joining ("Continue as <account>")

Sequence: `/token` → 403 `not_member` with `details.join_available: true` → show "Continue as
<email>" → on the user's click, join → call `/token` again.

```js
const resp = await fetch("https://api.mmlabs.online/api/v1/auth/apps/ricotdin/join", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json", "X-MM-App": "ricotdin" },
  body: JSON.stringify({ account_id }),
});
// 200 {"status": "member"} → call /token again
```

- **The click is the consent** (D8): only call join after the user chose the account on a
  "Continue as" screen, never automatically.
- The path key must be your own app (`X-MM-App`); an app cannot join users into another app.
- Idempotent: an existing member gets the same 200 and nothing changes.
- Under `join_policy = open` the user gets the app's default role (`<app>:member`) at app scope. A
  member that staff removed can join again; staff use `closed` (or tighten the role) to stop that.

| Status / code | Meaning |
|---|---|
| 200 `{"status":"member"}` | Joined now or already a member: call `/token`. |
| 401 `session_invalid` | Account not signed in on this device: re-list the accounts or sign in. |
| 403 `email_unverified` | Verify the email first (see "Email verification"). |
| 403 `join_closed` | The app does not accept joins (`admin` included). |
| 403 `join_approval_unavailable` | The app uses approval, which is not available yet (USR-03g). |
| 403 `join_unavailable` | The app is misconfigured (no usable default role): contact staff. |
| 403 `app_mismatch` | Bug: the path key must equal `X-MM-App`. |
| 403 `app_header_required` / `origin_not_allowed` | Bug or misconfiguration: header or origin. |
| 422 `validation_error` | Bug: bad body (only `account_id` is allowed). |
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
