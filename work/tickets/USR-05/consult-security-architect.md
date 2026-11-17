USR-05 security consult (security-architect, read-only)

Files read: work/tickets/USR-05/ticket.md, docs/adr/0001, docs/adr/0015, docs/identity-architecture.md §3–§5, docs/deploy.md (register-app semantics), app/modules/rbac/{service,models,actor}.py, app/modules/audit/models.py, app/core/{cors,rate_limit}.py, app/modules/admin_auth/route_classes.py, tests/test_route_auth_classes.py.

## Findings

F1. **This is a fourth auth class, and R3 only lists three.** R3 names Bearer, cookie and public. identity-architecture.md §4 lists `PUT /api/v1/apps/{key}/manifest` as "app credential". `tests/test_route_auth_classes.py` denies any route that is not classified (`test_every_route_is_classified`, `test_bearer_route_permission_map`), so the new route fails CI unless a new class is added. The class needs to go into the docs (CLAUDE.md R3 / ADR-0008 amendment) and into that test.

F2. **CORS already treats the route as server-to-server.** `/api/v1/apps/{key}/manifest` is outside the `/api/v1/auth/` prefix. `BearerRouteMatcher` only returns True for routes with `current_admin` in the dependency tree. So `DynamicCORSMiddleware` passes the route through with no `Access-Control-Allow-Origin` header, and a preflight falls through to a 405. Browsers cannot call it. Nothing needs adding, but a test should pin this.

F3. **The existing guards assume a privileged actor, which clashes with a non-privileged app credential.** `PermissionService.create` calls `require_super` (`admin:*`) for any `is_admin=true` permission and for any new non-reserved `scope=app` permission on `org_mode=required` apps (ADR-0015 §2). `update` needs `admin:*` for every `is_admin` change and for org→app. If the manifest reuses these services with a non-privileged actor, it cannot declare `is_admin` permissions, and on calechip it cannot declare app-scope permissions. If it reuses them with a privileged actor, a credential stolen from CI becomes `admin:*` for that app's catalog. The manifest needs its own explicit policy (R1 below), not a reuse of the staff guards.

F4. **The member-role rebuild undoes staff tightening.** `_build_for_app` sets `<app>:member` to every non-`is_admin` permission (on org apps, also `scope=app` only). That is D9, and deploy.md already accepts it for `register-app` ("drops permissions removed from it by hand"). For the manifest, an attacker, or simply every deploy, can re-grant a permission that staff deliberately removed from members. Without a rule this is a privilege escalation that needs no code change at all.

F5. **The audit actor model has no machine principal.** `audit_log.actor_kind` is CHECKed to `user|system|anonymous`. `Actor = AdminPrincipal | SystemActor`. The `SystemActor` labels are fixed strings, and `actor_label` is limited to 64 characters.

F6. **The rate limiter is in-process and per-IP** (`core/rate_limit.py`, a `Scope` Literal plus settings-driven `_LIMITS`). Adding a scope is cheap.

## Recommendations

**R1. Manifest write policy.** The credential is a non-privileged actor scoped to one app. Proposed policy (covers AC 2 and 3, R3, ADR-0015, D9):

| Manifest item | Rule |
|---|---|
| New permission, `is_admin` true or false | Create it. Creating with `is_admin=true` is fail-closed and needs no `admin:*` here (deliberately different from the staff path; document it). |
| New permission on an `org_mode=required` app | `scope` is required (422 if missing). Reserved keys are enforced (`reserved_scope` 422). A new non-reserved `scope=app` permission conflicts with ADR-0015 §2. See Open question 1. |
| Existing permission, `is_admin` true→false, or `scope` org→app on an org app | **Never applied.** Report it in the response as `drift` / `requires_staff` (or 409; the planner picks one, but be consistent). This is the main escalation path. |
| Existing permission, false→true or app→org (tightening) | Default: report, don't apply (the G3 / default-role guards in `update` would refuse anyway). Applying it is an owner call. |
| Description change | Apply it. |
| `<app>:*` | 422. Wildcard stays staff-only. |
| Permission missing from the manifest | Report as `removed`. Never delete it, never take it out of any role (AC 3). |
| Extra roles | Create if absent. Never delete or rename. Never touch `default_role_id`, `org_owner_role_id`, `join_policy`, `org_mode`, `origins` or `status`. A role's permissions may not include an `is_admin` permission when that role has assignments or is the default or owner role (otherwise its holders silently gain admin). Simplest v1: manifest roles may not hold `is_admin` permissions at all. |
| Member override | Must be a subset of non-`is_admin` permissions (and `scope=app` on org apps), otherwise 422. It must not target the org owner role, and it goes through `_build_for_app`, which keeps `member_is_org_owner` (409). |
| Member rebuild | Rebuild only when the push actually created a permission, or the override changed (same rule as `register-app`). A push with nothing new must not touch any role (idempotency, AC 3). Open question 2 covers re-widening. |

