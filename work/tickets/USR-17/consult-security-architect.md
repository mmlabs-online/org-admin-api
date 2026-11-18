# USR-17 security consult: role holders list

## Findings

**F1. The new data is personal data about many people at once.** Each row carries `email` and `display_name` (PII, spec §8 Q6). Today the same data needs `admin:users:read`:
- `GET /users` and `GET /users/{id}/role-assignments` (`app/modules/users/router.py:26`, `app/modules/rbac/assignments_router.py:25-39`)
- `GET /apps/{key}/join-requests` (`app/modules/join_requests/router.py:24-40`). This one also sets `Cache-Control: no-store` explicitly, with the comment "rows carry emails".

So no new kind of data is exposed. What is new is **aggregation**: with one call per role you get the full holder list. That matters most for the `admin` app. `GET /apps/admin/role-definitions/{super-admin id}/assignments` returns the whole staff and super-admin roster with emails, which is a ready target list for phishing or credential stuffing. Today the same list needs a sweep of `GET /users` plus N calls to `/users/{id}/role-assignments`. That was possible but slow and noisy.

**F2. Permission.** `admin:users:read` is the right gate. The rows are user PII, and that permission already covers the per-user list and join requests.
- `admin:roles:assign` would be wrong. Its holders do not necessarily have `admin:users:read`, so they would gain email access through this route. That widens PII access.
- `admin:apps:read` (USR-16) would be wrong for the same reason.

**F3. 401 → 403 → 404 order holds if the route goes on a router that has `dependencies=[Depends(current_admin)]` and uses `**require_permission(...)`.**
- Router-level and decorator dependencies run before the handler, so the app and role lookups (404) come after them.
- FastAPI raises path and query validation errors only after dependencies are solved. So a request with no token and a bad UUID, or `active=maybe`, still gets 401.
- USR-16 already shows this pattern (security-review "Error order").

**F4. Placement and CORS (ADR-0011).** Put the path on `app/modules/rbac/router.py` (prefix `/apps`, Bearer at router level) as `/{key}/role-definitions/{role_id}/assignments`.
- It does not collide with the public catalog `/apps/{key}/roles` (`catalog_router`).
- `BearerRouteMatcher` (`app/modules/admin_auth/route_classes.py:30-55`) then classifies it as Bearer. It gets admin CORS from `apps[admin].origins`, **without credentials**, plus `no-store` when no `Cache-Control` is set (`app/core/cors.py:103-117`).
- Do not put it on `assignments_router`: that router's prefix is `/users`.

**F5. Cross-app IDOR.** The role must be resolved with `repo.get_role(app.id, role_id)`, which is app-scoped (USR-16 detail). The assignment query must then filter on that resolved `role.id`. A role id from another app must return 404 even when that role has holders.

**F6. Disabled users.** Disabled users and users with an unverified email still have `role_assignments` rows. Those rows are inert for tokens, because the token/membership queries filter on active, verified users (`repository.py:574-580`). But they still count for the 409 guards and for revocation.
- If the list hid them, staff could not find and revoke them.
- If the list showed them as normal holders, staff would wrongly think those people have access.

**F7. Audit.** This is a read, so no audit entry (R4, spec OQ4, USR-16 D7). Failed auth is already logged by `admin_auth/deps.py`.

**F8. Free-text fields.** `display_name` is user-controlled free text, and `email` is only loosely validated. Both get rendered in admin-pan. The API returns JSON, so there is no server-side XSS, but the consumer must render them as text.

## Recommendations

**Rec 1 (AC1, R3).** Use `admin:users:read` only, declared via `require_permission` so `x-permission` is emitted. Add a `BEARER` entry in `tests/test_route_auth_classes.py` → `"admin:users:read"`. This brings in the classification test, the 401 sweeps (no token / cookie only / app credential), `test_cors_matcher_agrees_with_bearer_map` and `test_admin_cors.py::test_no_allow_credentials_on_any_bearer_route`. No `rate_limit`, no CORS change.

**Rec 2 (AC4, R3, R6). Error-order tests.**
- No token on an unknown app → 401.
- No token with a bad `role_id` UUID → 401.
- A principal without the permission (e.g. only `admin:apps:read`) → 403 with `details.required == "admin:users:read"` on all three of: an existing role, an unknown app, an unknown role. This prevents an existence oracle.
- An authorised principal → 404 for: unknown app, malformed key (404, not 422, same as USR-16), unknown role, and another app's role **that has holders**.
- Bad UUID for an authorised principal → 422.

