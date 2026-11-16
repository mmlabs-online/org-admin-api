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
6. **Deny by default.** `tests/test_route_auth_classes.py` enumerates every route (all routers are
   mounted since ADM-002b) and fails on any route that is neither guarded by exactly one `require_permission` nor in an
   explicit (method, path) public/cookie allow-list, or the self-scoped allow-list (USR-11 amendment).
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
- **Dedicated host:** D19 origins only, the apex `https://mmlabs.online` refused; production originally
  reserved `admin.mmlabs.online` for the future admin UI (superseded, see the note below; never
  routed to another service). Loopback is refused in staging/production.
  Owner, 2026-11-16 (ADM-014): production uses `https://admin-portal.mmlabs.online` (admin UI
  `admin-pan`, ADR-0011). `admin.mmlabs.online` is not used and must have no DNS record or tunnel
  route (see `docs/deploy.md` Host policy).
- **At most 2** distinct origins (rename or migration overlap), replace semantics.
- **Never shared:** an origin held by any other app (any status) is refused.
- **Origins only:** status, join policy, org mode, default role and the admin roles never change.
  The command **fails closed on drift** (except `--clear`, see the ADM-002d amendment): a missing
  admin row, or one not `active`/`closed`/`none` without a default role, is refused and never
  repaired.
- **Break-glass `--clear`:** empties the set; no new admin token after at most 60 s, issued tokens
  last up to their TTL (revoke the role or disable the user to cut them sooner).

## Amendment — admin RBAC guards (ADM-002b, 2026-11-15)
The users and RBAC routers are mounted unconditionally; `ADMIN_USERS_API_ENABLED` and
`ADMIN_RBAC_API_ENABLED` are gone (a stale env key is ignored). The route permission alone no
longer decides privileged writes:

- **Actor model.** Every RBAC and user-status mutator takes a required `actor`
  (`app/modules/rbac/actor.py`): the request's `AdminPrincipal`, or an explicit system actor —
  `system:cli` (privileged: `register-app`, `set-admin-origins`, `grant-super-admin`) or
  `system:join` (not privileged). `granted_by` is the principal's user id (NULL for system actors).
- **`admin:*` required (403 `admin_wildcard_required`, `details: {"required": "admin:*"}`)** for:
  any change on the `admin` app (G1; a no-op PATCH needs nothing), including its origins (same
  validation as the CLI: no apex, at most 2, no loopback in staging/production; the CLI
  `set-admin-origins` stays the documented path); creating an
  `is_admin` or `<app>:*` permission or any admin-app permission; flipping `is_admin` in either
  direction; any admin-app permission or role write; a role PUT whose diff adds or removes an
  `is_admin` permission or touches an admin-app role (G2; an unchanged re-PUT needs nothing);
  granting (including renewing an expired row) or revoking a privileged role — an admin-app role
  or one holding an `is_admin` permission (G4); disabling or re-enabling a user holding any such
  assignment, expired rows included (G5).
- **Self-actions (403, also for super-admins):** `self_assignment_forbidden` for granting or
  revoking one's own admin-app roles; `self_status_change_forbidden` for changing one's own status.
  To step down, another super-admin revokes the role.
- **State refusals:** 409 `role_has_assignments` when an `is_admin` permission would reach members
  of an assigned role (PUT adding one, or a false→true flip; every actor, every app; workaround
  revoke → edit → re-grant); 422 `wildcard_super_admin_only` for `admin:*` on any admin role other
  than `admin:super-admin`; 409 `last_super_admin` (revoke or disable) when no other active,
  verified, unexpired app-scope super-admin would remain; 409 `conflict`/`origin_taken` when an
  app create or PATCH claims a non-loopback origin another app (any status, `admin` included)
  holds (G6).
- **Check order:** lock the app (ADR-0003 §10) → 404 → request-shape 422s → 403 guards → state
  409/422 → write. User status locks `admin` first, then the user row, so the last-super-admin
  guard is serialized with revokes; disabling revokes the user's account sessions on every device
  (device sessions stay; issued app tokens last until `exp`).
- **Accepted risks (v1):** effective permissions are read before the app lock, so a concurrent
  revoke of `admin:*` can let one in-flight request pass; the origin-uniqueness check runs under
  different app locks, so two concurrent writes could claim one origin (staff-only, follow-up for
  a database-level guard).
- **API docs:** `/docs` and `/openapi.json` are off in production unless `API_DOCS_ENABLED=true`;
  on elsewhere by default.

## Amendment — hardening (ADM-002d, 2026-11-15)

