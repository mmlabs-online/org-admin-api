Status: READY

# Plan: USR-05b: Manifest push endpoint (app credential auth)

This plan follows the design fixed in `work/tickets/USR-05/plan.md` ("Split proposal", slice 2).
The owner approved that plan's "Needs owner's decision" defaults (items 1–11) by merging PR #47
(USR-05a). Deviations forced by the merged code are marked **[deviation]** with the reason.

## Spec summary
- Spec §5.3: "Apps are registered; each app declares namespaced permissions
  (`<app>:<resource>:<action>`) and its own roles; users get roles per app ...". Membership: "a
  default role `<app>:member` holding all its permissions except those flagged `is_admin` (for
  `org_mode = required` apps also except org-level permissions, `permissions.scope = 'org'`;
  ADR-0015 ...). Policy and role contents are data."
- ADR-0001 §6: "**Apps declare their catalog** (permissions + default roles) through an API call or
  a manifest at deploy time. This service stores and assigns them; it does not interpret them."
- `identity-architecture.md` §4: "`PUT /api/v1/apps/{key}/manifest` | app credential (ADR-0016,
  D25) | app pushes its permissions + default role | USR-05b".
- D9: "Default role `<app>:member` = all app permissions except `is_admin` ones ... Policy and role
  content are data." ADR-0003 Consequences: "A new permission reaches `<app>:member` only after an
  explicit rebuild (USR-02b app creation, USR-05 manifest) or a staff edit."
- ADR-0015 §2/§3: creating a non-reserved `scope = app` permission on an `org_mode = required` app
  needs `admin:*`; the build rule "covers `build_default_role`, app creation, `register-app` and the
  USR-05 manifest."
- ADR-0016 §1: auth class 4 "allowed on exactly one route, `PUT /api/v1/apps/{key}/manifest` ...
  listed by exact (method, path) in `APP_CREDENTIAL` ... Never accepted on any other route; an admin
  JWT is never accepted on that route. No CORS ... Rate-limited per IP before authentication."
  §3: "Only SHA-256(secret) is stored, compared with `hmac.compare_digest`." §4: "One 401 answer
  (`unauthorized`, `WWW-Authenticate: Bearer realm="app"`, no details) for missing, malformed,
  unknown, wrong-secret or revoked credentials. A valid credential for another app gives 403
  `app_credential_mismatch`, audited `denied`. A disabled app gives 403 `app_disabled`." §6: the
  manifest actor and write policy.
