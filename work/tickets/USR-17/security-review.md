## Attempt 1

## Security review: USR-17 (`GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`)

I read the code and ran git only, as asked; I did not run `make check` or pytest. I reviewed the diff from `git diff origin/main...HEAD` against `app/modules/rbac/{router,service,repository,schemas}.py`, `tests/test_route_auth_classes.py` and `tests/test_rbac_role_holders_api.py`.

### Findings

No 🔴 blockers and no 🟡 should-fix items.

- 💭 **Authorization is correct.** At `app/modules/rbac/router.py:217-225` the route declares `require_permission("admin:users:read")` through the `_with_404` helper. The BEARER map entry is at `tests/test_route_auth_classes.py:98`. Tests cover 401, a 403 without the permission, a 403 with a different permission, and that `admin:*` passes.
  - There is no `admin:*` gate for admin-app roles, which matches the accepted risk in the ticket. Anyone with `admin:users:read` can list the super-admin holders' emails. That is a useful reconnaissance list for phishing staff.
  - Suggested follow-up: record this acceptance in an ADR or the identity-architecture decision log so it is tracked.
- 💭 **The 404 is identical for an unknown role and another app's role.** `service.py:1110-1121` calls `_app(key)` and then `_get(app, role_id)`. That lookup is `repository.get_role` filtered by `app_id` and `role_id` (`repository.py:162-165`), so both cases raise the same "Role not found". A test checks that the bodies match (`test_holders_404_other_app_role_same_body`, line 136).
  - An unknown app returns "App not found", which is a different message. That only reveals which app keys exist, and the public role catalog already does that. Not an issue.
- 💭 **Only explicit user columns are selected.** `repository.py:534-535` adds `User.email`, `User.display_name` and `User.status`, never the `User` entity. The response model (`RoleHolderRead`, `schemas.py:295`) also limits the output fields. The test at line 195 asserts that `password_hash` is not in the response.
- 💭 **`Cache-Control: no-store` is set** at `router.py:243` and tested at line 198.
  - It is set only on successful responses. Error responses carry no PII, so that is acceptable.
- 💭 **No PII in logs.** The diff adds no logging.
- 💭 **The query is injection-free.** It is built only from SQLAlchemy expressions. `role_id` and `org_id` are typed as UUIDs, `active` as a bool, and `limit`/`offset` come from `PageDep` (`limit` ≤ 100, with a test for the 422).
- 💭 **The shared active-predicate refactor does not change other callers.** The new `_is_active()` (`repository.py:499-502`) is the same expression `_assignment_rows()` used inline before, so every existing caller gets the same SQL.
  - The `not_(...)` form used for `active=false` handles NULL correctly. A NULL `expires_at` makes the `IS NULL` branch TRUE, so `NOT` gives FALSE, with no three-valued-logic gap.
- 💭 **Hardening note on the count query.** The count at `repository.py:529-531` does not join `User`, but the row query does. If `role_assignments.user_id` is a non-null foreign key with cascade, the count and the rows always agree; if not, `total` could be larger than the rows returned. This is not a security issue.

### Follow-ups (out of scope)
- Document the accepted risk that `admin:users:read` reveals the emails of super-admin holders (ADR or D-log entry), and consider an `admin:*` gate for admin-app roles in a later ticket.

**Verdict: PASS (no security blockers).**
