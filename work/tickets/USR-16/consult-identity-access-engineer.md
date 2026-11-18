## USR-16 consult: identity-access-engineer (read-only)

### Findings

1. **How admin auth works today** (`/home/user/org-admin-api/app/modules/admin_auth/deps.py`, `service.py`)
   - `current_admin` reads the Bearer token from the header only. It verifies `aud=admin` and `iss`, then reloads the user on every request (active, email verified) and loads their effective `admin` app permissions into `AdminPrincipal.permissions`. `admin:*` stays unexpanded.
   - `require_permission(key)` returns route kwargs that do three things:
     - enforce the key through `RequirePermission` and `permits()` (exact match or `admin:*`);
     - add `openapi_extra={"x-permission": key}`;
     - add 401/403 responses.
   - The key must match `^admin:(res:action|\*)$`. The router-level `dependencies=[Depends(current_admin)]` is what marks a route as Bearer, both for the admin CORS matcher (`route_classes.py`, `BearerRouteMatcher`) and for the route-classification test.
   - `app/api/deps.py` only holds `SessionDep` and `PageDep`, so no auth logic lives there.

2. **The rules in use: reads take a leaf permission, privileged writes add `admin:*` in the service.** In `/home/user/org-admin-api/app/modules/rbac/router.py`:
   - `GET /apps`, `GET /apps/{key}` and `GET /apps/{key}/permissions` require only `admin:apps:read`, for every app, the `admin` app and disabled apps included. `PermissionService.list_permissions` has no admin-app guard.
   - So `admin:apps:read` already exposes every admin-app permission key with its `is_admin` flag, plus `apps.default_role_id` and `org_owner_role_id` (role ids) through `AppRead`.
   - ADR-0008's ADM-002b amendment (lines 82–91) applies `admin:*` only to **writes**: G1, G2 and G4. No read anywhere is guarded by "this is the admin app".
   - The one read that requires `admin:*` is the user sessions list (`users/router.py:76`). That case is about PII (IP, user agent), not configuration.

3. **Role ids are already partly visible to non-managers.** `AssignmentRead` (permission `admin:users:read`) returns `role_id` and `role_key` for assigned roles, admin-app roles included. `AssignmentCreate` takes `role_id`, so anyone holding `admin:roles:assign` needs role ids to work at all. Today they can only find them through `default_role_id` or existing assignments.

4. **The seeded description of `admin:apps:read` is "Read apps and their permissions"** (`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`). `admin:roles:manage` is described as "Manage permissions and roles of apps", which is write-oriented. No `admin:roles:read` exists.

5. **Path collision and router order.** `catalog_router` (public `GET /apps/{key}/roles`) is mounted through `api_router` **before** `rbac_router` (`app/main.py:108-110`).
   - An admin `GET /apps/{key}/roles` would be shadowed by the public route, and the CORS matcher (first full match wins) would classify it as public.
   - `/apps/{key}/role-definitions` is free.
   - A detail route `GET /apps/{key}/roles/{role_id}` would not collide technically, because PATCH and DELETE already live there. But having the list on one noun and the detail on another invites confusion.

6. **The existing definition of "privileged"** is `AssignmentService._guard_privileged` (`/home/user/org-admin-api/app/modules/rbac/service.py:1446-1455`): `app.key == ADMIN_APP_KEY or repo.role_has_admin_permission(role_id)`. That is exactly the ticket's definition, and ADR-0008 G4 uses the same wording. `Permission.is_admin` is forced true for `<app>:*` (CHECK constraint) and for every admin-app permission (ADM-002d), so in practice every role holding an admin-app permission is already caught by the `is_admin` arm. The `app.key == "admin"` arm still matters for an empty admin-app role.

7. `Role` has `TimestampMixin`, so `created_at` and `updated_at` exist. `RoleRead` today has no `app`, `is_default`, `privileged` or timestamps, and is returned by the write routes. Changing it would alter those responses.

### Recommendations

- **R-1: Use `admin:apps:read`, not `admin:roles:manage`** (AC1).
  - It is consistent with finding 2: every existing read of RBAC configuration uses it, including the admin app's permissions.
  - It serves `admin:roles:assign` holders who need role ids (finding 3). Gating the list on `admin:roles:manage` would force giving assigners write power just to read. That works against least privilege and is the more dangerous outcome.
  - Its scope matches what the endpoint exposes: configuration, no PII, no secrets.
  - The planner should record the choice and update the seeded description in docs only. Do not migrate to change the text; a data migration is not worth it for this ticket (R7). Note it as a follow-up if wanted.
- **R-2: No stronger guard for listing `admin` app roles** (AC3).
  - Admin-app role and permission keys are already readable with `admin:apps:read`, and the `admin:*` model in ADR-0008 is about writes and grants.
  - A read-time `admin:*` gate would make PAN-015b break for non-super staff on exactly the app they most need to inspect, and it buys little: the role-to-permission mapping is not a credential.
  - Defence stays where it is: G1, G2 and G4 on every write or grant addressed by `role_id`. Knowing an id grants nothing.
  - State this explicitly in the plan and in the ADR-0008 decision log ("reads of admin-app RBAC config need only the leaf read permission").
