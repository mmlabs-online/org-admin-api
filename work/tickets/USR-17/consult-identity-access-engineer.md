## USR-17 consult: identity-access-engineer (read-only)

### Findings
F1. **Reads that expose holder PII already use `admin:users:read`.**
- `GET /users` and `GET /users/{id}` (`app/modules/users/router.py:26,44`)
- `GET /users/{id}/role-assignments` (`app/modules/rbac/assignments_router.py:28`)
- `GET /apps/{key}/join-requests` (`app/modules/join_requests/router.py:27`). This is the closest precedent: it is app-scoped under `/apps/{key}/…`, and its rows carry `email`, `display_name`, `email_verified` and `user_status`.

Every reader in the codebase uses a `*:read` permission. `admin:roles:assign` is only ever a write permission (POST/DELETE role-assignments, join-request decisions).

F2. **No new information is exposed.** Someone with `admin:users:read` can already get every holder of every role, including `admin:super-admin`: call `GET /users`, then `GET /users/{id}/role-assignments` for each user. USR-17 makes that one call instead of N. The data reachable under the permission does not change.

F3. **No read is gated on the admin app today.** ADR-0008's guards G1, G2, G4 and G5 (`docs/adr/0008-admin-api-authentication.md:82-91`) cover writes and grants only. USR-16 D3 records the same decision for role definitions. The per-user list returns admin-app assignments to any `admin:users:read` holder.

F4. **`AssignmentRead.active` only means "not expired".** It is computed in `_assignment_rows` (`app/modules/rbac/repository.py:488-494`) from `expires_at` alone. It ignores user status, email verification and org status. A holder list showing `active=true` next to a disabled user will mislead staff who are checking "who can actually use this role". The join-requests schema exposes `user_status` and `email_verified` for exactly this reason.

F5. **`no-store` on email-bearing rows.** The join-requests list sets `Cache-Control: no-store` with the comment "rows carry emails" (`join_requests/router.py:39`). `GET /users` does not, so the two precedents disagree.

F6. **Query shape.** `page_assignments` (`repository.py:496-516`) is the existing per-user query. It already joins `Role` and `App`, orders by `created_at DESC, id` and runs one count plus one select. Adding a join to `User` gives email and display name without N+1.

### Recommendations
1. **Use `admin:users:read`** (AC1). Reasons:
   - It matches every existing PII-bearing read (F1).
   - The data is the same as the per-user list (F2).
   - Choosing `admin:roles:assign` would turn a write permission into a read gate. That breaks least privilege: owners would have to grant assign power so support staff can view a sheet, which is the same anti-pattern USR-16 D2 rejected for `admin:roles:manage`.
   - Add an `x-permission` entry and a `BEARER` entry in `tests/test_route_auth_classes.py` (R3).

   If the owner wants this sheet to be narrower than the user directory, the right tool is a new `admin:roles:read` with a seed migration. That is a follow-up, not this ticket.
2. **Add no extra gate on admin-app holders** (AC4/AC5 unaffected), for consistency with F2, F3 and USR-16 D3. Record this as an accepted residual risk in the plan: a stolen `admin:users:read` token now lists the super-admins' emails in one call, a cheaper target list for phishing. Mitigations already in place:
   - admin tokens are short-lived
   - `admin:*` is required to grant or revoke privileged roles (G4)
   - users cannot assign roles to themselves

   Lock this in with a test: a principal with only `admin:users:read` gets 200 on `GET /apps/admin/role-definitions/{super_admin_id}/assignments`. A future change to gating should then be a deliberate decision, not drift.
