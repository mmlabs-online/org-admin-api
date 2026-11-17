## USR-05 consult (api-platform-engineer): manifest contract, app-credential auth class, ETag, OpenAPI, docs

Read-only. I read the ticket, spec §5.3, ADR-0003 §7/§10/§11, ADR-0015, `identity-architecture.md` §4, `rbac/service.py` (`_build_for_app`, `_set_role_permissions`), `rbac/schemas.py`, `catalog_router.py` and `tests/test_route_auth_classes.py`. I did not read `audit` or `admin_auth` internals, so those points are marked "check".

### Findings

1. **Spec and rule conflicts to flag (the spec wins on scope):**
   - Spec §5.3 never mentions app credentials or a pushed manifest. Only ADR-0001 §6 and `identity-architecture.md` §4 (line 154) do. §5.3 does say "each app declares namespaced permissions", so this is an extension and not a contradiction. Ask the owner to add one sentence to §5.3 (product-manager's job, not the planner's).
   - **R3 lists only three classes** (Bearer admin / cookie / public). A fourth class, "app credential", needs an owner-approved edit to CLAUDE.md R3. It also needs an ADR-0008 amendment or a new ADR. `identity-architecture.md` already anticipates the row, so only CLAUDE.md and the ADR lag behind.
   - **R9:** `/apps/{key}/manifest` is a singleton document with a singular noun, addressed by app key. It fits the existing D18 key exception, but R9 must name it, as it does for the role catalog and `/me/permissions`.
   - **R6:** the PUT returns 200 (never 201, because it is an upsert). R6's code list does not name 200, but it is implied, so this is not a conflict.
   - **D9 tension:** ADR-0003 §7 says the member role is rebuilt only on explicit calls and never silently undoes staff tightening. The USR-05 acceptance criterion rebuilds `<app>:member` on every push, so a staff edit to `<app>:member` is lost at the app's next deploy. ADR-0003 §7 already names the manifest as an explicit rebuild point, so this is allowed. State it loudly in the docs and the plan, and tell apps to use the override if they want a fixed member role.

2. **Existing building blocks:**
   - `PermissionCreate` already carries `key`, `description`, `is_admin` and `scope`, with the ADR-0015 reserved-scope rules (`reserved_scope`, `_violates_reserved`) and the `wildcard_must_be_admin` rule.
   - `_set_role_permissions` and `_build_for_app` already give "no write when nothing changes" and a returned diff. `_build_for_app` refuses with 409 when the member role is the org owner role, and it filters on `scope = app` for org apps.
   - The role catalog ETag is computed from the response bytes, so the catalog code needs no change.

### Recommendations

**A. Credential type: API key, not client credentials (AC1).**
- Client credentials would need a token endpoint, a new JWT audience and key handling. That is heavy for one deploy-time call, and R3 says "no refresh tokens".
- Format: `mmk_<public_id>.<secret>`.
  - `<public_id>` is an indexed lookup column.
  - `<secret>` is 256-bit random, shown once.
  - The `mmk_` prefix makes it recognisable to secret scanners.
- Store `sha256(secret)` only. A high-entropy secret does not need Argon2. Compare with `hmac.compare_digest`. This follows R11 "stored only as hashes".
- Table `app_credentials`:
  - Columns: `id`, `app_id` (FK, `ON DELETE CASCADE`), `public_id` (unique), `secret_hash`, `created_by`, `created_at`, `revoked_at`, `last_used_at`.
  - `last_used_at` is updated at most about once per 10 minutes, not on every request.
  - Allow up to 2 active credentials per app, for zero-downtime rotation: issue the new one, update the app's deploy secret, revoke the old one.
- R2: hand-reviewed migration with a working `downgrade()`, and the model imported in `app/db/models.py`.
- Refuse issuing a credential for the `admin` app (409). The admin app has no manifest.
- Staff endpoints, all Bearer with an `admin:*` permission and an audit row in the same transaction (R4):
  - `POST /api/v1/apps/{key}/credentials` returns 201 with the secret once.
  - `GET /api/v1/apps/{key}/credentials` returns metadata only, never the secret or hash.
  - `DELETE /api/v1/apps/{key}/credentials/{id}` returns 204.
  - Reuse `admin:apps:write`, to avoid a new permission and a seed migration. Confirm this with the owner (see Open questions).
- Never log the credential. Redact the header.

