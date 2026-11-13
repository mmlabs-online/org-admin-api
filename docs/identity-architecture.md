# mmlabs Identity — architecture and plan

Status: **Plan** (owner decisions 2026-11-11). Not implemented yet; delivered by tickets USR-01…USR-06,
ADM-002, ADM-004 (see `work/backlog.md`).
Scope source of truth: `docs/spec-v1.md` §5.3, §5.8. Earlier decision: [ADR-0001](adr/0001-multi-app-authorization.md)
(app-scoped RBAC — still valid; its token flow §5 is replaced by this document, see D5–D7).

## 1. Overview

```
 Browser (one device, cookie jar of api.mmlabs.online)
 ┌───────────────────────────────────────────────────────────────────────────────────────┐
 │  stoktok.mmlabs.online        calechip.mmlabs.online        admin UI                  │
 │  own login page               own login page                own login page            │
 │  localStorage mm_account_id   localStorage mm_account_id    localStorage mm_account_id│
 │        └──────── @mmlabs/auth-client (TS, framework-agnostic; Angular/React wrappers) ─┘
 │                 fetch(..., credentials:"include", headers {X-MM-App: <key>})          │
 │                 cookie __Host-mm_device (HttpOnly, Secure, Lax, Path=/, host-only)    │
 └───────────────────────────────────────┬───────────────────────────────────────────────┘
                                         │ credentials go straight to the API, never via an app backend
 ┌───────────────────────────────────────▼───────────────── api.mmlabs.online ───────────┐
 │ mmlabs Identity                                            │ Admin API (app `admin`)   │
 │ users · device_sessions · account_sessions                 │ audit log, entitlements…  │
 │ apps (join_policy, org_mode, origins) · roles · permissions│ requires aud=admin token  │
 │ role_assignments · organizations                           │                           │
 │ /api/v1/auth/* (cookie) · /.well-known/jwks.json           │ + require_permission()    │
 │ /api/v1/apps/{key}/roles (ETag)                            │                           │
 └───────────────▲──────────────────────────────────────────────────────────▲────────────┘
                 │ Gmail SMTP (EmailSender)                                  │ JWKS + role catalog (ETag)
                                                       ┌────────────────────┴──────────┐
                                                       │ app backends: mmlabs-auth (Py)│
                                                       │ verify JWT, require_permission│
                                                       └───────────────────────────────┘
```

- **Self-hosted** (D1): users, passwords, sessions and tokens live in this service. No Supabase, no
  external IdP, no social login, **not** an OIDC provider (R10).
- **Shared accounts, per-app login pages** (D4): one account works in every app; each app renders its
  own login page with `@mmlabs/auth-client`.
- **Multi-account device sessions** (D5–D6): one browser can hold several signed-in accounts; each app
  independently picks which one it uses.
- **Per-app access tokens** (D7) carry only that app's roles; apps verify them locally (JWKS).
- **Membership** (D8–D9): a user joins an app (policy `open` / `approval` / `closed`) and gets its
  default role. A verified email is required to join or get any app token (D2).
- **Organizations** (D10) are optional per app (`apps.org_mode`).
- Business authorization on an app's own data stays in that app (ADR-0001 §1).

## 2. Main flows

**Sign up** — `POST /api/v1/auth/signup` (Turnstile + per-IP limit) → user created unverified → verification
email (USR-04). The user may log in, but cannot join an app or get an app token until verified.

**Log in (in app B, with app A already signed in as another account)**
1. App B posts email + password to `POST /api/v1/auth/login` (header `X-MM-App: B`, Origin checked).
2. API ensures a `device_session` for the `__Host-mm_device` cookie (creates one if missing), rotates the
   cookie (D6), creates an `account_session` for that user on the device, returns `{account_id, …}`.
3. App B stores `mm_account_id` in **its own** localStorage. App A's `mm_account_id` is untouched, so
   A keeps using its account (D5).

**Get an app token** — `POST /api/v1/auth/token {audience:"B", account_id, org_id?}` with the cookie →
checks the account session belongs to this device and is not revoked, the email is verified, the user
is a member of B (else `403 not_member` with `join_available`), the org (if any) → returns the JWT.

