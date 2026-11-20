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

**Join** — UI shows "Continue as <account>" (= consent) → `POST /api/v1/auth/apps/B/join {account_id}`
(cookie + Origin + `X-MM-App: B`; the path key must be B, else `403 app_mismatch`) → under the app lock,
checks the account (401 `session_invalid`) and the verified email (403 `email_unverified`); an existing
app-scope member gets 200 with no write; then applies `join_policy`: `open` assigns B's default role at
app scope (`org_id NULL`, an expired row is revived) and writes an audit entry → 200 `{"status":"member"}`
→ app retries `/api/v1/auth/token`. `closed` and `admin` → 403 `join_closed`; no usable default role →
403 `join_unavailable` (USR-03b). `approval` (USR-03g) queues one `join_requests` row (idempotent) and
writes `app.join_requested` → 202 `{"status":"pending","request_id"}`; inside the rejection cooldown
(`JOIN_REJECT_COOLDOWN_SECONDS`, default 7 days) → 403 `join_rejected`. Staff then approve (grants the
default role at app scope, `granted_by` = staff) or reject; the app polls `/token`. A new request is
refused with 403 `join_queue_full` when the app has `JOIN_MAX_PENDING_PER_APP` pending requests
(USR-15). Disabling a user cancels their pending requests (`cancelled`, no cooldown).

**Switch / list accounts** — `GET /api/v1/auth/accounts` lists the device's non-revoked account sessions
of active users, oldest first: `{"items": [{account_id, email, email_verified, display_name}]}` (a single
document, proposed D22). With the previous cookie value only accounts created before the rotation are
listed. Switching only changes the app's `mm_account_id`.

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
                    default_role_id → roles NULL  (CHECK: never equal to org_owner_role_id; USR-06c),
                    org_owner_role_id → roles NULL  (same-app FK; only when org_mode = required; never
                                       a role with is_admin permissions; USR-06a),
                    org_mode     none|required          default 'none'   (calechip = 'required'),
                    origins      text[]                 (allowed browser origins, CORS/CSRF),
                    created_at, updated_at)
permissions        (id, app_id, key, description, is_admin bool default false,
                    scope app|org default 'app'  (USR-06c, ADR-0015))              unique (app_id, key)
roles              (id, app_id, key, name, is_system)                            unique (app_id, key)
role_permissions   (role_id, permission_id)
role_assignments   (id, user_id, role_id, org_id → organizations NULL, granted_by NULL,
                    expires_at NULL, created_at)   org_id NULL = app scope; never 0
organizations      (id uuid, name, slug unique, description NULL, status active|suspended,
                    created_by → users NULL, created_at, updated_at)   [Identity module; existing table moved, D15]
join_requests      (id, app_id, user_id, status pending|approved|rejected|cancelled, created_at, decided_at NULL,
                    decided_by NULL) — join_policy = approval (USR-03g). CHECK pending iff decided_at
                    NULL; partial unique (app_id, user_id) WHERE pending; FKs cascade app/user,
                    decided_by SET NULL
org_entitlements   (id, org_id, app_id, plan slug, limits jsonb {key: int >= 0}, enabled, created_at,
                    updated_at) — ADM-006. UNIQUE (org_id, app_id); FKs RESTRICT org/app
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
  (for `org_mode = required` apps also `scope = 'app'`, ADR-0015) (D9). Tightening later = editing `role_permissions` / `apps.join_policy`, no code change.
- `organizations` was created by ADM-001. D15 "moves to the Identity module" means **ownership**: the
  code stays in `app/modules/organizations/` (the reference module, no rename); USR-06a added
  `created_by` (nullable, `ON DELETE SET NULL`; NULL for existing and staff-created rows), kept
  `description` and `status`, and added the FK `role_assignments.org_id → organizations.id`
  `ON DELETE RESTRICT` (admin delete of an org with any assignment answers 409 `org_has_assignments`).
- OQ1: organizations are platform-wide for now; adding `organizations.app_key` later is additive.

## 4. API surface

All identity endpoints live under **`/api/v1/auth`** (D13). The cookie is `__Host-mm_device` with `Path=/`
(D20, amends D5/D13 for the cookie); only the identity endpoints read it. The only endpoint outside `/api/v1` is `/.well-known/jwks.json` (standard location, public).

