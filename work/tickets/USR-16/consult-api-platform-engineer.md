USR-16 consult (api-platform-engineer, read-only). Based on app/modules/rbac/{router,catalog_router,schemas,service,repository}.py, app/core/pagination.py, app/modules/admin_auth/deps.py, tests/test_route_auth_classes.py and docs/identity-architecture.md §4 and D17.

FINDINGS
1. The collision is real. The same path `/api/v1/apps/{key}/roles` already carries the public GET (catalog_router.py, rate-limited, ETag, 404 for admin and disabled apps) and the admin writes (POST there; PATCH, DELETE and PUT under `/roles/{role_id}`). FastAPI would dispatch a second GET to whichever router is included first, so an admin GET there is not possible. The two routers also differ in R3 class, which tests/test_route_auth_classes.py enforces per (method, path).
2. `RoleRead` (id, key, name, is_system, permissions) is what the role writes return. The new item type must be a superset of it, so admin-pan can reuse its type after a PATCH or PUT.
3. The permission model:
   - `GET /apps` (list), `GET /apps/{key}` and `GET /apps/{key}/permissions` use `admin:apps:read`.
   - All role and permission writes use `admin:roles:manage`.
   - `admin:roles:assign` is for assignments.
   - There is no `admin:roles:read` key.
4. Page[T] is `{items, total, limit, offset}`. `PageDep` gives limit 1..100 (default 20) and offset >= 0. Existing list endpoints such as `list_apps` use the `repo.list_*` -> `(items, total)` -> router builds `Page` pattern.
5. `AppService.member_counts` / `repo.member_counts` is the existing batched, grouped-count pattern. It uses the OQ5 definition: active users and non-expired assignments, with the expiry test `expires_at IS NULL OR expires_at > func.now()`. A per-role `assignment_count` can copy this shape.
6. Existing `RoleService._read` does one query per role (`role_permission_keys`). Do not reuse it for a list, because it is N+1.
7. `RolePermission` has an `app_id` column, and `Permission` has `is_admin`. That makes `privileged` computable in one grouped query: `bool_or(Permission.is_admin)` grouped by role_id, OR `app.key == ADMIN_APP_KEY`.

RECOMMENDATIONS
A. Path (AC1). Use `GET /api/v1/apps/{key}/role-definitions`, as the ticket suggests.
   - It is unambiguous and disjoint from `/roles`.
   - It reads consistently with the sibling `/permissions` collection.
   - R9 says plural noun, and "role-definitions" is plural.
   - Keep `{key}` as the app key, per the D18 exception that already covers `/apps/{key}/…`.
   - Record at PLAN that this is a staff read model of role rows and not the public catalog, and add a one-line note in docs/identity-architecture.md §4 next to the line 174 row (and D17 as a cross-reference).
   - Put the route in rbac/router.py (the Bearer router), not catalog_router.py. Put a comment on the public route saying the admin variant lives under `/role-definitions`.
   - Do not add an R9 exception: no new exception is needed.
   - Rejected alternatives:
     - A query flag on `/roles`: it mixes auth classes on one path.
     - `/admin/roles`: it breaks R9.
     - A global `/api/v1/roles?app=`: it breaks the D18 addressing.
B. Detail endpoint. Add `GET /apps/{key}/role-definitions/{role_id}`.
   - It is cheap: the same item builder with a single-row filter.
   - It gives admin-pan a refresh after writes without refetching the page.
   - It is symmetrical with `GET /apps/{key}` (single document, 404 "Role not found" via the existing `RoleService._get`, also 404 for a role that belongs to another app).
   - R9 is satisfied by the `{role_id}` UUID. Rather than an R9 exception, just state in the plan that it is the singular-resource form of the collection.
   - Add `("GET", .../role-definitions/{role_id})` to the route-class table.
   - If the planner wants a smaller diff, skip it. It is the only optional part where cost is not trivial, because of the extra test matrix (404 unknown role, role of another app).
C. Permission (AC1). Choose `admin:apps:read`.
   - Reasons: it is the read key already used for every sibling read in this family (apps, permissions, credentials). There is no `admin:roles:read`, and inventing one is an RBAC catalogue change plus a migration/seed and an admin-role update.
   - `admin:roles:manage` as a read gate would conflate read with write and would prevent read-only staff from seeing roles.
   - The data exposed (role names and permission keys) is already readable through `/permissions` and the public catalog. The admin and disabled apps are the only additions, and they are covered by the same `admin:apps:read` that already lets staff `GET /apps/admin`.
   - Declare it via `**require_permission("admin:apps:read")`. This sets `openapi_extra["x-permission"]`, and the 401 and 403 responses come from the helper. Add the (method, path) -> "admin:apps:read" entries to tests/test_route_auth_classes.py (the expected-permission dict near lines 85-105).
   - Risk note: a staff member with `admin:apps:read` but not `admin:roles:manage` can now see admin-app role definitions, including `privileged`. This is consistent with `GET /apps/admin` but flag it for the security consult.
D. Page[T] shape and ordering (AC2).
   - Return `Page[RoleDefinitionRead]` with `PageDep`.
   - Order by `Role.key ASC, Role.id ASC`. The `id` tie-breaker is required by the ticket, and since `(app_id, key)` is unique it is only a belt-and-braces guarantee.
   - Key ordering matches the public catalog's "sorted by key", so admin-pan shows the same order.
   - Total comes from a `count(*)` over `Role.app_id == app.id`.
   - Lookup order in the service: first resolve the app by key (404 `NotFoundError("App not found")`, using whatever helper `list_permissions` uses at service.py ~810), with no status or admin filter. Then page the roles, then do batched lookups for the page's role ids.