**Join** — UI shows "Continue as <account>" (= consent) → `POST /api/v1/auth/apps/B/join {account_id}` →
applies `join_policy`; `open` assigns B's default role at app scope (`org_id NULL`) and writes an audit
entry → app retries `/api/v1/auth/token`.

**Switch / list accounts** — `GET /api/v1/auth/accounts` lists the device's non-revoked account sessions
(id, email, display info). Switching only changes the app's `mm_account_id`.

**Log out** — `POST /api/v1/auth/logout {account_id}` revokes one account session (all apps using it on this
device lose it at their next token request); `{all:true}` revokes the device session.

## 3. Data model

New or changed tables (Identity module). `users` and RBAC tables from ADR-0001 stay unless noted.

```
users              (id uuid, email citext unique, password_hash, status active|disabled,
                    email_verified_at NULL, display_name NULL, created_at, updated_at)

device_sessions    (id uuid, token_hash unique, prev_token_hash NULL, prev_valid_until NULL,
                    created_at, last_seen_at, rotated_at, revoked_at NULL, ip inet, user_agent)
account_sessions   (id uuid, device_session_id → device_sessions, user_id → users,
                    created_at, last_used_at, revoked_at NULL)
                    unique (device_session_id, user_id) where revoked_at is null
                    account_id (API) = account_sessions.id (per device, opaque; USR-03, ADR-0004)
login_failures     (email_hmac bytea PK = HMAC-SHA256(IDENTITY_HMAC_KEY, normalized email),
                    failures, last_failed_at, blocked_until NULL)   — D11 backoff (ADR-0002 §5)

apps               (id, key unique, name, status,
                    join_policy  open|approval|closed   default 'open'   (admin = 'closed'),
                    default_role_id → roles NULL,
                    org_mode     none|required          default 'none'   (calechip = 'required'),
                    origins      text[]                 (allowed browser origins, CORS/CSRF),
                    created_at, updated_at)
permissions        (id, app_id, key, description, is_admin bool default false)   unique (app_id, key)
roles              (id, app_id, key, name, is_system)                            unique (app_id, key)
role_permissions   (role_id, permission_id)
role_assignments   (id, user_id, role_id, org_id → organizations NULL, granted_by NULL,
                    expires_at NULL, created_at)   org_id NULL = app scope; never 0
organizations      (id uuid, name, slug unique, description NULL, status active|suspended,
                    created_by → users NULL, created_at, updated_at)   [Identity module; existing table moved, D15]
join_requests      (id, app_id, user_id, status pending|approved|rejected, created_at, decided_by NULL)
                    — only needed for join_policy = approval (USR-03b)
email_tokens       (id, user_id, purpose verify|reset, token_hash, expires_at, used_at NULL,
                    created_at)  (USR-04)
email_send_log     (id, email_hmac, purpose, sent_at)   — resend limits; HMAC-SHA256 with
                    IDENTITY_HMAC_KEY (USR-04)
```

Notes
- **`refresh_tokens` is dropped.** The earlier design (opaque refresh token per login, rotated on use)
  is replaced by the `__Host-mm_device` cookie + `device_sessions` (rotation, grace, reuse detection) and
  `account_sessions` (one per signed-in account on the device). Revoking an account session is the
  new "revoke refresh token"; revoking a device session logs out every account on that browser.
- The 60 s grace (D6) needs the previous hash: `prev_token_hash` + `prev_valid_until`.
- Default role content is data: `<app>:member` = all permissions of the app with `is_admin = false`
  (D9). Tightening later = editing `role_permissions` / `apps.join_policy`, no code change.
- `organizations` already exists in the Admin module (ADM-001); USR-06 moves it to the Identity module and
  adds `created_by` (nullable for existing rows), keeping `description` and `status` (D15).
- OQ1: organizations are platform-wide for now; adding `organizations.app_key` later is additive.

## 4. API surface

All identity endpoints live under **`/api/v1/auth`** (D13). The cookie is `__Host-mm_device` with `Path=/`
(D20, amends D5/D13 for the cookie); only the identity endpoints read it. The only endpoint outside `/api/v1` is `/.well-known/jwks.json` (standard location, public).

