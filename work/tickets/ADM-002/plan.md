Status: READY

# Plan — ADM-002: Lock admin endpoints (aud=admin + require_permission)

This plan covers **slice 1 of 3** (see `## Split proposal`): Bearer verification, `current_admin`,
`require_permission` on every admin route, admin token issuance, the seeded admin permissions,
the route-enumeration test and the OpenAPI bearer scheme. The two flags stay, so the users and
RBAC routers stay unmounted in staging and production until ADM-002b makes the guards real.

## Spec summary

- spec §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`. Health endpoints stay public."
- spec §5.3: "Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap
  super-admin (`admin:*`) exists." and "the role catalog `GET /api/v1/apps/{key}/roles` is public (D17)".
- CLAUDE.md R3 class (1): "admin endpoints under `/api/v1/*`: access token with `aud=admin` + a
  declared `require_permission("admin:…")`"; class (3) public: health, `/.well-known/jwks.json`, the
  role catalog, and `/api/v1/auth` signup, login, verify-email, password forgot/reset.
- identity-architecture.md §4: "Admin: `/api/v1/users`, `/api/v1/apps`, roles, assignments … Bearer,
  `aud=admin` + `admin:*`". Token (D7): "JWT, ES256 (P-256, ADR-0005), ~15 min, header `kid` +
  `typ: at+jwt`. Claims `iss` … `aud=<app key>`, `sub`, `email`, `email_verified`, `roles` … `iat`,
  `exp`, `jti`."
- D9: "Default join_policy `open`, except `admin` = `closed`."
- D16 / §10 U1: release ADM-002 with or after USR-04. USR-04 is DONE and deployed, so this no longer
  blocks.
- Ticket owner note (USR-03f N-d): "admin Bearer auth must never accept or fall back to
  `__Host-mm_device`".

## Acceptance criteria → verification

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | No token, invalid/expired token, or token with another `aud` → 401 in the error envelope (`code=unauthorized`, `details=null`, `WWW-Authenticate: Bearer realm="admin"`; with `error="invalid_token"` when a token was sent). Wrong `iss`, unknown or missing `kid`, `alg=none`, HS256, `typ`≠`at+jwt`, missing required claim, `email_verified`≠true, unknown/non-UUID `sub`, disabled user and unverified user also → 401. | `tests/test_admin_auth.py::test_no_token_401`, `::test_rejected_tokens_401[...]` (parametrized), `::test_disabled_user_401`, `::test_unverified_user_401`, `::test_unknown_sub_401`; unit tests in `tests/test_access_token.py` |
| 2 | Bearer only: a valid `__Host-mm_device` cookie, an `access_token` query parameter or a non-Bearer `Authorization` scheme never authenticates an admin route → 401. | `tests/test_admin_auth.py::test_cookie_only_401`, `::test_query_token_401`, `::test_basic_scheme_401`; `tests/test_route_auth_classes.py::test_every_bearer_route_401_with_cookie_only` |
| 3 | Missing permission → 403 `permission_denied`, `details={"required": "<key>"}`; `admin:*` grants everything; permissions come from the DB per request (revoke, expiry or disable takes effect on the next request). | `tests/test_admin_auth.py::test_missing_permission_403`, `::test_leaf_permission_allows_only_its_routes`, `::test_super_admin_wildcard_allows_all`, `::test_role_revoked_after_issue_403`, `::test_expired_assignment_403`, `::test_disabled_after_issue_401`, `::test_other_app_wildcard_does_not_grant_admin` |
| 4 | Every existing admin endpoint declares and enforces exactly one permission through one helper; a test enumerates all routes (both flags on) and fails on any route that is neither in the explicit public/cookie allow-list (matched per method+path) nor guarded. | `tests/test_route_auth_classes.py::test_every_route_is_classified`, `::test_bearer_route_permission_map`, `::test_allow_lists_have_no_stale_entries`, `::test_public_and_cookie_routes_have_no_bearer_guard` |
| 5 | `/healthz`, `/readyz`, `/.well-known/jwks.json`, `GET /api/v1/apps/{key}/roles` and the public and cookie `/api/v1/auth/*` endpoints stay reachable without a Bearer token (they answer with their own status, never Bearer 401). | `tests/test_route_auth_classes.py::test_public_routes_reachable_without_token`, `::test_cookie_routes_not_bearer_guarded`; the existing auth, health, jwks and catalog suites stay green |
| 6 | Staff can get an `aud=admin` token from `POST /api/v1/auth/token` (verified, active, holding an admin-app role); a verified user without an admin role gets 403 `not_member` with `join_available=false`; an unverified one gets 403 `email_unverified`. The issued token is accepted by the admin routes. | `tests/test_auth_token.py::test_token_admin_audience_issued`, `::test_token_admin_not_member`, `::test_token_admin_unverified`; `tests/test_admin_auth.py::test_issued_admin_token_end_to_end` |
| 7 | The admin leaf permissions are seeded by a migration with a working `downgrade()`; `alembic check` reports no drift. | `tests/test_rbac_schema.py::test_admin_leaf_permissions_seeded`; the session fixture runs `downgrade base` then `upgrade head` (`tests/conftest.py::_migrate_db`); CI `alembic upgrade head && alembic check` |
| 8 | OpenAPI shows the `bearerAuth` scheme (http/bearer/JWT), a `security` requirement, `x-permission`, and 401/403 responses on every Bearer operation, and on no public or cookie operation. | `tests/test_route_auth_classes.py::test_openapi_bearer_scheme` |
| 9 | Production safety: the users/RBAC routers still need their flags, and the flags are still refused in staging/production. | Existing `tests/test_config.py` and `tests/test_users_admin.py` flag-gate tests stay green, unchanged |
| 10 | `make check` is green. | `make check` |

## Files to create / modify

| File | Change | Required because |
|---|---|---|
| `app/core/access_token.py` (new) | Pure verifier `verify_access_token(token, key_set, *, audience, issuer, leeway=30) -> dict[str, Any]`, raising `InvalidAccessTokenError` (a plain `Exception` carrying a fixed reason code, not an `AppError`). No ORM, no FastAPI. | AC1; must stay copyable into `mmlabs-auth` next to `permits()` (D4, D21) |
| `app/core/jwt_keys.py` | `KeySet` gains `public_keys: Mapping[str, ec.EllipticCurvePublicKey]` (signing kid + extra kids), filled in `build_key_set`. `repr=False`. | The verifier needs a public key per `kid`, rotation keys included (ADR-0005) |
| `app/core/errors.py` | Add `UnauthorizedError(AppError)`: 401, code `unauthorized`. | R6; no 401 `AppError` exists yet |
| `app/core/permissions.py` | **Delete**. Its only content, `permission()`, is replaced by `require_permission`. | One source for declare and enforce |
| `app/modules/admin_auth/__init__.py` (new) | Empty. | New module |
| `app/modules/admin_auth/service.py` (new) | `AdminPrincipal` dataclass (`user_id`, `permissions: frozenset[str]`, `token_id`) and `AdminAuthService(session).principal(claims)`: reloads the user (exists, `active`, `email_verified_at` set), else `UnauthorizedError`; permissions from `AuthzService.effective_permissions(user_id, "admin")`. | R1: the dependency calls a service and never touches the ORM; AC1 and AC3 |
| `app/modules/admin_auth/deps.py` (new) | `bearer_scheme = HTTPBearer(scheme_name="bearerAuth", bearerFormat="JWT", auto_error=False)`; `current_admin` dependency; `require_permission(key) -> dict[str, Any]` (route kwargs); `RequirePermission` callable class; `AdminDep` alias. | AC1–AC4, AC8 |
| `app/modules/organizations/router.py` | `APIRouter(..., dependencies=[Depends(current_admin)])`; each route gets `**require_permission("admin:organizations:read" \| "…:write")`. | AC4; closes the open orgs CRUD in production (security consult F1) |
| `app/modules/users/router.py` | Router-level `Depends(current_admin)`; `openapi_extra=permission(k)` → `**require_permission(k)`; docstring "Unauthenticated until ADM-002" → "Bearer; still flag-gated until ADM-002b". | AC4 |
| `app/modules/rbac/router.py` | Same as users (13 routes). | AC4 |
| `app/modules/rbac/assignments_router.py` | Same as users (3 routes). | AC4 |
| `app/modules/rbac/catalog_router.py` | Docstring only: say it is public by design and must never get `current_admin`. | Prevents a future "fix" of the allow-listed route |
| `app/main.py` | Update the flag comments ("unauthenticated until ADM-002" → "guards still pending, ADM-002b") and the `/docs` comment. No new middleware. | Accurate comments; no behaviour change |
| `app/core/config.py` | Flag comments only: "Removed by ADM-002" → "Removed by ADM-002b", and the validator message "before ADM-002" → "before ADM-002b". | Flags and gate stay (split) |
| `app/modules/tokens/service.py` | Remove the `app.key == ADMIN_APP_KEY` refusal. All other checks stay as they are. | AC6; the consults (F3, finding 1) note that otherwise nobody can get a token |
| `app/modules/tokens/errors.py` | Remove `AudienceNotSupportedError` (now unused). | No dead code |
| `migrations/versions/20261115_<rev>_seed_admin_permissions.py` (new) | Seeds 8 admin leaf permissions; downgrade deletes them. Data only. | AC7, R2 |
| `tests/admin_auth_factories.py` (new) | `make_staff(session, *perm_keys, verified=True, status="active", expires_at=None) -> User`: admin-app test role with those (seeded) permissions, plus an app-scope assignment. `mint(user_or_sub, **claim_overrides) -> str`: signs with `build_key_set(TEST_JWT_SIGNING_KEY, [])` (same key as the app). `mint_raw(headers, claims, key, algorithm) -> str`: for the alg, typ and kid cases. `bearer(token) -> dict`. | Shared by the new tests |
| `tests/conftest.py` | `make_client` / `make_strict_app` / `make_strict_client` take `admin_permissions: Sequence[str] \| None = None`. When set, they override `current_admin` with an `AdminPrincipal(uuid4(), frozenset(perms), "test")`. New fixture `admin_client` = `make_client(admin_permissions=["admin:*"])`. | Existing admin API tests keep testing business rules without minting tokens; real verification is covered in `test_admin_auth.py` |
| `tests/test_access_token.py` (new) | Verifier unit tests. | AC1 |
| `tests/test_admin_auth.py` (new) | API-level auth tests. | AC1–AC3, AC6 |
| `tests/test_route_auth_classes.py` (new) | Enumeration, allow-lists, OpenAPI and runtime 401 sweep. | AC2, AC4, AC5, AC8 |
| `tests/test_organizations.py` | `client` → `admin_client`. | Orgs now require auth |
| `tests/test_users_admin.py` | `users_client` fixture adds `admin_permissions=["admin:*"]`; `test_users_routes_declare_permissions` moves into `test_route_auth_classes.py::test_bearer_route_permission_map` (delete it here). | Routes now guarded |
| `tests/test_rbac_apps_api.py`, `tests/test_rbac_assignments_api.py`, `tests/test_rbac_permissions_api.py`, `tests/test_rbac_roles_api.py`, `tests/test_rbac_locking.py`, `tests/test_role_catalog_api.py`, `tests/test_auth_cors.py` | Every `make_client(admin_rbac_api_enabled=True)` / `make_strict_client(...)` call that hits an admin route adds `admin_permissions=["admin:*"]`. Delete the per-file `x-permission` map tests (`test_rbac_routes_declare_permissions`, `test_route_permission_map`, and the catalog one at `test_role_catalog_api.py:242` if it duplicates the map), which move into the central map. In `test_rbac_roles_api.py::test_put_super_admin_without_wildcard_409`, use the seeded `admin:apps:read` instead of `make_permission(...)`, because that would now violate the unique key. | Routes now guarded; seed added |
| `tests/test_auth_token.py` | Replace `test_token_admin_audience_refused` with the three admin cases of AC6. | AC6 |
| `tests/test_rbac_schema.py` | Add `test_admin_leaf_permissions_seeded`. | AC7 |
| `docs/adr/0008-admin-api-authentication.md` (new) | ADR for Bearer-only admin auth: verifier checks, permissions read from the DB on every request (no permissions claim, no revocation list), 401/403 shapes, one helper for declaring and enforcing, deny-by-default enumeration test, admin TTL = `ACCESS_TOKEN_TTL_SECONDS`. | Planner rule 7: decision outlives the ticket |
| `docs/adr/0005-access-token-signing.md` | Item 8: "`audience=admin` is refused until ADM-002" → "issued since ADM-002, verified per ADR-0008; admin origins are set only by ADM-002c, exclusive to `admin`". Item 9: drop `audience_not_supported` from the error order. | Docs match code |
| `docs/identity-architecture.md` | §4: replace the "Admin:" row with one row per admin router group (orgs, users, apps/permissions/roles, assignments), each with "Bearer `aud=admin` + `require_permission`", plus a short paragraph on the verifier and 401/403 (link ADR-0008). Mark `POST /auth/token` as also issuing `aud=admin`. | Ticket docs requirement |
| `docs/deploy.md` | New section "Admin access (ADM-002)": what changed in production (orgs CRUD now 401 without a token), and the runbook "deploy → sign up/verify (through a registered app) → grant-super-admin → set admin origins (ADM-002c) → log in with `X-MM-App: admin` → `POST /auth/token` `audience=admin` → call with `Authorization: Bearer`". State clearly that until ADM-002c ships no admin token can be obtained in production, so the admin API is closed to everyone (fail-closed, intended). One-time setup step 4: replace "the API has no auth until ADM-002" with the new state. | Ticket docs requirement |
| `docs/spec-v1.md` | **Not touched** (product-manager owns it). | Follow-up: §6.1 accepted-risk line becomes obsolete (see Assumptions) |

## Tests to write

| Test (file › name) | Covers |
|---|---|
| `test_access_token.py › test_valid_token_returns_claims` | Happy path, signing kid |
| `test_access_token.py › test_extra_key_token_verifies` | A token signed by a rotation key in `JWT_PUBLIC_KEYS_EXTRA` (its kid) verifies |
| `test_access_token.py › test_expired_rejected` / `test_expiry_within_leeway_accepted` | `exp` now−31 s is rejected; now−10 s is accepted (leeway 30) |
| `test_access_token.py › test_future_iat_rejected` | `iat` > now + leeway |
| `test_access_token.py › test_wrong_audience_rejected` | `aud="stoktok"`, and `aud=["stoktok"]` |
| `test_access_token.py › test_wrong_issuer_rejected` | `iss` mismatch |
| `test_access_token.py › test_unknown_kid_rejected` / `test_missing_kid_rejected` | kid not in `public_keys` / no kid |
| `test_access_token.py › test_alg_none_rejected` | Unsigned token, header `alg=none`, valid kid/typ |
| `test_access_token.py › test_hs256_rejected` | HS256 signed with the public PEM as the secret (algorithm confusion) |
| `test_access_token.py › test_wrong_typ_rejected[JWT, missing]` | `typ` ≠ `at+jwt` |
| `test_access_token.py › test_missing_claim_rejected[exp,iat,sub,jti,iss,aud]` | Required claims |
| `test_access_token.py › test_email_verified_must_be_true[False, missing, "true"]` | `email_verified is True` exactly |
| `test_access_token.py › test_wrong_signature_rejected` | Signed by another P-256 key, but claiming the signing kid |
| `test_access_token.py › test_garbage_rejected` | `"abc"`, `"a.b.c"`, empty string |
| `test_access_token.py › test_error_carries_no_token` | `str(exc)` and `repr(exc)` hold only the reason code, never the token (R11) |
| `test_admin_auth.py › test_no_token_401` | Envelope `unauthorized`, `details is None`, `WWW-Authenticate == 'Bearer realm="admin"'` |
| `test_admin_auth.py › test_basic_scheme_401` / `test_empty_bearer_401` / `test_query_token_401` | Only the `Authorization: Bearer` header counts |
| `test_admin_auth.py › test_rejected_tokens_401[expired, wrong_aud, wrong_iss, unknown_kid, alg_none, hs256, typ_jwt, not_a_jwt]` | Same body for every case; header contains `error="invalid_token"` |
| `test_admin_auth.py › test_unknown_sub_401` / `test_non_uuid_sub_401` | `sub` checks |
| `test_admin_auth.py › test_disabled_user_401` | `status=disabled` with a valid token |
| `test_admin_auth.py › test_unverified_user_401` | `email_verified_at` NULL in the DB, even though the claim says true |
| `test_admin_auth.py › test_cookie_only_401` | A valid device cookie (`make_device`) and no Authorization header → 401 |
| `test_admin_auth.py › test_missing_permission_403` | Verified staff with no admin role → 403 `permission_denied`, `details == {"required": "admin:organizations:read"}` |
| `test_admin_auth.py › test_leaf_permission_allows_only_its_routes` | `admin:organizations:read`: GET list 200; POST 403 with `required=admin:organizations:write` |
| `test_admin_auth.py › test_super_admin_wildcard_allows_all` | Seeded `admin:super-admin` role → GET orgs 200, POST orgs 201, GET users 200 and GET apps 200 (flags on) |
| `test_admin_auth.py › test_other_app_wildcard_does_not_grant_admin` | A `stoktok:*` role → 403 |
| `test_admin_auth.py › test_role_revoked_after_issue_403` | Token minted, assignment deleted → next request 403 |
| `test_admin_auth.py › test_expired_assignment_403` | `expires_at` in the past → 403 |
| `test_admin_auth.py › test_disabled_after_issue_401` | Token minted, user disabled → next request 401 |
| `test_admin_auth.py › test_auth_runs_before_validation` | No token + invalid body (POST orgs `{}`) → 401; no token + `GET /organizations/not-a-uuid` → 401 (not 422) |
| `test_admin_auth.py › test_issued_admin_token_end_to_end` | `admin.origins=[ORIGIN]`; super-admin logs in with `X-MM-App: admin`, then `POST /auth/token` `audience=admin`, then `GET /api/v1/organizations` with that token → 200 |
| `test_admin_auth.py › test_denials_logged_without_secrets` | caplog: a 401/403 line holds a reason, `jti`/`user_id` when known, and never the token or email |
| `test_route_auth_classes.py › test_every_route_is_classified` | `iter_route_contexts(app.routes)` with both flags on: every (method, path) is in `PUBLIC`, `COOKIE`, `DOCS` or guarded; any other route fails |
| `test_route_auth_classes.py › test_bearer_route_permission_map` | Exact map of the 24 guarded operations to keys (API contract below): exactly one `RequirePermission` in the dependant tree, its key equals the OpenAPI `x-permission`, and `current_admin` is in the tree |
| `test_route_auth_classes.py › test_allow_lists_have_no_stale_entries` | Every allow-list entry exists as a route |
| `test_route_auth_classes.py › test_public_and_cookie_routes_have_no_bearer_guard` | No `current_admin` / `RequirePermission` in their dependant trees |
| `test_route_auth_classes.py › test_openapi_bearer_scheme` | `components.securitySchemes.bearerAuth == {type: http, scheme: bearer, bearerFormat: JWT}`; each guarded op has `security: [{"bearerAuth": []}]`, `401` and `403`; no public/cookie op has `bearerAuth` |
| `test_route_auth_classes.py › test_every_bearer_route_401_without_token` | Parametrized over the 24 operations. Path params are filled (`uuid4()`, `key="stoktok"`); body `{}` → 401 + `WWW-Authenticate` Bearer |
| `test_route_auth_classes.py › test_every_bearer_route_401_with_cookie_only` | Same sweep with a valid device cookie → 401 |
| `test_route_auth_classes.py › test_public_routes_reachable_without_token` | `/healthz` 200, `/readyz` 200, jwks 200, catalog 200 for a registered app. `POST /auth/login`, `/signup`, `/verify-email`, `/password/forgot`, `/password/reset` answer without Bearer 401: status ≠ 401, or code ≠ `unauthorized` |
| `test_route_auth_classes.py › test_cookie_routes_not_bearer_guarded` | Each cookie route without a cookie returns its own code (`app_header_required` / `session_invalid`), never `unauthorized` with a Bearer challenge; a valid admin Bearer token does not stand in for the cookie |
| `test_auth_token.py › test_token_admin_audience_issued` | Super-admin (verified) gets 200 with `audience="admin"`; claims verified with `verify_access_token`; `roles == ["admin:super-admin"]` |
| `test_auth_token.py › test_token_admin_not_member` | Verified user, no admin role → 403 `not_member`, `join_available: false` |
| `test_auth_token.py › test_token_admin_unverified` | Unverified super-admin → 403 `email_unverified` |
| `test_rbac_schema.py › test_admin_leaf_permissions_seeded` | The 8 keys exist on app `admin` with `is_admin=true`; `admin:super-admin` still holds only `admin:*` |

## Approach

1. **Keys.** Add `public_keys` to `KeySet`, built from the signing key and every extra key
   (`{kid: public_key}`).
2. **Verifier** (`app/core/access_token.py`, pure), checked in this order:
   1. `jwt.get_unverified_header`, raising on decode errors.
   2. `typ == "at+jwt"`.
   3. `alg == "ES256"`.
   4. `kid` is a str and is in `key_set.public_keys`.
   5. `jwt.decode(token, key, algorithms=["ES256"], audience=audience, issuer=issuer, leeway=leeway, options={"require": ["exp","iat","sub","jti","iss","aud"]})`.
   6. `claims["email_verified"] is True`.

   Every failure raises `InvalidAccessTokenError(reason)` with a fixed reason (`malformed`, `typ`,
   `alg`, `kid`, `signature`, `expired`, `audience`, `issuer`, `claims`, `email_verified`). The
   reason is used only for logging, never in the response. This mirrors `tests/jwt_verifier.py` and
   `docs/consumers.md`, so the server and consumers share one rule set.
3. **Errors.** Add `UnauthorizedError` (401, `unauthorized`). The dependency builds it with
   message `"Authentication required"`, `details=None`, and the header
   `WWW-Authenticate: Bearer realm="admin"`, adding `, error="invalid_token"` when a token was sent
   (RFC 6750).
4. **`current_admin`** (`admin_auth/deps.py`):
   1. Read `HTTPAuthorizationCredentials | None` from `Security(bearer_scheme)`. That is the only
      input, so there is no cookie or query fallback.
   2. Missing → 401.
   3. `verify_access_token(creds.credentials, request.app.state.key_set, audience=ADMIN_APP_KEY, issuer=settings.jwt_issuer)`.
   4. Parse `sub` as a UUID.
   5. `await AdminAuthService(session).principal(user_id, jti)` reloads the user (`UserRepository.get`;
      `status == active` and `email_verified_at is not None`, else 401) and returns
      `AdminPrincipal(user_id, frozenset(await AuthzService.effective_permissions(user_id, "admin")), jti)`.
   6. On a denial, log `admin_auth_denied reason=<code> jti=<jti|-> user_id=<id|->` at INFO. Never
      log the token or email (R11).
5. **`require_permission(key)`**:
   - Validate `key` at import time against `^admin:([a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}|\*)$`
     (`ValueError` otherwise).
   - Return route kwargs `{"dependencies": [Depends(RequirePermission(key))], "openapi_extra": {"x-permission": key}, "responses": {401: _ERR, 403: _ERR}}`.
   - `RequirePermission.__call__(principal: AdminDep)` raises
     `PermissionDeniedError("Missing permission", details={"required": key})` unless
     `permits(principal.permissions, key)`, and logs `admin_permission_denied user_id jti required`.
   - The router-level `Depends(current_admin)` is defence in depth: a route that forgot the helper
     still needs a valid token. FastAPI caches the dependency per request, so it runs once.
6. **Routers.** Apply the helper with the permission map in `## API contract`. Change no handler
   bodies and no service signatures (actor plumbing is ADM-002b). R1: the routers only add
   dependencies, and the service does the DB reload.
7. **Tokens.** Remove the admin refusal in `TokenService.issue`. The admin app is `closed`, has no
   default role and has `origins: []` in production. Today only the CLI (`grant-super-admin`)
   gives admin roles, and only ADM-002c will set admin origins. So in production nobody can get an
   admin token until ADM-002c ships, which keeps the system fail-closed. The TTL stays
   `ACCESS_TOKEN_TTL_SECONDS` (900 s); see Assumptions A3.
8. **Migration** (data only). See `## Data model / migration`.
9. **OpenAPI.** The scheme comes from `HTTPBearer(scheme_name="bearerAuth", bearerFormat="JWT")`
   through `Security(...)`. No custom `openapi()` override.
10. **Tests.**
    - The existing admin-route suites switch to `admin_permissions=["admin:*"]`, which overrides
      `current_admin`. Real verification is exercised in `test_admin_auth.py`,
      `test_route_auth_classes.py` and `test_auth_token.py`, with no override.
    - Enumerate routes through `fastapi.routing.iter_route_contexts(app.routes)`. FastAPI 0.162
      includes routers lazily (`_IncludedRouter`), so plain `app.routes` does not list them. Walk
      `ctx.route.dependant` (recursively, `.dependencies[*].call`) to find `current_admin` and
      `RequirePermission`. If `RouteContext` does not expose the dependant, fall back to
      `ctx.original_route` and record that in the impl log.
    - Allow-lists (method + path, exact):
      - `PUBLIC` = GET `/healthz`, `/readyz`, `/.well-known/jwks.json`, `/api/v1/apps/{key}/roles`;
        POST `/api/v1/auth/signup`, `/login`, `/verify-email`, `/password/forgot`, `/password/reset`.
      - `COOKIE` = POST `/api/v1/auth/logout`, `/token`, `/apps/{key}/join`, `/verify-email/resend`;
        GET `/api/v1/auth/accounts`.
      - `DOCS` = `/openapi.json`, `/docs`, `/docs/oauth2-redirect` (Starlette routes).
11. **Docs.** ADR-0008, ADR-0005 items 8–9, identity-architecture §4, and the deploy.md
    "Admin access" section (see file list).
12. Run `make check`.

Rules:
- R1 holds: routers only declare dependencies, and the reload lives in `AdminAuthService`.
- R2: one data migration with a downgrade.
- R3: this ticket implements class 1.
- R4: no mutation is added, so there is no audit entry (ADM-004).
- R5: no new config.
- R6: 401 and 403 use the envelope.
- R7: slices 2 and 3 are listed, not implemented.
- R8: every guarded route gets 401/403 tests through the sweep.
- R11: tokens, emails and reasons never appear in responses, and tokens and emails never appear in
  logs.

## Specialist input used

- **api-platform-engineer:**
  - Adopted: the endpoint inventory and permission keys, one helper that both declares and enforces,
    a router-level `current_admin` as defence in depth, the `bearerAuth` scheme, 401/403 in
    `responses`, a generic 401 with no details, 403 `details.required`, the enumeration plus runtime
    401 sweep, and the `admin_token`/`make_token` fixtures (as `make_staff`/`mint`/`mint_raw`).
  - Adopted: `admin:apps:read|write` instead of `admin:apps:*`, because `permits()` has no resource
    wildcard.
  - Deferred to ADM-002b: rec. 7 (remove the flags) and the guard work.
  - Deferred to follow-ups: rec. 10 (CORS) and hiding `/docs`.
  - Changed: `is_admin=true` for the seeds. Identity suggested false; see Data model.
- **identity-access-engineer:**
  - Adopted: R-1 (pure verifier: `typ`, `kid`, ES256, `iss`, `aud`, `exp`, required claims, leeway
    ≤ 30 s, no cookies, cookie-only test), R-2 (reload active and verified user per request,
    permissions from the DB), R-3, and R-4 (allow admin tokens).
  - R-4 origins: moved to ADM-002c. There is no safe existing path, because the CLI refuses
    `admin` and the API is flag-gated.
  - Deferred to ADM-002b: R-5, R-6 and R-7.
  - R-8: kept 900 s (A3). The finding that `KeySet` has no public-key map is handled by step 1.
- **security-architect:**
  - Adopted: rec. 1 (route-object matching, not path prefix; enumeration with both flags on),
    rec. 2 (orgs permissions, the main reason to ship this slice), rec. 3, and rec. 6 (the
    `NotMemberError` path for admin, seeds, runbook order).
  - Deferred to ADM-002b: rec. 4, 5 and 8, with the flags kept until then. This keeps the
    architect's production-safety property: the routers never mount in production before the
    guards are real.
  - Deferred to a follow-up: rec. 7 (CORS), since no admin UI exists yet. Risk "public `/docs`" →
    follow-up.

## Assumptions

Owner decisions defaulted by the orchestrator. They are not blockers, and the owner can override
each one.

- **A1** Permissions are loaded from the DB on every request. They are not a token claim. This
  costs one indexed query per admin call.
- **A2** `admin:*` is restricted to `admin:super-admin`. That is enforced in ADM-002b, together with
  the guards. In this slice no new path can grant `admin:*` in production, because the RBAC router
  stays unmounted there.
- **A3** The admin token TTL stays at `ACCESS_TOKEN_TTL_SECONDS` (default 900 s, maximum 3600 s).
  There is no separate admin ceiling, because the per-request reload already revokes on disable,
  role revoke and expiry. A shorter ceiling, `ADMIN_ACCESS_TOKEN_TTL_SECONDS`, is a one-line
  follow-up if the owner wants it.
- **A4** Admin origins in production are set by ADM-002c, a CLI run through the ops workflow. Until
  then nobody can get an admin token in production, so the whole admin API is closed. That is
  intended.
- **A5** Nothing else calls `/api/v1/organizations` in production today. The consults found no
  documented consumer. Locking it is a breaking change that is accepted because the endpoint was
  never authenticated. If a consumer does exist, the owner must say so before release.
- **A6** Join requests (named in the ticket scope) do not exist yet (USR-03g). They will declare
  `admin:*`-style permissions when they land, and the enumeration test forces that.
- **A7** `/docs` and `/openapi.json` stay public. In production they now list only the orgs routes
  plus the auth routes, with the bearer scheme. The spec §6.1 "revisit when ADM-002 mounts the
  admin routes" point therefore lands in ADM-002b, which actually mounts them.
- **A8** spec §6.1's accepted-risk line ("until ADM-002 lands, anyone … can call the admin
  endpoints") becomes obsolete. The product-manager should update spec §6.1 after merge. It is not
  changed here.
- **A9** In this slice the `# ADM-002 guard` comment sites stay comments. Because the flags are
  refused in staging/production, that is local/test only.
- **Proposed CLAUDE.md R3 wording** (the owner applies it; this plan does not edit CLAUDE.md):
  replace "a declared `require_permission("admin:…")` (once ADM-002 lands; until then new endpoints
  note the permission they will need)" with "`require_permission("admin:<resource>:<action>")` from
  `app/modules/admin_auth/deps.py` on every route plus `current_admin` on the router; Bearer from the
  `Authorization` header only, never the device cookie (ADR-0008)". Also, R3 class (2) still says
  `mm_device`. It has been `__Host-mm_device` since D20, a pre-existing mismatch the owner may fix in
  the same edit.

## Blocking questions

None.

## Split proposal

The whole ticket would be roughly 1,500+ changed lines across auth, 8 guard sites, users, config
and two migrations, which is over the planner size guide. The split keeps production safe at every
step: the users/RBAC routers never mount in production before their guards are real.

| Slice | ID | Title | Scope | Production state after release |
|---|---|---|---|---|
| 1 (**this plan**) | ADM-002 | Lock admin endpoints: Bearer verifier, require_permission, admin tokens | Everything in this plan | Orgs CRUD → 401 without a token. Users/RBAC still unmounted. Admin tokens issuable in principle, but admin origins are empty |
| 2 | ADM-002b | Enforce admin RBAC guards and mount the users/RBAC APIs | (a) Make the 8 `# ADM-002 guard` sites real. The router passes `actor=principal.user_id` and `actor_permissions=principal.permissions` into the services (R1). Require `admin:*` for admin-app origins/roles/permissions, for creating or flipping `is_admin` or `<app>:*`, for putting an is_admin permission into a role, and for granting or revoking any admin-app role or any role holding an is_admin permission. (b) No self-grant or self-revoke of admin-app roles (403). (c) 409 for an is_admin grant onto a role that has assignments (USR-02d member escalation). (d) `admin:*` allowed only on `admin:super-admin` (409 elsewhere). (e) `granted_by` = actor. (f) `UserService.set_status` disable: `lock_app("admin")` then the user row; last-super-admin guard; refuse self-disable; revoke the user's device/account sessions in the same transaction. (g) `AppService.create/update` refuses origins held by another app, the admin app included (exclusivity, ADR-0005 item 8). (h) Remove `ADMIN_USERS_API_ENABLED`, `ADMIN_RBAC_API_ENABLED`, `_admin_apis_gate` and their tests; mount unconditionally. (i) Owner decision on `/docs` in production (spec §6.1). (j) Pre-release check: a read-only query that lists `admin:`-prefixed permissions/roles and admin-app assignments created while a flag was on (local/staging only, since production never had the flags). Output goes in the impl log. | Full staff API behind real guards |
| 3 | ADM-002c | Set admin app origins from the CLI | Add `python -m app.cli set-admin-origins --origin <o> [--origin …] [--dry-run]` through a new `AppService.set_admin_origins(origins, app_env)`. It locks the admin app, normalises origins (D19), refuses loopback in staging/production, refuses any origin held by another app, replaces the list and calls `mark_apps_changed`. The output is masked like the other CLI commands. `register-app` also refuses an origin held by `admin` (it already refuses overlap). Add a `deploy.md` runbook step and an ops-workflow input (a deployment-repo PR the owner makes). Optional, only if an admin UI exists by then: non-credentialed CORS for admin paths from `apps[admin].origins` (owner note A1). | Staff can get an `aud=admin` token in production |

Order: ADM-002 → ADM-002c (small, can follow at once) → ADM-002b. ADM-002c does not depend on
002b. ADM-002b depends on ADM-002. Other follow-ups (not tickets yet): staff 2FA (OQ3), the admin
TTL ceiling (A3), and the spec §6.1 cleanup (A8).

## Data model / migration

Migration `seed_admin_permissions`, `down_revision = "4d4b5d67dc44"`. It is data only, with no DDL,
so `alembic check` is unaffected.

- **upgrade:**

  ```sql
  INSERT INTO permissions (id, app_id, key, description, is_admin)
  VALUES (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', :key, :desc, true), …
  ON CONFLICT (app_id, key) DO NOTHING
  ```

  The rows are given in the table below. Use `ON CONFLICT` because local and staging databases may
  already hold some of these keys, created by hand while the flags were on.

  `gen_random_uuid()` is built in on PG 13+, or set ids in Python with `uuid.uuid4()` and
  `op.execute(sa.text(...).bindparams(...))`. The implementer picks one; both are fine. Rows are
  not assigned to any role: `admin:super-admin` keeps only `admin:*`.

  | key | description | is_admin |
  |---|---|---|
  | `admin:organizations:read` | Read organizations | true |
  | `admin:organizations:write` | Create, update and delete organizations | true |
  | `admin:users:read` | Read users and their role assignments | true |
  | `admin:users:write` | Enable or disable users | true |
  | `admin:apps:read` | Read apps and their permissions | true |
  | `admin:apps:write` | Register, update and delete apps | true |
  | `admin:roles:manage` | Manage permissions and roles of apps | true |
  | `admin:roles:assign` | Grant and revoke role assignments | true |

  `is_admin=true`: all of them are staff powers. The admin app has no member role, so the flag
  only makes every ADM-002b guard that keys on `is_admin` catch them, which is the conservative
  choice.

- **downgrade:**

  ```sql
  DELETE FROM permissions
  WHERE app_id = '…a1' AND key IN (<the 8 keys>)
  ```

  `role_permissions` rows cascade (`ondelete=CASCADE`). Known limitation: this also deletes
  same-key rows that existed before the upgrade (local/staging only). Note it in the migration
  docstring.

- **Verification:** the test session runs `downgrade base` then `upgrade head`, which exercises the
  downgrade every run. Add `test_admin_leaf_permissions_seeded`.

- **Code constant:** keep the key list in the migration only. Do not import app code into
  migrations. The enumeration test's permission map is the code-side source.

## API contract

No new endpoints. Existing endpoints get auth. Every guarded row:
- Request: `Authorization: Bearer <aud=admin token>`.
- Errors added: **401** `unauthorized` (`WWW-Authenticate: Bearer realm="admin"[, error="invalid_token"]`, `details=null`)
  and **403** `permission_denied` (`details={"required": "<key>"}`).
- Auth runs before validation, so 401/403 come before 404/422.
- Existing request and response schemas and success codes are unchanged.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | /api/v1/organizations | Bearer; `limit`, `offset` | `Page[OrganizationRead]` | 200, 401, 403, 422 | `admin:organizations:read` |
| POST | /api/v1/organizations | Bearer; `OrganizationCreate` | `OrganizationRead` | 201, 401, 403, 409, 422 | `admin:organizations:write` |
| GET | /api/v1/organizations/{org_id} | Bearer | `OrganizationRead` | 200, 401, 403, 404, 422 | `admin:organizations:read` |
| PATCH | /api/v1/organizations/{org_id} | Bearer; `OrganizationUpdate` | `OrganizationRead` | 200, 401, 403, 404, 409, 422 | `admin:organizations:write` |
| DELETE | /api/v1/organizations/{org_id} | Bearer | — | 204, 401, 403, 404 | `admin:organizations:write` |
| GET | /api/v1/users | Bearer; `email`, `status`, page | `Page[UserRead]` | 200, 401, 403, 422 | `admin:users:read` (flag-gated) |
| GET | /api/v1/users/{user_id} | Bearer | `UserRead` | 200, 401, 403, 404 | `admin:users:read` (flag-gated) |
| PATCH | /api/v1/users/{user_id} | Bearer; `UserStatusUpdate` | `UserRead` | 200, 401, 403, 404, 422 | `admin:users:write` (flag-gated) |
| GET | /api/v1/users/{user_id}/role-assignments | Bearer; `app`, page | `Page[AssignmentRead]` | 200, 401, 403, 404 | `admin:users:read` (flag-gated) |
| POST | /api/v1/users/{user_id}/role-assignments | Bearer; `AssignmentCreate` | `AssignmentRead` | 201, 401, 403, 404, 409, 422 | `admin:roles:assign` (flag-gated) |
| DELETE | /api/v1/users/{user_id}/role-assignments/{assignment_id} | Bearer | — | 204, 401, 403, 404, 409 | `admin:roles:assign` (flag-gated) |
| GET | /api/v1/apps | Bearer; page | `Page[AppRead]` | 200, 401, 403 | `admin:apps:read` (flag-gated) |
| POST | /api/v1/apps | Bearer; `AppCreate` | `AppRead` | 201, 401, 403, 409, 422 | `admin:apps:write` (flag-gated) |
| GET | /api/v1/apps/{key} | Bearer | `AppRead` | 200, 401, 403, 404 | `admin:apps:read` (flag-gated) |
| PATCH | /api/v1/apps/{key} | Bearer; `AppUpdate` | `AppRead` | 200, 401, 403, 404, 409, 422 | `admin:apps:write` (flag-gated) |
| DELETE | /api/v1/apps/{key} | Bearer | — | 204, 401, 403, 404, 409 | `admin:apps:write` (flag-gated) |
| GET | /api/v1/apps/{key}/permissions | Bearer; page | `Page[PermissionRead]` | 200, 401, 403, 404 | `admin:apps:read` (flag-gated) |
| POST | /api/v1/apps/{key}/permissions | Bearer; `PermissionCreate` | `PermissionRead` | 201, 401, 403, 404, 409, 422 | `admin:roles:manage` (flag-gated) |
| PATCH | /api/v1/apps/{key}/permissions/{permission_id} | Bearer; `PermissionUpdate` | `PermissionRead` | 200, 401, 403, 404, 409, 422 | `admin:roles:manage` (flag-gated) |
| DELETE | /api/v1/apps/{key}/permissions/{permission_id} | Bearer | — | 204, 401, 403, 404, 409 | `admin:roles:manage` (flag-gated) |
| POST | /api/v1/apps/{key}/roles | Bearer; `RoleCreate` | `RoleRead` | 201, 401, 403, 404, 409, 422 | `admin:roles:manage` (flag-gated) |
| PATCH | /api/v1/apps/{key}/roles/{role_id} | Bearer; `RoleUpdate` | `RoleRead` | 200, 401, 403, 404, 409, 422 | `admin:roles:manage` (flag-gated) |
| DELETE | /api/v1/apps/{key}/roles/{role_id} | Bearer | — | 204, 401, 403, 404, 409 | `admin:roles:manage` (flag-gated) |
| PUT | /api/v1/apps/{key}/roles/{role_id}/permissions | Bearer; `RolePermissionsReplace` | `RoleRead` | 200, 401, 403, 404, 409, 422 | `admin:roles:manage` (flag-gated) |
| POST | /api/v1/auth/token | unchanged (cookie class) | `TokenResponse` | as before; `aud=admin` is now issued, and 403 `audience_not_supported` is gone | — (cookie + Origin + `X-MM-App`) |
| GET | /api/v1/apps/{key}/roles | unchanged | `RoleCatalog` | unchanged | public (D17), allow-listed |
