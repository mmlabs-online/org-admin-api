**Findings**

- **Existing indexes on `role_assignments`:**
  - `ix_role_assignments_role_id (role_id)`, `ix_role_assignments_org_id (org_id)`, and the unique `uq_role_assignments_user_role_org (user_id, role_id, org_id)` with NULLS NOT DISTINCT. All three are in migration `/home/user/org-admin-api/migrations/versions/20261111_b638a7c84c31_create_rbac.py`.
  - The unique index leads with `user_id`, so it does not serve a role-first lookup. `ix_role_assignments_role_id` does.
  - There is no `created_at` index.
- **`users`:**
  - `id` is the PK and `email` is a unique `CITEXT`. `display_name` is `String(100)` and nullable.
  - `role_assignments.user_id` is `NOT NULL` with `ON DELETE CASCADE`, so an inner join to `users` never drops rows. `roles.id` has the same guarantee through its FK.
- **Existing per-user list:**
  - `RbacRepository.page_assignments` (`/home/user/org-admin-api/app/modules/rbac/repository.py`, around lines 496-525) builds on `_assignment_rows()`.
  - It selects `(RoleAssignment, App.key, Role.key, active)`, joins Role and App, and orders by `created_at.desc(), id` with limit and offset.
  - It runs a separate `count(*)` over the same joins and filters.
  - The active flag is `or_(expires_at IS NULL, expires_at > func.now())`. It is a label on the row, not a filter, which matches the ticket's "default includes expired rows marked `active=false`".
  - The same predicate is repeated inline in several other methods and in `_active_assignments`.
- **Role lookup:** `get_role(app_id, role_id)` (repository line 159) is already used by `get_role_definition` in `router.py`. It is the right basis for the app-and-role 404 check.

**Recommendations**

1. Add a new repository method, for example `page_role_holders(role_id, *, active: bool | None, org_id: UUID | None, limit, offset)`. The service resolves app by key (404), then `get_role(app.id, role_id)` (404), then calls this method. It issues two statements in total, which satisfies the "no N+1" criterion.
   - Rows: `select(RoleAssignment, App.key, Role.key, active.label("active"), User.id, User.email, User.display_name)`.
     - Join `Role`, `App` and `User` on `User.id == RoleAssignment.user_id`.
     - Filter `RoleAssignment.role_id == role_id`.
     - Order `created_at.desc(), id`, then limit and offset.
   - Count: `select(count()).select_from(RoleAssignment).where(role_id, filters)`. It needs no joins, because the role is already validated and the FKs are NOT NULL. It must carry the same `active` and `org_id` filters as the rows query.
   - Reuse one shared predicate, for example `_is_active()` returning the `or_`. Use it for the label and for the filter: `active=True` filters on it, and `active=False` filters on `not_()` of it. The expression is never NULL, so `not_` is safe. Keep using the database clock (`func.now()`).
   - `org_id` is plain equality: `RoleAssignment.org_id == org_id`.
   - If the response schema extends `AssignmentRead`, `_assignment_rows` needs an extension or a sibling builder. Do not change the shape of the existing tuple used by the per-user paths (R7).
2. **No migration is needed (R2).** The planner should state this explicitly in `plan.md`.
   - Rows for a single role are filtered through `ix_role_assignments_role_id`, and the sort runs on that small set. A holder list per role is bounded by users, not by the table.
   - A `(role_id, created_at DESC, id)` index would make the top-N sort free, but it adds write cost and a migration. It is only worth it if a role reaches roughly 10^5 holders. Defer it and record it as a follow-up.
   - The `org_id` filter combined with `role_id` is also fine on the single-column indexes. The planner can pick a BitmapAnd or just filter the `role_id` result.
3. **Tests (R8):** assert the number of statements in the happy-path test, or at least check that email and display_name come from a single call. Also cover:
   - the expired boundary: `expires_at` in the past gives `active=false`, and `expires_at` in the future or NULL gives `active=true`;
   - ordering ties on `id`;
   - a total that equals the filtered count, not the page size.
4. **Permission:** `admin:users:read` fits. The endpoint reads users' PII and does not mutate anything.

**Risks**

- If the `active` predicate is copied rather than shared, the row label and the filter can drift apart.
- Offset paging with `ORDER BY created_at DESC, id` is stable only because the sort includes `id`. Concurrent inserts can still shift pages, as with the existing list.
- The `uq_role_assignments_user_role_org` index with NULLS NOT DISTINCT means one holder appears at most once per `(role, org)`. The same user can appear several times for one role, once per org. Clients must not assume `user_id` is unique within the page.
- Email exposure is PII. It is accepted in the ticket, but keep it out of logs and audit details (R5/R11).

**Open questions**

- Can the `org_id` filter select app-scope rows (`org_id IS NULL`)? A plain `org_id=` equality cannot. The ticket does not ask for it. A flag such as `scope=app` would be an addition and needs planner sign-off.
- Should an unknown `org_id` filter return 404, or an empty page? Recommend an empty page, with no org lookup.
- Should holders whose user status is `disabled` be listed? Recommend yes, as the per-user list does. Surface the user's status only if the owner wants it, since the ticket does not list `status`.
- Is a role with `is_system` or the `admin` app listable here? Recommend yes, for staff with the permission, unlike the public catalog's 404 for the `admin` app.
