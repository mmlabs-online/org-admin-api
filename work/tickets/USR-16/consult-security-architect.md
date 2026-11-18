USR-16 security-architect consult (read-only)

The planner can go ahead with this endpoint without any new security controls, provided it reuses the existing admin router and two specific guards described below. Staff with `admin:apps:read` can already see every role id and every permission this endpoint would show, so it adds very little new exposure.

## Findings

F1. **Little new exposure.** Staff with `admin:apps:read` can already see:
- every app key, including `admin` and disabled apps (`GET /api/v1/apps`);
- each app's full permission list with `is_admin`, the admin app included (`GET /apps/{key}/permissions`, `PermissionService.list_permissions` at `app/modules/rbac/service.py:810`, which has no admin-app guard);
- `default_role_id` and `org_owner_role_id` (`AppRead`, `app/modules/rbac/schemas.py:64,90`);
- role ids and role keys through `AssignmentRead` (schemas.py:258).

  What is new is the full role-to-permission mapping per role, including which admin-app roles hold `admin:*`. That is an internal picture of who holds what, useful to an attacker choosing a staff account to take over. It is not a privilege escalation: knowing a role id does not let anyone do anything. Every write needs `admin:roles:manage`, and `admin:*` for privileged roles (`_guard_privileged`, service.py:1446; `RoleService.replace_permissions`, around line 1181). Severity: Informational/Low.

F2. **Public catalog (D17) vs the new endpoint.** The public catalog (`catalog_router.py`, `RoleCatalogService.read_catalog`, service.py:246):
- hides unknown, disabled, `admin` and malformed keys behind one 404;
- has no ids;
- drops permission keys from other apps;
- is rate-limited (`role_catalog`) and has no CORS (ADR-0011 §7).

  The new endpoint deliberately returns the opposite: ids, the admin app and disabled apps. The main risk is getting them mixed up: reusing `RoleCatalogService`, adding fields to `RoleCatalogEntry` / `RoleCatalog` (whose ETag depends on field order and is append-only), or adding a query parameter on `/apps/{key}/roles` that switches into "admin mode".

F3. **App-key enumeration through 404.** This doesn't matter here. The endpoint is behind Bearer auth, and the same caller can list every key anyway. "404 only for unknown key" matches `AppService.get` (service.py:339). The one thing that matters is order: the 401 and 403 checks must run before the app lookup, so an unauthenticated caller cannot tell a real key from a fake one. That is already true if the route sits on `rbac/router.py`, because `current_admin` and `RequirePermission` run before the handler.

F4. **Route classification and CORS (ADR-0011).** `BearerRouteMatcher` treats a request as admin when the first fully matching route has `current_admin` in its dependency tree. A separate path like `/apps/{key}/role-definitions` on the admin router therefore gets admin CORS without credentials automatically:
- it answers only the `apps[admin].origins` origins;
- it sends `Vary: Origin` and `Cache-Control: no-store`.

  If the path were nested under `/apps/{key}/roles/...` with GET, the public catalog route would match `/apps/{key}/roles` itself, and a future `GET /apps/{key}/roles/{role_id}` would sit next to the public route. The matcher would still classify it correctly, but it invites confusion.

F5. **Rate limiting.** No Bearer admin route uses `rate_limit` today (all 16 uses are auth, invitations, org, catalog, manifest and JWKS). Leaving the new route unlimited matches the ticket's acceptance criterion and the existing practice. The real limit is that the caller needs a valid `aud=admin` token held by a staff account. The query cost is bounded by `limit ≤ 100`.

F6. **Audit.** R4 covers mutations only. The owner already ruled that reads are not audited (spec §5.x OQ4: "Read-only, so no audit entry"). Failed auth is already logged by `admin_auth_denied` and `admin_permission_denied` (`admin_auth/deps.py:45,90`). No audit entry is needed.

F7. **Which permission to require.** A user with only `admin:roles:assign` needs role ids to call `POST /users/{id}/role-assignments`. Under `admin:roles:manage` they could not discover them (except indirectly through `AppRead.default_role_id`). `admin:apps:read` matches the other read routes (`list_permissions`, `get_app`, credentials list) and the `deploy.md` seed role sets.

## Recommendations

1. **Permission (acceptance criterion 1):** use `admin:apps:read`, not `admin:roles:manage`. Reasons:
   - it is consistent with `GET /apps/{key}/permissions`, which already exposes the same permission data;
   - it does not lock out users who only assign roles, or auditors.

   Do not require `admin:*` for the admin app: that permission set is already readable with `admin:apps:read`, and protection belongs on the writes, where it already exists. Record this choice in `plan.md` and in `docs/identity-architecture.md` (the §4 table row for `/apps/{key}/roles`).