**B. Transport and auth class (R3 class 4).**
- Use a dedicated header, `X-App-Key: mmk_…`. Do **not** use `Authorization: Bearer`. The admin `current_admin` and `is_bearer_dependant` machinery, the CORS matcher and `WWW-Authenticate: Bearer` would all conflate the two.
- A server-to-server call needs no CORS. Because `X-App-Key` is not an allowed header on any CORS policy, browsers cannot call it.
- New dependency `RequireAppCredential(key_param="key")`:
  - Missing or invalid credential, or one that is revoked or belongs to a disabled or deleted app: **401 `unauthorized`**, with no `www-authenticate: Bearer`.
  - A valid credential used against a path `{key}` that is not its own app: **403 `forbidden`** (AC6). Resolve the credential first and compare the path key second, so the response never reveals whether the path app exists.
  - Per-IP rate limit, plus a failure backoff keyed by `public_id`.
- Authorize only this one route. A credential must never be accepted on any other route or as a Bearer token.
- Route-test impact in `tests/test_route_auth_classes.py`:
  - Add an exact allow-list `APP_CREDENTIAL = {("PUT", "/api/v1/apps/{key}/manifest")}` with an exactness test, like `SELF_SCOPED`.
  - `test_every_route_is_classified`: add a branch for "has `RequireAppCredential`, no bearer guard, no permission key".
  - `test_bearer_route_permission_map`: `guarded` (line 141) must subtract `APP_CREDENTIAL`. The three new staff credential routes go into `BEARER`.
  - `test_allow_lists_have_no_stale_entries` and `test_public_and_cookie_routes_have_no_bearer_guard`: extend both to the new set.
  - `test_openapi_bearer_scheme`: the else-branch asserts no `bearerAuth` and no `x-permission`. Add an `APP_CREDENTIAL` branch asserting security `[{"appCredential": []}]` and responses 401 and 403.
  - `test_cors_matcher_agrees_with_bearer_map`: add an assertion that `BearerRouteMatcher` returns False for `PUT /apps/x/manifest` and that no CORS headers are set.
  - Runtime sweep: add `PUT` manifest with no credential → 401.
  - Add `_fill` support if new path params appear (`credential_id`).

**C. `PUT /api/v1/apps/{key}/manifest` contract (AC2, AC3).** It is a full-state declarative PUT. Use `extra="forbid"`, a body cap (for example 64 KiB), and at most 200 permissions and 50 roles.

Request:
```
{
 "permissions": [ {"key","description"?,"is_admin"?,"scope"?} ],   // required, may be []
 "roles": [ {"key","name","permissions":[key,...]} ]               // optional extra roles
}
```
- **Member override:** a `roles` entry with key `<app>:member` replaces the rebuild rule with that exact permission set. If it is absent, the member role is rebuilt through `_build_for_app`.
- **`is_admin`:** absent means false, except `<app>:*`, which is forced true. This matches `PermissionCreate`.
- **`scope`:**
  - Required (422 `scope_required`) on `org_mode = required` apps.
  - Optional on other apps (defaults to `app`); `scope = org` on a non-org app is 422 `org_mode_required`, as in register-app.
  - Reserved keys follow ADR-0015: `<app>:org:create` is always `app`, `<app>:org:invite` is always `org`. A contradiction is 422 `reserved_scope`.
- **Validation (422, `details[{field, reason}]`):**
  - Every permission and role key must start with `<app>:` (`prefix_mismatch`).
  - Duplicate keys inside the manifest (`duplicate_key`).
  - Role entries that reference an unknown permission (neither in the manifest nor already stored), listing every offender.
  - A member override containing an `is_admin` permission, or on an org app a `scope = org` permission.
  - Display-text rules for `description` and `name`.
  - The existing `wildcard_must_be_admin` rule.
- **Apply order:** take the app lock (`_locked_app`) first, so concurrent deploys serialise. Then:
  1. Upsert permissions. New ones are inserted; existing ones get `description`, `is_admin` and `scope` updated.
  2. Upsert extra roles and set their permission sets with `_set_role_permissions`. Roles not mentioned in the manifest are untouched. Manifest-declared roles are owned by the manifest.
  3. Rebuild the member role with `_build_for_app` (the no-override case), or apply the override target with `_set_role_permissions`.
  4. Run the existing invariants, including "default role holds no `is_admin`".
  - Never touch `default_role_id` (except the existing set-if-null), `join_policy`, `org_mode`, `origins` or `org_owner_role_id`.