| Method & path | Auth | Purpose | Ticket |
|---|---|---|---|
| `POST /api/v1/auth/signup` `{email, password, turnstile_token}` | public, Turnstile, per-IP limit + Origin/`X-MM-App` check (USR-04c) | create unverified user; send verification email | USR-01 (+USR-04 email) |
| `POST /api/v1/auth/login` `{email, password, turnstile_token?}` | public (per-IP limit; Turnstile after 3 failures) + Origin/`X-MM-App` check (sets `__Host-mm_device`, ADR-0004) | create/rotate device session, add account session → `{account_id, user}` | USR-03 |
| `GET /api/v1/auth/accounts` | cookie + Origin + `X-MM-App` | accounts signed in on this device | USR-03b |
| `POST /api/v1/auth/token` `{audience, account_id, org_id?}` | cookie + `X-MM-App` | per-app JWT (also `aud=admin` since ADM-002), or `403 {error:"not_member", join_available}` / `403 email_unverified` | USR-03c, ADM-002 |
| `POST /api/v1/auth/apps/{key}/join` `{account_id}` | cookie + Origin + `X-MM-App` (= `{key}`) | apply join policy, assign default role, audit; `approval`: 202 pending (USR-03g) | USR-03b |
| `GET /api/v1/apps/{key}/join-requests?status=` | Bearer `admin:users:read` | staff list (default `pending`; also `approved`, `rejected`, `cancelled`) | USR-03g |
| `POST /api/v1/apps/{key}/join-requests/{request_id}/approve` \| `/reject` | Bearer `admin:roles:assign` | decide a request (200; 409 reasons in the response) | USR-03g |
| Admin: `/api/v1/organizations/{org_id}/entitlements` (GET `?active=`, POST, PATCH/DELETE `/{entitlement_id}`) | Bearer `admin:entitlements:read` (GET) / `admin:entitlements:write` | grant, update, revoke and list an org's product entitlements (409 reasons in `details[0].reason`) | ADM-006 |
| Admin: `GET /api/v1/stats/dashboard` | Bearer `aud=admin` + `require_permission` (`admin:stats:read`, `is_admin`, granted to no role; `admin:*` covers it) | dashboard counters, plus every app's key and name incl. disabled and `admin` apps via `members_per_app` (single document, `Cache-Control: no-store`, no audit row) | ADM-015 |
| Admin: `/api/v1/bug-reports` (GET list `?app_key=&status=&reporter_id=&from=&to=`, GET `/{report_id}`, PATCH `/{report_id}`) | Bearer `aud=admin` + `admin:bug-reports:read` (GET) / `admin:bug-reports:write` (PATCH), both `is_admin`, granted to no role; `admin:*` covers them | staff triage of end-user bug reports from every app: list (reporter email is PII, `no-store`), detail, mark done / reopen / resolution note with optional `If-Match` (412); PATCH is audited with ids and status only (`bug_report.marked_done` / `reopened` / `note_updated`), reads are not | ADM-029 |
| `POST /api/v1/auth/logout` `{account_id}` \| `{all: true}` | cookie | revoke one account session / the device session | USR-03 |
| `POST /api/v1/auth/verify-email` `{token}`; `POST /api/v1/auth/verify-email/resend` `{account_id}` | public / cookie | email verification; resend accepts `X-MM-App: admin` for staff only (USR-12); signup and forgot refuse admin (ADM-002d) | USR-04 |
| `POST /api/v1/auth/password/forgot` `{email, turnstile_token}` → always 202 | public, Turnstile, per-IP limit + Origin/`X-MM-App` check (USR-04b) | send reset link (silent per-email limit) | USR-04b |
| `POST /api/v1/auth/password/reset` `{token, new_password}` | public, per-IP limit | set password, revoke all account sessions of the user (every device) | USR-04b |
| `GET /.well-known/jwks.json` | public | signing public keys (`kid`) | USR-03c |
| `GET /api/v1/apps/{key}/roles` (ETag / 304) | public (D17), per-IP limit | role → permissions catalog | USR-02e |
| `PUT /api/v1/apps/{key}/manifest` | app credential (ADR-0016, D25) | app pushes its permissions + default role | USR-05b |
| Admin: `POST\|GET /api/v1/apps/{key}/credentials`, `DELETE /api/v1/apps/{key}/credentials/{id}` | Bearer `aud=admin` + `require_permission` (`admin:apps:write` and `admin:*` to issue; `admin:apps:read`; `admin:apps:write` to revoke) | staff issue (shown once), list and revoke app credentials | USR-05a |
| Admin: `POST\|GET /api/v1/deployer-credentials`, `DELETE /api/v1/deployer-credentials/{id}` | Bearer `aud=admin` + `require_permission` (`admin:*` on all three) | staff issue (shown once, `no-store`), list and revoke deployer credentials (ADR-0021; at most 2 active; no route accepts the key until ADM-036) | ADM-035 |
| `POST /api/v1/auth/orgs` `{account_id, name, slug, description?}` | cookie; needs the app-scope permission `<app>:org:create` (ADR-0013) | create org, creator gets `apps.org_owner_role_id` in it; 201 | USR-06a |
| `GET /api/v1/auth/orgs?account_id=` | cookie | the account's orgs where it holds an org-exact role of the calling app (`X-MM-App`; no `app=` parameter), `Page[…]` with the caller's roles | USR-06a |
| `POST /api/v1/auth/token` with `org_id` | cookie | adds `org`, `org_roles`, `ent` claims; 403 `org_access_denied` (unknown, suspended or not-a-member org, one answer); 422 `org_not_supported` for `org_mode = none` apps | USR-06a |
| `POST /api/v1/auth/orgs/{id}/invitations` `{account_id, email, role}` · `POST /api/v1/auth/invitations/accept` `{account_id, token}` | cookie; invite needs `<app>:org:invite` in that org (org-exact); accept is bound to the invited verified email (token in the body, ADR-0014) | invite / accept into an org | USR-06b |
| `POST /api/v1/auth/bug-reports` `{account_id, description, org_id?, page_url?, app_version?, user_agent?}` | cookie + Origin + `X-MM-App` (not `admin`); signed-in, active, verified member of the app | end-user bug report (text); 201 `{id, status, created_at}`; caps: 10 per user per 24 h, 10 open per user per app (429) | ADM-028 |
| Admin: `/api/v1/organizations` (CRUD; list `?status=&q=&sort=`, ADM-020) | Bearer `aud=admin` + `require_permission` (`admin:organizations:read\|write`) | organizations | ADM-001, ADM-002 |
| Admin: `GET /api/v1/organizations/{org_id}/members` · `GET\|DELETE /api/v1/organizations/{org_id}/invitations[/{id}]` | Bearer `aud=admin` + `require_permission` (`admin:users:read`; `admin:organizations:read`; `admin:organizations:write` to revoke) | staff read of an org's org-scoped role assignments and invitations (status derived on the DB clock, no invitee email or token); revoke is idempotent, 409 if accepted | USR-23 |
| Admin: `/api/v1/users` (list, get, status) | Bearer `aud=admin` + `require_permission` (`admin:users:read\|write`; `admin:*` for privileged users) | staff user management | USR-01, ADM-002 |
| Admin (self-scoped): `GET /api/v1/me/permissions` | Bearer `aud=admin`, self-scoped, no permission (ADR-0008 USR-11 amendment) | caller's own `user_id`, admin role keys, effective admin permissions | USR-11 |
| Admin: `/api/v1/apps` (list `?status=&join_policy=&org_mode=&q=&sort=`, USR-18), `/apps/{key}/permissions`, `/apps/{key}/roles` (writes) | Bearer `aud=admin` + `require_permission` (`admin:apps:read\|write`, `admin:roles:manage`; `admin:*` for the admin app and `is_admin` changes); PATCH `org_mode` required→none: 409 `org_mode_in_use` while org entitlements or org-scoped assignments exist (ADM-019) | apps, permissions, roles | USR-02, ADM-002 |
| Admin: `GET /api/v1/apps/{key}/role-definitions[/{role_id}]` | Bearer `aud=admin` + `admin:apps:read` | staff read model of roles with ids (`Page`), `admin` and disabled apps included, no rate limit; distinct from the public catalog | USR-16 |
| Admin: `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` | Bearer `aud=admin` + `admin:users:read` (no `admin:*` gate, `admin` and disabled apps included) | holders of a role (`Page`; assignment fields + user `id`, email, display name, status); `total` counts assignments, not distinct users; `Cache-Control: no-store` (emails, §8 Q6) | USR-17 |
| Admin: `/api/v1/users/{id}/role-assignments` | Bearer `aud=admin` + `require_permission` (`admin:users:read`, `admin:roles:assign`; `admin:*` for privileged roles) | role assignments | USR-02, ADM-002 |
| Admin: `GET\|DELETE /api/v1/users/{id}/sessions`, `DELETE /api/v1/users/{id}/sessions/{session_id}` | Bearer `aud=admin` + `admin:*` (self → 403 `self_session_change_forbidden`) | list active account sessions (`Page`, `no-store`), revoke one / all (account sessions only; device sessions stay). Revoking sessions stops the user from getting **new** access tokens (`/auth/token` -> 401 `session_invalid`) and ends the user's admin (`aud=admin`) tokens bound to those sessions on their next admin request (USR-14). Tokens of other apps stay valid until `exp`, at most `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway. It does not stop a new sign-in with the password. IP and user agent are recorded when the browser first signed in and are shared by every account on that browser. | USR-10 |
| Admin: `GET /api/v1/audit-logs` (filters `actor_user_id`, `actor_kind`, `action`, `target_type`, `target_id`, `app_key`, `org_id`, `from`/`to`) | Bearer `aud=admin` + `require_permission` (`admin:audit:read`, held only via `admin:*`) | read-only audit log | ADM-004, ADM-021 |
| Admin: `GET /api/v1/audit-logs/{audit_log_id}` | Bearer `aud=admin` + `require_permission` (`admin:audit:read`) | read-only single audit entry | ADM-021 |
| Admin: GET /api/v1/audit-logs/export (same filters as the list; text/csv, ≤ 10,000 rows, X-Export-Truncated) | Bearer aud=admin + require_permission (admin:audit:read) | audited CSV export of the audit log | ADM-022 |