| Method & path | Auth | Purpose | Ticket |
|---|---|---|---|
| `POST /api/v1/auth/signup` `{email, password, turnstile_token}` | public, Turnstile, per-IP limit + Origin/`X-MM-App` check (USR-04c) | create unverified user; send verification email | USR-01 (+USR-04 email) |
| `POST /api/v1/auth/login` `{email, password, turnstile_token?}` | public (per-IP limit; Turnstile after 3 failures) + Origin/`X-MM-App` check (sets `__Host-mm_device`, ADR-0004) | create/rotate device session, add account session → `{account_id, user}` | USR-03 |
| `GET /api/v1/auth/accounts` | cookie | accounts signed in on this device | USR-03b |
| `POST /api/v1/auth/token` `{audience, account_id, org_id?}` | cookie + `X-MM-App` | per-app JWT, or `403 {error:"not_member", join_available}` / `403 email_unverified` | USR-03c |
| `POST /api/v1/auth/apps/{key}/join` `{account_id}` | cookie + `X-MM-App` | apply join policy, assign default role, audit | USR-03b |
| `POST /api/v1/auth/logout` `{account_id}` \| `{all: true}` | cookie | revoke one account session / the device session | USR-03 |
| `POST /api/v1/auth/verify-email` `{token}`; `POST /api/v1/auth/verify-email/resend` `{account_id}` | public / cookie | email verification | USR-04 |
| `POST /api/v1/auth/password/forgot` `{email, turnstile_token}` → always 202 | public, Turnstile, per-IP limit + Origin/`X-MM-App` check (USR-04b) | send reset link (silent per-email limit) | USR-04b |
| `POST /api/v1/auth/password/reset` `{token, new_password}` | public, per-IP limit | set password, revoke all account sessions of the user (every device) | USR-04b |
| `GET /.well-known/jwks.json` | public | signing public keys (`kid`) | USR-03c |
| `GET /api/v1/apps/{key}/roles` (ETag / 304) | public (D17), per-IP limit | role → permissions catalog | USR-02e |
| `PUT /api/v1/apps/{key}/manifest` | app credential | app pushes its permissions + default role | USR-05 |
| `POST /api/v1/auth/orgs` `{account_id, name, slug}` | cookie; needs `<app>:org:create` | create org, creator gets the app's org owner role | USR-06 |
| `GET /api/v1/auth/orgs?account_id=&app=` | cookie | the account's orgs for an org-scoped app | USR-06 |
| `POST /api/v1/auth/orgs/{id}/invitations` `{email, role}` · `POST /api/v1/auth/invitations/{token}/accept` | cookie | invite / accept into an org | USR-06 |
| Admin: `/api/v1/users`, `/api/v1/apps`, roles, assignments, sessions revoke | Bearer, `aud=admin` + `admin:*` | staff management | USR-01/02, ADM-002 |

Token (D7): JWT, ES256 (P-256, ADR-0005), ~15 min, header `kid` + `typ: at+jwt`.
Claims `iss` (from `JWT_ISSUER`; production `https://api.mmlabs.online`), `aud=<app key>`, `sub`,
`email`, `email_verified`, `roles` (app-scope roles + roles in the selected org), `org` (only when
`org_id` given, USR-06), `iat`, `exp`, `jti`.

## 5. Security rules (complements R11 in CLAUDE.md)

- **S1 Cookie (D5, D20):** `__Host-mm_device` = opaque random (≥ 256 bit), `HttpOnly; Secure;
  SameSite=Lax; Path=/`, no `Domain` (host-only on `api.mmlabs.online`); only its hash is stored. The
  `__Host-` prefix stops sibling `*.mmlabs.online` hosts from setting or shadowing it.
- **S2 Rotation (D6):** rotate on login and at most every ~24 h; the previous value stays valid for
  60 s. Presenting an old value after the grace window = reuse → revoke the whole device session.
- **S3 Brute force (D11):** no hard lockout. Per-email exponential backoff after 5 failures (1, 2, 4 …
  min, cap 15 min); per-IP ~20 req/min on login/signup/forgot; Turnstile required after 3 failures
  (and always on signup / forgot). Same error body for unknown email and wrong password.