- **Removed permissions:** a permission stored but missing from the manifest is never deleted and never dropped from the roles holding it. It is reported. Because the member rebuild reads from the stored permissions, a stale non-admin permission stays in `<app>:member`, which is the intended behaviour. Staff delete such permissions through the USR-02 endpoints.
  - Expected noise: permissions seeded by register-app, such as `<app>:org:create`, will show up if the app omits them. Document that apps should declare them.
- **Scope changes on existing permissions:** ADR-0015 §3 forbids org→app without `admin:*`. An app credential does not carry it, so refuse the whole push with 409 `scope_change_requires_staff`. Apply app→org via the member rebuild, which strips it. Verify this against ADR-0015 §2–3 in the plan.
- **Response 200** (always 200, even when everything is new):
```
{ "app": "x", "changed": bool,
  "permissions": { "added": [key], "updated": [{"key","fields":["description"|"is_admin"|"scope"]}],
                   "not_in_manifest": [{"key","held_by_roles":[role keys]}] },
  "roles": { "added": [key], "updated": [key],
             "member": {"role_key","created","added":[key],"removed":[key]} } }
```
  - All lists are sorted, so the response is deterministic.
  - Optionally add `catalog_etag`, so a deploy step can verify what consumers will see. Skip it if it forces catalog code to be shared across modules.
- **Idempotency:** PUT is naturally idempotent. A second identical push returns 200 with `changed:false`, every list empty (except `not_in_manifest`, which is a state report and is repeated), no DB writes, no audit row and an unchanged ETag. No `Idempotency-Key` is needed. Test "pushing twice never adds a `scope = org` permission to member" with two pushes on an org app (the USR-06c note).
- **Status codes:**

| Code | When |
|---|---|
| 200 | applied or no-op |
| 401 | missing, invalid or revoked credential |
| 403 | credential belongs to another app; app disabled |
| 409 | `scope_change_requires_staff`; `member_is_org_owner`; concurrent write; the `admin` key |
| 413 | body over the cap |
| 422 | `validation_error` or the domain reasons above |
| 429 | rate limit, with `Retry-After` |

  - 404 should not occur for a valid credential. Do not use it for the path key mismatch.
- **Audit (R4):**
  - One row `app.manifest_synced`, written only when `changed`, in the same transaction, with the added, updated and removed key lists.
  - Check how `AuditLogger` and `Actor` model the actor. They may be user-only, and an app-actor kind (`actor_type=app`, `actor_id=credential id`) might need an enum or column change and a migration.
  - Add actions for `credential.issued` and `credential.revoked`.
  - Do not call `mark_apps_changed` unless an `apps` row changes (it only matters for `default_role_id` set-if-null).

**D. Role catalog ETag (AC4).**
- No code change. The ETag hashes the exact response bytes in `catalog_router.py` and the DTO is unchanged.
- The catalog exposes only role key, name, `is_system` and permission keys. It does not expose descriptions or `is_admin`. So the ETag changes when a catalog-visible fact changes: a permission added to or removed from a role, or a role added or renamed. A description-only or `is_admin`-only update correctly leaves it unchanged. Reword the AC in the plan as "changes when the catalog content changes".
- Tests:
  - A push adding a non-admin permission changes the ETag, and an `If-None-Match` with the old tag returns 200.
  - An identical re-push leaves the ETag unchanged, and `If-None-Match` returns 304.
  - A description-only push leaves it unchanged.
  - An extra role with no permissions still changes it, because `catalog_rows` returns roles with a NULL permission key.

**E. OpenAPI and compatibility.**
- Additive only, so no v1 break. The role catalog DTO is untouched.
- Add `components.securitySchemes.appCredential = {type: apiKey, in: header, name: X-App-Key}` and set that on the manifest operation only.
- Add a tag (for example `app-manifest`), request and response models with examples, and 401, 403, 409, 413, 422 and 429 documented using the single error envelope.
- Describe the 200-always upsert semantics in the operation description.
- `extra="forbid"` is consistent with the rest of the codebase. Request fields can be added later without breaking old clients, because the server ships first.

**F. Docs (AC5).**
- Create `docs/auth-integration.md` (the ticket names it). `consumers.md` is the browser and token-verification checklist, and the manifest is a backend deploy-time concern, so keeping it separate is cleaner.
- Link it from `consumers.md` and `identity-architecture.md` §7.
- Contents:
  - A manifest JSON example, including one org app showing `scope`.
  - A table of rules and 422 reasons.
  - A deploy-step `curl` snippet that reads the credential from a CI secret, plus a minimal CI example.
  - The member-rebuild warning (staff tightening is overwritten unless the app declares the override).
  - How removed permissions are reported and cleaned up.
  - Credential rotation (issue the second credential, switch the secret, revoke the old one).
  - Using the role catalog ETag in the app.
