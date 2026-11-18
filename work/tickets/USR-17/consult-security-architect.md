# USR-17 security consult: who holds a role (staff read)

**Bottom line:** gate the endpoint on `admin:users:read`. Do not add an `admin:*` check, even for admin-app roles. Set `Cache-Control: no-store` explicitly. Reads are not audited (R4 covers only mutations), so add no audit entry. Make the response field list explicit. Ask the owner to accept one risk: a ready-made list of super-admin emails.

## Findings

**F1. Permission catalogue and how reads are gated today.**
- Seeded permissions (`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`):
  - `admin:users:read` is "Read users and their role assignments".
  - `admin:roles:assign` is "Grant and revoke role assignments".
- All existing assignment and user reads use `admin:users:read`:
  - `app/modules/rbac/assignments_router.py:28` (per-user role-assignments list)
  - `app/modules/users/router.py:26,44` (`GET /users`, which already returns `email` and `display_name`)
  - `join_requests/router.py:27` (staff list with emails)
- USR-16 role definitions use `admin:apps:read` (`app/modules/rbac/router.py:188,202`).
- ADR-0008 (ADM-002b amendment, lines 82–91) puts `admin:*` only on writes (G1, G2, G4, G5). No read is gated on "this is the admin app".
- The only read that needs `admin:*` is the user sessions list (`users/router.py:76`). That is because it shows IP and user agent, which is a different kind of PII.

**F2. Listing super-admins adds no new data, but makes it much easier to get.**
- Someone with `admin:users:read` can already find every super-admin today: `GET /users`, then `GET /users/{id}/role-assignments?app=admin` for each user. The per-user rows include admin-app `role_key`.
- USR-17 turns that N+1 crawl into one call that returns a short list of the most valuable staff emails: targets for phishing or account takeover.
- It grants no privilege. Every grant or revoke of a privileged role still goes through `_guard_privileged` (`app/modules/rbac/service.py:1509`): `admin:*` plus a self-assignment ban. The last-super-admin guard is at service.py:1533.
- Severity: Low (information exposure to authenticated staff only).

**F3. Choice of permission.**
- `admin:roles:assign` is a write permission. Requiring it for a read breaks least privilege: a read-only support role would need grant power just to view holders.
- `admin:users:read` matches the per-user list, which is the same data rotated.
- The ticket's own default and USR-16's choice of a read leaf permission both point the same way.

**F4. Cache-Control is already covered, but only implicitly.**
- `DynamicCORSMiddleware` with `BearerRouteMatcher` (`app/main.py:68-73`, `app/core/cors.py:116-117`) adds `Cache-Control: no-store` to every Bearer route that does not set one.
- Routes that return emails set it explicitly anyway, e.g. `join_requests/router.py:39` ("rows carry emails").
- The per-user assignments list and `GET /users` rely on the middleware alone.

**F5. Audit.** R4 and ADR-0008 audit only mutations and denied writes. No read in the codebase writes an audit row; `admin:audit:read` itself is not audited.

**F6. Enumeration oracle.** `require_permission` is a route dependency, so it runs before the handler: 401/403 come before 404. That means role ids cannot be probed without the permission. Inside the service, "role of another app" and "unknown role" must look identical.

## Recommendations

1. **Use `require_permission("admin:users:read")`** and declare it in `x-permission` (acceptance criterion 1; R3 class 1).
   - Do not add an `admin:*` read gate for `key == admin` or for privileged roles. It would be inconsistent with the per-user list (F2) and would break admin-pan's holders sheet for non-super staff.
   - Record the decision and the accepted risk in `plan.md` and in the §4 table of `docs/identity-architecture.md`.
2. **Return one 404 for both "role not found" and "role belongs to another app"**: same code and same message, e.g. `NotFoundError("Role not found")`, checked as `role.app_id == app.id` (acceptance criterion 4).
   - The unknown-app 404 can keep its own message ("App not found"), as USR-16 does.
   - Look up the app by `key` regardless of status, so disabled apps and `admin` are included, as in USR-16.