- **R-3: Compute `privileged` with the same rule as G4, in one place** (AC2).
  - SQL: `privileged = (app.key == 'admin') OR bool_or(permissions.is_admin)` per role. Implement it as one aggregate over `roles LEFT JOIN role_permissions LEFT JOIN permissions` grouped by `role.id`, with `coalesce(bool_or(...), false)`. Aggregate the permission keys in the same query too: `array_agg(p.key ORDER BY p.key) FILTER (WHERE p.key IS NOT NULL)`, or a second batched query keyed by the page's role ids. No N+1.
  - Ideally `_guard_privileged`'s condition and the listing flag share a named predicate or helper, so the UI flag can never drift from the enforcement rule. If refactoring the guard is out of scope (R7), add a test asserting that a role flagged `privileged=true` gets 403 `admin_wildcard_required` on assign for a leaf-only actor, and that `false` does not.
  - Document that `privileged` is a UI hint. The server still enforces on every write.
- **R-4: Path.** Use `GET /api/v1/apps/{key}/role-definitions`, and `GET …/role-definitions/{role_id}` if the detail route is in scope. Put it on `rbac_router` so it inherits `current_admin`.
  - Add a test that the route is classified Bearer by `BearerRouteMatcher` and that `/apps/{key}/roles` stays public.
  - Record the R9 exception (key-addressed path) next to D18. USR-17 should nest `…/role-definitions/{role_id}/assignments` under the same noun.
- **R-5: Use a new response schema** (e.g. `RoleDefinitionRead`) rather than extending `RoleRead`, so the write responses don't change (R9: no breaking or unplanned change in v1).
  - `is_default`: compare against `app.default_role_id`.
  - Order: `role.key, role.id`.
  - 404 only when `get_app_by_key` returns None. Apply no status or `admin` filter, unlike `RoleCatalogService`.
- **R-6: Since this is a read, write no audit entry** (R4 covers mutations, as with OQ4 and ADM-015). Do not reuse the catalog's `rate_limit` (AC1).
- **R-7: Tests** (AC5, R8):
  - 401 with no token and with a wrong `aud`;
  - 403 with `details.required == "admin:apps:read"` for a principal holding only `admin:roles:assign`. If R-1 is adopted, also test that the same principal is allowed once it holds `admin:apps:read`;
  - an `admin:*` principal passes;
  - 404 for an unknown key;
  - the `admin` app is listed by a leaf-only principal (this locks in R-2);
  - a disabled app is listed;
  - paging and `total`;
  - `is_default` is true only on the default role;
  - `privileged` is true for an admin-app role with no permissions, true for a non-admin-app role holding an `is_admin` permission, and false otherwise;
  - the OpenAPI `x-permission` and the new schema are present.
- **R-8: `assignment_count`** (optional AC):
  - If included, count rows with `expires_at IS NULL OR expires_at > now()` using the database clock (as `AssignmentRead.active` does), in one grouped query per page.
  - Under `admin:apps:read` it exposes only a number, the same reasoning that let `member_count` use `admin:apps:read` (OQ4).
  - Watch the semantics: should it also exclude disabled users and inactive orgs? `member_count` made a choice; match it or document the difference.

### Risks

- **Choosing `admin:roles:manage`** blocks assign-only staff from finding role ids, which pushes owners to over-grant manage. Manage plus the G-guards is safe, but it is broader than needed.
- **Choosing `admin:*` or adding an admin-app read gate** breaks the admin-pan role screens for non-super staff and is inconsistent with the permissions list (finding 2).
- **If `privileged` is computed differently from `_guard_privileged`** (for example only `bool_or(is_admin)`), the UI could offer a "safe" grant that the server then refuses, or the reverse after future rule changes. That is why R-3 asks for a shared predicate or a cross-check test.
- **Mounting the route on a router without `current_admin`, or on a path the public catalog matches first,** would make it public and CORS-public, a data exposure. That is why R-4 asks for the classification test.
- **Accepted, unchanged:** permissions are reloaded per request but read before any lock. For a read, this is irrelevant.

### Open questions for the planner or owner

1. Confirm `admin:apps:read` for USR-16 (my recommendation), and whether to update the seeded description to "Read apps, their permissions and roles" (docs only now, a data migration later).
2. Should the role-definitions read be recorded as a formal ADR-0008 note ("RBAC config reads: leaf read permission only, admin app included")? I recommend yes.
3. Is the detail `GET …/role-definitions/{role_id}` in scope now, or deferred to USR-17's path work?
4. If `assignment_count` is included: count only unexpired assignments, or also exclude disabled users and inactive orgs to match `member_count`?

Key files: `/home/user/org-admin-api/app/modules/admin_auth/deps.py`, `/home/user/org-admin-api/app/modules/admin_auth/service.py`, `/home/user/org-admin-api/app/modules/admin_auth/route_classes.py`, `/home/user/org-admin-api/app/modules/rbac/router.py`, `/home/user/org-admin-api/app/modules/rbac/catalog_router.py`, `/home/user/org-admin-api/app/modules/rbac/service.py` (lines 1446-1455 `_guard_privileged`), `/home/user/org-admin-api/app/modules/rbac/repository.py`, `/home/user/org-admin-api/app/modules/rbac/schemas.py`, `/home/user/org-admin-api/app/main.py` (router order 108-116), `/home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md`, `/home/user/org-admin-api/migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`.
