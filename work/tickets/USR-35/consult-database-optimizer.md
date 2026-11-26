# Consult — database-optimizer — USR-35 (2026-11-26)

**Findings**
- `app/modules/users/repository.py:77-93`: `list()` builds one `conditions` list for both `count(*)` and the page select (order `created_at DESC, id`, limit/offset); EXISTS conditions there keep total and page consistent, no DISTINCT/GROUP BY.
- `app/modules/rbac/models.py:222-231`: `uq_role_assignments_user_role_org` (leading user_id, NULLS NOT DISTINCT), `ix_role_assignments_role_id`, `ix_role_assignments_org_id`; `uq_roles_app_id_key` (leading app_id) covers `roles.app_id`.
- `app/modules/rbac/repository.py:596-599`: private `_is_active()` (`expires_at IS NULL OR > func.now()`), repeated inline at 57, 479, 513, 541, 843, 987, 1009.
- `users/repository.py` imports no RBAC models.
- App/org scope needs no extra predicate (both `org_id IS NULL` and `org_id = X` count).

**Recommendations**
1. App filter: `EXISTS (SELECT 1 FROM role_assignments ra JOIN roles r ON r.id = ra.role_id WHERE ra.user_id = users.id AND r.app_id = :app_id AND active)`; resolve key → `app_id` first.
2. Role filter: `EXISTS (... ra.role_id = :rid AND active)`; with both filters use only the role EXISTS after validating role.app_id = app.id (else `role_not_in_app`).
3. Validate in the service: app by key, role by id; 422 `unknown_app`, `unknown_role`, `role_not_in_app`; disabled apps accepted; both unknown → `unknown_app` first.
4. Share the predicate: module-level `active_assignment()` next to `RoleAssignment` in rbac models (High path — pure move / minimal touch).
5. Bundle USR-35 with USR-34 in one PR.
6. No migration; role filter uses `ix_role_assignments_role_id`, app filter via `uq_roles_app_id_key` + role_id index; EXPLAIN only if p95 > 200 ms.
7. `include=access` shows all active access, independent of the filter.
8. Tests: expired-only excluded; two matching assignments counted once; with email/status/offset past end; disabled app; each 422; malformed `role_id`.

**Risks**
- EXISTS must be correlated to `users.id` — use `.correlate(User)` explicitly.
- Sharing `_is_active` touches a High path.
- Race with app deletion — harmless.

**Open questions for Ming (defaults)**
1. Both unknown → `unknown_app` first.
2. Bundle USR-34 + USR-35 in one PR — yes.
3. Shared active predicate in `app/modules/rbac/models.py`.
