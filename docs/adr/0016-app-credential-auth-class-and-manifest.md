# ADR-0016: App credential auth class and the app manifest

Status: Accepted — owner merged USR-05a (PR #47), 2026-11-17
Amended by ADR-0021 (§1 scopes, §3 limit per (app, scope), §5 `admin` refusal manifest-only)
Date: 2026-11-17

## Context
ADR-0001 §6: apps declare their catalog (permissions and default roles) through an API call at
deploy time; this service stores and assigns them. That call is made by a deploy job of the app,
not by a signed-in person, so none of the three R3 auth classes (Bearer admin, cookie, public)
fits. USR-05a adds the credential and its staff endpoints; USR-05b adds the route that accepts it.

## Decision

### 1. Auth class 4, "app credential"
Allowed on exactly one route, `PUT /api/v1/apps/{key}/manifest` (added in USR-05b), and listed by
exact (method, path) in `APP_CREDENTIAL` in `tests/test_route_auth_classes.py`. Never accepted on
any other route; an admin JWT is never accepted on that route. No CORS (server-to-server; the
route is outside `/api/v1/auth/` and has no `current_admin`). Rate-limited per IP before
authentication.

### 2. Type
An opaque per-app API key, not OAuth client credentials. Client credentials would add a token
endpoint, a second JWT audience and machine clients, which moves toward the R10 / ADR-0001
"later" OIDC provider, for one deploy-time call.

### 3. Format and storage
- Format `mmak_<key_id>_<secret>`: `key_id` is 16 hex chars (public, used for lookup), the secret
  is 256 bits (`secrets.token_urlsafe(32)`, 43 chars).
- Only SHA-256(secret) is stored, compared with `hmac.compare_digest`. No pepper: this is the
  house pattern for 256-bit secrets (`device_token_hash`) and adds no env secret.
- At most 2 active credentials per app (rotation overlap). No expiry in v1.
- Revocation is immediate (every request reads the database). Disabling the app blocks the
  credential; deleting the app cascades.

### 4. Transport
`Authorization: Bearer mmak_…` only, never query, body or cookie. One 401 answer (`unauthorized`,
`WWW-Authenticate: Bearer realm="app"`, no details) for missing, malformed, unknown, wrong-secret
or revoked credentials. A valid credential for another app gives 403 `app_credential_mismatch`,
audited `denied`. A disabled app gives 403 `app_disabled`.

### 5. Issuance
Bearer `admin:apps:write` plus `admin:*` (a long-lived external secret that writes the catalog).
Revoke needs `admin:apps:write` (it only removes access). Both are audited. Issuing is refused for
the `admin` app (409 `admin_app`) and for disabled apps (409 `app_disabled`). The value is shown
once, with `Cache-Control: no-store`; list returns metadata only.

### 6. Manifest actor and write policy (applies in USR-05b)
- Audit actor `SystemActor(f"app:{key}/{key_id}", privileged=False)`, `actor_kind = system`, so no
  audit-schema migration.
- Create-only carve-out for new `is_admin` permissions (inert).
- No `is_admin` or `scope` flips.
- New non-reserved `scope = app` permissions on `org_mode = required` apps are refused (ADR-0015 §2
  unchanged).
- Extra roles never hold `is_admin` permissions and never touch system, default or owner roles.
- The manifest never touches app fields.
- `<app>:member` is rebuilt as in USR-05b's plan (register parity).

### 7. R9 exception
`PUT /api/v1/apps/{key}/manifest`: a singular noun and a single document (like D18/D23).

## Rejected alternatives
- OAuth client credentials (see §2).
- An `X-App-Key` header: proxies and APM redact `Authorization`, not custom headers.
- An HMAC pepper: a new env secret with little gain for a 256-bit random secret.
- Argon2: a DoS amplifier for a high-entropy secret.
- A new `actor_kind = 'app'`: needs a CHECK rewrite migration; the label already identifies the app
  and key.

## Consequences
- A leaked key can add non-admin permissions that reach every member, add inert admin permissions,
  and add permissions to manifest-managed extra roles. It cannot flip flags, touch other apps or
  `admin`, grant roles, or delete anything.
- Mitigations: `admin:*` to issue, at most 2 keys, `last_used_at`, full audit, immediate revoke.
- Follow-ups: credential `expires_at` and `label`, a per-credential rate-limit bucket.