Admin auth (ADR-0008): the Bearer token comes from the `Authorization` header only, never the
device cookie. It is verified like `docs/consumers.md` prescribes (`typ`, ES256, `kid`, `iss`,
`aud=admin`, `exp`, required claims, `email_verified`); then the user is reloaded (active,
verified) and its admin permissions are read from the database on every request; the reload also checks that the token's `sid` account session (and its device) is not revoked (USR-14). 401
`unauthorized` (`WWW-Authenticate: Bearer realm="admin"`, no details) for a missing or bad token
or an unusable user; 403 `permission_denied` with `details.required` for a missing permission.
`admin:*` grants every admin permission.

Privileged writes need `admin:*` on top of the route permission (ADM-002b, ADR-0008 amendment):
anything on the `admin` app, any change across the `is_admin` boundary, granting or revoking a
role that is an admin-app role or holds an `is_admin` permission, and the status of a user holding
one. Codes: 403 `admin_wildcard_required` (`details.required = "admin:*"`), 403
`self_assignment_forbidden` / `self_status_change_forbidden` / `self_session_change_forbidden` (nobody changes their own admin roles
or status), 409 `last_super_admin`, 409 `role_has_assignments`, 422 `wildcard_super_admin_only`,
409 `conflict`/`origin_taken`, 409 `app_key_taken`. OpenAPI marks these operations with
`x-requires-super` (`always` / `conditional` + `x-requires-super-when`). Disabling a user revokes its account sessions on every device.