**Rec 3 (AC2, F1).** Response hygiene:
- Explicit field list on a new item schema, e.g. `RoleHolderRead` = the `AssignmentRead` fields + `user_id`, `email`, `display_name`. Never `from_attributes` on `User` wholesale, so no `password_hash` or other columns can leak (R11).
- Set `response.headers["Cache-Control"] = "no-store"` in the handler, as `join_requests` does ("rows carry emails"). This is defense in depth if the CORS matcher ever stops matching.
- Do **not** resolve `granted_by` to an email or name. Keep it as a UUID, as in `AssignmentRead`.
- Do **not** add an `email`/`q` search query parameter. That would put emails into URLs and access logs (R11), and it is not in the ACs (R7).

**Rec 4 (F6, AC2; additive, planner's call within R7).**
- Include disabled and unverified users by default. Do not filter them out.
- Add `user_status` (`active`/`disabled`) to the item so admin-pan can mark inert holders. Optionally add `email_verified: bool` as well.
- If the planner keeps the AC field set strictly, record `user_status` as a follow-up and say that disabled holders appear unmarked.
- Test: a disabled user's assignment is listed (and shows the status, if the field is added).

**Rec 5 (AC2, F5).**
- One joined query: `RoleAssignment ⨝ Role ⨝ App ⨝ User`, selecting `active` computed with `func.now()`, the same expression as `_assignment_rows` (`repository.py:488-494`).
- The count query must apply the **same** `active` and `org_id` predicates, so `total` matches the items.
- Order by `created_at DESC, id`. Use bound parameters only.
- `active=false` means `expires_at IS NOT NULL AND expires_at <= now()`.
- An `org_id` that does not exist or is not related → 200 with an empty page, not 404, so the filter is not an org-existence oracle. (Org ids are not secret to staff, but keep the behaviour simple and consistent.)

**Rec 6 (R5, R11).** No emails in log lines, error messages or `details`. The 404 messages stay generic ("App not found" / "Role not found").

**Rec 7 (R4).** No audit entry. State this in the plan, as USR-16 D7 did.

## Risks

- **R-a (accepted, but the owner should see it).** Any holder of `admin:users:read` gets the full staff roster with emails in one call: all holders of `admin:super-admin` and the other admin-app roles. This is consistent with the existing per-user access and with the owner's note in the ticket, but it is a cheaper reconnaissance path if a token is stolen (12 h TTL).
  - Mitigation option, not my default: require `admin:*` when `key == "admin"`, via a service guard after the app lookup. The admin app's existence is public, so a 403 there leaks nothing. That mirrors how the staff-session endpoints require `admin:*`.
  - My default recommendation is to accept and note it in the PR, because admin-pan's "who holds this role" sheet for staff roles is a core use case.
- **R-b.** Bulk PII reads are not audited (spec Q6 is open). Any future audit-on-read decision should cover this route together with `GET /users` and join requests.
- **R-c. Large roles.** Default or member roles may have 10^4–10^5 holders. Response size is capped at limit ≤ 100. Deep offsets and `count(*)` scan all of the role's rows; the database consult covers this (EXPLAIN in impl-log; composite index only as a follow-up). Only staff can call the route, so this is a low DoS risk.
- **R-d.** Without `user_status` (Rec 4), staff may wrongly read disabled holders as having access, or miss that a revoke is needed.
- **R-e.** If admin-pan renders `display_name` or `email` as HTML, that is a stored-XSS path from end users into staff sessions. This is the consumer's responsibility (PAN-015c); mention it in the PR.

## Open questions

1. Should holders of `admin`-app roles need `admin:*` (R-a mitigation), or is `admin:users:read` accepted for all apps, including `admin`? (Recommendation: accept, noted in the PR.)
2. Add `user_status` (and optionally `email_verified`) to the item now, or as a follow-up (AC2 lists only `user_id`, `email`, `display_name`)?
3. `org_id` filter: is a way to ask for app-scope-only rows (`org_id IS NULL`) wanted? It is not in the ACs, so it would be a follow-up.

Files read:
- /home/user/org-admin-api/work/tickets/USR-17/ticket.md
- /home/user/org-admin-api/work/tickets/USR-17/consult-database-optimizer.md
- /home/user/org-admin-api/work/tickets/USR-16/plan.md
- /home/user/org-admin-api/work/tickets/USR-16/security-review.md
- /home/user/org-admin-api/app/modules/rbac/assignments_router.py
- /home/user/org-admin-api/app/modules/rbac/router.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/rbac/models.py
- /home/user/org-admin-api/app/modules/rbac/schemas.py
- /home/user/org-admin-api/app/modules/users/router.py
- /home/user/org-admin-api/app/modules/users/schemas.py
- /home/user/org-admin-api/app/modules/join_requests/router.py
- /home/user/org-admin-api/app/core/cors.py
- /home/user/org-admin-api/app/modules/admin_auth/route_classes.py