- **S4 CORS/CSRF (D12):** `Access-Control-Allow-Origin` echoes only origins in `apps.origins`, with
  `Allow-Credentials: true`. Every cookie-authenticated POST checks `Origin` against the app's origins
  and requires `X-MM-App: <key>` (the custom header forces a CORS preflight). CORS covers
  `/api/v1/auth/*` only, from a per-process cache of active apps' origins (TTL 60 s): ADR-0006.
- **S5 Verified email:** no join and no app token for an unverified email (D2). Login is allowed.
- **S6 Email (D3):** verification/reset resend at most 1/min and 5/day per email; tokens single-use,
  expiring, stored hashed. Gmail App Password only in env.
- **S7 Tokens (D7, R11):** signing keys from env, `kid`, documented rotation; apps must check `aud` (ADR-0005, rotation in deploy.md).
- **S8 Audit:** login (success/failure), logout, join, role/assignment changes, org membership changes
  are audit events (ADM-004).

## 6. Client libraries (D4)

- **`@mmlabs/auth-client`** (TypeScript, no framework dependency): `signup`, `login`, `listAccounts`,
  `getToken(audience, {orgId})` with in-memory caching and refresh before expiry, `join`, `logout`,
  account choice in `localStorage.mm_account_id`, error types (`not_member`, `email_unverified`, backoff).
  Thin wrappers: Angular (service + guard/interceptor), React (provider + hooks). Credentials are posted
  straight to the API, never through the app's backend.
- **`mmlabs-auth`** (Python, for app backends): JWKS fetch + cache, JWT verification (`iss`, `aud`,
  `exp`, `kid`), role-catalog cache with ETag, `require_permission("<app>:<res>:<action>")` (FastAPI
  dependency first).
- Where they live (this repo under `clients/`, or their own repos) and how they are published: decided
  in the USR-03b plan.

## 7. Risks

- Gmail SMTP daily sending limits (consumer accounts ~500/day, Workspace ~2,000/day) and possible
  throttling/spam filtering → `EmailSender` interface allows switching providers.
- Same-site requirement: the cookie flow relies on apps being on `*.mmlabs.online` (same site as the
  API). An app on another registrable domain would be cross-site and its credentialed requests would
  lose the cookie under third-party-cookie restrictions.
- `localStorage.mm_account_id` is per origin; two apps on the same origin would share it.
- Open join (D9) + open sign-up (D2) means anyone with a verified email gets `<app>:member` in every
  non-admin app; tightening is a data change (D9).

## 8. Decision log — 2026-11-11 (owner)

| # | Decision |
|---|---|
| D1 | Auth fully self-hosted here. No Supabase, no external IdP, not an OIDC provider (R10 non-goal stays). Email + password only; no social login. |
| D2 | Open self-registration, protected by Cloudflare Turnstile + per-IP rate limit. Unverified users may log in, but must verify their email before joining any app or getting an app token. |
| D3 | Email via Gmail SMTP (dedicated account, App Password, `smtp.gmail.com:587`) behind an `EmailSender` interface; async with retry; resend limit 1/min and 5/day per email. Gmail daily limits are a known risk. |
| D4 | Each app keeps its own login page (no central SSO page); accounts are shared. Apps use `@mmlabs/auth-client` (TS, framework-agnostic, Angular/React wrappers) posting credentials straight to the API. Backends use `mmlabs-auth` (Python): JWKS verification, role-catalog cache with ETag, `require_permission()`. |
| D5 | Multi-account browser sessions: cookie `mm_device` (opaque, HttpOnly, Secure, SameSite=Lax, Path=/auth, host-only, stored hashed); tables `device_sessions`, `account_sessions`; each app keeps `mm_account_id` in localStorage; logging into another account in app B does not affect app A. |
| D6 | Rotate `mm_device` on login and at most every ~24 h, with a 60 s grace for the previous value; reuse after the grace revokes the device session. |
| D7 | `POST /auth/token {audience, account_id, org_id?}` → JWT (~15 min): iss, aud, sub, email, email_verified, roles, org?, iat, exp; asymmetric signing, keys from env, `kid`. |
| D8 | Non-member → `403 {error:"not_member", join_available}`; `POST /auth/apps/{key}/join {account_id}` applies `join_policy` (open/approval/closed), assigns the default role at app scope, writes audit. "Continue as <account>" = consent. |
| D9 | Default join_policy `open`, except `admin` = `closed`. Default role `<app>:member` = all app permissions except `is_admin` ones (e.g. `<app>:roles:manage`). Policy and role content are data. |
| D10 | Org usage per app: `apps.org_mode` (`none` default, `required` for calechip). No org = `org_id NULL`. `organizations` lives in the Identity module. Org-scoped apps: join gives an app-scope role (e.g. with `calechip:org:create`); org roles from creating an org (owner) or invitation; token `roles` = app-scope + selected-org roles. |
| D11 | No hard lockout; per-email exponential backoff after 5 failures (cap 15 min), per-IP ~20/min, Turnstile after 3 failures; identical error for wrong email or password. |
| D12 | CORS echoes only `apps.origins` with credentials; every cookie-authenticated POST checks Origin and requires `X-MM-App: <key>`. |