- CLAUDE.md R3 class (4) (merged): "only `PUT /api/v1/apps/{key}/manifest`: `Authorization: Bearer
  mmak_<key_id>_<secret>` ... accepted only for its own app (another app → 403) and on no other
  route; no CORS; per-IP rate-limited." R9: "`PUT /api/v1/apps/{key}/manifest` (singular
  `manifest`, a single document; D25)."
- Ticket note (ADR-0015): "pushing the manifest twice never adds a `scope = org` permission to
  `<app>:member`".

Spec gap (carried from USR-05, owner item 3, not blocking): spec §5.3 does not mention the pushed
manifest; ADR-0001 §6 / ADR-0016 do. A one-line spec addition stays on the owner list.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | App credential accepted only on the manifest route and only for its own app: wrong app → 403 `app_credential_mismatch` (audited `denied`); missing / malformed / unknown / wrong secret / revoked → one identical 401; disabled app → 403 `app_disabled`. Admin JWT on the manifest → 401; `mmak_` credential on any Bearer route → 401. | `tests/test_app_credential_auth.py` (all `test_*` listed below); `tests/test_route_auth_classes.py` (`APP_CREDENTIAL` entries, exactness, sweep, CORS) |
| 2 | Manifest = permissions (key, description, is_admin, scope) + optional extra roles + optional member override. Only the `<app>:` namespace accepted (422 `prefix_mismatch`); `scope` required on `org_mode = required` apps (422 `scope_required`). | `tests/test_app_manifest_api.py › test_namespace_422_*`, `› test_org_app_scope_required_422`, `› test_validation_reasons` (parametrized) |
| 3 | Idempotent push; new permissions added; `<app>:member` rebuilt via `_build_for_app` unless overridden; no `is_admin`/`scope` flips (409); removed permissions reported, not dropped from roles. | `› test_push_twice_is_idempotent`, `› test_new_permission_reaches_member`, `› test_is_admin_excluded_from_member`, `› test_override_narrows_never_widens`, `› test_flip_is_409`, `› test_not_in_manifest_reported_and_still_held` |
| 4 | Role catalog `ETag` changes when catalog-visible content changes (owner item 4 interpretation). | `› test_etag_changes_on_member_reaching_addition`, `› test_etag_changes_on_new_role`, `› test_etag_unchanged_on_repush_admin_only_and_description` |
| 5 | One audit row per changing push; none for a no-op. | `› test_changing_push_writes_one_row`, `› test_noop_push_writes_no_row`; `tests/test_audit_coverage.py › test_app_credential_routes_are_audited` |
| 6 | `docs/auth-integration.md`: manifest format and pushing it at deploy time. | File exists with the sections listed under Approach step 11; linked from `docs/consumers.md` and `identity-architecture.md` |
| 7 | Tests: namespace, idempotency, is_admin excluded from member, pushing twice never adds `scope = org` to member, ETag, wrong app credential → 403, route-class tests. | The test files below; `make check` green |
| 8 | Appsec carry-overs from USR-05 security review: `hmac.compare_digest`; single 401; per-IP rate limit before credential parsing; `last_used_at`; `mmak_` on admin routes → 401; admin JWT on manifest → 401. | `tests/test_app_credential_auth.py › test_secret_compared_with_compare_digest`, `› test_all_401_answers_identical`, `› test_rate_limit_before_auth`, `› test_last_used_at_*`; `tests/test_route_auth_classes.py › test_credential_on_bearer_routes_401`, `› test_admin_jwt_on_manifest_401` |
| 9 | Appsec 🟡 from USR-05: the `docs/deploy.md` credential-issue runbook fails loudly (no silent empty secret file), checks the file is non-empty, and tells the operator to revoke an orphan `key_id`. | Reviewer reads the snippet: no unchecked pipeline; `[ -s "$cred" ]` guard; orphan-revoke line present |
| 10 | `make check` green (R8) | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/app_credentials/errors.py` | **new.** `AppCredentialMismatchError(PermissionDeniedError)` `code = "app_credential_mismatch"`; `AppCredentialAppDisabledError(PermissionDeniedError)` `code = "app_disabled"`. **[deviation]** not in the USR-05 file list; the house pattern puts domain 403 codes in a module `errors.py` (`invitations/errors.py`, `organizations/errors.py`). | AC1, R6 |
| `app/modules/app_credentials/deps.py` | **new.** `app_credential_scheme = HTTPBearer(scheme_name="appCredential", bearerFormat="mmak", auto_error=False)`; `async def current_app_credential(key: str, session: SessionDep, ctx: AuditContextDep, credentials: Annotated[HTTPAuthorizationCredentials \| None, Security(app_credential_scheme)]) -> AuthenticatedApp` calling `AppCredentialService(session).authenticate(raw_or_none, key, ctx=ctx)`. `AppCredentialDep` alias. No logic beyond the call. | R3 class 4, R1 |
| `app/modules/app_credentials/service.py` | Add `AuthenticatedApp` dataclass (`credential_id, key_id, app_id, app_key`, property `actor -> SystemActor(f"app:{app_key}/{key_id}", privileged=False)`) and `authenticate(raw, path_key, *, ctx)` (order in Approach step 2). Optional tidy-up of the 💭 note (drop the `assert` re-parse) is **not** done (R7; follow-up). | AC1, AC8, R1 |
| `app/modules/app_credentials/repository.py` | Add `get_with_app(key_id) -> tuple[AppCredential, App] \| None` (one join, `populate_existing=True`) and `touch_last_used(credential_id)` (`UPDATE ... SET last_used_at = now()`). | AC8 |
| `app/modules/rbac/manifest.py` | **new.** `ManifestService.sync(key, data, *, caller: AuthenticatedApp, ctx) -> ManifestResult` (Approach steps 4–8). | AC2–AC5 |
| `app/modules/rbac/manifest_router.py` | **new.** `APIRouter(prefix="/apps", tags=["app-manifest"])`, no `current_admin`. `PUT /{key}/manifest`, `dependencies=[rate_limit("app_manifest")]`, params `caller: AppCredentialDep`, `body: ManifestPut`, responses 401/403/409/422/429 documented. | R3 class 4, R9 |
| `app/modules/rbac/schemas.py` | Add `ManifestPermission`, `ManifestRole`, `ManifestMember`, `ManifestPut` (all `extra="forbid"`) and `ManifestResult` + nested result models. | AC2, AC3 |
| `app/modules/rbac/service.py` | `_build_for_app(..., only: set[str] \| None = None)`: when given, target = eligible ∩ `only`. `PermissionService._create(..., admin_create_allowed: bool = False)`: the `is_admin` `require_super` is skipped only when `admin_create_allowed and app.key != ADMIN_APP_KEY`; the org-app `scope = app` guard is unchanged. No other change. | AC3, ADR-0016 §6 |
| `app/modules/rbac/repository.py` | Add `roles_holding(app_id, perm_ids) -> dict[uuid.UUID, list[str]]` (permission id → sorted role keys). | AC3 (`not_in_manifest.held_by_roles`) |
| `app/core/rate_limit.py` | Scope `"app_manifest"` in `Scope` and `_LIMITS`. | ADR-0016 §1 |
| `app/core/config.py` | `app_manifest_rate_limit: int = Field(default=30, ge=1)`, `app_manifest_rate_window_seconds: int = Field(default=60, ge=1)`. | R5 |
| `.env.example` | `APP_MANIFEST_RATE_LIMIT=`, `APP_MANIFEST_RATE_WINDOW_SECONDS=` (no values). | R5 |
| `app/modules/audit/actions.py` | `APP_MANIFEST_SYNCED = "app.manifest_synced"`. | R4 |
| `app/main.py` | `app.include_router(manifest_router, prefix=settings.api_prefix)` next to `app_credentials_router`. | route mounted |
| `docs/auth-integration.md` | **new.** Manifest format and deploy-time push (Approach step 11). | AC6 |
| `docs/consumers.md` | One link line to `auth-integration.md` near the "Organizations" section's mention of "manifest sync". | discoverability (USR-05 plan) |
| `docs/identity-architecture.md` | §5 S8: add `app.manifest_synced` (system actor `app:<key>/<key_id>`; wrong-app `denied`). §7: one risk line (leaked app credential, link ADR-0016 Consequences and `auth-integration.md`). §8 D25: "Proposed, pending owner" → "Accepted (owner merged USR-05a, PR #47, 2026-11-17)". | USR-05 plan file list; D25 status is now stale |
| `docs/adr/0016-app-credential-auth-class-and-manifest.md` | Status line only → `Accepted — owner merged USR-05a (PR #47), 2026-11-17`. **[deviation]** not in the USR-05 list; the status is stale after the merge. No content change. | doc accuracy |
| `docs/deploy.md` | "App credentials (USR-05a)" section: harden the issue snippet (Approach step 12); add one line on `APP_MANIFEST_RATE_LIMIT` / `_WINDOW_SECONDS` (optional, default 30 / 60 s) and a link to `auth-integration.md`. | AC9, USR-05 plan (rate-limit keys) |
| `tests/test_app_credential_auth.py` | **new.** Auth tests (list below). | AC1, AC8 |
| `tests/test_app_manifest_api.py` | **new.** Sync behaviour tests (list below). | AC2–AC5, AC7 |
| `tests/test_route_auth_classes.py` | `APP_CREDENTIAL` allow-list and the test changes in Approach step 10. | R3, AC1 |
| `tests/test_audit_coverage.py` | `APP_CREDENTIAL_AUDITED = {("PUT", MANIFEST): {"app.manifest_synced"}}` + one test (below). `AUDITED` and its count 18 are unchanged (the route is not Bearer). | R4 coverage gate |

