# consult-identity-access-engineer — ADM-002

**Findings**

1. **No admin token can be issued yet.** `TokenService.issue` raises `AudienceNotSupportedError` for `aud=admin` (app/modules/tokens/service.py, "until ADM-002"). The seeded `admin` app has `origins: []`, so `/api/v1/auth/token` (Origin + `X-MM-App` check) cannot be called for `admin` from any browser. Both have to change in this ticket, or the Bearer guard locks everyone out.
2. **Signing is done, verification is missing.** `KeySet.sign` writes ES256 with header `kid` and `typ=at+jwt`, and claims `iss/aud/sub/email/roles/iat/exp/jti`. The codebase has no verify function, no `Authorization` parsing and no `current_user`. `KeySet` keeps only the signing private key and `jwks_body`. It holds no public-key map by kid, so a verifier would have to parse the JWKS or extend `KeySet`.
3. **Token `roles` holds role keys, not permissions.** `require_permission` cannot read the token alone. It has to call `AuthzService.effective_permissions(user, "admin")` per request, or the token must carry permissions.
4. **`permits()`** matches the exact key or `<app>:*`, with no global `*`, so `admin:*` already grants everything in `admin:`. The super-admin role is the only seeded holder of `admin:*`, and no other `admin:*` permissions are seeded.
5. **Permission declarations** (`openapi_extra=permission(...)`, app/core/permissions.py) exist on the users, apps/roles and assignments routers. **The organizations router declares none** (5 routes, mounted unconditionally via `api_router`).
6. **The 8 `# ADM-002 guard` sites in rbac/service.py are comments only.** They cover app origins, permission create/update(is_admin), admin-role create/update, replace_permissions, `_grant_locked` and revoke. Every service method already takes `actor: uuid.UUID | None = None`, and the routers never pass it.
7. **Last-super-admin guard** exists only on assignment revoke. `UserService.set_status` (disable) has no guard and takes no `lock_app("admin")`. It also does not revoke device sessions (comment only).
8. **Flags.** `ADMIN_USERS_API_ENABLED` and `ADMIN_RBAC_API_ENABLED` gate mounting in `create_app`. `_admin_apis_gate` forbids them in staging/production.
9. **Admin `org_mode`.** It cannot change through bulk upsert (line ~349). I could not find a pin in `AppService.update`; the planner should verify that `AppUpdate` has no `org_mode` field. `_grant_locked` does refuse `org_id` when `org_mode=none`.

**Recommendations**

- **R-1 (AC1, R3, R11): add `app/core/access_token.py`, pure with no ORM, next to `permits` so mmlabs-auth can copy both.**
  - `jwt.decode(algorithms=["ES256"], audience="admin", issuer=settings.jwt_issuer, options={"require": ["exp","iat","sub","aud","iss","jti"]})`.
  - Check header `typ == "at+jwt"` and that `kid` is one of the signing kid or the extra kids, before verifying.
  - Leeway of 30 s or less.
  - Every failure gives one 401 `invalid_token` with `WWW-Authenticate: Bearer` and no details.
  - Read only `Authorization: Bearer`. Never read cookies, and add a test that `__Host-mm_device` alone gets 401.
- **R-2: `current_user` loads the user on every request.**
  - `sub` must be a valid UUID; then the user must exist, have `status == active` and `email_verified_at` set. Otherwise 401, so a disabled staff member is cut off immediately.
  - Return a `Principal(user_id, permissions)`, with permissions taken from `effective_permissions(user_id, "admin")` (which drops expired assignments).
- **R-3 (AC2, AC3): `require_permission(key)` is a dependency factory.**
  - It gives 403 `permission_denied` when `permits(principal.permissions, key)` fails.
  - Wire it from the existing `permission(...)` declarations so one source drives both OpenAPI and enforcement, e.g. a helper that returns `dependencies=[...]` plus `openapi_extra`.
  - Add declarations to organizations (`admin:organizations:read|write`).
  - Add the route-enumeration test, with an explicit public allow-list (health, jwks, catalog, `/auth/*`).
  - Seed the leaf permissions in a migration with `is_admin=false`, except `admin:*`.
- **R-4: enable admin tokens.**
  - Remove `AudienceNotSupportedError` for `admin` and keep every other issue check.
  - Set `apps[admin].origins` through config or the API, not hard-coded.
  - Add non-credentialed CORS for Bearer from `apps[admin].origins` (owner note).
- **R-5: turn the guard sites into real checks.** Pass `actor` and `actor_permissions` from the router into the service, and raise `PermissionDeniedError`.
  - Require `admin:*` for:
    - anything on the `admin` app, including origins
    - creating or flipping `is_admin`/`<app>:*`
    - putting an is_admin permission into a role
    - granting or revoking a role that holds any is_admin permission
  - **No self-grant** of any admin-app role: 403 when `actor == user_id`.
  - Add a 409 for an `is_admin` grant onto a role that already has assignments (USR-02d member escalation).
- **R-6 (F2): put the last-super-admin guard on user disable (and any delete).**
  - Lock order: `lock_app("admin")`, then the user row, as in `bootstrap_super_admin`.
  - Also refuse self-disable.
  - Revoke the user's device sessions in the same transaction.
- **R-7: delete both flags.** Mount the users and rbac routers unconditionally behind the guard, and remove `_admin_apis_gate` and its tests. Before go-live, audit `admin:`-prefixed rows and admin-app assignments created while a flag was on (F3): a one-off query or migration check whose output goes in the impl log.
- **R-8: lifetime.** Admin tokens use `access_token_ttl_seconds` (900 s default, 3600 s max). Give `admin` a separate ceiling of 5–10 min. There is no revocation list: the per-request DB reload in R-2 is the revocation path (disable, role revoke, expiry). Log `jti` and `sub` on 401/403.

**Risks**

- If this releases before USR-04 (D16), the admin API is unusable in production, because no staff member can have a verified email.
- Pinning only the `admin` aud and forgetting `typ` would let other access tokens through if an app key ever collides. Missing the `iss` pin weakens key rotation.
- Reloading permissions on every request costs one indexed query per admin call, which is acceptable at this scale. Caching would bring back the revocation lag.
- If R-5 is incomplete, `admin:roles:manage` and `admin:roles:assign` become paths to super-admin.
- Rows created while the flags were on may already hold escalated grants.
- The pre-ADM-002 deploy remains unauthenticated until this ships.

**Open questions**

1. Should `admin:*` be held only via `admin:super-admin`? I recommend refusing `admin:*` on any other role.
2. Should the admin token TTL be separate and shorter than other apps'? Proposed: 300–600 s.
3. Should the token carry `permissions` for `aud=admin`, or stay with roles plus a DB lookup? I recommend the DB lookup.
4. Is 2FA required for staff (OQ3)? It is out of scope here but affects the release.
5. Where do `apps[admin].origins` come from in production: API, bootstrap CLI, or a migration?
6. Should `GET /api/v1/apps/admin/roles` keep returning 404 (D17)? Yes; confirm nothing changes there.