Token (D7): JWT, ES256 (P-256, ADR-0005), lifetime `ACCESS_TOKEN_TTL_SECONDS` (default 15 min, 12 h in production; ADR-0005 §6 as amended by USR-13), header `kid` + `typ: at+jwt`.
Claims `iss` (from `JWT_ISSUER`; production `https://api.mmlabs.online`), `aud=<app key>`, `sub`,
`email`, `email_verified`, `roles` (app-scope roles + roles in the selected org), `org` (only when
`org_id` given, USR-06), `org_roles` (only with `org`: roles held in that org only, ADR-0015), `ent` (only with `org`, never `aud=admin`: `{plan, limits}` of the active entitlement or `null`, ADR-0017), `iat`, `exp`, `jti`, `sid` (only for `aud=admin`: the account-session id, USR-14).

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
- **S8 Audit (ADR-0009):** an append-only `audit_log` (DB trigger refuses UPDATE/DELETE/TRUNCATE).
  `AuditLogger.record` adds one row in the same transaction as the change, after the last guard, for
  every admin mutation (organizations, user status, apps, permissions, roles, role permissions,
  role assignments), every CLI write (`system:cli`: register-app, set-admin-origins,
  grant-super-admin, cleanup summary, create-account: `user.created` + `app.joined`) and `app.joined`
  (ADM-004); join requests (USR-03g): `app.join_requested`, `app.join_request_approved`,
  `app.join_request_rejected`, `app.join_request_cancelled` (user disabled, USR-15), `join_request_retention.purged` (cleanup summary); staff session revoke (USR-10): `user.session_revoked`, `user.sessions_revoked` (session ids and counts only; no-ops write nothing). Already-issued access tokens stay valid until `exp`, at most `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway for app tokens; admin tokens end on the next admin request (USR-14). Identity events (ADM-004b):
  `auth.login_succeeded`, `auth.login_failed` (after the password check only; anonymous actor, no
  email), `auth.logged_out`, `auth.logged_out_all`, `device_session.reuse_revoked`,
  `auth.password_reset_completed`, `auth.email_verified`. Manifest pushes (USR-05b): one
  `app.manifest_synced` row per changing push (system actor `app:<key>/<key_id>`; a credential of
  another app writes one `denied` row). Guard refusals of a staff principal
  (`admin_wildcard_required`, self-guards, `last_super_admin`) write `outcome=denied`; failed logins
  `outcome=failure`. Failure rows are committed alone and an audit error never changes the intended
  error (ADR-0009 amendment). 429s, Turnstile refusals and no-ops write nothing. `changes` holds
  allowlisted fields only, never emails, hashes, tokens or cookies. Each row carries ip, user agent
  and `X-Request-ID` (kept only from trusted proxies). Retention: the operator-only
  `audit-purge` CLI deletes rows older than N ≥ 90 days (DB-enforced floor, ADM-004c); org
  membership events come with USR-06.

## 6. Client libraries (D4)

- **`@mmlabs/auth-client`** (TypeScript, no framework dependency): `signup`, `login`, `listAccounts`,
  `getToken(audience, {orgId})` with in-memory caching and refresh before expiry, `join`, `logout`,
  account choice in `localStorage.mm_account_id`, error types (`not_member`, `email_unverified`, backoff).
  Thin wrappers: Angular (service + guard/interceptor), React (provider + hooks). Credentials are posted
  straight to the API, never through the app's backend.
- **`mmlabs-auth`** (Python, for app backends): JWKS fetch + cache, JWT verification (`iss`, string-only
  `aud` via `strict_aud`, `exp`, `kid`), role-catalog cache with ETag, `require_permission("<app>:<res>:<action>")` (FastAPI
  dependency first).
- They live in a **separate repo** for the `@mmlabs` libraries (owner creates it) and are built in
  USR-08 (D21). Until then apps call the API with plain `fetch` (`docs/consumers.md`).

## 7. Risks

- Gmail SMTP daily sending limits (consumer accounts ~500/day, Workspace ~2,000/day) and possible
  throttling/spam filtering → `EmailSender` interface allows switching providers.
- Same-site requirement: the cookie flow relies on apps being on `*.mmlabs.online` (same site as the
  API). An app on another registrable domain would be cross-site and its credentialed requests would
  lose the cookie under third-party-cookie restrictions.
- `localStorage.mm_account_id` is per origin; two apps on the same origin would share it.
- Open join (D9) + open sign-up (D2) means anyone with a verified email gets `<app>:member` in every
  non-admin app; tightening is a data change (D9).
- Under `open`, a member that staff removed can join again (no ban list); staff use `closed` or tighten
  the role. Reviving an expired default-role assignment makes it permanent (`expires_at NULL`).
- A leaked app credential can rewrite its own app's catalog (never `is_admin` or `scope` flips,
  never other apps, never `<app>:*`): revoke it at once (ADR-0016 Consequences,
  [auth-integration.md](auth-integration.md)).
- `GET /api/v1/auth/accounts` shows every account signed in on the browser to any registered app
  origin (D4/D8 "Continue as"); XSS in one app exposes those emails (USR-03b plan O3).

## 8. Decision log — 2026-11-11 (owner)

| # | Decision |
|---|---|
| D1 | Auth fully self-hosted here. No Supabase, no external IdP, not an OIDC provider (R10 non-goal stays). Email + password only; no social login. |
| D2 | Open self-registration, protected by Cloudflare Turnstile + per-IP rate limit. Unverified users may log in, but must verify their email before joining any app or getting an app token. |
| D3 | Email via Gmail SMTP (dedicated account, App Password, `smtp.gmail.com:587`) behind an `EmailSender` interface; async with retry; resend limit 1/min and 5/day per email. Gmail daily limits are a known risk. |
| D4 | Each app keeps its own login page (no central SSO page); accounts are shared. Apps use `@mmlabs/auth-client` (TS, framework-agnostic, Angular/React wrappers) posting credentials straight to the API. Backends use `mmlabs-auth` (Python): JWKS verification, role-catalog cache with ETag, `require_permission()`. |
| D5 | Multi-account browser sessions: cookie `mm_device` (opaque, HttpOnly, Secure, SameSite=Lax, Path=/auth, host-only, stored hashed); tables `device_sessions`, `account_sessions`; each app keeps `mm_account_id` in localStorage; logging into another account in app B does not affect app A. |
| D6 | Rotate `mm_device` on login and at most every ~24 h, with a 60 s grace for the previous value; reuse after the grace revokes the device session. |
| D7 | `POST /auth/token {audience, account_id, org_id?}` → JWT (TTL default 15 min, 12 h in production; owner, 2026-11-17, USR-13): iss, aud, sub, email, email_verified, roles, org?, org_roles?, ent?, iat, exp; asymmetric signing, keys from env, `kid`. |
| D8 | Non-member → `403 {error:"not_member", join_available}`; `POST /auth/apps/{key}/join {account_id}` applies `join_policy` (open/approval/closed), assigns the default role at app scope, writes audit. "Continue as <account>" = consent. |
| D9 | Default join_policy `open`, except `admin` = `closed`. Default role `<app>:member` = all app permissions except `is_admin` ones (e.g. `<app>:roles:manage`); on `org_mode = required` apps also except `scope = org` ones (ADR-0015). Policy and role content are data. |
| D10 | Org usage per app: `apps.org_mode` (`none` default, `required` for calechip). No org = `org_id NULL`. `organizations` lives in the Identity module. Org-scoped apps: join gives an app-scope role (e.g. with `calechip:org:create`); org roles from creating an org (owner) or invitation (ADR-0014); token `roles` = app-scope + selected-org roles; `org_roles` = selected-org roles only (ADR-0015). |
| D11 | No hard lockout; per-email exponential backoff after 5 failures (cap 15 min), per-IP ~20/min, Turnstile after 3 failures; identical error for wrong email or password. |
| D12 | CORS echoes only `apps.origins` with credentials; every cookie-authenticated POST checks Origin and requires `X-MM-App: <key>`. |

Follow-up decisions (owner, 2026-11-11, resolving §10):

| # | Decision |
|---|---|
| D13 | **Path prefix (C1 = option B):** all identity endpoints are under `/api/v1/auth/*` and the cookie is `Path=/api/v1/auth` (amends D5, D7, D8 paths). Only `/.well-known/jwks.json` stays at the root. R9 needs no exception. |
| D14 | **R3 reworded (C2):** three endpoint classes — Bearer (admin, `aud=admin` + permission), cookie-authenticated identity endpoints, and public endpoints. Refresh tokens are gone. |
| D15 | **Organizations (C3):** the existing `organizations` table (ADM-001) moves to the Identity module and gains `created_by`; `description` and `status` are kept. One concept, used per app through `apps.org_mode`. |
| D16 | **Email verification ordering (U1):** no interim "mark verified" path. Joining an app and getting an app token stay blocked until USR-04 ships; backlog order unchanged. *Amended by ADR-0010 (USR-09, 2026-11-15):* the CLI `create-account` is the single operator-vouched exception (verified email at creation, TTY only, never on an existing account). |
| D17 | **Role catalog is public (Q1, 2026-11-11):** `GET /api/v1/apps/{key}/roles` joins R3 class 3 (public). Rate-limited per IP; 404 for unknown, disabled and `admin` apps; exposes only role key, name, `is_system` and permission keys. |
| D18 | **R9 exceptions (Q2, 2026-11-11):** apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…`; the role catalog is a single document, not `Page[T]`. Permissions, roles and assignments keep UUID ids. `/apps/{key}/role-definitions` (USR-16) is the staff `Page[T]` read of roles with ids, distinct from the public catalog; no new exception. |
| D19 | **Allowed app origins (Q3, 2026-11-11):** only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback (`http(s)://localhost`, `127.0.0.1`, `[::1]`). Any other origin → 422. |
| D20 | **Cookie `__Host-mm_device`, `Path=/` (USR-03f, owner 2026-11-12):** the device cookie is `__Host-mm_device` — `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Domain` — so sibling `*.mmlabs.online` hosts cannot set or shadow it (cookie tossing). Amends D5 and D13 for the cookie only; identity endpoints stay under `/api/v1/auth/*`. A legacy `mm_device` is ignored. Logout `{account_id}` with the previous value only reaches account sessions created before the rotation; `{all:true}` with the previous value revokes the whole device (denial of service within 60 s only, never escalation). |

Decisions from the USR-03b plan (2026-11-13):

| # | Decision |
|---|---|
| D21 | **Client libraries (owner, 2026-11-13):** `@mmlabs/auth-client` and `mmlabs-auth` move to USR-08 and live in a separate repo for the `@mmlabs` libraries. |
| D22 | **Proposed, pending owner:** R9 exception — `GET /api/v1/auth/accounts` returns a single document `{"items": [...]}`, not `Page[T]` (bounded per device). |
| D23 | **Proposed, pending owner (USR-11):** self-scoped admin read `GET /api/v1/me/permissions` — R3 class 1 without `require_permission`, and R9 exception (singular `me`, single document). |
| D24 | **Decided (USR-12, 2026-11-16):** verification resend accepts `X-MM-App: admin` for users with an effective admin role only (403 `origin_not_allowed` otherwise); signup and forgot keep refusing admin. See ADR-0008 amendment. |
| D25 | **Accepted (owner merged USR-05a, PR #47, 2026-11-17):** new R3 auth class 4 "app credential" (opaque per-app API key `mmak_<key_id>_<secret>`, SHA-256 hash only, accepted only on `PUT /api/v1/apps/{key}/manifest` and only for its own app) and R9 exception (singular `manifest`, a single document). See ADR-0016. |
| D26 | **Proposed in ADM-015, accepted when the owner merges it (answers OQ4–OQ6, 2026-11-17):** R9 exception — `GET /api/v1/stats/dashboard` is a single, bounded document (fixed counters, 12 months, one entry per app), not `Page[T]`. Permission `admin:stats:read` (`is_admin`): counts, no audit rows exposed; it also reveals every app's key and name, including disabled apps and the `admin` app, through `members_per_app` (accepted by the owner, appsec finding 1, so no `admin:apps:read` is needed for that list). Members = OQ5 definition, one shared repository function (`RbacRepository.member_counts`) also used by ADM-016. |
| D27 | **Entitlement consumption (owner, 2026-11-17/18, ADM-018):** products read an org's entitlement through the additive `ent` token claim (`{plan, limits}`, or `null` when not entitled; present iff `org` is present, never on `aud=admin`). No pull endpoint; R3 class 4 and ADR-0016 unchanged. See ADR-0017. |
| D28 | **Bug-report submit (owner, 2026-11-19, ADM-028):** `POST /api/v1/auth/bug-reports` is an R3 class 2 (cookie) route under `/auth/*` although it is not an identity action; `admin` is refused as a report target; a disabled reporter gets 401 `session_invalid`. Caps and field rules in ADR-0019. |
| D29 | **Bug-report triage (owner, 2026-11-20, ADM-029):** permissions `admin:bug-reports:read` / `:write` (`is_admin`, granted to no role); no tightening of ADM-028 submit validation, untrusted text is returned verbatim and documented as plain text; note semantics (absent = unchanged, `null` = clear, blank = 422); three audit actions; done on done is a 200 no-op. See ADR-0020. |
| D31 | **Usage key scopes (owner, 2026-11-20, ADM-041):** app-credential scopes `usage:read`, `usage:write` (also the per-user usage read), `limits:write` (runtime keys, one scope per key, refused for `admin`); no `lock_app` on runtime auth; `on_behalf_of` bound to the app admin's access token; per-app idempotency keys; fail-closed default, staff-only fail-open grace; per-key rate limit; issuance stays ungated. R3 class 4 rewritten scope-generic. Amends ADR-0021. See ADR-0022. |

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