2. **Route placement (criteria 1, R3, ADR-0011):**
   - Put the route on the existing `app/modules/rbac/router.py`, which has `dependencies=[Depends(current_admin)]`, with `**require_permission("admin:apps:read")`.
   - Use a separate path such as `GET /api/v1/apps/{key}/role-definitions` (and `/{role_id}` if you add a detail route).
   - Never add a query parameter or "admin mode" to the public `/apps/{key}/roles`.
   - Add the route to `BEARER_PERMISSIONS` in `tests/test_route_auth_classes.py`.
   - Add one CORS test: an admin origin gets `ACAO` and no `Allow-Credentials`; a non-admin app origin gets no `ACAO`.

3. **Keep it apart from D17 (F2):**
   - Use a new response schema (e.g. `RoleDefinitionRead`) and a new service method or repository query.
   - Do not touch `RoleCatalogService`, `RoleCatalogEntry` or `catalog_rows`.
   - Add a regression assertion that `GET /apps/admin/roles` and `GET /apps/<disabled>/roles` still return 404 and that the public catalog body has no `id`.

4. **The `privileged` flag (criterion 2) must match the server-side check.** Use exactly the `_guard_privileged` predicate: `app.key == "admin"` or the role holds any `is_admin` permission. Compute it with one grouped query (e.g. `bool_or(Permission.is_admin)` per role), not a per-role `role_has_admin_permission` call (N+1). If the UI's flag and the server guard disagree, admin-pan will show wrong warnings; add a test that flips a role's permission to `is_admin` and checks the flag changes. The flag is advisory only and never replaces the guard.

5. **Permissions list per role.** Keep the same-app filter (ADR-0003 §4: the database's composite foreign keys already enforce it). Return keys sorted. Order the page by `(key, id)` so ties are always broken by `id`.

6. **`assignment_count` (optional criterion).** If you include it, count only non-expired assignments, filtering active users the same way `member_counts` does (repository.py:581), in one grouped query for the page's role ids.
   - It is safe under `admin:apps:read`, since `member_count` was already approved there (OQ4).
   - Do not return assignee identities.

7. **No audit and no rate limit (F5, F6).** State both explicitly in the plan, citing R4, spec OQ4 and the acceptance criterion, so the reviewer doesn't flag them.

8. **Error order (R6, R8).** Tests must show:
   - no token returns 401 even for an unknown key;
   - a token without `admin:apps:read` returns 403 even for an unknown key;
   - only an authorised caller gets 404;
   - a malformed `key` returns 404 (not 422), matching `AppService.get`;
   - a malformed `role_id` on the detail route returns 422 (UUID validation), and a `role_id` from another app returns 404 (scope the query by `app_id`, as `get_role` does).

## Risks

- R-a: Someone later "simplifies" by merging this with the catalog and leaks ids or the admin app on the public route. The recommendation 3 tests prevent this.
- R-b: `privileged` drifts from `_guard_privileged` if the privilege rule changes, for example if `org_owner_role_id` were made privileged. Put the logic in one shared helper or document the coupling.
- R-c: A stolen staff token with `admin:apps:read` now reveals which admin-app roles carry `admin:*`, which makes picking a target easier. This is accepted: the information was mostly derivable already, and access tokens have a 12 h TTL (USR-13). If the owner wants tighter control, require `admin:*` only for `key == admin`. I don't recommend it, because it is inconsistent with `/apps/admin/permissions`.
- R-d: A disabled app's roles become visible and editable through ids. Writes already work on disabled apps, so this is not new, but confirm it is intended.

## Open questions

1. Owner or planner: `admin:apps:read` (recommended) or `admin:roles:manage`? This decides whether users with only `admin:roles:assign` can pick role ids in admin-pan.
2. Should the admin app's role list itself be restricted to `admin:*`? (I recommend no, for consistency with `/apps/admin/permissions`.)
3. Is the `role-definitions` path a new R9 exception, or does it fall under the existing D18 `apps/{key}/…` exception? The path is still a `Page[T]` and a plural noun. Record the answer in the plan as the ticket notes ask.

Relevant files:
- /home/user/org-admin-api/app/modules/rbac/router.py
- /home/user/org-admin-api/app/modules/rbac/catalog_router.py
- /home/user/org-admin-api/app/modules/rbac/service.py (lines 246, 339, 810, 1446)
- /home/user/org-admin-api/app/modules/rbac/repository.py (lines 136, 581)
- /home/user/org-admin-api/app/modules/rbac/schemas.py
- /home/user/org-admin-api/app/modules/admin_auth/deps.py
- /home/user/org-admin-api/tests/test_route_auth_classes.py
- /home/user/org-admin-api/docs/adr/0011-admin-cors.md
- /home/user/org-admin-api/docs/adr/0003-rbac-keys-and-matching.md
- /home/user/org-admin-api/docs/identity-architecture.md