No migration (no schema change: `app_credentials.last_used_at` exists since USR-05a; audit
actions are not DB-constrained). No other file is touched.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_app_credential_auth.py › test_valid_credential_200` | own app, active → 200; response `app == key` |
| `› test_all_401_answers_identical` (parametrized) | no header; `Basic …`; `Bearer` + empty; malformed (`mmak_` short, uppercase hex, trailing newline, 10 kB); JWT-shaped; unknown well-formed key_id; known key_id + wrong secret; revoked → each 401, `code == "unauthorized"`, `details is None`, `WWW-Authenticate == 'Bearer realm="app"'`; all bodies equal except `X-Request-ID` |
| `› test_wrong_app_403_and_denied_row` | credential of app A on `/apps/B/manifest` → 403 `app_credential_mismatch`; exactly one `audit_log` row: `action = app.manifest_synced`, `outcome = denied`, `actor_kind = system`, `actor_label = "app:A/<key_id>"`, `app_key = "A"`; B unchanged. Setup committed first (the failure path rolls back, as in `test_denied_guard_writes_row`) |
| `› test_wrong_app_unknown_path_app_same_403` | credential of A on `/apps/does-not-exist/manifest` → same 403 (no existence oracle); denied row has no out-of-format `app_key` |
| `› test_disabled_app_403` | app disabled → 403 `app_disabled`; no audit row; nothing written |
| `› test_disabled_between_auth_and_lock_403` | service-level: `ManifestService.sync` on a disabled app (lock re-check) → 403 `app_disabled` |
| `› test_secret_compared_with_compare_digest` | monkeypatch spy on `hmac.compare_digest` in the service module: called once with the stored hash and the computed hash for a known key_id |
| `› test_last_used_at_set_on_success_only` | 200 → `last_used_at` set (DB clock); 401 wrong secret → still NULL |
| `› test_rate_limit_before_auth` | `make_client(app_manifest_rate_limit=2)`: 3 requests without a credential → 401, 401, 429 (`Retry-After` set); a 4th with a valid credential → 429 (counted before auth) |
| `› test_schema_invalid_body_without_credential_is_401` | invalid manifest body + no credential → 401, not 422 |
| `› test_no_secret_in_logs` | `caplog` over the 401 matrix and a 200: neither the raw value nor the secret part appears; `key_id` may |
| `test_app_manifest_api.py › test_namespace_422_permissions` / `_roles` / `_member` | `other:x:read` in permissions, roles key or role permission, member → 422 with `reason` `prefix_mismatch` / `undeclared_permission`; nothing written |
| `› test_validation_reasons` (parametrized) | 422 before any write for `wildcard_not_allowed`, `duplicate_key`, `reserved_scope`, `org_mode_required`, `undeclared_permission`, `admin_permission_in_role`, `ineligible_member_permission`, `reserved_role`; and `extra="forbid"` → `validation_error` |
| `› test_org_app_scope_required_422` | org app, non-reserved key without `scope` → 422 `scope_required`; reserved `org:invite` without scope → accepted as `org` |
| `› test_new_permission_reaches_member` | non-org app: push adds `a:x:read` → 200, `permissions.added`, `member.rebuilt == true`, member holds it |
| `› test_is_admin_excluded_from_member` | `is_admin: true` permission created (inert, no `admin:*` needed), never in member; `member.added` excludes it |
| `› test_push_twice_is_idempotent` | second identical push → `changed: false`, all lists empty, no audit row, catalog unchanged |
| `› test_org_app_push_twice_never_adds_org_scope_to_member` | org app: manifest with `scope: org` and reserved keys; push twice → member never holds a `scope = org` permission; second push `changed: false` |
| `› test_requires_staff_409` | org app, new non-reserved `scope: app` → 409 `requires_staff`; nothing written; after staff create it (`admin:*`), the same push → 200 |
| `› test_flip_is_409` (parametrized) | stored `is_admin=false` declared `true` → 409 `is_admin_mismatch`; stored `scope=org` declared `app` (org app) → 409 `scope_mismatch`; nothing written |
| `› test_override_narrows_never_widens` | `member.permissions = [a]` with eligible `{a, b}` → member = `{a}`; re-push no-op; override with an `is_admin` key → 422 |
| `› test_rebuild_readds_staff_removed_only_when_creating` | staff removed `a:x` from member; push with no new permission → member untouched; push that creates `a:y` → member re-gains `a:x` and `a:y`, both in `member.added` (owner item 5) |
| `› test_extra_roles_created_and_additive` | role created with `is_system=false`; second push adding a permission → `roles.updated`; a permission staff added by hand is kept; `name` change is ignored on an existing role |
| `› test_role_not_manifest_managed_409` (parametrized) | role key equal to a system role, the default role (non-`member` default set by staff), or the org owner role → 409 `role_not_manifest_managed` |
| `› test_member_is_org_owner_409` | org app whose owner role is `<app>:member`, push that would rebuild → 409 `member_is_org_owner`, nothing written |
| `› test_not_in_manifest_reported_and_still_held` | stored `a:old` held by member and `a:editor`, absent from manifest → listed in `not_in_manifest` with `held_by_roles`, still stored and held |
| `› test_description_updated` | changed description → `description_updated`; omitted/`null` description leaves the stored one |
| `› test_never_touches_app_fields` | `apps` row (`name`, `status`, `origins`, `default_role_id`, `org_owner_role_id`, `updated_at`) identical after a changing push |
| `› test_etag_changes_on_member_reaching_addition` | GET catalog ETag before/after → different |
| `› test_etag_changes_on_new_role` | different ETag |
| `› test_etag_unchanged_on_repush_admin_only_and_description` | re-push → `If-None-Match` gives 304; `is_admin`-only addition and description-only change → same ETag |
| `› test_changing_push_writes_one_row` | one `app.manifest_synced` row: `outcome=success`, `actor_kind=system`, `actor_label="app:<key>/<key_id>"`, target `(app, app.id)`, `changes` keys ⊆ allowed list, no raw/secret/hash in any column |
| `› test_noop_push_writes_no_row` | count unchanged |
| `› test_limits_422` | 201 permissions → `validation_error`; 51 roles → `validation_error` |
| `test_route_auth_classes.py › test_app_credential_allow_list_is_exact` | `APP_CREDENTIAL == {("PUT", f"{V1}/apps/{{key}}/manifest")}`; `current_app_credential` appears in the dependant tree of exactly these routes |
| `› test_every_route_is_classified` (updated) | `APP_CREDENTIAL` routes accepted with `(has_admin, keys) == (False, [])` |
| `› test_bearer_route_permission_map` / `› test_allow_lists_have_no_stale_entries` / `› test_public_and_cookie_routes_have_no_bearer_guard` (updated) | subtract / include `APP_CREDENTIAL` |
| `› test_openapi_bearer_scheme` (updated) | `securitySchemes.appCredential == {"type": "http", "scheme": "bearer", "bearerFormat": "mmak"}`; manifest op `security == [{"appCredential": []}]`, `401`/`403` present, no `x-permission`, no `bearerAuth` |
| `› test_cors_matcher_agrees_with_bearer_map` (updated) | matcher False for the manifest `PUT` |
| `› test_manifest_no_cors` | `PUT` and preflight with a registered admin origin and a registered app origin → no `Access-Control-Allow-Origin` |
| `› test_app_credential_route_401_without_credential` | sweep: 401, `WWW-Authenticate` starts with `Bearer realm="app"` |
| `› test_admin_jwt_on_manifest_401` | `mint(staff with admin:*)` as Bearer on the manifest → 401 |
| `› test_credential_on_bearer_routes_401` (parametrized over `BEARER` + `SELF_SCOPED`) | a valid `mmak_` value as Bearer → 401 `unauthorized`, realm `admin` |
| `test_audit_coverage.py › test_app_credential_routes_are_audited` | `set(APP_CREDENTIAL_AUDITED) == APP_CREDENTIAL` (imported from `test_route_auth_classes`); a changing push writes exactly one row with an allowed action; a failing push (422) writes none |

## Approach
1. **Config and rate limit (R5).** Add the two settings, the `app_manifest` scope and the
   `.env.example` keys. Defaults 30 / 60 s (USR-05 plan).
2. **`AppCredentialService.authenticate(raw: str | None, path_key: str, *, ctx) ->
   AuthenticatedApp`** (R1: HTTP-free; raises `AppError` subclasses only). Order:
   1. `raw is None` → reject `missing`.
   2. `parse_credential(raw)` → `None` → reject `malformed`.
   3. `repo.get_with_app(key_id)` → `None` → reject `unknown`.
   4. `hmac.compare_digest(row.secret_hash, secret_hash(secret))` false → reject `secret`.
   5. `row.revoked_at is not None` → reject `revoked`.
   6. `app.status != active` → log `disabled`, raise `AppCredentialAppDisabledError("The app is
      disabled")`.
   7. `app.key != path_key` → log `mismatch`, `audit.record_failure(APP_MANIFEST_SYNCED,
      outcome=DENIED, actor=SystemActor(f"app:{app.key}/{key_id}", privileged=False), ctx=ctx,
      app_key=app.key, changes={"reason": "app_credential_mismatch"})`, then raise
      `AppCredentialMismatchError("Credential belongs to another app")`. The path key is never
      written to the row (it is unvalidated input and `app_key` has a format CHECK). Resolving the
      credential first means the answer never reveals whether the path app exists.
   8. `repo.touch_last_used(row.id)` (same request transaction; after every check so the
      failure-path rollback cannot matter).
   9. Return `AuthenticatedApp`.
   - "reject" = `logger.info("app_credential_denied reason=%s key_id=%s", reason, key_id or "-")`
     then `UnauthorizedError("Authentication required", headers={"WWW-Authenticate": 'Bearer
     realm="app"'})`. One message, no details, one header for all five reasons (ADR-0016 §4).
     Never log `raw` or the secret (R11).
3. **`current_app_credential`** (deps.py) only extracts the header via `HTTPBearer(auto_error=False)`
   and calls `authenticate`. Rate limit comes first because the route declares
   `dependencies=[rate_limit("app_manifest")]`; FastAPI runs route-level `dependencies` before
   parameter dependencies, so the order is rate limit → parse → lookup → compare_digest → revoked →
   app active → app match (the USR-05 order). Dependency errors are raised before body validation
   errors, so a bad credential with an invalid body is 401, not 422.
4. **Schemas** (`rbac/schemas.py`, all `extra="forbid"`):
   - `ManifestPermission{key: PermissionKey, description: Description | None = None, is_admin: bool
     = False, scope: PermissionScope | None = None}`
   - `ManifestRole{key: str (ROLE_KEY_RE, ≤160), name: RoleName, permissions: list[PermissionKey]
     (≤200)}`
   - `ManifestMember{permissions: list[PermissionKey] (≤200)}`
   - `ManifestPut{permissions: list[ManifestPermission] (≤200), roles: list[ManifestRole] = [] (≤50),
     member: ManifestMember | None = None}`
   - `ManifestResult` exactly as in API contract below; every list sorted.
5. **`rbac/service.py` changes** (minimal):
   - `_build_for_app(app, *, set_default=True, only=None)`: after computing `target`, `if only is
     not None: target = {pid: k for pid, k in target.items() if k in only}`. Callers unchanged.
   - `PermissionService._create(key, data, *, actor, admin_create_allowed=False)`: replace `if
     app.key == ADMIN_APP_KEY or is_admin: require_super(actor)` with `if app.key ==
     ADMIN_APP_KEY or (is_admin and not admin_create_allowed): require_super(actor)`. The
     org-app `scope = app` guard (ADR-0015 §2) stays as is.
6. **`ManifestService.sync`** (`rbac/manifest.py`). Uses `RbacRepository`,
   `PermissionService._create`, `RoleCatalogService._build_for_app`, `_set_role_permissions`,
   `scopes.reserved_scope` / `default_scope`, `_violates_reserved`, `AuditLogger`. Raises
   `AppError` only. All refusals happen before the first write.
   1. **Shape checks (422, before the lock)**, `details=[{field, reason, key?}]` (`key` only for
      keys that passed the format pattern, per `UnprocessableError` doc):
      - `prefix_mismatch`: first segment ≠ path key (permissions, role keys, role and member
        permission keys);
      - `wildcard_not_allowed`: `<app>:*`;
      - `duplicate_key`: duplicate permission key or role key (duplicates inside a role's or
        member's list are ignored, like `RolePermissionsReplace`);
      - `reserved_scope`: declared scope contradicts `reserved_scope`;
      - `undeclared_permission`: a role or member references a key not in `permissions`;
      - `admin_permission_in_role`: a role lists a declared `is_admin` permission;
      - `reserved_role`: `<app>:member` in `roles`;
      - `ineligible_member_permission`: member lists an `is_admin` permission.
   2. **Lock** `app = await repo.lock_app(key)`. `None` → 404 (unreachable after auth; defensive).
      `app.key == admin` → 409 `admin_app` (defensive). `status != active` → 403 `app_disabled`
      (re-check under the lock).
   3. **Mode-dependent checks (422):** resolved scope per permission = declared, else reserved,
      else (non-org app) `app`; on org apps a non-reserved key without `scope` → `scope_required`;
      `scope = org` on a non-org app → `org_mode_required`; on org apps a member key whose resolved
      scope is `org` → `ineligible_member_permission`.
   4. **Conflict checks (409, under the lock):** load stored permissions by the declared keys.
      - stored `is_admin` ≠ declared → `is_admin_mismatch`;
      - stored `scope` ≠ resolved scope → `scope_mismatch` (on non-org apps, scope is always `app`,
        so this only fires on org apps or reserved keys);
      - new, non-reserved, resolved `scope = app`, on an org app → `requires_staff` (also when
        `is_admin`, fail closed: `_create`'s org guard would refuse it);
      - each declared role that exists and is `is_system`, `== app.default_role_id` or
        `== app.org_owner_role_id` → `role_not_manifest_managed`;
      - a member rebuild will run (override present, or at least one permission is new) and the
        app is an org app with `org_owner_role_id ==` the `<app>:member` role's id →
        `member_is_org_owner` (pre-checked so it fails before any write).
      Every 409 is `ConflictError(message, details=[{"field", "reason", "key"?}])`.
   5. **Apply** (one transaction; the request session commits on success, rolls back on error):
      1. create new permissions via `PermissionService(session)._create(key,
         PermissionCreate(key, description, is_admin, scope=resolved), actor=caller.actor,
         admin_create_allowed=True)` in declared-key order;
      2. for existing permissions whose declared description is not `None` and differs: set it and
         `repo.save()`;
      3. extra roles: absent → `repo.add_role(Role(app_id, key, name, is_system=False))`; then
         `_set_role_permissions(repo, role, current ∪ declared)` (never removes; name of an
         existing role is never changed);
      4. member: override present → `_build_for_app(app, set_default=False, only=set(override))`;
         else only if step 1 created something → `_build_for_app(app, set_default=False)`;
      5. never touch `apps` fields or `default_role_id`; no `mark_apps_changed()` (origins are not
         touched).
   6. **Report:** `not_in_manifest` = stored permissions of the app (excluding `<app>:*`) whose key
      is not declared, each with `held_by_roles` from `repo.roles_holding`. Never deleted, never
      stripped from roles.
   7. **`changed`** = any of: permissions added, descriptions updated, roles added, roles updated
      (≥1 permission added to an existing role), member role created, member added/removed.
   8. **Audit (R4):** only when `changed`, one `AuditLogger.record(APP_MANIFEST_SYNCED,
      actor=caller.actor, ctx, target=(APP, app.id), app_key=key, changes=...)` in the same
      transaction, after the last write. `changes` keys (only those non-empty):
      `permissions_added`, `descriptions_updated`, `roles_added`, `roles_updated` (each
      `diff.change([], sorted keys)`), `member_permissions` (`diff.change(removed, added)`). Key
      lists only; no descriptions, no secrets (`_check_keys` passes: no forbidden substring).
      The label `app:<key>/<key_id>` is ≤ 53 chars (app key ≤ 32) against the 64 CHECK.
7. **Router** (`rbac/manifest_router.py`): `PUT /{key}/manifest`, `response_model=ManifestResult`,
   200, `dependencies=[rate_limit("app_manifest")]`, `responses` for 401, 403, 409, 422, 429. Handler:
   `return await ManifestService(session).sync(key, body, caller=caller, ctx=ctx)` (no ORM). No
   `current_admin`, so `BearerRouteMatcher` is False (no admin CORS), and the path is outside
   `/api/v1/auth/` (no identity CORS). Mount in `create_app`.
8. **ETag (AC4):** no catalog code change; the catalog ETag is a hash of the catalog body, which
   holds role keys, names, `is_system` and role permission keys only (ADR-0003 §11), so it changes
   exactly when catalog-visible content changes (owner item 4).
9. **Audit action** `app.manifest_synced` added to the enum (no DB constraint on actions).
10. **Route-class tests** (`tests/test_route_auth_classes.py`):
    - `APP_CREDENTIAL = {("PUT", f"{V1}/apps/{{key}}/manifest")}`; exactness test also checks that
      `current_app_credential` is in the dependant tree of exactly these routes.
    - `test_every_route_is_classified`: `route in APP_CREDENTIAL and (has_admin, keys) == (False,
      [])` is ok. `test_bearer_route_permission_map`: guarded = routes not in `PUBLIC | COOKIE |
      APP_CREDENTIAL`. Stale-entry test includes it; "no bearer guard" test covers it.
    - OpenAPI, CORS, runtime sweep, admin JWT → 401, `mmak_` on Bearer routes → 401 (list above).
      `mmak_` on Bearer routes already fails in `verify_access_token`; the test pins it.
11. **`docs/auth-integration.md`** (new), sections:
    - What it is (ADR-0016, R3 class 4), who issues credentials (link `deploy.md` "App credentials").
    - Manifest format: JSON schema summary, limits, a non-org example and an org-app example
      (`scope` on every non-reserved key; `org:create` / `org:invite` reserved).
    - Rules table: namespace, wildcard, `is_admin` (create-only, never in member or roles), no
      flips (409), `requires_staff`, extra roles (additive, name set once, never system/default/
      owner), member rebuild only when a permission is created and that it re-adds staff-removed
      non-admin permissions (owner item 5), the override (narrow only), `not_in_manifest` cleanup
      by staff, app fields never touched.
    - Pushing at deploy time: a POSIX `sh` step that reads the credential from an env var set from
      a GitHub Environment secret, sends it only via a 0600 header file (`-H @file`, never argv),
      `curl -fsS --retry 2 -X PUT ... --data-binary @manifest.json`, fails the deploy on non-200;
      run from the deploy job only (never on `pull_request` / `pull_request_target`); push
      **before** rolling out the new app version.
    - Responses and codes (table), the response body, idempotency (`changed: false`), 429.
    - Rotation (link deploy.md) and ETag behaviour for consumers caching the catalog.
12. **`docs/deploy.md` hardening (AC9, appsec 🟡).** Replace the issue snippet with a version that
    has no unchecked pipeline. `set -o pipefail` is **not** used: the runbook runs in POSIX
    `sh`/`dash` under `set -eu`, where `pipefail` may be unsupported and would abort the subshell
    **[deviation from the appsec wording; same effect]**. Shape:
    ```sh
    resp=$(mktemp); cred=$(mktemp)
    if curl -fsS -X POST -H @"$hdr" "$api/apps/<key>/credentials" -o "$resp" \
       && python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["credential"]);print("key_id: "+d["key_id"],file=sys.stderr)' < "$resp" > "$cred" \
       && [ -s "$cred" ]; then
      rm -f -- "$resp"
    else
      rm -f -- "$resp" "$cred"
      echo "issue failed: list credentials and revoke any key_id you do not hold" >&2
      exit 1
    fi
    # copy "$cred" into the GitHub Environment secret, then:
    rm -f -- "$cred"
    ```
    Plus one line: "If the value is lost before it is stored (file deleted, wrong secret pasted),
    revoke that `key_id` at once; it counts toward the 2-active limit." Plus the rate-limit keys
    line and the `auth-integration.md` link.
13. **identity-architecture.md / ADR-0016 / consumers.md** edits as in the file table.
14. `make check` green (R8).

Rules: **R1** router → service → repository; deps only call the service; services raise `AppError`
subclasses (`UnauthorizedError`, the two new 403s, `ConflictError`, `UnprocessableError`). **R2** no
schema change, `alembic check` stays clean. **R3** class 4 on exactly one route, pinned by
`APP_CREDENTIAL`; no `current_admin`; no CORS; rate-limited before auth. **R4** one row per
changing push in the same transaction; wrong-app denied row via `record_failure`; no row for
no-ops, 401s, 422s, 409s or 429s (S8: "429s ... and no-ops write nothing"). **R5** two new optional
env keys in `config.py` and `.env.example`. **R6** envelope; 200/401/403/404/409/422/429. **R7**
everything else to follow-ups. **R8** tests above. **R9** documented exception (D25). **R10** no
OAuth client-credentials grant. **R11** hash compared with `compare_digest`; never log raw value or
secret; `key_id` only.

## Specialist input used
Same consults as USR-05 (`work/tickets/USR-05/consult-*.md`), as resolved in the USR-05 plan
("Specialist input used") and approved by merge. Specific to this slice:
- **`hmac.compare_digest`** (identity F6, api-platform, security R; security-review follow-up):
  adopted (step 2.4, with a spy test).
- **Single 401, realm `app`, no details** (security R5, ADR-0016 §4): adopted. api-platform's "no
  `WWW-Authenticate: Bearer`" rejected (ADR-0016 fixed it).
- **Rate limit before auth** (security R6 "scope `manifest` ... e.g. 10/min"): adopted as scope
  `app_manifest`, 30/60 s (USR-05 plan default; a deploy may retry and several apps can share a
  runner IP). Per-credential bucket: follow-up.
- **`last_used_at`** (identity "best-effort inside the transaction", security "on success"):
  adopted on every successful authentication, in the request transaction. api-platform's "at most
  once per 10 min": rejected (deploy-time call, low volume).
- **Body cap 413** (api-platform): rejected again; a global body-size limit is a follow-up.
- **Security-review follow-ups:** `mmak_` on Bearer routes → 401 and admin JWT on manifest → 401
  tests: adopted. Concurrency test for the 2-active cap: not in this ticket (05a area; follow-up).
  The `assert` tidy-up (💭): follow-up.
- **Audit `auth_failed` rows for known key_id** (security R7): rejected (USR-05 plan); log line only.

## Assumptions
- Malformed JSON (not parseable) returns 422 `validation_error` before the credential is checked:
  FastAPI reads and decodes the body before running dependencies. Schema-invalid but parseable
  bodies still get 401/403 first. Nothing about the credential or the app is disclosed by this.
  The global body-size limit stays a follow-up.
- `last_used_at` is written only when authentication fully succeeds; it is rolled back if the push
  then fails with 409/422 (the request transaction). Documented in `auth-integration.md` as "last
  successful authentication of a push that was applied or was a no-op".
- An omitted or `null` permission `description` leaves the stored description unchanged (never
  clears it).
- Duplicates inside a role's or the member's permission list are ignored; duplicate permission keys
  or role keys in the manifest are 422 `duplicate_key`.
- A new `is_admin` permission with resolved `scope = app` on an org app is refused with
  `requires_staff` like any new non-reserved `scope = app` permission (fail closed; `_create`'s
  ADR-0015 guard is kept).
- `roles.updated` lists role keys that gained at least one permission; the audit row carries role
  keys, not per-role permission lists (the response has no per-role detail either).
- The `<app>:*` wildcard, if stored, is excluded from `not_in_manifest`.
- Disabled app: not audited (ADR-0016 only audits the wrong-app 403).
- Size: about 550 lines of production code, about 200 lines of docs and about 650 lines of tests.
  Above the ~400-line guide, but it is one feature with its design fixed and every guard reusing
  merged helpers; splitting the docs or the auth dep off would leave an unusable or undocumented
  route. Kept as one cycle at the orchestrator's preference.

## Blocking questions
None.

## Split proposal
None (see the size note under Assumptions). Follow-ups (not in this ticket, R7):
- `mmlabs-auth` `push_manifest` helper (USR-08 repo);
- credential `expires_at` and `label`;
- a global request body-size limit (would also move malformed-JSON 422 behind the limit);
- a per-credential rate-limit bucket;
- the deployment-repo workflow step that pushes the manifest;
- `actor_kind` filter value `app` if the owner wants machine rows separated;
- 05a 💭: drop the `assert` re-parse in `issue`; concurrency test for the 2-active cap;
- spec §5.3 one-line addition (owner item 3).

## Data model / migration
None. Uses existing `app_credentials.last_used_at` (USR-05a), `permissions`, `roles`,
`role_permissions`, `audit_log` (system actor label; no new `actor_kind`).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| PUT | `/api/v1/apps/{key}/manifest` | Header `Authorization: Bearer mmak_<key_id>_<secret>`. Body `{"permissions": [{"key", "description"?, "is_admin"?: false, "scope"?: "app"\|"org"}] (≤200), "roles"?: [{"key", "name", "permissions": [key] (≤200)}] (≤50), "member"?: {"permissions": [key] (≤200)}}` | 200 `{"app", "changed", "permissions": {"added", "description_updated", "not_in_manifest": [{"key", "held_by_roles"}]}, "roles": {"added", "updated"}, "member": {"role_key", "rebuilt", "added", "removed"}}` (lists sorted) | 200; 401 `unauthorized` (`WWW-Authenticate: Bearer realm="app"`, no details); 403 `app_credential_mismatch` / `app_disabled`; 404 (defensive, unreachable); 409 `conflict` with `details[].reason` `is_admin_mismatch` / `scope_mismatch` / `requires_staff` / `role_not_manifest_managed` / `member_is_org_owner` / `admin_app`; 422 `validation_error` (shape, limits, extra fields) or `unprocessable` with `details[].reason` `prefix_mismatch` / `wildcard_not_allowed` / `duplicate_key` / `reserved_scope` / `scope_required` / `org_mode_required` / `undeclared_permission` / `admin_permission_in_role` / `ineligible_member_permission` / `reserved_role`; 429 `rate_limited` + `Retry-After` | R3 class 4: app credential of app `{key}`; no `x-permission`; OpenAPI security `appCredential` |
