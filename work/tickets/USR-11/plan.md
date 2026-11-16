Status: READY

# Plan — USR-11: Caller's effective admin permissions endpoint for the admin UI

> **Owner confirmation needed at PR (not blocking):** this ticket adds a narrow exception to spec
> §5.3 ("Every admin `/api/v1` endpoint declares the `admin:*` permission it needs"), CLAUDE.md R3
> class (1) ("+ a declared `require_permission("admin:…")`") and R9 (`/api/v1/<plural-noun>`,
> lists return `Page[T]`). The plan records it in an ADR-0008 amendment plus short pointer edits to
> spec §5.3, CLAUDE.md R3/R9 and the decision log (D23). The spec is the source of truth, so the PR
> description must state the conflict and ask the owner to confirm the wording. If the owner refuses,
> the alternative is a seeded permission (for example `admin:self:read`) granted to every admin role,
> which needs a migration and a data dependency (rejected below).

## Spec summary
- Spec §5.3 (`docs/spec-v1.md` line 45): "One model for every mmlabs app, the admin API included
  (app `admin`). … Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A
  bootstrap super-admin (`admin:*`) exists."
- ADR-0008 §3: "Per-request reload, no permissions claim, no revocation list. … Permissions are the
  user's effective `admin` permissions read from the database on every request, so disable, role
  revoke and assignment expiry take effect on the next call."
- ADR-0008 §5: "Each admin router has `dependencies=[Depends(current_admin)]` (defence in depth);
  each route has `**require_permission(…)`." §6: "`tests/test_route_auth_classes.py` … fails on any
  route that is neither guarded by exactly one `require_permission` nor in an explicit (method, path)
  public/cookie allow-list."
- CLAUDE.md R3 class (1): "Bearer — admin endpoints under `/api/v1/*`: access token with `aud=admin`
  + a declared `require_permission("admin:…")`." R9: "`/api/v1/<plural-noun>` … lists return
  `Page[T]` … Exceptions (D18): …".