E. Item fields (AC2). New schema `RoleDefinitionRead` in rbac/schemas.py (not a subclass of RoleRead, so response_model and OpenAPI stay explicit):
   - `id: UUID`
   - `app: str` (app key; mirrors `AssignmentRead.app`)
   - `key`, `name`
   - `is_system: bool`
   - `is_default: bool` (`role.id == app.default_role_id`; false when the default is NULL)
   - `permissions: list[str]` (sorted keys)
   - `privileged: bool`
   - `created_at`, `updated_at: datetime`
   - Optional `assignment_count: int = Field(ge=0)`.
   - Verify that `Role` has `created_at` and `updated_at` columns in the model (I did not open models.py). If `updated_at` does not exist or is not bumped when permissions are replaced, flag it rather than adding a migration. The ticket requires both.
   - Describe the fields in `Field(description=...)`, especially `privileged` ("app is `admin`, or any held permission has `is_admin`") and `is_default`.
   - All `permissions` keys are strings, never ids, matching RoleRead and the catalog.
F. `assignment_count` (optional). Include it.
   - It is nearly free once the batch pattern exists: one grouped query `select(RoleAssignment.role_id, count(RoleAssignment.id)).where(role_id IN page_ids, expires_at IS NULL OR expires_at > now()).group_by(role_id)`.
   - Default missing ids to 0, as `member_counts` callers do.
   - Define it precisely: count of active non-expired assignments (rows, app scope plus every org), not distinct users, and do not join `User.status`. That differs from `member_count`, so say so in the description to avoid confusion. If the planner wants it to equal "users", say so explicitly instead.
   - Reuse the expiry predicate, ideally factored with the one in `member_counts` rather than copy-pasted.
   - Value for admin-pan: the delete and permission-replace guards return 409 when the role has assignments, so the count lets the UI pre-disable those actions.
   - If it is included, it belongs on the list and detail both (same builder). If it is skipped, adding it later is an additive, safe change.
G. No N+1 (AC4). Three fixed queries per page whatever the page size: roles page (+ count), `(role_id, permission_key, is_admin)` for the page ids in one query, and the assignment counts. Then assemble in Python, sort keys with `sorted`, and compute `privileged = app.key == ADMIN_APP_KEY or any(is_admin)`. Add these as new repo methods (R1: SQLAlchemy only in the repository; the service builds the schema; the router stays HTTP-only, like `list_apps`). Add a test that asserts a bounded query count if the test infrastructure supports it. Otherwise, a size-N page with a mixed set is enough.
H. Rate limit. Do not attach `rate_limit(...)`, as in the other Bearer admin reads. Do not touch the public router.
I. Tests (AC5), in addition to the route-class table entries:
   - 401, 403 (token without the permission).
   - 404 unknown key.
   - `admin` app listed (all `privileged=true`).
   - Disabled app listed (200 with items).
   - Paging: limit/offset/total, `limit=101` gives 422, stable order with ties.
   - `is_default` true for exactly one role, and false for all when the default is NULL.
   - `privileged` true for a role holding an `is_admin` permission in a normal app.
   - `assignment_count` ignores expired assignments.
   - A new OpenAPI contract assertion in tests/test_openapi_contract.py (the pattern used for `/me/permissions`): the 200 schema `$ref` equals `#/components/schemas/Page_RoleDefinitionRead_`, the `x-permission` value, and the 401/403/404 responses share the error envelope.
   - The existing test at line 56 asserts the public catalog path is in `checked`: leave it alone, and add a negative assertion that `/roles` GET still has no `x-permission`.
   - Docs: update docs/identity-architecture.md §4 table and the admin-pan consumer doc (docs/consumers.md) if it lists endpoints.

RISKS
- Splitting the catalog and the staff list means two role views that can drift. Both should come from the same `roles` and `role_permissions` tables. A test asserting that the catalog's permission set for an enabled non-admin app equals the staff list's `permissions` will catch drift.
- Naming risk: "role-definitions" is a new term. If the owner prefers the sibling shape `/apps/{key}/roles/list`, that is worse. Do not use it.
- Response size: a role can hold up to 200 permissions and a page up to 100 roles. This is bounded and acceptable, but note that the default limit of 20 is the default from `page_params`.
- Information exposure of the `admin` app's role and permission names to any `admin:apps:read` holder. This is consistent with the existing `GET /apps/admin` and `/apps/admin/permissions`, but should be confirmed by security (the ticket has the `security` area).
- Once published, `privileged` semantics are frozen by the consumer (admin-pan PAN-015b/c). Keep the definition exactly as in AC2.
- Adding the optional `assignment_count` or fields later is additive, but removing one is breaking inside v1 (R9), so decide now.

OPEN QUESTIONS
1. Permission: confirm `admin:apps:read` (recommended) over `admin:roles:manage`; no new permission key.
2. Include the detail endpoint and `assignment_count`? Recommended: yes to both. The planner may drop the detail endpoint to save diff.
3. `assignment_count`: assignment rows (recommended) or distinct active users?
4. Do `Role.created_at` and `Role.updated_at` exist, and is `updated_at` bumped on permission replace? If not, is a no-migration fallback acceptable (it would need to be a ticket decision, since R2 forces a migration)?
5. Should `is_default` also expose an `is_org_owner` flag (`apps.org_owner_role_id`)? It is not in the ticket, so R7 says it goes to follow-ups. Mention it to admin-pan.
