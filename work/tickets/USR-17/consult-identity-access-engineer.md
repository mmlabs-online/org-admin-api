USR-17 consult from identity-access-engineer (read-only)

## Findings
- **Assignment model** (`app/modules/rbac/models.py:195-229`). Each `role_assignments` row has `user_id`, `role_id`, `org_id` (NULL means app scope, otherwise one org), `granted_by` and `expires_at` (NULL means it never expires). Uniqueness is `(user_id, role_id, org_id)` with NULLS NOT DISTINCT. `ix_role_assignments_role_id` already exists, so looking up rows by role is indexed. Assignments have no app column: the app comes from `roles.app_id`.
- **Naming.** The table and ORM class are `roles` / `Role`. `role-definitions` is only the URL noun from USR-16 (plan D1), and the detail route `GET /apps/{key}/role-definitions/{role_id}` already exists. To check that a role belongs to an app, use `repo.get_role(app.id, role_id)`, which is app-scoped and returns `None` for another app's role, giving a 404.
- **What "active" means today.** It is not defined the same way everywhere.
  - Per-user list (`AssignmentRead.active`, `repository.py:497-503`): active only means not expired, `expires_at IS NULL OR expires_at > now()`, using the database clock. User status and org status are ignored.
  - Token or effective-role paths: `org_role_keys` and `org_permission_keys` also require `organizations.status='active'`, so a suspended org yields no org roles. Disabled users cannot get tokens at all, and their sessions are revoked.
  - `member_counts` (ADM-015/016, OQ5) counts distinct users with `users.status='active'` and a non-expired assignment.
  - The super-admin guard (`_counting_super_admin`) requires not expired, app scope, active user and verified email.
- **The ticket's "like the per-user list" is inaccurate.** `GET /users/{id}/role-assignments` has **no** `active` filter. It only has `app`, and it returns every row with a computed `active` flag. So USR-17 adds a new filter; there is no existing one to mirror.
- **`org_id` cannot express app scope.** A filter `org_id: UUID | None` cannot ask for "app-scope rows only", because a missing value means "no filter".
- **Holder status is not exposed.** `AssignmentRead` has no holder status. `users.status` (active/disabled) and `email_verified_at` are on `User`, and `UserRead` already exposes both to holders of `admin:users:read`.
- **Permission.** The per-user list and `GET /users` use `admin:users:read`. USR-16 used `admin:apps:read` because it exposes no PII. USR-17 returns emails.

## Recommendations
1. **Permission `admin:users:read`** (AC1). The response carries holders' emails (owner note, §8 Q6), and this matches the per-user list and `GET /users`. `admin:apps:read` would leak PII to a config-read permission. Do not use `admin:roles:assign`: it is a write permission, and requiring it for a read pushes owners to over-grant (same reasoning as USR-16 D2).
2. **Path:** `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` on the Bearer `rbac` router, added to the `BEARER` map in `tests/test_route_auth_classes.py`. This matches USR-16 and needs no new R9 exception (D18 already covers it). Look up the app with no status or admin filter, as USR-16 does: disabled apps and the `admin` app are listed, and only an unknown or malformed key gives 404 (AC4).
3. **`active` means not expired, using the database clock**, exactly like the computed `AssignmentRead.active` (AC3). Do not fold user status or org status into it. Otherwise the same row would show `active=true` in the per-user list and `false` here, which breaks consistency. Reuse the one expression from `_assignment_rows()`. Do not write a second copy. `func.now()` is the transaction timestamp, so the count query and the rows query agree within one request. Document in OpenAPI that `active` only means "not expired" and that it is not "effective in tokens".
4. **Show disabled holders and flag them**, rather than hiding them. Add `user_status: UserStatus` to the item, and consider `email_verified_at` too. It is a small additive field read from the same join, so there is no N+1. Without it, the "who holds this role" view lists disabled accounts as active holders, which is misleading in an access review. The ticket's field list says "plus", so the planner should record this as a decision. If it is rejected, record it as a follow-up and note in the description that `active` ignores user status. I recommend against `org_status` in v1: it has to be joined per row and adds little. Follow-up if admin-pan asks.
5. **Scope filter.**
   - Keep `org_id: UUID` meaning "rows in that org only". An unknown org gives an empty page, not 404, like the audit filters.
   - Add `scope: Literal["app","org"] | None`, where `app` means `org_id IS NULL` and `org` means `org_id IS NOT NULL`.
   - `scope=app` together with `org_id` should give a 422 (contradictory).
   - Alternative if the planner wants to stay strictly within the AC: only `org_id`, and note "app-scope-only" as a follow-up. Do not overload `org_id` with a sentinel such as `null` or `none`.
6. **Schema:** a new `RoleHolderRead` with the `AssignmentRead` fields plus `user_id`, `email`, `display_name` (and `user_status` if adopted). Do not change `AssignmentRead`: R9, additive only.
7. **Repository:** one count query and one rows query, `RoleAssignment ⋈ Role ⋈ App ⋈ User`, filtered on `role_id`, ordered by `created_at DESC, id`. Without the user join there would be an N+1. Add a bounded-query test using the `_capture_sql` pattern. No migration is needed (R2).
8. **Tests, beyond AC5:**
   - Expired row: returned by default, `active=false`; excluded by `active=true`; the only row returned by `active=false`.
   - App-scope row vs org row with `org_id` (and `scope` if adopted).
   - A disabled user is listed, with `user_status=disabled` if adopted.
   - Another app's role id under this app gives 404.
   - 401 comes before 403, and 403 before 404 (`details.required == "admin:users:read"`).
   - A principal with only `admin:apps:read` gets 403.
   - `limit=101` gives 422.
   - Paging is stable across the `created_at` tie.
9. **No audit and no rate limit.** These are reads (R4 and ADM-015 precedent).

## Risks
- **Staff enumeration.** On `/apps/admin/role-definitions/{super-admin id}/assignments`, any holder of `admin:users:read` sees every super-admin and staff member with their emails in one call. They can already see this per user, so this is not new exposure, but it is now a single targeted query. Recommend accepting it and recording it in the plan, as USR-16 D3 did. Gating admin-app reads on `admin:*` would be a new rule, so it needs the owner.
- **`total` as a holder count.** USR-16 D5 deferred `assignment_count` to this endpoint's `Page.total`. That total counts assignments, not distinct users: one user can hold the same role in several orgs plus at app scope. It also includes disabled users unless they are filtered out. admin-pan must not show it as "members", which is defined differently (OQ5). State this in the field descriptions.
- **Disabled users or suspended orgs shown as `active=true`** (if recommendation 4 is rejected) could lead a reviewer to think access is live when it is not, or the reverse.

## Open questions
1. Owner or admin-pan: should holders show `user_status` (and `email_verified_at`)? I recommend yes.
2. Is an "app-scope only" filter needed in v1 (`scope=app|org`), or is `org_id` enough for PAN-015c?
3. Should holder lists for the `admin` app need `admin:*` on top of `admin:users:read`? I recommend no, for consistency with the per-user list, but the owner should confirm.
4. Should the ticket text "like the per-user list" be corrected? That list has no `active` filter, only the computed flag.

Relevant files: /home/user/org-admin-api/app/modules/rbac/models.py, /home/user/org-admin-api/app/modules/rbac/repository.py (lines 368-640), /home/user/org-admin-api/app/modules/rbac/assignments_router.py, /home/user/org-admin-api/app/modules/rbac/schemas.py (lines 276-291), /home/user/org-admin-api/app/modules/users/models.py, /home/user/org-admin-api/work/tickets/USR-16/plan.md
