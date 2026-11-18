## Findings
- Per-user list (`/users/{id}/role-assignments`, `assignments_router.py`) uses `AssignmentRepository.page_assignments`. Count and rows are separate queries, joined Role and App. `active` is computed with `func.now()` and labelled. Order is `created_at DESC, id`. `AssignmentRead` = id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at.
- It has no `active` or `org_id` filter today. The expired-vs-active rule is duplicated in several repo places (lines 373, 407, 435, 489, 579, 625). Do not invent a new one; reuse the `_assignment_rows()` expression.
- USR-16 sits on the Bearer `router` under `/apps/{key}`. Its `role-definitions/{role_id}` route plus `RoleService._get(app, role_id)` already gives "404 unknown app / unknown role / role of another app". `get_app_by_key` has no status filter, so disabled and `admin` apps work.
- `Page[T]` is `{items,total,limit,offset}` with `PageDep`.

## Recommendations
1. **Path (AC1).** Use `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`. It nests under the USR-16 plural noun, so there is no new R9 exception. `assignments` is a plural noun. It does not collide with the public catalog. Put it on the Bearer `rbac_router`, not the catalog router. Add one `BEARER` entry in `tests/test_route_auth_classes.py`, and add the path to the OpenAPI contract test and the §4 table row in `docs/identity-architecture.md`.
2. **Permission.** Use `admin:users:read`, not `admin:roles:assign`. The response is user PII, the same class as `GET /users` and the per-user list. Role-assign holders do not need emails to assign. Tradeoff: staff who hold only `admin:apps:read` can see role definitions but not holders. That is intended.
3. **Auth order.** 401, then 403, then 404 (the dependency runs before the handler). Test 403 on an unknown key.
4. **Item schema.** Create a new `RoleHolderRead`. Prefer composition over a subclass of `AssignmentRead`:
   - Either `class RoleHolderRead(AssignmentRead)` adding `user_id: uuid.UUID`, `email: str`, `display_name: str | None`; or flat fields.
   - Subclassing is acceptable and additive. Never add the holder fields to `AssignmentRead` itself. It is returned by POST and the per-user list, where `user_id` is redundant.
   - Inherited `app`, `role_id` and `role_key` are redundant (constant for the page) but cheap. Keeping them keeps the per-user and per-role shapes parallel for the SPA. Do not drop them.
   - Use `EmailStr` or `str` to match whatever `UserRead` uses. Check `display_name`'s nullability against the users module. Do not create a nested `user` object (a flat shape matches the ticket wording "plus the holder's user_id, email, display_name").
   - Cast `email` and `display_name` to the same types as the users module, so no new PII formatting rules appear.
5. **Query (no N+1).** One repo method `page_role_holders(role_id, *, active: bool | None, org_id, limit, offset)`:
   - `select(RoleAssignment, User.email, User.display_name, active_expr.label("active")).join(User, User.id == RoleAssignment.user_id).where(RoleAssignment.role_id == role_id)`.
   - Role and app are already known from the 404 lookup, so no Role/App join. Set `app=app.key` and `role_key=role.key` in the service.
   - Use one count query with the same WHERE. Use `order_by(RoleAssignment.created_at.desc(), RoleAssignment.id)`.
   - Add a bounded-query test (the `_capture_sql` pattern, 1 vs 6 holders give equal statement counts).
6. **`active` filter semantics.**
   - `active: bool | None = None`. Omitted returns all rows, including expired ones with `active=false` (as in AC3 and the per-user list).
   - `active=true` means `expires_at IS NULL OR expires_at > now()` (database clock, the same expression as the item field, so filter and field never disagree).
   - `active=false` means the negation (`expires_at IS NOT NULL AND expires_at <= now()`). Write it as `~active_expr` or the explicit form; do not use `NOT (...)` in a way that mishandles NULL (expires_at NULL gives NULL under negation, so write it explicitly).
   - Decide explicitly whether "active" also considers the holder's user status (disabled or unverified user). The existing definition (`repository.py:574`) of "active holder" for some guards includes an active, verified user. Recommendation: the filter means assignment-not-expired only, to match the `active` field and the per-user list. Document it in the query param description, and say that a disabled user's row still shows `active=true`. Optionally follow up with a user `status` field. Ask the owner if admin-pan expects "effective access" (see Open questions).
