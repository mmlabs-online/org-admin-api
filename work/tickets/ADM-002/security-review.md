## Attempt 1

## Security review: ADM-002, attempt 1 (appsec-engineer)

**Scope:** `git diff origin/main...HEAD`. I read `app/core/access_token.py`, `app/core/jwt_keys.py`, `app/modules/admin_auth/{deps,service}.py`, `app/modules/rbac/authz.py`, `app/modules/tokens/service.py`, the router guards, the seed migration, `tests/test_route_auth_classes.py`, `docs/deploy.md`, and the app-context and origin code it depends on. I ran only `tests/test_access_token.py` (28 passed), plus a throwaway Python check of how list-valued `aud` is handled. I did not run `make check` or the full suite.

### Findings

**🔴 Blockers:** none.

**🟡 Should fix (or file as follow-ups)**

1. **`aud` given as a list is accepted.** `app/core/access_token.py:44-52`. I checked this directly: a token with `aud=["stoktok","admin"]` passes `verify_access_token(..., audience="admin")`. It cannot be exploited today. `TokenService.issue` (`app/modules/tokens/service.py:88`) only ever signs a single string `aud`, and only holders of the signing key can mint tokens. But this module is meant to be copied into `mmlabs-auth` for every consumer app, and ADR-0005's model of one audience per token should be enforced by the verifier, not left to the issuer. **Fix:** add `"strict_aud": True` to `options` (PyJWT 2.15.1 supports it; I confirmed it raises `InvalidAudienceError` on a list, which maps to reason `audience`). Add `["admin"]` and `["stoktok","admin"]` to the parametrized cases in `tests/test_access_token.py:63`.

2. **The seed migration does not correct `is_admin` on rows that already exist.** `migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py` uses `ON CONFLICT (app_id, key) DO NOTHING`. If a local database already holds, say, `admin:users:write` with `is_admin=false`, it stays a non-staff permission. The config validator keeps the flags off in staging and production, so this affects local databases only, but the result is quietly wrong there. **Fix:** `ON CONFLICT (app_id, key) DO UPDATE SET is_admin = true`. The docstring already notes the matching limitation in `downgrade()`.

3. **ADM-002b must add the privilege-escalation guards before it removes the flags.** In `app/modules/rbac/service.py` the guards at `:480`, `:516`, `:650`, `:784` and `:835` are comments only: "ADM-002 guard: … requires admin:*". With this diff, a holder of the leaf permission `admin:roles:assign` or `admin:roles:manage` can grant themselves `admin:super-admin` or `admin:*`. Production is safe today because the users and RBAC routers are unmounted there (`app/main.py:91-95`) and the flags are refused in staging and production (`app/core/config.py:150-153`). Please write into ADM-002b's acceptance criteria that these guards land, with tests, in the same change that removes the flags.

**💭 Notes (no action needed)**

- **Verifier:** solid overall (`access_token.py:32-67`).
  - `typ`, `alg` and `kid` are checked before any key is used. `alg=none` and HS256 are rejected.
  - `algorithms=["ES256"]` is pinned, and the key is chosen from `public_keys`, which holds the signing key plus `JWT_PUBLIC_KEYS_EXTRA` (rotation).
  - Required claims are enforced, and so are `iss` and `exp` with 30 s leeway. A future `iat` is rejected (reason `claims`), `email_verified` must be exactly `true`, and a non-string `sub` is rejected.
  - One operational point: any key listed in `JWT_PUBLIC_KEYS_EXTRA` can now mint admin tokens. `docs/deploy.md` should say that the list holds only retired or next signing keys of this service.
- **`current_admin`** (`deps.py:116-146`) reads the Bearer token from the `Authorization` header only. Cookie-only and `?access_token=` requests both get 401 (tests at `test_admin_auth.py:76` and `:130`, and the parametrized sweep in `test_route_auth_classes.py:204`). It reloads the user on every request: missing, disabled or unverified users get 401, and permissions come from the database, not the token's `roles` claim, so a revoked role takes effect on the next request. Auth runs before body validation (`test_admin_auth.py:136`), so callers without permission cannot probe whether a resource exists via 404 or 422.
- **`require_permission`:** denies by default. `effective_permissions` drops any key that does not start with `admin:`, and `permits()` accepts only an exact match or `admin:*`. `stoktok:*` cannot grant admin access (`test_admin_auth.py:180`). The key format is validated when the route is defined (`deps.py:169`).
- **401/403 envelope:**
  - Every 401 carries the same fixed message, with no reason code and no token text.
  - `WWW-Authenticate: Bearer realm="admin"` is sent, plus `error="invalid_token"` when a token was presented.
  - 403 includes `details.required`, which names the needed permission. That is acceptable: the permission keys appear in OpenAPI anyway.
- **Logs:** they contain only the reason code, the `jti` (taken from verified claims only) and the user id. The token never appears, and `InvalidAccessTokenError` does not carry it (covered by tests).
- **Who can get an `aud=admin` token:** only an active user with a verified email, a live device session, and at least one effective admin-app role (`tokens/service.py:63-81`).
  - Users cannot join the admin app themselves (`membership/service.py:50`).
  - A role with no permissions still gets a token but gets 403 everywhere, which is acceptable.
  - In production the `admin` app has no origins, and `allowed_origins` drops loopback origins there. So no admin token can be minted, not even from curl with a forged `Origin`, until ADM-002c ships. That is fail-closed, as `docs/deploy.md` states.
  - Admin tokens are not tied to the device session: logging out leaves an issued token valid for up to 15 minutes (`ACCESS_TOKEN_TTL_SECONDS`, maximum 3600). This is documented and acceptable.
- **Production after deploy:** `/api/v1/organizations` moves from unauthenticated to Bearer plus permission, which closes the existing exposure. Users and RBAC stay unmounted. Health, JWKS, the role catalog and `/auth/*` are unchanged and asserted.
- **Route-classification test** (`tests/test_route_auth_classes.py`): robust.
  - It walks the whole dependency tree with both flags on and requires `current_admin` plus exactly one `RequirePermission` on every route that is not explicitly allow-listed.
  - It catches mounts and other non-`APIRoute` routes (`_other_paths <= DOCS`) and checks the allow-lists for stale entries.
  - It cross-checks `x-permission` in OpenAPI and adds runtime 401 sweeps without a token and with only a cookie.

### Follow-ups (out of scope)

- `strict_aud` (or an equivalent check) in the `mmlabs-auth` copy of the verifier and in `docs/consumers.md`.
- ADM-002b: the escalation guards from finding 3, before the flags are removed.
- Optional: a `jti` deny-list if staff need to revoke a token immediately, rather than waiting up to 15 minutes.

**Verdict:** PASS from a security standpoint, with no blockers. I recommend fixing 🟡1 (`strict_aud`) and 🟡2 in this ticket, since each is a one-line change, and recording 🟡3 as a gating requirement on ADM-002b.
