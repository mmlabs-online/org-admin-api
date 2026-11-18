**Findings**
- USR-16 shipped in /home/user/org-admin-api/app/modules/rbac/router.py: `GET /api/v1/apps/{key}/role-definitions` and `/role-definitions/{role_id}`, permission `admin:apps:read`, returning `RoleDefinitionRead`. Writes stay on `/apps/{key}/roles/...`. `GET /apps/{key}/roles` is the public catalog (D17). The ticket's example path `/apps/{key}/role-definitions/{role_id}/assignments` therefore matches the USR-16 shape.
- `RoleService._app(key)` raises 404 "App not found". `get_definition` calls `self._get(app, role_id)`, which looks the role up by (app_id, role_id) and so gives 404 for a role of another app. Reuse both rather than writing new lookups. I did not read `_get` itself; check it raises 404.
- The `admin` app is NOT excluded from role-definitions. Only the public catalog 404s for `admin`/disabled apps (D17).
- Per-user list (`/users/{user_id}/role-assignments`, assignments_router.py) uses `admin:users:read`. Its only filter is `app`. There is no `active` filter today, and expired rows are returned with `active=false`. `active` is computed in SQL as `expires_at IS NULL OR expires_at > now()` (repository `_assignment_rows`), i.e. DB clock. Order is `created_at DESC, id`.
- `AssignmentRead` has: id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at.
- `User` has `email` (CITEXT) and `display_name` (nullable, 100 chars).
- `Page[T]` is built as `Page(items, total, limit, offset)` using `PageDep`.
- In tests/test_route_auth_classes.py, the BEARER map (around lines 84-101) is keyed `(method, path)` to permission. `_fill` replaces `{key}` with `stoktok` and fills uuid4 for `role_id`, `user_id` etc. A new `{role_id}` path works with no `_fill` change. A new path param name would need adding to the `_fill` tuple.

**Recommendations**
1. Path: `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` (R9 plural noun, app addressed by key per D18). Add the BEARER map row `("GET", f"{V1}/apps/{{key}}/role-definitions/{{role_id}}/assignments"): "admin:users:read"`. Put the route in the rbac router or the assignments router. A prefix clash is not a concern, but keep the `apps/{key}` prefix.
2. Permission: `admin:users:read`. The list exposes user emails, and the per-user list and `GET /users` already gate that on `admin:users:read`, so access to the same PII stays the same. `admin:apps:read` (role-definitions) and `admin:roles:assign` (a write permission) would be wrong. Note this means a holder of `admin:apps:read` alone can see the role but not its holders, which is intended. Update the docs/identity-architecture.md §4 table row.
3. Response: new schema `RoleHolderRead(AssignmentRead)` plus `user_id: UUID`, `email: str`, `display_name: str | None`. Reuse `_assignment_read` and extend it. Add a repo method `page_role_assignments(app_id, role_id, active, org_id, limit, offset)`. It should reuse `_assignment_rows()` with `.join(User, User.id == RoleAssignment.user_id)` and select the User columns in the same query, which avoids N+1. The count query needs the same joins and filters. `AssignmentRow` is a 4-tuple, so either add a new row type or select `User.email` and `User.display_name` as extra columns.
4. Filters: `active: bool | None = None`, so the default includes all rows. `active=true` filters on `expires_at IS NULL OR expires_at > now()`. `active=false` filters on the negation, which is expired rows only. Define the predicate once and reuse it in the column label and the filter, so they cannot diverge. `org_id: UUID | None` filters on `RoleAssignment.org_id == org_id`. Decide whether app-scope rows (`org_id IS NULL`) can be asked for. Suggest documenting that `org_id` filters only org-scoped rows and not adding an app-scope filter. Mention this in the ticket.
5. 404s: unknown app returns 404 "App not found". A role that does not exist or belongs to another app returns 404 "Role not found". Use `get_role(app.id, role_id)` and keep the app-first order. Allow the `admin` app. Staff need to see the holders of super-admin, and it is already visible through role-definitions. Do not 404 it as the public catalog does.
6. Ordering: `created_at DESC, id`, as the per-user list does, and as the ticket says. Total counts the filtered rows.
7. Tests: 401, 403 (token without `admin:users:read`, and with only `admin:apps:read`), 404 for unknown app, unknown role and role of another app, expired rows (active false), org-scoped rows, each filter, paging, an `admin` app case, and that email and display_name are present. Also assert the query count to show there is no N+1.

**Risks**
- PII (emails) goes to any `admin:users:read` holder. That is the owner-accepted and documented position (ticket notes, spec §8 Q6).
- If `active` is computed separately in the label and in the filter, one request could see the DB clock differently in two places. Use a single expression.
- `GET /users` and the user list may include disabled or deleted users. Check whether a role assignment can outlive an inactive user. The inner join on User is fine, but decide whether to show suspended users. I suggest showing them, because staff need to see everyone who holds the role. This is not verified.
- Total must use the same joins as rows, otherwise the count drifts from the items.

**Open questions**
- Should `org_id` filter also support "app scope only" (for example `org_id=null`)? Suggest not in v1.
- Should the response indicate the user's status? Suggest no (additive later, so it is a safe addition).