3. **Use an explicit response schema**, e.g. `RoleHolderRead(AssignmentRead)` plus `user_id`, `email`, `display_name` (acceptance criterion 2; R11).
   - The repository should select explicit `User` columns in one join, never the whole `User` entity. That rules out `password_hash` reaching the response (and the N+1).
   - Add a test that asserts the item's keys are exactly the expected set.
   - Optional, the planner's call under R7: add `user_status`, so staff can see disabled holders of privileged roles. This matters for super-admin hygiene.
4. **Set `response.headers["Cache-Control"] = "no-store"` explicitly**, with the comment "rows carry emails", as in `join_requests/router.py:39`. Add a test that asserts the header (R5/R11 hygiene, defence in depth if the CORS matcher ever changes).
5. **Write no audit entry** (R4 is for mutations). Log nothing per row: no emails, no user ids in logs (R11).
6. **Validate inputs at the boundary** (acceptance criterion 3; R9):
   - `role_id` and `org_id` as `uuid.UUID`;
   - `active` as `bool | None`;
   - paging through `PageDep` (`limit` ≤ 100);
   - evaluate `active` and expiry with the database clock (`expires_at IS NULL OR expires_at > now()`), as the per-user list does.
   - `org_id` for an app with no orgs should return an empty page rather than a 422 that reveals the app's mode. Either is acceptable; keep it consistent with the per-user list.
7. **Tests, beyond acceptance criterion 5:**
   - 403 for a principal holding only `admin:apps:read` or only `admin:roles:assign`;
   - an `admin:*` principal passes;
   - the admin app's `super-admin` holders are listed for a plain `admin:users:read` holder (proves the decision is deliberate);
   - the role-of-another-app 404 body equals the unknown-role 404 body.

## Risks

- **R-a (needs owner acceptance):** a stolen staff token with `admin:users:read` can pull the full super-admin and staff email list in one request, a phishing target list.
  - Mitigations already in place: 12 h access-token TTL (USR-13), Bearer-only admin authentication, and every privileged write needs `admin:*`.
  - If the owner wants it tighter: require `admin:*` only when the role is privileged (admin app, or `RoleDefinitionRead.privileged`). The cost is inconsistency with the per-user list, which would then also need gating to close the gap.
- **R-b:** the PII surface grows. Emails go to every `admin:users:read` holder, which is the same population as `GET /users` today (spec §8 Q6).
- **R-c:** if the item type is built from `User` ORM objects, sensitive columns could leak in future. Recommendation 3 removes this.
- **R-d (follow-up, not in this diff per R7):** `GET /users`, `GET /users/{id}` and the per-user role-assignments list return emails but rely only on the middleware for `no-store`. Add the explicit header in a `/solo` or a later ticket.

## Open questions

1. Does the owner accept R-a: privileged-role holders listed under `admin:users:read` with no `admin:*` gate? I recommend yes, for consistency with the per-user list.
2. Should items include the holder's `status`, so disabled super-admins are visible? Small addition, useful for security hygiene. This is a scope call.
3. Should the `org_id` filter on an app whose `org_mode` is `none` return an empty page or a 422? Align with the per-user list.

Relevant files:
- /home/user/org-admin-api/app/modules/rbac/assignments_router.py
- /home/user/org-admin-api/app/modules/rbac/service.py (lines 1362-1370, 1509-1518)
- /home/user/org-admin-api/app/modules/rbac/router.py (lines 185-207)
- /home/user/org-admin-api/app/modules/rbac/schemas.py (lines 225, 282)
- /home/user/org-admin-api/app/modules/users/router.py
- /home/user/org-admin-api/app/modules/join_requests/router.py (line 39)
- /home/user/org-admin-api/app/core/cors.py (lines 116-117)
- /home/user/org-admin-api/app/main.py (lines 63-73)
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md (lines 76-109)
- /home/user/org-admin-api/work/tickets/USR-16/consult-security-architect.md