- Ticket goal: "The admin UI (`admin-pan`) can read the signed-in caller's own `user_id`, admin roles
  and effective `admin:*` permissions with its `aud=admin` Bearer token … The API keeps enforcing
  every permission; tokens stay without a permissions claim."

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/me/permissions` (outside `/api/v1/auth/*`), Bearer `aud=admin` through `current_admin`, returns `{"user_id", "roles", "permissions"}` for the caller only; `permissions` from the same logic enforcement uses (per request, sorted, `admin:*` unexpanded); no email/PII | `tests/test_me_permissions.py` › `test_returns_own_data_only`, `test_query_user_id_ignored`, `test_super_admin_wildcard_unexpanded`, `test_permissions_and_roles_sorted`, `test_exact_keys_no_pii`; code read: router uses `sorted(principal.permissions)` (the `AdminPrincipal` that `current_admin` built for this request), no second permission query |
| 2 | `Cache-Control: no-store`; no token → 401, other `aud` → 401, R6 envelope | `test_no_token_401`, `test_other_audience_401`, `test_list_audience_401` (envelope `{"error": {"code": "unauthorized", "message": "Authentication required", "details": null}}`, `WWW-Authenticate`, `cache-control: no-store`); `test_cache_control_no_store_on_200` |
| 3 | Grant/revoke for the caller visible on the next request; disabled caller same as other admin endpoints | `test_grant_visible_on_next_request`, `test_revoke_visible_on_next_request` (200, empty lists), `test_expired_assignment_dropped`, `test_disabled_caller_401`, `test_unverified_caller_401` |
| 4 | Explicit allow-list entry "Bearer, self-scoped, no permission"; no placeholder permission; ADR-0008 records the exception | `tests/test_route_auth_classes.py` › `SELF_SCOPED` set, `test_every_route_is_classified`, `test_bearer_route_permission_map`, `test_self_scoped_routes_have_no_permission`, `test_self_scoped_allow_list_is_exact`; `grep -rn "admin:self\|admin:me" app migrations` returns nothing; ADR-0008 has the "Self-scoped admin reads (USR-11)" amendment |
| 5 | Route in OpenAPI (ADM-013 envelope on 401) and in `docs/consumers.md` "Calling the admin API from the admin UI" | `tests/test_openapi_contract.py` › `test_every_error_response_references_envelope` (now asserts `/api/v1/me/permissions` in `checked`), `test_me_permissions_operation`; `test_route_auth_classes.py` › `test_openapi_bearer_scheme` (self-scoped branch); reviewer reads the consumers.md bullet |
| 6 | Tests: 401 (missing, wrong `aud`), own data only, role change on next request, `admin:*` unexpanded, allow-list entry | all tests listed under "Tests to write"; `make check` green |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/admin_auth/schemas.py` (new) | `MePermissions` response model | AC1: strict response shape |
| `app/modules/admin_auth/service.py` | add `AdminSelfView` frozen dataclass and `AdminAuthService.self_view(principal)` | R1: router stays HTTP-only; roles read through `AuthzService` |
| `app/modules/admin_auth/me_router.py` (new) | `router = APIRouter(prefix="/me", tags=["me"], dependencies=[Depends(current_admin)])` with `GET /permissions` | AC1/AC2: the endpoint |
| `app/main.py` | import `me_router` and `app.include_router(me_router, prefix=settings.api_prefix)` after `audit_router` | mount so `BearerRouteMatcher`, OpenAPI and the route sweep see it |
| `tests/test_me_permissions.py` (new) | behaviour tests | AC1–AC3, AC6, R8 |
| `tests/test_route_auth_classes.py` | add `SELF_SCOPED` allow-list, thread it through the classification, OpenAPI, CORS-matcher and runtime 401 sweeps; update module docstring | AC4, deny-by-default stays exact |
| `tests/test_openapi_contract.py` | add `/api/v1/me/permissions` to `checked` assertions; new `test_me_permissions_operation` | AC5 |
| `docs/adr/0008-admin-api-authentication.md` | new amendment "Self-scoped admin reads (USR-11, 2026-11-16)"; one-line pointer in §6 | AC4; records the exception |
| `docs/spec-v1.md` | §5.3 sentence gets "except self-scoped reads of the caller's own authorization (`GET /api/v1/me/permissions`, ADR-0008)" | spec wins; owner confirms at PR |
| `CLAUDE.md` | R3 class (1) pointer to ADR-0008 self-scoped reads; R9 exceptions sentence adds `/api/v1/me/permissions` (singular `me`, single document) via D23 | R3/R9 wording must match code; owner confirms at PR |
| `docs/identity-architecture.md` | §4 API table: one row for `GET /api/v1/me/permissions`; §8 decision log: row D23 | keeps D14/D18 log consistent |
| `docs/consumers.md` | bullet(s) in "Calling the admin API from the admin UI"; amend the "after a role change" sentence | AC5 |

No other file. In particular: no change to `app/modules/admin_auth/deps.py`, `route_classes.py`,
`app/core/cors.py`, `app/core/openapi.py`, tokens, RBAC models, or migrations.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_me_permissions.py` › `test_no_token_401` | AC2: 401 envelope `{"error": UNAUTHORIZED}`, `WWW-Authenticate: Bearer realm="admin"`, `cache-control: no-store` |
| › `test_other_audience_401` | AC2: `mint(staff, aud="stoktok")` → 401, envelope, `WWW-Authenticate: Bearer realm="admin", error="invalid_token"` |
| › `test_list_audience_401` | AC2: `mint(staff, aud=["admin"])` → 401 (strict `aud`, ADR-0008 ADM-002d amendment) |
| › `test_returns_own_data_only` | AC1: two staff A (`admin:organizations:read`) and B (`admin:users:read`); each token returns its own `user_id` and only its own permission; A's body never contains B's id or keys |
| › `test_query_user_id_ignored` | AC1/security: `GET …/me/permissions?user_id=<B.id>` with A's token returns A's data |
| › `test_exact_keys_no_pii` | AC1: `set(body) == {"user_id", "roles", "permissions"}`; staff email not in `resp.text` |
| › `test_super_admin_wildcard_unexpanded` | AC1: user assigned `super_admin_role(...)` → `roles == ["admin:super-admin"]`, `permissions == ["admin:*"]` |
| › `test_permissions_and_roles_sorted` | AC1: staff with two roles / several permission keys (inserted in non-sorted order) → both lists equal their `sorted()` |
| › `test_grant_visible_on_next_request` | AC3: same token; call, then `make_role` + `grant` + `assign` a second admin role in `db_session`, call again → new role key and permission present |
| › `test_revoke_visible_on_next_request` | AC3: same token; delete the caller's `RoleAssignment` rows (`delete(RoleAssignment).where(user_id=…)`), call again → `200 {"user_id": …, "roles": [], "permissions": []}` |
| › `test_expired_assignment_dropped` | AC3: `make_staff(..., expires_at=<past>)` → 200, empty lists |
| › `test_disabled_caller_401` / `test_unverified_caller_401` | AC3: set `status="disabled"` / `email_verified_at=None` after minting → 401 identical envelope and challenge to `GET /api/v1/organizations` with the same token |
| › `test_org_scoped_admin_assignment_ignored` | AC1: assign an admin-app role with `org_id=<new Organization>` → not in `roles`/`permissions` (matches enforcement, `org_id=None`). If the database refuses an org-scoped admin-app assignment, omit the test and record why in `impl-log.md` (the constraint then guarantees the property) |
| › `test_cache_control_no_store_on_200` | AC2: 200 response has `cache-control: no-store`, no `etag` |
| › `test_admin_cors_on_me_permissions` | CORS stays admin, non-credentialed: with `AppService(db_session).set_admin_origins(AdminOriginsSet(origins=[ADMIN_ORIGIN]), app_env="test")`, a request with `Origin: ADMIN_ORIGIN` gets `access-control-allow-origin == ADMIN_ORIGIN`, no `access-control-allow-credentials`, `Vary` contains `Origin` |
| `tests/test_route_auth_classes.py` › `test_every_route_is_classified` (modified) | AC4: a route in `SELF_SCOPED` is classified only if `_guard(dependant) == (True, [])` |
| › `test_bearer_route_permission_map` (modified) | AC4: `guarded == set(BEARER) | SELF_SCOPED`; `SELF_SCOPED` and `BEARER` disjoint |
| › `test_self_scoped_routes_have_no_permission` (new) | AC4: each `SELF_SCOPED` route has `current_admin` and zero `RequirePermission`; the operation has no `x-permission` |
| › `test_self_scoped_allow_list_is_exact` (new) | AC4/governance: `SELF_SCOPED == {("GET", f"{V1}/me/permissions")}`; every entry is `GET`, has no `{` in its path, and is not under `f"{V1}/auth/"` |
| › `test_openapi_bearer_scheme` (modified) | AC5: `SELF_SCOPED` operations have `security == [{"bearerAuth": []}]`, `"401"` in responses, `"403"` not in responses, no `x-permission`; the `else` branch excludes `BEARER` and `SELF_SCOPED` |
| › `test_cors_matcher_agrees_with_bearer_map` (modified) | iterate `list(BEARER) + list(SELF_SCOPED)` |
| › `test_every_bearer_route_401_without_token`, `test_every_bearer_route_401_with_cookie_only` (modified) | parametrize over `list(BEARER) + sorted(SELF_SCOPED)` |
| `tests/test_openapi_contract.py` › `test_every_error_response_references_envelope` (modified) | AC5: add `assert "/api/v1/me/permissions" in checked` |
| › `test_me_permissions_operation` (new) | AC5: `paths["/api/v1/me/permissions"]["get"]`: 200 schema `$ref` to `#/components/schemas/MePermissions`; responses keys == `{"200", "401"}`; 401 references `ErrorEnvelope`; no `x-permission`; `MePermissions` component has exactly the three properties, all required |

R8 note: the endpoint has no path, query or body input, so there is no 404 or 422 case and no 403
case (nothing can return 403). The reviewer should not require them; `test_query_user_id_ignored`
stands in for the "input" case.

## Approach
1. **Schema** (`app/modules/admin_auth/schemas.py`):
   ```python
   class MePermissions(BaseModel):
       """The caller's own admin authorization (USR-11, ADR-0008 amendment). A UI hint only:
       the API enforces every permission. `admin:*` is returned unexpanded and matches every
       `admin:` key (same rule as `permits()`). Additive within v1: new fields may appear."""
       model_config = ConfigDict(extra="forbid")
       user_id: uuid.UUID
       roles: list[str]
       permissions: list[str]
   ```
   Field descriptions: `roles` = "App-scope `admin` role keys, sorted"; `permissions` = "Effective
   `admin:` permission keys, sorted; `admin:*` unexpanded".
2. **Service** (`service.py`, R1): add
   ```python
   @dataclass(frozen=True)
   class AdminSelfView:
       user_id: uuid.UUID
       roles: list[str]
       permissions: list[str]

   async def self_view(self, principal: AdminPrincipal) -> AdminSelfView:
       roles = await self.authz.effective_roles(principal.user_id, ADMIN_APP_KEY)
       return AdminSelfView(principal.user_id, roles, sorted(principal.permissions))
   ```
   `permissions` is the exact set `current_admin` loaded for this request (no second permission
   query). Roles use `org_id=None`, the same scope enforcement uses. Do **not** add roles to
   `AdminPrincipal` (that would add a query to every admin request).
3. **Router** (`me_router.py`, R1/R3): module docstring "Self-scoped admin reads (R3 class 1,
   ADR-0008 USR-11 amendment): Bearer `aud=admin`, no `require_permission`, caller's own data only.
   Adding a route here needs an ADR-0008 amendment and an entry in `SELF_SCOPED`."
   ```python
   router = APIRouter(prefix="/me", tags=["me"], dependencies=[Depends(current_admin)])

   @router.get(
       "/permissions",
       response_model=MePermissions,
       responses={401: {"description": "Missing or invalid Bearer token"}},
       summary="The caller's own admin roles and effective permissions",
   )
   async def get_my_permissions(
       session: SessionDep, principal: AdminDep, response: Response
   ) -> MePermissions:
       response.headers["Cache-Control"] = "no-store"
       view = await AdminAuthService(session).self_view(principal)
       return MePermissions(user_id=view.user_id, roles=view.roles, permissions=view.permissions)
   ```
   No query/path/body parameters. Inline `responses` dict (do not import the private
   `_ERROR_RESPONSES`); `install_openapi` deep-copies and adds the envelope + `X-Request-ID`.
   No 403 entry. No `x-permission`. No logging beyond what `current_admin` already logs.
4. **Mount** in `app/main.py` after `audit_router`. Path is outside `/api/v1/auth/`, so the two CORS
   scopes stay disjoint; `BearerRouteMatcher` picks it up through `current_admin`, which also adds
   `no-store` to the 401 responses (handler never runs on 401).
5. **Deny-by-default test** (`tests/test_route_auth_classes.py`): add, below `BEARER`,
   ```python
   # Bearer, self-scoped, no permission (ADR-0008 USR-11 amendment). GET only, no path/query/body
   # input, caller's own data only. Adding an entry needs an ADR-0008 amendment (owner approval).
   SELF_SCOPED = {("GET", f"{V1}/me/permissions")}
   ```
   Classification in `test_every_route_is_classified`:
   `ok = route in PUBLIC | COOKIE or (route in SELF_SCOPED and (has_admin, keys) == (True, [])) or (route not in SELF_SCOPED and has_admin and len(keys) == 1)`.
   Exact allow-list only — no marker dependency the classifier would trust (security consult R-e).
   Update the other tests as listed above. `BEARER` itself is unchanged, so `test_audit_coverage.py`
   (non-GET `BEARER` routes) and `test_admin_cors.py` (parametrized over `BEARER`) keep working
   unmodified; CORS on the new route is covered by `test_admin_cors_on_me_permissions`.
6. **Docs.**
   - **ADR-0008 amendment** "Self-scoped admin reads (USR-11, 2026-11-16) — pending owner
     confirmation": one explicit exception to §5/§6. `GET /api/v1/me/permissions` is Bearer
     `aud=admin` through `current_admin` (§1–§4 all apply: per-request reload, 401 for disabled or
     unverified users) and declares no `admin:*` permission. A route qualifies only if all hold: GET;
     no path, query or body input; the subject is always the principal; it returns only the caller's
     own authorization state (`user_id`, app-scope admin role keys, `sorted(principal.permissions)`
     with `admin:*` unexpanded), no PII; no side effects (no audit row, R4 covers mutations only);
     `Cache-Control: no-store`; listed by exact (method, path) in `SELF_SCOPED` in
     `tests/test_route_auth_classes.py`. Adding a route needs a new amendment. Self-scoped mutations
     are out of scope. Tokens still carry no permissions claim. Response 200 with empty lists after
     a full revoke (token valid up to its TTL). Accepted risks: roles and permissions are two reads
     (a concurrent grant can make them disagree for one response; advisory data, next call
     corrects it); a token holder learns its own keys (already learnable from 403
     `details.required`); no rate limit (same as every Bearer route). Rejected alternative: a seeded
     `admin:self:read` permission (migration + every admin role must hold it, else the UI breaks).
     Add one sentence to §6: "…nor in an explicit public/cookie allow-list, or the self-scoped
     allow-list (USR-11 amendment)."
   - **Spec §5.3**: "Every admin `/api/v1` endpoint declares the `admin:*` permission it needs,
     except self-scoped reads of the caller's own authorization (`GET /api/v1/me/permissions`,
     ADR-0008)."
   - **CLAUDE.md R3** class (1): append ", except the self-scoped reads listed in ADR-0008 (USR-11:
     `GET /api/v1/me/permissions`), which declare no permission and return only the caller's own
     data". **R9** exceptions: "Exceptions (D18, D23): … ; `GET /api/v1/me/permissions` (singular
     `me`, a single document, not `Page[T]`)."
   - **identity-architecture.md** §4 table row: `GET /api/v1/me/permissions` | Bearer `aud=admin`,
     self-scoped, no permission (ADR-0008 USR-11 amendment) | caller's own `user_id`, admin role
     keys, effective admin permissions | USR-11. §8 row **D23** "Proposed, pending owner (USR-11):
     self-scoped admin read `GET /api/v1/me/permissions` — R3 class 1 without `require_permission`,
     and R9 exception (singular `me`, single document)." (Same "Proposed, pending owner" style as D22.)
   - **consumers.md**, after the 401 bullet in "Calling the admin API from the admin UI": to learn
     the signed-in staff member's own id, roles and permissions, call `GET /api/v1/me/permissions`
     with the same Bearer token (example body `{"user_id": "…", "roles": ["admin:super-admin"],
     "permissions": ["admin:*"]}`); do not decode the token for `sub`; it is a UI hint only, the API
     still enforces every call (403 `permission_denied`, and `admin_wildcard_required`,
     `self_assignment_forbidden`, `self_status_change_forbidden` cannot be inferred from the list);
     match with `has(k) = perms.includes(k) || perms.includes("admin:*")` — no other wildcard;
     unknown keys may appear (additive); empty lists mean "no admin access" (re-login); not cached
     (`no-store`), keep in memory only; fetch on session start, after each `/auth/token` refresh and
     after any 403, not on every navigation. Change "and after a role change" in the token bullet to
     say role changes show up in `GET /api/v1/me/permissions` on the next call, and that the token's
     `roles` claim may lag up to the token TTL.
7. Run `make check`.

Rules: R1 (router → service → `AuthzService`/repository), R3 (Bearer class with the recorded
exception), R4 (read-only, no audit), R5 (no config), R6 (401 envelope from `current_admin`), R7
(nothing outside this list), R8 (tests above), R9 (exception recorded as D23), R11 (no tokens or
emails logged or returned).

## Specialist input used
- **api-platform-engineer**: adopted path `/api/v1/me/permissions` (rejected `/users/me/…`,
  `/auth/me`, `/admin/me`, bare `/me`); module placement in `admin_auth`; explicit `no-store`;
  `sorted(principal.permissions)` rather than a second query; explicit 401 only, no 403; ignore
  `?user_id`; consumers.md content; R9 exception. Not adopted: optional `x-self-scoped` OpenAPI
  extension and explicit `operation_id` (R7; no existing route sets one). Not adopted: adding the
  path to `test_cli_openapi.test_dump_contains_all_routers` (covered by the contract test).
- **identity-access-engineer**: adopted the 200-with-empty-lists behaviour, roles via
  `effective_roles(..., org_id=None)`, no roles on `AdminPrincipal`, no audit row, the
  ADR/R3/§5.3 draft wording, the D23 decision-log row, the 403-codes caveat in consumers.md.
  Rejected: the `SelfScoped` marker dependency and `admin_self_scoped()` helper in `deps.py` — the
  exact allow-list with the `(True, [])` check already fails closed, and a marker is something a
  future edit could copy onto a new route.
- **security-architect**: adopted exact-tuple `SELF_SCOPED` with `(True, [])` (R-e), strict
  `extra="forbid"` schema (R-b), empty-grants 200 (R-c), explicit `no-store` and no ETag plus
  list-`aud` 401 test (R-d), ADR qualification criteria (R-f), no audit and no permission-list
  logging (R-g), no rate limit (R-h), consumers.md guidance (R-i). Rate limit for Bearer routes goes
  to follow-ups.

## Assumptions
1. **Exception, not placeholder permission.** The route is "Bearer, self-scoped, no permission",
   recorded in ADR-0008 and the `SELF_SCOPED` allow-list. No `admin:self:read`-style permission is
   added. The owner confirms the wording at PR (see banner).
2. **Path** `/api/v1/me/permissions` (as in the ticket); no bare `GET /api/v1/me` now.
3. **Empty grants → 200** with `roles: []`, `permissions: []` (valid token after a full revoke or
   expiry), not 403.
4. **`roles` = app-scope `admin` role keys only** (sorted), no names, descriptions, org scope or
   assignment ids. Org-scoped admin-app assignments are ignored, matching enforcement.
5. **No `expires_at`** or other fields in v1; the response is additive-only within v1.
6. **No rate limit** on this route (no Bearer route has one); follow-up for a per-`sub` admin limit.
7. **Disabled/unverified caller → 401** exactly as `current_admin` gives on every admin route; no
   special case.
8. Roles and permissions are two reads in one request; a one-response disagreement under a
   concurrent change is accepted (recorded in the ADR).
9. Spec §5.3 / CLAUDE.md R3, R9 edits ship in this PR, flagged "needs owner's confirmation at PR".
   If the owner rejects them, the code change must be reverted or redesigned in a follow-up ticket.

## Blocking questions
None.

## Split proposal
None. Estimated diff ~350–400 lines (about 80 app code, ~230 tests, ~70 docs).

## Data model / migration
None. No schema change; `alembic check` unaffected.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/me/permissions` | none (no path, query or body; unknown query params ignored) | `200 MePermissions {"user_id": uuid, "roles": [str], "permissions": [str]}` — both lists sorted, `admin:*` unexpanded; headers `Cache-Control: no-store`, `Vary: Origin`, `X-Request-ID` | 200; 401 `unauthorized` (missing/invalid token, other or list `aud`, disabled/unverified/deleted user) | none — Bearer `aud=admin` via `current_admin`, self-scoped exception (ADR-0008 USR-11 amendment) |

## Follow-ups (not in this diff)
- Per-`sub` rate limit for Bearer admin routes (security consult R-h).
- `admin-pan` (PAN-011b) switches UI gating to this endpoint.