- Add a short staff runbook to `docs/deploy.md` (issue and revoke a credential).
- The ticket's "mmlabs-auth support" belongs to a separate package (USR-08, D21). Limit this ticket to docs and a `curl` snippet, and list a `push_manifest` helper as a follow-up (R7).

### Tests required (R8 plus the ticket)
- **Namespace:** foreign prefix → 422 for both permissions and roles.
- **Idempotency:** a second push returns `changed:false` and writes no audit row.
- **Member rebuild:** `is_admin` permissions are excluded from `<app>:member`, and an override is honoured.
- **Org apps:** `scope = org` never reaches member, across two pushes; `scope_required` on an org app; an org permission in the override → 422.
- **Removed permissions:** reported, kept in the roles holding them, and not deleted.
- **ETag:** as listed under D.
- **Credential:**
  - A credential for another app → 403.
  - Missing or revoked → 401.
  - Disabled app → 403.
  - Credential not accepted on any other route, and an admin Bearer token not accepted on the manifest route.
- **Staff endpoints:** issue and revoke with the 401, 403 and 404 matrix; the secret is shown once; the hash is never returned.
- **Failure backoff:** repeated bad credentials get 429.
- **Concurrency:** two simultaneous pushes serialise with no 500.
- **Audit:** the row is written in the same transaction.
- Route-class tests as in B.

### Risks
1. **Unreviewed new auth class.** It is a standing credential with write power over an app's permission catalog. A leaked key lets an attacker add admin-flagged permissions, but not assign roles or grant users anything. The residual risk is widening the member role by flipping `is_admin` to false on a sensitive permission. Mitigations: flipping `is_admin` true→false could be refused or audited, and it shows up in the audit diff. Decide in the plan (see Open questions). An appsec review is warranted (areas include security).
2. **Org-app re-widening.** A manifest that skips `_build_for_app` or ignores `scope` would break ADR-0015. Route everything through it and keep the two-push test.
3. **Staff tightening overwritten on every deploy** (see Findings 1).
4. **Audit actor model** may not support a non-user actor, which would pull in a migration.
5. **Secret hygiene:** `X-App-Key` must be redacted in request logs, error details and traces (R5, R11).
6. **No Idempotency-Key** is fine for a PUT, but a retry after a timeout runs the full diff again. It is safe because of the app lock and the idempotent diff.

### Open questions for the planner and owner
1. Staff credential management: API endpoints (as proposed) or a CLI command like `register-app`? The ticket says "issued/rotated by staff". Which `admin:*` permission: reuse `admin:apps:write` or add `admin:apps:credentials`?
2. Owner approval is needed for the CLAUDE.md R3 class 4 and R9 edits, and for the ADR-0008 amendment or a new ADR-0016, plus a one-line addition to spec §5.3.
3. May a manifest push flip `is_admin` from true to false on an existing permission, which widens the member role? Recommend allowing it, audited and clearly shown in the response, or else refusing it with 409.
4. Org→app scope changes via the manifest: refuse with 409 (my recommendation) or silently skip and report? Verify against ADR-0015 §3.
5. Should the manifest also be allowed to set `default_role_id`, `join_policy` or the org owner role? Recommend no; it is a staff concern.
6. Does an app deleted or disabled by staff immediately invalidate credentials? Recommend yes: the check runs on every request, with no cache.
7. Should `not_in_manifest` also emit a `Warning` response header so CI notices? Optional.

Relevant files:
- `/home/user/org-admin-api/work/tickets/USR-05/ticket.md`
- `/home/user/org-admin-api/app/modules/rbac/service.py`
- `/home/user/org-admin-api/app/modules/rbac/schemas.py`
- `/home/user/org-admin-api/app/modules/rbac/catalog_router.py`
- `/home/user/org-admin-api/app/core/etag.py`
- `/home/user/org-admin-api/tests/test_route_auth_classes.py`
- `/home/user/org-admin-api/docs/adr/0003-rbac-keys-and-matching.md`
- `/home/user/org-admin-api/docs/adr/0015-org-roles-claim-and-permission-scope.md`
- `/home/user/org-admin-api/docs/identity-architecture.md`
- `/home/user/org-admin-api/docs/consumers.md`
- `/home/user/org-admin-api/docs/deploy.md`