- **String-only `aud`.** `verify_access_token` decodes with PyJWT `strict_aud=True`: a list `aud`
  (even `["admin"]`) fails with reason `audience`. The consumers guide and `mmlabs-auth` require
  the same.
- **Admin-app permissions are always `is_admin`.** Create stores `true` when `is_admin` is
  omitted or null and refuses an explicit `false` (422 `admin_app_permission_must_be_admin`);
  PATCH `is_admin: false` gets the same 422. Migration `a7c3e5f19b42` backfills stored rows
  (no-op downgrade, no audit row).
- **`--clear` skips the drift check.** `set-admin-origins --clear` empties the origins of a
  drifted admin row: the break-glass must work exactly when something is wrong. It still locks
  the app, refuses a missing row, writes only `origins` and writes the audit row. Non-empty
  origins on a drifted row are still refused.
- **No admin context on mail-link routes.** Signup and password forgot
  refuse `X-MM-App: admin` with the same 403 `origin_not_allowed` as an unregistered app, before
  Turnstile, any write or any email. Login, token, logout, verify-email and reset are unchanged. Verify-email resend
  was narrowed by the USR-12 amendment (accepted for staff only).
- **Admin `org_mode` pinned.** PATCH `org_mode` other than `none` on the admin app → 409, like
  status, join policy and default role.

## Amendment — Self-scoped admin reads (USR-11, 2026-11-16) — pending owner confirmation

One explicit exception to §5 and §6. `GET /api/v1/me/permissions` is Bearer `aud=admin` through
`current_admin` (§1–§4 all apply: per-request reload, 401 for disabled or unverified users) and
declares no `admin:*` permission. A route qualifies only if all of these hold:

- GET; no path, query or body input; the subject is always the principal.
- It returns only the caller's own authorization state (`user_id`, app-scope admin role keys,
  `sorted(principal.permissions)` with `admin:*` unexpanded), no PII.
- No side effects (no audit row; R4 covers mutations only).
- `Cache-Control: no-store`.
- Listed by exact (method, path) in `SELF_SCOPED` in `tests/test_route_auth_classes.py`.

Adding a route needs a new amendment. Self-scoped mutations are out of scope. Tokens still carry
no permissions claim. After a full revoke the response is 200 with empty lists (the token stays
valid up to its TTL).

Accepted risks: roles and permissions are two reads, so a concurrent grant can make them disagree
for one response (advisory data, the next call corrects it); a token holder learns its own keys
(already learnable from 403 `details.required`); no rate limit (same as every Bearer route).
Rejected alternative: a seeded `admin:self:read` permission (needs a migration and every admin role
must hold it, else the UI breaks).

## Amendment — Admin verification resend (USR-12, 2026-11-16)

Supersedes the resend part of the ADM-002d bullet "No admin context on mail-link routes".
`POST /api/v1/auth/verify-email/resend` accepts `X-MM-App: admin` from a registered admin origin,
but only for a user with at least one effective (unexpired, app-scope) `admin` role: the same
predicate `/auth/token` uses for `aud=admin`. Any other user gets 403 `origin_not_allowed`, the
same body as an unregistered origin, checked before the already-verified 409. Signup and password
forgot keep refusing `admin`. Staff then verify through the link and call `/auth/token`.

**Reason ADM-002d gave (inferred; the ADR recorded only the rule).** The ADM-002d consults
proposed refusing signup and forgot, and added resend by analogy ("all three mail a link to the
admin origin"). The real threat is the unauthenticated routes: anyone can name any address, so
forgot would make the admin origin collect password-reset tokens for every user, and signup would
allow open account creation and admin-branded mail.

**Why it does not hold for resend.** Resend is cookie class: it needs a `__Host-mm_device` with an
active account, so the caller proved the password. It mails only that account's own address, runs
no Turnstile path and creates no account. The token only marks the email verified; admin access
still needs an `admin:*` role. Limits (1/min, 5/day per email HMAC shared across apps, per-IP, global
cap) are unchanged, so admin context adds no sending capacity and no new oracle.

**Staff-only rule.** Without it, an attacker could pre-register a future staff member's email,
sign in at admin-portal and send the victim a genuine admin-branded verification mail (security
consult F6). An attacker's account holds no admin role, so it is refused. Planner default; the
owner may revert it at PR review.

**Residual risks.** Verification links to admin-portal now exist (the admin origin must serve
`/verify-email`). Pre-hijack through other apps' origins is unchanged (follow-up: confirm with the
person before granting an admin role to an existing account).

Rejected alternative: allow any signed-in account (simpler, same outcome for staff, but re-opens
F6 for no staff benefit).