3. **Path:** `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` on the Bearer `rbac` router, nested under the USR-16 noun. This needs no new R9 exception. Resolve the app with no status or admin filter, as USR-16 does, so disabled apps and the `admin` app work. Then look up the role scoped by app with `get_role(app_id, role_id)`, so another app's role returns 404 (AC4). Error order: 401 → 403 → 404 (unknown app or role) → 422 (bad UUID or `org_id`).
4. **Keep holder fields consistent with the per-user list and join-requests** (AC2):
   - Return the exact `AssignmentRead` fields, plus `user_id`, `email` and `display_name`.
   - Make the new schema `RoleHolderRead` a separate model. Do not change `AssignmentRead` (R9).
   - Strongly consider adding `user_status` and `email_verified`, named as in `JoinRequestRead` (F4). Both are additive and both are already exposed under the same permission. Without them, `active` will be misread as "effective".
   - Document `active` in a `Field(description=…)` as "not expired (database clock); does not reflect user, verification or org status".
   - Mark `display_name` as untrusted user text, as `JoinRequestRead` does.
5. **Filters** (AC3):
   - `active=true` → `expires_at IS NULL OR expires_at > now()`. `active=false` → expired rows only. Omitted → all rows.
   - Use the same SQL predicate as `_assignment_rows`, so the filter and the field cannot disagree. Ideally factor it into one expression in the repository.
   - `org_id`: UUID, exact match. Return an empty page for an unknown org, not 404, so the filter does not reveal whether an org exists.
   - Apply each filter to both the count and the select (a `total` mismatch is a classic paging bug).
6. **Set `Cache-Control: no-store`** on the response, following the join-requests precedent (F5, R11 spirit). Add one test assertion for it.
7. **Make no audit entry and no rate limit.** R4 covers mutations only (USR-16 D7). Never put email in logs or error details (R11). The 404 messages stay generic ("Role not found").
8. **Tests beyond AC5:**
   - 403 with `details.required == "admin:users:read"` for a principal holding only `admin:roles:assign` and for one holding only `admin:apps:read`. This shows neither neighbouring permission is enough.
   - 403 comes before 404 on an unknown app.
   - `admin:*` passes.
   - The admin app with only the leaf permission returns 200 (rec. 2).
   - Each item's `email` equals the user's email. No `password_hash` or other user columns leak: assert the exact key set.
   - A bounded query count (the `_capture_sql` pattern) for page sizes 1 and N.

### Risks
- **R-a.** One call now lists every admin-app holder's email for anyone with `admin:users:read` (the accepted residual from rec. 2). Severity is low to medium, because the data was already reachable in N calls.
- **R-b.** If `user_status` and `email_verified` are left out, staff may read disabled or unverified holders as effective holders (F4). The consequence is operational mistakes, for example believing someone still has admin access.
- **R-c.** The `active` filter could drift from the `active` field if the predicate is copied instead of shared (rec. 5).
- **R-d.** `granted_by` exposes staff user ids, the same as the per-user list. Nothing new.
- **R-e.** If the `User` join is ever loosened to an outer join or a soft-delete filter, rows could appear with a null email. Users are never hard-deleted while they have assignments, so an inner join is correct. Assert this in a test.

### Open questions
1. May the planner add `user_status` and `email_verified` to the item? This is additive and has the join-requests precedent. Otherwise it goes on the follow-ups list. My recommendation is to include them.
2. Should `GET /users` and the per-user assignments list also set `no-store`, for consistency? That is out of scope here, so it would be a follow-up.
3. Does the owner want a dedicated `admin:roles:read`, or a stricter gate on admin-app holder lists, in future? That would need a seed migration and an ADR-0008 note, so not in this ticket.

Relevant files:
- /home/user/org-admin-api/app/modules/rbac/assignments_router.py
- /home/user/org-admin-api/app/modules/rbac/repository.py (lines 487-516)
- /home/user/org-admin-api/app/modules/rbac/schemas.py (lines 282-291)
- /home/user/org-admin-api/app/modules/join_requests/router.py
- /home/user/org-admin-api/app/modules/join_requests/schemas.py
- /home/user/org-admin-api/app/modules/users/router.py
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md (lines 73-109)
- /home/user/org-admin-api/work/tickets/USR-16/plan.md
