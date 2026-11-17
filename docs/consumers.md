# Verifying mmlabs access tokens (for consuming apps)

How an mmlabs app gets and verifies its per-app access token (D7, ADR-0005). The worked example is
**ricotdin**; replace the app key for any other app.

Prerequisites for ricotdin, in order: USR-03c (this endpoint and JWKS), USR-03e (dynamic CORS,
done), USR-04 (verified email), USR-07 (ricotdin integration, registers its origins).

## Client libraries

[`mmlabs-online/mmlabs-auth`](https://github.com/mmlabs-online/mmlabs-auth) implements this page:

- [`@mmlabs/auth-client`](https://github.com/mmlabs-online/mmlabs-auth/blob/main/ts/README.md)
  (TypeScript): browser client and React wrapper (sign-in, account choice, per-app tokens in
  memory, join, logout, email verification, password reset).
- [`mmlabs-auth`](https://github.com/mmlabs-online/mmlabs-auth/blob/main/python/README.md)
  (Python): backend verification (JWKS, ES256 checks, role catalog cache, FastAPI
  `require_permission()`).

Install pinned to a release tag (the repository is public; no token needed):

```bash
# TypeScript: vendor the Release tarball (check its SHA-256 against the release notes)
curl -fsSLo vendor/mmlabs-auth-client-0.1.0.tgz \
  https://github.com/mmlabs-online/mmlabs-auth/releases/download/ts-v0.1.0/mmlabs-auth-client-0.1.0.tgz
pnpm add ./vendor/mmlabs-auth-client-0.1.0.tgz

# Python
uv add "mmlabs-auth[fastapi] @ git+https://github.com/mmlabs-online/mmlabs-auth@py-v0.1.0#subdirectory=python"
```

The libraries follow the contract on this page; if they disagree, this page wins and the library
gets a fix.

## Calling the identity API from the browser

CORS for `/api/v1/auth/*` echoes only the origins of active apps (ADR-0006).

- `credentials: "include"` for **login** (it reads the existing device cookie to add the account,
  and sets or rotates the cookie) and for every cookie-class call: `/token`, `/accounts`,
  `/apps/{key}/join`, `/logout`, `/verify-email/resend`. With `omit` there the browser drops
  `Set-Cookie` and sign-in or multi-account silently breaks. Only login creates the cookie; every cookie-class call may rotate it (R11),
  and logout or a 401 clears it.
- `credentials: "omit"` for **signup, forgot, verify-email and reset**: they neither read nor set
  the cookie.
- Example: `fetch(url, {credentials: "include", headers: {"Content-Type": "application/json",
  "X-MM-App": "ricotdin"}})`. The custom header forces a preflight; that is expected, and the
  credentials mode does not change whether CORS answers it.
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

## Calling the admin API from the admin UI

For the admin UI (`admin-pan`, `https://admin-portal.mmlabs.online`) only. Admin endpoints take
**Bearer only; no cookies** (ADR-0008, ADR-0011). CORS on them is non-credentialed and echoes only
the `admin` app's origins (set with `set-admin-origins`).

- Get the token from `POST /api/v1/auth/token` like any SPA (see "Getting a token (SPA)"):
  `credentials: "include"`, `X-MM-App: admin`, `audience: "admin"`.
- Call admin endpoints with `fetch(url, {credentials: "omit", headers: {Authorization: "Bearer "
  + token, "Content-Type": "application/json"}})`. Never `credentials: "include"`, never send the
  cookie, never send `X-MM-App` (the admin preflight refuses it).
- Keep the token in memory only (not `localStorage`). Call `/auth/token` again about 60 s before
  `expires_in` runs out. Role changes show up in `GET /api/v1/me/permissions` on the next call;
  the token's `roles` claim may lag up to the token TTL (`ACCESS_TOKEN_TTL_SECONDS`, default 15
  min; 12 h in production).
- On a 401 from an admin endpoint, call `/auth/token` once; if that fails too, send the user to
  login (401 `session_invalid` from `/token` = sign in again).
- To learn the signed-in staff member's own id, roles and permissions, call
  `GET /api/v1/me/permissions` with the same Bearer token, for example
  `{"user_id": "…", "roles": ["admin:super-admin"], "permissions": ["admin:*"]}`. Do not decode the
  token for `sub`. It is a UI hint only: the API still enforces every call (403
  `permission_denied`, and `admin_wildcard_required`, `self_assignment_forbidden`,
  `self_status_change_forbidden` cannot be inferred from the list). Match with
  `has(k) = perms.includes(k) || perms.includes("admin:*")`; there is no other wildcard. Unknown
  keys may appear (additive). Empty lists mean "no admin access" (re-login). The response is
  `no-store`: keep it in memory only. Fetch on session start, after each `/auth/token` call and
  after any 403, not on every navigation.
- 401/403/404/409/422 envelopes are readable; `Retry-After`, `ETag` and `X-Request-ID` are
  exposed. A 500 is not readable cross-origin. Responses carry `Cache-Control: no-store`.
- `admin-portal` cannot host the sign-up or forgot-password screens (ADM-002d); it is exempt from
  `/reset-password`, but must serve `/verify-email`.
- **Unverified staff email (USR-12).** `/auth/token` answers 403 `email_unverified`. The UI may
  call `POST /api/v1/auth/verify-email/resend` `{account_id}` with `credentials: "include"`,
  `X-MM-App: admin` (cookie class, like `/auth/token`; the only admin-context call that sends mail). 202: tell the user to
  open the link; 429: back off for `Retry-After` (1/min, 5/day per email); 409: already verified,
  call `/auth/token`; 403 `origin_not_allowed`: the account has no admin role. The link is
  `https://admin-portal.mmlabs.online/verify-email#token=...`; the page is public (outside the auth
  guard), reads `location.hash` then `history.replaceState(null, "", "/verify-email")`, sends
  `Referrer-Policy: no-referrer`, posts `POST /api/v1/auth/verify-email` with
  `credentials: "omit"` and no `X-MM-App`, and on 204 re-calls `/auth/token`.

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
- Never log `Authorization` headers (reverse proxy, APM, Sentry breadcrumbs).
- On logout, drop the in-memory token. After a join or role change, re-mint instead of waiting for
  `exp`.
- Treat developer tokens (copied for scripts) as a 12 h password: never in committed `.env` files,
  shell history, CI logs or chat.
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
| 403 `org_access_denied` | `org_id` given but the user holds no role of this app in that org, or the org is unknown or suspended (one answer for all three). Re-list the user's orgs. |
| 422 `validation_error` / `org_not_supported` | Bug: bad body, or `org_id` sent to an app with `org_mode = none`. |
| 429 `rate_limited` | Back off for `Retry-After` seconds. |

## Organizations (`org_mode = required` apps, USR-06a)

Flow for calechip: join (`/auth/apps/{key}/join`) -> `POST /api/v1/auth/orgs`
`{account_id, name, slug}` (cookie class; needs `<app>:org:create`, held at app scope by the
default role; the creator becomes the org owner) -> `GET /api/v1/auth/orgs?account_id=` (the
caller's orgs for the calling app, with their roles) -> `/auth/token` with `org_id`.

- Staff must configure the app first: `PATCH /api/v1/apps/{key}` with `org_owner_role_id`. Until
  it is set, create answers 403 `org_create_unavailable`. Keep the default role to app-level
  permissions plus `<app>:org:create`.
- The org owner role must differ from the default role (422 `same_as_default_role`; the database
  also refuses it). Mark org-level permissions `scope = org` (the default on `org_mode = required`
  apps): the default-role build (`build_default_role`, manifest sync) skips them, so `<app>:member`
  keeps app-level permissions plus `<app>:org:create` (ADR-0015).
- Create errors: 403 `org_create_forbidden` (no `<app>:org:create`), `org_limit_reached`, 409
  `conflict` (slug taken; slugs are global), 422 `org_not_supported` (app has no orgs).
- A role change in an org reaches tokens within the token TTL (up to `ACCESS_TOKEN_TTL_SECONDS`: default 15 min, 12 h in production).

### Invitations (USR-06b, ADR-0014)

A member holding `<app>:org:invite` **in that org** invites an email address:
`POST /api/v1/auth/orgs/{org_id}/invitations` `{account_id, email, role}` (cookie class,
`credentials: "include"`, `X-MM-App`). `role` is a role key of the calling app: no `is_admin`
permission, and nothing beyond the inviter's own permissions in that org (the owner role is
invitable by an owner; there is no member removal in v1, staff only). The answer is always
`201 {id, org_id, role, expires_at}`, whether or not the address has an account, is a member, or
hit the per-address send limit: do not promise delivery. The global daily send cap answers
429 `rate_limited` with `Retry-After` instead. The mail links to
`{Origin}/accept-invitation#token=<token>`; the link works once and expires (default 7 days).

Each app implements the `/accept-invitation` page: read the token from the fragment, clear it
with `history.replaceState`, serve it with `Referrer-Policy: no-referrer`, make the user sign in
(or sign up and verify) with **exactly the invited address** (Gmail dots and plus tags are not
folded), then `POST /api/v1/auth/invitations/accept` `{account_id, token}`. Success is
`200 {org_id, roles}`; accepting also joins the app (default role) if the account is not a member,
then `/auth/token` with `org_id` works.

| Status / code | Meaning |
|---|---|
| 201 | invitation created (or silently not sent: per-address limit) |
| 429 `rate_limited` | per-IP limit, or the global daily send cap / invite share; back off for `Retry-After` |
| 403 `org_access_denied` | unknown, suspended or not-a-member org (one answer) |
| 403 `org_invite_forbidden` | no `<app>:org:invite` in that org |
| 403 `invite_role_forbidden` | the role holds an admin permission or more than the inviter has |
| 403 `invitation_limit_reached` | inviter's daily cap (default 5) or the org's pending cap |
| 403 `org_invite_unavailable` | app setup does not allow invitations (see below) |
| 403 `email_unverified` | the account's email is not verified |
| 403 `invitation_email_mismatch` | the token is for another address; not consumed |
| 422 `unknown_role` | not a role key of this app |
| 422 `token_invalid` | unknown, used, expired, revoked/superseded token or another app's; the same answer for all |

Staff configuration requirement (the S1 guard, defense in depth): the app needs
`org_mode = required`, `join_policy = open`, a default role and an org owner role, and the default
role must hold no `scope = org` permission. Otherwise invitations answer 403
`org_invite_unavailable` (create and accept; nothing is consumed). Any member holding `org:invite`
can supersede another member's pending invitation to the same address by re-inviting it (inside
the org). Residual risk: a staff-granted app-scope role that carries org-level permissions still
reaches every org token through `roles`; authorize org data from `org_roles` only (below).

## Email verification

A new account is unverified; `/token` (and join, USR-03b) answer 403 `email_unverified` until the
user opens the link from the verification email (D2, ADR-0007).

**Sign-up.** `POST /api/v1/auth/signup` **requires** `X-MM-App` and a browser `Origin` registered
for that app. Without them it answers 403 `app_header_required` (missing or malformed key) or
`origin_not_allowed` (unknown or disabled app, missing or unregistered origin) before any account
is created or any email is queued. Call it from the browser with `credentials: "omit"`: sign-up neither
reads nor sets the cookie; only login creates it. The link
points at the page origin that made the request; there is no API-wide fallback origin.

> **v1 contract change (USR-04c, 2026-11-13).** Sign-up without `X-MM-App` used to succeed (the
> link then used a fixed fallback origin); it now answers 403. This change is made inside v1 (R9)
> because no consumer used sign-up yet.

**Link format (fixed for every app):** `{origin}/verify-email#token=<43 chars>`. **Every registered
origin of an app must serve `/verify-email` and `/reset-password`** (see "Password reset"; admin-portal: `/verify-email` only, see the admin UI section). The
token is in the fragment, so it never reaches a server log. The page:

1. reads `location.hash`, then removes it at once (`history.replaceState(null, "", "/verify-email")`);
2. is served with `Referrer-Policy: no-referrer`;
3. calls

   ```js
   await fetch("https://api.mmlabs.online/api/v1/auth/verify-email", {
     method: "POST",
     credentials: "omit",
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
origin and invalidates earlier links. `admin` is accepted for staff only (USR-12).

| Status / code | What to do |
|---|---|
| 202 | Sent (asynchronously; delivery is not confirmed). |
| 401 `session_invalid` | Sign in again. |
| 409 `email_already_verified` | Nothing to do; fetch a new `/token`. |
| 429 `rate_limited` | At most 1 per minute and 5 per day per email (sign-up counts as the first), plus a per-IP limit; show `Retry-After`. |

## Password reset

**Forgot.** `POST /api/v1/auth/password/forgot` `{email, turnstile_token}` **requires** `X-MM-App`
and a browser `Origin` registered for that app, like sign-up (403 otherwise, before anything is
sent). Call it from the browser with `credentials: "omit"` (with `X-MM-App`, from a
registered origin). A Turnstile token is always required. The answer is **202 for every email**, registered or not:
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
     credentials: "omit",
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
`session_invalid`, so sign in again. Access tokens already issued stay valid until they expire
(up to `ACCESS_TOKEN_TTL_SECONDS`: default 15 min, 12 h in production). A reset also marks the email verified (the user proved
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
4. Require `iss == "https://api.mmlabs.online"` exactly, and `aud` must be a **string** equal to
   `"ricotdin"` (a list is rejected; PyJWT `strict_aud=True`).
5. Require `exp`, `iat`, `sub`, `jti`. Leeway at most 60 s (30 s recommended).
6. Require header `typ == "at+jwt"` and claim `email_verified is True`.
7. Map `roles` to permissions only through ricotdin's own role catalog
   (`GET /api/v1/apps/ricotdin/roles`).
8. Never log tokens.
9. Use `PyJWT[crypto]>=2.10.1` (2.10.0 has CVE-2024-53861, a partial `iss` match; also needed
   for `strict_aud`).
10. Set a `User-Agent` on the JWKS fetch: Cloudflare may block the urllib default.
11. Revocation lag: a logout, a disabled user, a removed role or org role, a suspended org and an
    expired assignment reach a token only at its `exp` (up to `ACCESS_TOKEN_TTL_SECONDS`: default 15 min, 12 h in production).
    Apps that need faster revocation check server-side, as the admin API does, or keep an
    app-local deny list by `sub`.
12. Never cache an authorization decision beyond the request. Map `roles` through the catalog per
    request. Do not turn a token into a longer app session; any app session must end at or before
    the token's `exp`.
13. Sensitive or destructive actions may require a fresh token. Refuse `now - iat > N` (for example
    900 s) with 401; the SPA silently re-mints through `/auth/token`, which re-checks status,
    verification, membership and roles.
14. Do not add your own maximum-age check below the TTL for ordinary requests; tokens up to the TTL
    are valid.

`sub` is the mmlabs user id (stable across devices). Claims: `iss`, `aud`, `sub`, `email`,
`email_verified`, `roles`, `org` (only when the token was requested with `org_id`; the org UUID
as a string), `org_roles` (only with `org`: the sorted role keys held in that org only), `iat`,
`exp`, `jti`.

For `org_mode = required` apps (calechip): `roles` mixes scopes (app-scope roles plus the roles
held in the `org` org). Authorize org-scoped data and actions **only** from `org_roles`, mapped
through the role catalog, and scope all data by the `org` claim. Use `roles` of a token **without**
`org_id` for app-level actions (create or pick an org). Never authorize org data from the `roles`
of an org token. If `org` is present and `org_roles` is absent, reject the token. When staff change
`org_mode` from `none` to `required`, they mark org-level permissions `scope = org` first (ADR-0015).

Follow-up (not in this API): `mmlabs-auth` needs an `org_roles` accessor, a
`require_org_permission(k)` that maps only `org_roles` through the catalog, rejection of a token
with `org` and no `org_roles`, and a release before calechip relies on it.

Python backends: prefer `mmlabs-auth`, which implements this checklist; the sketch stays for
other stacks.

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
        options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"], "strict_aud": True},
    )
    if claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("email_verified")
    return claims
```

The server's own tests run the same checks (`tests/jwt_verifier.py`).
