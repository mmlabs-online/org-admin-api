# ADR-0008: Admin API authentication

Status: Accepted (ADM-002 plan, 2026-11-15)
Date: 2026-11-15

## Context
Spec §5.2/§5.3 require that only authenticated staff call the admin `/api/v1/*` endpoints and that
every admin endpoint declares the `admin:*` permission it needs. CLAUDE.md R3 puts every endpoint
in exactly one auth class: Bearer (admin), cookie (identity) or public. Staff get access tokens the
same way as any app user (`POST /api/v1/auth/token`, D7, ADR-0005), with `aud=admin`. Since D20 the
device cookie `__Host-mm_device` reaches every path, so admin auth must not read it.

## Decision
1. **Bearer only.** Admin routes read the access token from the `Authorization: Bearer` header
   and nothing else: no device cookie, no `access_token` query parameter, no other scheme. FastAPI
   `HTTPBearer(scheme_name="bearerAuth", bearerFormat="JWT", auto_error=False)`.
2. **Verifier** (`app/core/access_token.py`, pure, copyable into `mmlabs-auth`), in this order:
   header decodes; `typ == "at+jwt"`; `alg == "ES256"`; `kid` is one of the published keys
   (signing key and `JWT_PUBLIC_KEYS_EXTRA`); signature, `aud == "admin"`, `iss == JWT_ISSUER`,
   `exp`/`iat` with 30 s leeway, required claims `exp iat sub jti iss aud`; `email_verified is
   True`. Each failure has a fixed reason code, used in logs only. Same rule set as
   `docs/consumers.md`.
3. **Per-request reload, no permissions claim, no revocation list.** After verification the user
   is reloaded: it must exist, be `active` and have `email_verified_at` set, else 401. Permissions
   are the user's effective `admin` permissions read from the database on every request, so
   disable, role revoke and assignment expiry take effect on the next call (one indexed query).
4. **Error shapes** (R6 envelope). 401 `unauthorized`, message `Authentication required`,
   `details: null`, header `WWW-Authenticate: Bearer realm="admin"` plus `, error="invalid_token"`
   when a token was sent (RFC 6750). The response never says why. 403 `permission_denied`,
   `details: {"required": "<key>"}`. Authentication runs before request validation, so 401/403
   come before 404/422.
5. **One helper declares and enforces.** Each admin router has `dependencies=[Depends(current_admin)]`
   (defence in depth); each route has `**require_permission("admin:<resource>:<action>")`, which
   adds the enforcing dependency, the OpenAPI `x-permission` and the 401/403 responses. `admin:*`
   grants every admin permission (`permits()`, ADR-0003); no other app's wildcard does.
6. **Deny by default.** `tests/test_route_auth_classes.py` enumerates every route with all flags on
   and fails on any route that is neither guarded by exactly one `require_permission` nor in an
   explicit (method, path) public/cookie allow-list.
7. **Admin token TTL** = `ACCESS_TOKEN_TTL_SECONDS` (default 900 s). No separate ceiling: the
   per-request reload already revokes. Admin tokens need a registered admin origin (ADM-002c).

## Consequences
- An admin call costs one user lookup and one permission query.
- Tokens are never logged; denials log reason, `jti` and `user_id` when known, never email.
- Adding an admin route without `require_permission` fails the test suite.
- No admin token can be obtained in an environment until `set-admin-origins` (ADM-002c) sets
  the admin origins there: the admin API is closed to everyone until then (fail-closed, intended).

## Amendment — admin origins (ADM-002c, 2026-11-15)
Completes §7. The `admin` app's origins follow this policy, enforced in
`AppService.set_admin_origins`:

- **CLI-only write path:** `python -m app.cli set-admin-origins` (ops workflow). `register-app`
  keeps refusing `admin`; the admin API gets an `admin:*` guard on admin origin changes in ADM-002b.
- **Dedicated host:** D19 origins only, the apex `https://mmlabs.online` refused; production uses
  `https://admin.mmlabs.online`, reserved for the future admin UI (no DNS or tunnel route until it
  exists, never routed to another service). Loopback is refused in staging/production.
- **At most 2** distinct origins (rename or migration overlap), replace semantics.
- **Never shared:** an origin held by any other app (any status) is refused.
- **Origins only:** status, join policy, org mode, default role and the admin roles never change.
  The command **fails closed on drift**: a missing admin row, or one not `active`/`closed`/`none`
  without a default role, is refused and never repaired.
- **Break-glass `--clear`:** empties the set; no new admin token after at most 60 s, issued tokens
  last up to their TTL (revoke the role or disable the user to cut them sooner).