**R2. Namespace check.** Compare the exact first segment: `key.split(":", 1)[0] == credential.app_key` on top of `PERMISSION_KEY_RE` / `ROLE_KEY_RE`. Never use `startswith(app_key)`, because `stoktok` would then match `stoktok2:…`. Path `{key}` ≠ the credential's app → 403 (AC 1, AC 6). Never issue or accept a credential for the `admin` app (staff issuing one → 422/409; a push to `/apps/admin/manifest` → 403). A disabled app → 403 `app_disabled`.

**R3. Credential type: an opaque API key, not client credentials.** Client credentials means minting JWTs for machines. That moves toward an OAuth/OIDC provider (R10 non-goal), and ADR-0001 lists it as "later". Details:
- Format: `mmak_<key_id>_<secret>`. `key_id` is about 12 random base32 characters (public, the lookup key). `secret` is `secrets.token_urlsafe(32)` (256 bits). The `mmak_` prefix lets GitHub secret scanning and gitleaks find leaked keys.
- Storage: HMAC-SHA256(secret) with a new env pepper `APP_CREDENTIAL_HMAC_KEY` (R5; add it to `.env.example` and the production checks), or plain SHA-256. Not Argon2: the secret is high-entropy, and Argon2 on every request is a DoS amplifier. Compare with `hmac.compare_digest`.
- Table `app_credentials`:
  - Columns: `id`, `app_id` (FK, `ON DELETE CASCADE`), `key_id` (unique), `secret_hash`, `label`, `created_by` (`users`, NULL), `created_at`, `expires_at` (NULL), `last_used_at`, `revoked_at`.
  - Partial index on active rows.
  - At most 2 active credentials per app, enforced under the app lock, so rotation can overlap.
  - Ships with an R2 migration that has a working downgrade.
- Staff endpoints (Bearer, R3 class 1): `POST /api/v1/apps/{key}/credentials` (201; the secret is returned once with `Cache-Control: no-store`), `GET` (metadata only, never the hash), `DELETE /…/credentials/{id}` (204, sets `revoked_at`). See Open question 3 for the permission.
- Rotation is "issue new → update the CI secret → revoke old". No grace logic is needed. Revocation takes effect immediately because every request reads the database. A disabled or deleted app invalidates its credentials.

**R4. Transport.**
- The credential comes only in `Authorization: Bearer mmak_…`. Proxies, Cloudflare and APM tools already redact `Authorization`, which they don't do for a custom header.
- Never read it from the query string, body or cookie. Never read `mm_device` on this route.
- 401 `unauthorized` with `WWW-Authenticate: Bearer realm="app"` and no details, the same answer for: missing, malformed, unknown `key_id`, wrong secret, expired, revoked.
- An admin JWT on this route → 401. An `mmak_` key on any admin route → 401 (the JWT parse fails). Test both.
- Never echo or log the header value. Log at most the `key_id`.
- Auth runs as a dependency, so FastAPI resolves it before body validation. Test that a wrong credential plus an invalid body gives 401/403, not 422.

**R5. DoS and size limits.** Cap the request body (for example 256 KiB), the permission count (for example ≤ 500), the extra roles (≤ 50) and the description length. All of the work runs under `lock_app`, so an oversized manifest blocks staff writes to that app.

**R6. Rate limiting.** New scope `manifest`, per IP, applied before auth (for example 10/min), so failed lookups are bounded too. Optionally a second per-credential bucket. 429 uses the existing `RateLimitedError` with `Retry-After` and is not audited (S8).