7. **`org_id` filter.**
   - `org_id: uuid.UUID | None`. Malformed value gives 422 (FastAPI). Exact match on `RoleAssignment.org_id == org_id`.
   - It cannot express "app-scope only (org_id IS NULL)". Options: (a) accept that limitation; (b) accept a sentinel. Do not use a sentinel string such as `org_id=null`, because it breaks the UUID typing and the OpenAPI schema. Recommend (a) and list the limitation as a follow-up (`scope=app|org`, additive). Items still show `org_id: null`, so the UI can filter client-side within a page, but total would be wrong, so note it as a follow-up.
   - Do not validate that the org exists. An unknown org simply returns an empty page (200). A 404 would leak existence and add a query. Test this.
   - Filters combine with AND. Each filter is covered by a test, plus a combined test.
8. **Count/total.** `total` reflects the filters. It is the "holders of this role" count that USR-16 deferred (D5), so say so in the docs. Note it counts assignment rows, not distinct users. A user can hold the same role in several orgs, so the same `user_id` can appear more than once. State this in the endpoint description. Do not dedupe: the rows carry distinct `id`, `org_id` and `expires_at`.
9. **Ordering.** `created_at DESC, id` is the ticket order and matches the per-user list. A stable tie-breaker is present. Index check (database-optimizer's area): `role_assignments` needs a `role_id` prefix index or the unique constraint (user_id, role_id, org_id) will not serve `WHERE role_id = ?`. Verify in the models; if absent, a migration (R2) with `downgrade()` is needed. This is the main db-scope item for the planner.
10. **OpenAPI.**
    - The 200 schema is `Page[RoleHolderRead]` (component name by FastAPI, copy from the existing naming). Responses 401/403/404/422 reference `ErrorEnvelope`. Set `x-permission: admin:users:read` through `require_permission`.
    - Give the query params descriptions: `active` ("omitted: all rows; expired rows are `active=false`"), `org_id` ("exact organization; app-scope rows cannot be selected").
    - Add an operation `summary`, and a PII note in the description. Add the path to the checked set in `test_every_error_response_references_envelope`.
11. **Tests (AC5).** 401 (no token; cookie-only via the route sweep); 403 (with `admin:apps:read` only, and `admin:roles:assign` only); 404 unknown app, unknown role, role of another app; expired rows present with `active=false` by default; `active=true` and `active=false`; `org_id` filter (org-scoped plus app-scope in the same role); unknown org gives an empty 200; `limit=101` gives 422; bad `org_id` UUID gives 422; bad `role_id` UUID gives 422; paging with `total` and stable order (identical `created_at`, `id` tie-break); item key set equality (assignment fields plus `user_id,email,display_name`); the same user with the same role in two orgs gives two rows; disabled-app and `admin`-app roles are listable; `admin:*`-only principal passes.
12. **Service layering.** Put it in `RoleService` or `AssignmentService`; the lookup `get_app_by_key` then `_get(app, role_id)` already exists in `RoleService` (USR-16). Reuse it to avoid duplicating the 404 logic. No audit (read), no rate limit, same as USR-16 D7.

## Risks
- PII: the page exposes emails of every holder (including `admin:super-admin`) to any `admin:users:read` holder. Accepted by the owner (§8 Q6). Keep emails out of logs and error details (R5/R11). A large `admin` super-admin list is low-cardinality.
- Missing `role_assignments(role_id, ...)` index would make this a table scan at scale. Verify.
- `active` filter vs "effective access" confusion (disabled or unverified users still "active"). Document it.
- A route-ordering collision is not expected (`/role-definitions/{role_id}/assignments` is a distinct, longer path), but `{role_id}` UUID typing returns 422 for a bad value before auth-independent 404; auth still runs first.
- Same-user-multiple-rows can surprise a "number of holders" UI. Document it.

## Open questions
1. Should `active` include user status (`users.status`, verified), or only assignment expiry? Recommended: expiry only.
2. Is "app-scope only" filtering needed by admin-pan PAN-015c? If yes, add an additive `scope` param now or as a follow-up.
3. Is there an existing `role_id` index on `role_assignments`? (Verify in `app/modules/rbac/models.py`; I did not confirm it.)
4. Flat holder fields vs a nested `user` object: flat is recommended per the ticket text. Confirm with admin-pan.