Follow-up decisions (owner, 2026-11-11, resolving §10):

| # | Decision |
|---|---|
| D13 | **Path prefix (C1 = option B):** all identity endpoints are under `/api/v1/auth/*` and the cookie is `Path=/api/v1/auth` (amends D5, D7, D8 paths). Only `/.well-known/jwks.json` stays at the root. R9 needs no exception. |
| D14 | **R3 reworded (C2):** three endpoint classes — Bearer (admin, `aud=admin` + permission), cookie-authenticated identity endpoints, and public endpoints. Refresh tokens are gone. |
| D15 | **Organizations (C3):** the existing `organizations` table (ADM-001) moves to the Identity module and gains `created_by`; `description` and `status` are kept. One concept, used per app through `apps.org_mode`. |
| D16 | **Email verification ordering (U1):** no interim "mark verified" path. Joining an app and getting an app token stay blocked until USR-04 ships; backlog order unchanged. |
| D17 | **Role catalog is public (Q1, 2026-11-11):** `GET /api/v1/apps/{key}/roles` joins R3 class 3 (public). Rate-limited per IP; 404 for unknown, disabled and `admin` apps; exposes only role key, name, `is_system` and permission keys. |
| D18 | **R9 exceptions (Q2, 2026-11-11):** apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…`; the role catalog is a single document, not `Page[T]`. Permissions, roles and assignments keep UUID ids. |
| D19 | **Allowed app origins (Q3, 2026-11-11):** only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback (`http(s)://localhost`, `127.0.0.1`, `[::1]`). Any other origin → 422. |
| D20 | **Cookie `__Host-mm_device`, `Path=/` (USR-03f, owner 2026-11-12):** the device cookie is `__Host-mm_device` — `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Domain` — so sibling `*.mmlabs.online` hosts cannot set or shadow it (cookie tossing). Amends D5 and D13 for the cookie only; identity endpoints stay under `/api/v1/auth/*`. A legacy `mm_device` is ignored. Logout `{account_id}` with the previous value only reaches account sessions created before the rotation; `{all:true}` with the previous value revokes the whole device (denial of service within 60 s only, never escalation). |

## 9. Open questions

- **OQ1** Organizations platform-wide or per app? Working assumption: platform-wide + `apps.org_mode`;
  `organizations.app_key` can be added later without breaking anything.
- **OQ2** (was Q9) No migration from Supabase Auth; working assumption: existing users re-register.
- **OQ3** (was Q10) Is 2FA mandatory for staff (admin app)? Open.

## 10. Conflicts with earlier rules/spec — resolved 2026-11-11

- **C1 — Path prefix** → D13: `/api/v1/auth/*`, cookie `Path=/api/v1/auth`. The cookie path is superseded by
  D20 (`__Host-mm_device`, `Path=/`, 2026-11-12); the endpoint prefix is unchanged.
- **C2 — R3 categories** → D14: R3 in CLAUDE.md reworded.
- **C3 — Organizations ownership** → D15: one `organizations` table, moved to Identity, `created_by` added.
- **U1 — Verified email before USR-04** → D16: wait for USR-04. Consequence: between the ADM-002 and USR-04
  releases nobody (staff included) can obtain an app token, so the admin endpoints would be unusable in
  production — release ADM-002 together with or after USR-04 (see ADM-002 notes).