**R7. Audit (R4, S8, ADR-0009).**
- Add `actor_kind = 'app'` with `actor_label = "app:<key>/<key_id>"` (≤ 64 characters). This needs an Alembic migration that rewrites `ck_audit_log_actor_kind_valid` and `actor_consistent` by hand (Alembic doesn't diff CHECKs), plus the `actor_kind` filter on `GET /audit-logs`. Fallback: a `SystemActor(f"app:{key}", privileged=False)` constructed per request under `actor_kind='system'`. That needs no migration but mixes machine and operator rows. I prefer `app`.
- Rows:
  - `app_credential.created` and `app_credential.revoked` (staff actor; `changes` = key_id, label, expires_at; never the secret or hash).
  - `app.manifest_applied`, one row per push that changes anything, in the same transaction. `changes` lists only keys: permissions added, descriptions changed, roles created, member added/removed, `removed` reported, `drift` refused.
  - An idempotent no-op writes nothing.
  - A credential valid for app A used on app B → `outcome=denied` row (403), committed alone.
  - A revoked, expired or wrong-secret use of a **known** `key_id` → `app_credential.auth_failed`, anonymous actor, `outcome=failure`. Unknown ids → nothing (noise).
- Update `last_used_at` on success.

**R8. Route class wiring.**
- Add an `APP_CREDENTIAL` set to `test_route_auth_classes.py` with a marker dependency (for example `current_app_credential`). Assert it is present and `current_admin` is absent, keep it disjoint from `PUBLIC`, `COOKIE` and `BEARER`, and add it to the runtime 401 sweep.
- Add a test that an `OPTIONS` preflight and a `PUT` with `Origin` set to a registered app origin get no `Access-Control-Allow-Origin` header.
- Document the class in R3 / ADR-0008, and add an OpenAPI security scheme `appKey` (type http bearer) distinct from `bearerAuth`.

**R9. CI hygiene in the docs (AC 5).** In `docs/auth-integration.md`:
- Keep the key in a GitHub **environment** secret on a protected environment (required reviewers or branch restriction to `main`/`release/*`).
- Push only from the deploy job, never from `pull_request` or `pull_request_target` workflows.
- Never pass it as a CLI argument (it would show in the process list or logs); use env/stdin.
- Prefer pushing from the shared `mmlabs-online/deployment` repo, which already holds the deploy secrets, so app repos hold no credential.
- Rotate when a maintainer leaves or on a leak.
- Describe the 403/422/`drift` responses so a failed push fails the deploy.

**R10. ETag (AC 4).** The catalog ETag is computed over the response body, so it changes only when role↔permission content changes. A push that adds a permission held by no role leaves the ETag unchanged. The test must push a change that reaches a role (or the AC wording should be fixed).

## Risks

- **Stolen CI credential, with R1 applied.** The thief can add new non-admin permissions and have them granted to all members. Those keys only matter if the app's code checks them, so the realistic damage is limited to granting a permission the app already enforces but members don't yet hold. They can also create junk roles or permissions (staff cleanup) and DoS the app lock. They cannot cross `is_admin`, touch other apps, touch `admin`, change `join_policy`, `origins` or `org_owner`, or delete anything.
- **Without R1** (reusing the privileged path), the same theft is full catalog control for that app, including making admin permissions member-held. That is Critical.
- **Re-widening the member role** (F4) on every push.
- **Org apps:** manifest-created extra roles that hold `scope=org` permissions and are later granted app-wide by staff hit ADR-0015's residual risk ("staff-granted app-scope role that holds org permissions").
- **Rate limits** are per process; with multiple uvicorn workers the effective limit multiplies (already accepted in ADR-0002).
- **Description text** is stored and served in the public catalog. The admin UI must output-encode it (stored XSS risk if rendered raw). Keep a length cap and a plain-text charset.

## Open questions (owner/planner)

1. **ADR-0015 §2 vs the manifest.** New non-reserved `scope=app` permissions on `org_mode=required` apps currently need `admin:*`. Should the manifest (a) be refused for them (calechip seeds app-scope permissions via staff), (b) be allowed, which needs an ADR-0015 amendment, or (c) create them as `scope=org` and report them for staff to promote? I recommend (c), which is fail-closed.
2. **Member re-widening.** Must staff removals from `<app>:member` survive pushes? I recommend that a rebuild only adds permissions created in that push (and still strips `is_admin`/`org`), rather than a full "all non-`is_admin`" rebuild. As written, AC 3 says full rebuild, so this needs an owner decision.
3. **Permission to issue or revoke credentials:** `admin:roles:manage` or `admin:*`? I recommend `admin:*`: a long-lived external secret that writes the catalog without human review is a privileged act, consistent with ADM-002b.
4. Should tightening (`is_admin` false→true, `scope` app→org) be applied from the manifest or only reported?
5. Should credentials have a mandatory `expires_at` (for example ≤ 1 year), or should it be optional?
6. `actor_kind='app'` (migration + filter) or the `SystemActor` label fallback?
