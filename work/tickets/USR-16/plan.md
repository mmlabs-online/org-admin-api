Status: READY

# Plan — USR-16: Staff role listing with ids (admin Bearer), including the admin app and disabled apps

## Spec summary
- spec §5.3: "One model for every mmlabs app, the admin API included (app `admin`). Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, …"
- spec §5.3: "The role catalog `GET /api/v1/apps/{key}/roles` is public (D17)". This ticket leaves the catalog unchanged.
- spec §5.8 Admin: "staff can list, look up, disable/enable users, manage their roles per app …"
- spec §5.9 context: "the admin UI **`admin-pan`** … is a browser SPA that calls this API with Bearer `aud=admin` tokens. … Field names are **snake_case**".
- `docs/identity-architecture.md` D17 says the public catalog returns 404 for unknown, disabled and `admin` apps and exposes no ids. D18 lets apps be addressed by their immutable `key` in `/api/v1/apps/{key}/…`; roles keep UUID ids.

## Planner decisions (within the ticket's stated latitude)
These are the planner's calls, as the ticket allows ("chosen at PLAN", "planner decides"). All three consults agree on D1 to D3.

| # | Decision | Reasoning |
|---|---|---|
| D1 | **Path:** `GET /api/v1/apps/{key}/role-definitions` (list) and `GET /api/v1/apps/{key}/role-definitions/{role_id}` (detail). | `/apps/{key}/roles` GET is already the public catalog. `catalog_router` is mounted before `rbac_router` (`app/main.py:108-110`), so a second GET there would be shadowed, and the CORS matcher would classify it as public. `role-definitions` is a plural noun, returns `Page[T]` and is addressed under the existing D18 `apps/{key}/…` exception. **No new R9 exception** (the ticket's Notes ask that this be recorded). The detail route is the usual `{id}` singular form of the collection. USR-17 will nest `…/role-definitions/{role_id}/assignments` under the same noun. |
| D2 | **Permission:** `admin:apps:read` on both routes. | Every RBAC-config read already uses it: `GET /apps`, `GET /apps/{key}`, `GET /apps/{key}/permissions` (which already exposes admin-app permissions with `is_admin`), and the credentials list. Holders of `admin:roles:assign` need role ids for `POST /users/{id}/role-assignments`. Gating the read on `admin:roles:manage` would push owners to grant write power just so staff can read. There is no `admin:roles:read`, and adding one means a seed migration (out of scope). |
| D3 | **No `admin:*` read gate on the `admin` app.** | ADR-0008's ADM-002b guards (G1/G2/G4) cover writes and grants only. No read is gated on "this is the admin app" today, and `/apps/admin/permissions` is readable with `admin:apps:read`. Knowing a role id grants nothing. Accepted residual risk (security consult R-c): a stolen `admin:apps:read` token shows which admin roles hold `admin:*`. |
| D4 | **Detail endpoint: included.** | It reuses the same builder with a one-row filter. admin-pan can refresh one role after a PATCH or PUT, which return `RoleRead` without flags. It also fixes the `{role_id}` path that USR-17 nests under. The extra cost is three tests. |
| D5 | **`assignment_count`: not included** (follow-up). | The ticket defines it as "active (non-expired) assignments". The server's 409 guards count **any** assignment, expired ones included: `RoleService.delete` → `repo.role_has_assignments` (`service.py:1141`, `repository.py:188`), and G3 (`service.py:1227`). A non-expired count would show 0 on roles whose delete still returns 409, which is exactly the UI pre-disable use the API consult cited. Fields cannot be removed inside v1 (R9), and adding one later is additive. USR-17 (holders of a role, `active` filter, `Page.total`) gives admin-pan the count with explicit semantics. |
| D6 | **`privileged` uses the `_guard_privileged` rule.** | `app.key == ADMIN_APP_KEY or role holds any is_admin permission` (`service.py:1446-1455`). It is computed through one small pure helper in `service.py`. The guard itself is not refactored (R7). Drift is caught by a cross-check test against the real guard (see Tests). |
| D7 | **No audit entry, no rate limit.** | R4 covers mutations only; spec OQ4 / ADM-015 say reads are not audited. Failed auth is already logged by `admin_auth/deps.py`. AC1 says the endpoint is not rate-limited like the catalog, and no Bearer admin route uses `rate_limit` today. |

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | New Bearer admin endpoint on a path that does not collide with `GET /apps/{key}/roles`. It declares one `admin:*` permission in `x-permission` and is not rate-limited. | `tests/test_route_auth_classes.py`: `test_every_route_is_classified`, `test_bearer_route_permission_map` (new `BEARER` entries → `admin:apps:read`), `test_cors_matcher_agrees_with_bearer_map`, the parametrized 401 sweeps. `tests/test_admin_cors.py::test_no_allow_credentials_on_any_bearer_route` picks up the new entries automatically. Code check: there is no `rate_limit(` on the new routes, and `catalog_router.py` is unchanged in the diff. |
| 2 | Returns `Page[T]` (limit ≤ 100, offset, stable order with `id` tie-breaker). Each item has `id, app, key, name, is_system, is_default, permissions` (sorted), `privileged, created_at, updated_at`. | `test_role_definitions_item_shape`, `test_role_definitions_paging_and_order`, `test_role_definitions_limit_over_100_422`, `test_role_definitions_is_default`, `test_role_definitions_privileged_flags`, `test_role_definitions_bounded_queries`. Code check: `order_by(Role.key, Role.id)`. |
| 3 | Lists roles of the `admin` app and of disabled apps; 404 only for an unknown app key. | `test_role_definitions_admin_app_listed_with_leaf_permission`, `test_role_definitions_disabled_app_listed`, `test_role_definitions_unknown_app_404`, `test_role_definitions_malformed_key_404`. |
| 4 | Optional: `assignment_count`; optional detail `GET …/{role_id}`. | Detail is included (D4): `test_role_definition_detail`, `test_role_definition_detail_404_unknown_role_and_other_app`, `test_role_definition_detail_bad_uuid_422`. `assignment_count` is deliberately not included (D5); reviewer checks it is absent from the schema (`test_role_definitions_item_shape` asserts the exact key set). |
| 5 | Tests: 401, 403, 404 unknown app, admin app listed, disabled app listed, paging, `is_default` and `privileged` flags; OpenAPI shows the new schema. | All tests below, plus `tests/test_openapi_contract.py::test_role_definitions_operations`. `make check` is green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `RoleDefinitionRead`. | New response item (AC2). It is a separate model so `RoleRead` (returned by the write routes) is unchanged. |
| `app/modules/rbac/repository.py` | Add `page_roles(app_id, *, limit, offset) -> tuple[list[Role], int]` and `role_permission_rows(role_ids) -> list[tuple[uuid.UUID, str, bool]]`. | R1: SQLAlchemy only in the repository. Batched queries avoid N+1 (AC4 spirit). |
| `app/modules/rbac/service.py` | Add the pure helper `role_is_privileged(app_key: str, holds_admin_permission: bool) -> bool`. Add `RoleService.list_definitions(key, *, limit, offset) -> tuple[list[RoleDefinitionRead], int]`, `RoleService.get_definition(key, role_id) -> RoleDefinitionRead`, and the private builder `_definitions(app, roles)`. | Business assembly (flags, sorting) and `NotFoundError` belong in the service (R1). |
| `app/modules/rbac/router.py` | Two GET routes on the existing Bearer `router` with `**require_permission("admin:apps:read")`. | AC1, AC4 (detail). This router carries `dependencies=[Depends(current_admin)]` (R3 class 1, ADR-0011 admin CORS). |
| `tests/test_rbac_role_definitions_api.py` (new) | API tests listed below. | R8, AC5. |
| `tests/test_route_auth_classes.py` | Add 2 entries to `BEARER`: `("GET", f"{V1}/apps/{{key}}/role-definitions")` and `("GET", f"{V1}/apps/{{key}}/role-definitions/{{role_id}}")` → `"admin:apps:read"`. | Deny-by-default classification test (R3). |
| `tests/test_openapi_contract.py` | Add `test_role_definitions_operations`. Add `assert "/api/v1/apps/{key}/role-definitions" in checked` to `test_every_error_response_references_envelope`. | AC5: "OpenAPI shows the new schema". |
| `docs/identity-architecture.md` | §4 table: add a row after line 174 for `GET /api/v1/apps/{key}/role-definitions[/{role_id}]`. It says: Bearer `aud=admin` + `admin:apps:read`; staff read model with ids, `admin` and disabled apps included, no rate limit; USR-16. Append one sentence to D18: "`/apps/{key}/role-definitions` (USR-16) is the staff `Page[T]` read of roles with ids, distinct from the public catalog; no new exception." | Records the D1/D2/D3 decisions where the route table lives (the ticket's Notes). |

No other files. `catalog_router.py`, `RoleCatalogService`, `RoleCatalogEntry`, `RoleCatalog`, `catalog_rows` and `_guard_privileged` must **not** change (security consult rec. 3, R7).

## Tests to write
All tests are in `tests/test_rbac_role_definitions_api.py` unless noted. Use `make_client(admin_permissions=[…])` for the principal (as in `tests/test_rbac_roles_api.py`), and factories from `tests/rbac_factories.py`. The seeded `admin` app and the `admin:super-admin` role come from migrations.

| Test (file › name) | Covers |
|---|---|
| `test_role_definitions_item_shape` | 200 `Page`. The item key set equals exactly `{id, app, key, name, is_system, is_default, privileged, permissions, created_at, updated_at}` (no `assignment_count`). `app == "stoktok"`. `permissions` are sorted keys (grant out of order, e.g. c, a). Timestamps are present. |
| `test_role_definitions_paging_and_order` | 5 roles. `limit=2` at offsets 0/2/4 → `total == 5`; the concatenated keys equal `sorted(keys)` with no duplicates. Response `limit`/`offset` echo the request. The default limit is 20. |
| `test_role_definitions_limit_over_100_422` | `limit=101` → 422 envelope; `offset=-1` → 422. |
| `test_role_definitions_is_default` | The app's `default_role_id` set to role A → exactly A has `is_default=true`. With `default_role_id` NULL → all false. |
| `test_role_definitions_privileged_flags` | Normal app: a role holding an `is_admin=True` permission → true; a role holding only non-admin permissions → false; an empty role → false. `admin` app: a new empty role `admin:empty` → true, and `admin:super-admin` → true. |
| `test_privileged_flag_matches_assignment_guard` | Cross-check against `_guard_privileged` (D6). As a principal with only `admin:roles:assign`, `POST /api/v1/users/{id}/role-assignments` for a role listed `privileged=true` → 403 `admin_wildcard_required`; for a role listed `false` → 201. Reuse the assignee setup from `tests/test_rbac_assignments_api.py`. |
| `test_role_definitions_admin_app_listed_with_leaf_permission` | A principal with **only** `admin:apps:read` → `GET /apps/admin/role-definitions` 200, contains `admin:super-admin`, and every item is `privileged=true` (locks D3). |
| `test_role_definitions_disabled_app_listed` | `make_app(..., status="disabled")` with a role → 200 with the role. |
| `test_role_definitions_unknown_app_404` | Authorised principal, unknown key → 404 `not_found` envelope. |
| `test_role_definitions_malformed_key_404` | Key `BAD_KEY!` (or `a`) → 404, not 422 (matches `AppService.get`). |
| `test_role_definitions_403_without_permission` | A principal with `["admin:roles:assign"]` → 403, `details.required == "admin:apps:read"`, on an existing app **and** on an unknown key (403 before 404). The same principal plus `admin:apps:read` → 200. `["admin:*"]` → 200. |
| `test_role_definitions_401_before_404` | No token on an unknown key → 401 (an explicit case; the route sweep in `test_route_auth_classes.py` also covers it). |
| `test_role_definition_detail` | `GET …/role-definitions/{id}` → 200 with the same object as that role's list item (equality of dicts). |
| `test_role_definition_detail_404_unknown_role_and_other_app` | Random UUID → 404. A role id of app B requested under app A → 404. Unknown app → 404. |
| `test_role_definition_detail_bad_uuid_422` | `…/role-definitions/not-a-uuid` → 422 envelope. |
| `test_role_definitions_bounded_queries` | Using the `_capture_sql` pattern from `tests/test_rbac_roles_api.py` (copy it locally), call the service or endpoint for a page of 1 role and a page of 6 roles (each with permissions). The number of SQL statements is equal for both (no N+1). |
| `test_catalog_unchanged_and_consistent` | Regression and drift check. Public `GET /apps/admin/roles` and `GET /apps/<disabled>/roles` → 404. For an active app, the catalog body has no `id`, and for each role, catalog `permissions` == role-definitions `permissions`. |
| `tests/test_openapi_contract.py › test_role_definitions_operations` | List 200 schema `$ref` is the `Page[RoleDefinitionRead]` component (FastAPI names it `Page_RoleDefinitionRead_`; the implementer confirms the exact name). Detail 200 `$ref` is `#/components/schemas/RoleDefinitionRead`. Both have `x-permission == "admin:apps:read"` and the responses `401`, `403`, `404` reference `ErrorEnvelope`. The component's `required` covers all 10 fields. |
| `tests/test_route_auth_classes.py` (existing tests, new `BEARER` entries) | Classification, permission map, CORS matcher, 401 sweeps (no token, cookie only, app credential). |

## Approach
1. **Schema** (`schemas.py`). Add `RoleDefinitionRead(BaseModel)` with these fields in order:
   - `id: uuid.UUID`
   - `app: str` (app key, like `AssignmentRead.app`)
   - `key: str`, `name: str`, `is_system: bool`
   - `is_default: bool`: `Field(description="This role is the app's default role (apps.default_role_id).")`
   - `privileged: bool`: `Field(description="App is `admin`, or the role holds a permission with is_admin. Granting or revoking it needs admin:* (ADR-0008 G4). UI hint; the server enforces on every write.")`
   - `permissions: list[str]` (sorted permission keys)
   - `created_at: datetime`, `updated_at: datetime`. `updated_at` description: "Last change to the role row (e.g. name); permission-set changes do not bump it."

   It does not subclass `RoleRead`. Its fields are a superset of `RoleRead`'s.
2. **Repository** (`repository.py`). These are the only SQLAlchemy changes (R1).
   - `page_roles`: `count(*)` over `Role.app_id == app_id`, then `select(Role).where(Role.app_id == app_id).order_by(Role.key, Role.id).limit().offset()`. Same shape as `page_permissions`.
   - `role_permission_rows(role_ids: Sequence[uuid.UUID])`: returns `[]` when the input is empty. Otherwise one query: `select(RolePermission.role_id, Permission.key, Permission.is_admin).join(Permission, Permission.id == RolePermission.permission_id).where(RolePermission.role_id.in_(role_ids))`. It is unordered; the service sorts.
   - Detail reuses the existing `get_role(app_id, role_id)`. It is already scoped by app, so another app's role gives `None` → 404.
3. **Service** (`service.py`).
   - Module-level `role_is_privileged(app_key, holds_admin_permission) -> bool`, returning `app_key == ADMIN_APP_KEY or holds_admin_permission`. Its docstring says it mirrors `AssignmentService._guard_privileged` (ADR-0008 G4) and that both must change together.
   - `RoleService._definitions(app, roles)`: one `role_permission_rows` call. Group keys and an `any_admin` flag per role id in Python. Build `RoleDefinitionRead(..., app=app.key, is_default=(role.id == app.default_role_id), privileged=role_is_privileged(app.key, any_admin), permissions=sorted(keys))`.
   - `list_definitions`: `app = await self.repo.get_app_by_key(key)` with **no** status or admin filter (unlike `RoleCatalogService`); `None` → `NotFoundError("App not found")`. Then `page_roles`, then `_definitions`.
   - `get_definition`: same app lookup, then the existing `self._get(app, role_id)` (404 "Role not found"), then `_definitions(app, [role])[0]`.
   - No locking (read-only; `_locked_app` is for writers). No audit (D7).
4. **Router** (`router.py`). In the `# --- roles ---` section, add:
   - `@router.get("/{key}/role-definitions", response_model=Page[RoleDefinitionRead], **require_permission("admin:apps:read"))`, with `(session: SessionDep, key: str, page: PageDep)`. It calls the service and builds `Page(items, total, limit=page.limit, offset=page.offset)`.
   - `@router.get("/{key}/role-definitions/{role_id}", response_model=RoleDefinitionRead, **require_permission("admin:apps:read"))`, with `role_id: uuid.UUID`.

   HTTP only (R1). No `rate_limit` (D7). The `current_admin` and `RequirePermission` dependencies run before the handler, so 401 and 403 come before the 404 lookup (security F3).
5. **Tests** as listed. Update `BEARER` and the OpenAPI contract test.
6. **Docs**: the §4 row and the D18 sentence in `docs/identity-architecture.md`.
7. Run `make check`.

Rules:
- **R1**: layering as above.
- **R2**: no schema change, no migration.
- **R3**: class 1 Bearer + `admin:apps:read`, classified by test.
- **R4**: read-only, no audit.
- **R5**: no config.
- **R6**: errors via `NotFoundError` and the existing 401/403/422 envelope.
- **R7**: only the listed files; `_guard_privileged` and the catalog are untouched.
- **R8**: happy path, 404, 422, 401, 403 tests.
- **R9**: plural noun, `Page[T]` with `limit ≤ 100`, UUID ids, `timestamptz`, key addressing under D18. Additive only: no existing response changes.
- **R10/R11**: not affected.

## Specialist input used
- **api-platform-engineer**
  - Adopted: A (path), B (detail; D4), C (`admin:apps:read`), D (ordering `key, id`; app lookup without filters), E (new schema with field descriptions), G (fixed query count; the bounded-query test uses the existing `_capture_sql` pattern), H (no rate limit), I (test list, OpenAPI contract test, catalog-drift test).
  - Rejected: F (`assignment_count`), because its "non-expired" definition contradicts the 409 guards that count expired rows too (D5). OQ5 (`is_org_owner`) is out of ticket scope (R7) and recorded as a follow-up.
  - OQ4 is answered: `Role` has `TimestampMixin` (`app/db/base.py:25-31`); `updated_at` is not bumped by permission replace, which is documented in the field description.
- **identity-access-engineer**
  - Adopted: R-1 (`admin:apps:read`), R-2 (no `admin:*` read gate; D3), R-4 (path on `rbac_router`; classification test through `BEARER`), R-5 (new schema; `RoleRead` untouched), R-6 (no audit, no rate limit), R-7 (403 with `details.required`, leaf-only principal sees the admin app, `admin:*` passes).
  - R-3 is adopted in its fallback form. A shared pure helper is used for the read, and a cross-check test runs against the real guard, rather than refactoring `_guard_privileged` (R7). The single-query `array_agg` variant is replaced by one batched `(role_id, key, is_admin)` query, which gives the same N+1 guarantee with simpler typing.
  - Their suggestion of a formal ADR-0008 note is downgraded to the §4 row plus the D18 sentence. This adds no new rule: leaf-permission reads of admin-app config already exist (`/apps/admin/permissions`).
  - Updating the seeded description of `admin:apps:read` is a follow-up (it would need a data migration).
- **security-architect**
  - Adopted: rec. 1 (permission), 2 (route placement, `BEARER` entry; CORS covered by the parametrized `test_no_allow_credentials_on_any_bearer_route` and `test_cors_matcher_agrees_with_bearer_map`), 3 (catalog untouched, plus the regression test), 4 (`privileged` mirrors the guard, plus the cross-check test), 5 (sorted keys, `(key, id)` order), 7 (no audit and no rate limit stated in D7), 8 (error order 401 → 403 → 404, malformed key 404, bad UUID 422, other app's role 404).
  - Rec. 6 (`assignment_count` filtering active users) does not apply (D5).
  - R-d is acknowledged: disabled-app roles were already writable by id, so nothing new.

## Assumptions
- A1. The seeded `admin` app and `admin:super-admin` role exist in the test DB (committed by migrations, as `tests/rbac_factories.py::super_admin_role` relies on).
- A2. `make_app(session, key, status="disabled")` works, because `make_app` forwards `**kwargs` to `App`.
- A3. The `admin` app's `default_role_id` may be NULL. Tests on the admin app assert `privileged` and the presence of `admin:super-admin`, not `is_default` or exact totals (the seed may hold more roles).
- A4. The Page component name in OpenAPI follows the existing `Page[AppRead]` naming. The implementer copies whatever FastAPI emits for that existing component.
- A5. Consumers treat `privileged` as advisory. The server keeps enforcing G1/G2/G4 on every write or grant.

## Blocking questions
None.

## Split proposal
None. Estimated diff is about 150 lines of source and about 250 lines of tests, with no migration and one spec feature.

## Follow-ups (not in this diff)
- `assignment_count` per role. Only with a definition the owner picks (all rows, matching the 409 guards, or non-expired), or rely on USR-17's `Page.total`.
- `is_org_owner` flag (`apps.org_owner_role_id == id`) on `RoleDefinitionRead`. This is additive; ask admin-pan.
- Seeded description of `admin:apps:read` → "Read apps, their permissions and roles" (data migration).
- Optionally bump `roles.updated_at` when the permission set is replaced.
- Optionally refactor `_guard_privileged` to call `role_is_privileged` (removes the duplicated rule; the cross-check test covers it meanwhile).

## Data model / migration
None. Reads the existing `apps`, `roles`, `role_permissions` and `permissions` tables. Existing indexes are sufficient: `uq_roles_app_id_key` covers `app_id` + `key` ordering, and the `pk_role_permissions` (role_id, …) prefix covers the `role_id IN (…)` lookup.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/role-definitions` | path `key` (app key, any status, `admin` included); query `limit` 1..100 (default 20), `offset` ≥ 0 | `Page[RoleDefinitionRead]` = `{items, total, limit, offset}`, items ordered by `key ASC, id ASC` | 200; 401 (no or invalid Bearer); 403 (`details.required = "admin:apps:read"`); 404 unknown app key (also malformed); 422 bad `limit`/`offset` | `admin:apps:read` (`x-permission`) |
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}` | path `key`, `role_id: UUID` | `RoleDefinitionRead` | 200; 401; 403; 404 unknown app, unknown role, or role of another app; 422 malformed UUID | `admin:apps:read` (`x-permission`) |

`RoleDefinitionRead`:
```json
{
  "id": "uuid",
  "app": "stoktok",
  "key": "stoktok:viewer",
  "name": "Viewer",
  "is_system": false,
  "is_default": false,
  "privileged": false,
  "permissions": ["stoktok:a:read", "stoktok:b:read"],
  "created_at": "2026-11-18T10:00:00Z",
  "updated_at": "2026-11-18T10:00:00Z"
}
```
- `is_default` = `apps.default_role_id == id` (false when NULL).
- `privileged` = app key is `admin`, or any held permission has `is_admin`.
- `permissions` = held permission keys, sorted.

No CORS change: the Bearer admin CORS from `apps[admin].origins` applies automatically, without credentials (ADR-0011). No rate limit, no audit. The public `GET /api/v1/apps/{key}/roles` is unchanged.
