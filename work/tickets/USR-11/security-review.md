## Attempt 1

## Security review: USR-11, attempt 1 (branch claude/blissful-wozniak-fx4lm9, diff `origin/main...HEAD`)

This was a read-only review. I did not run `make check` or pytest, as you asked, so my verdict depends on the reviewer's green `make check`.

### Findings

There are no blockers.

🟡 **should-fix: the "no path, query or body input" rule for self-scoped routes is written down but no test checks it**
- Where: `tests/test_route_auth_classes.py:149-164` (`test_self_scoped_routes_have_no_permission`, `test_self_scoped_allow_list_is_exact`).
- Risk: ADR-0008's amendment (`docs/adr/0008-admin-api-authentication.md:129`) says a self-scoped route takes no input and its subject is always the principal. The tests only check that the method is GET, the path has no `{`, it is not under `/auth/`, and it declares no permission. A later change could add `user_id: uuid.UUID | None = None` as a query parameter to `get_my_permissions` (or to a future `SELF_SCOPED` entry). That would turn the route into an IDOR read of any staff member's roles and permissions with no permission required, and every current test would still pass. `test_query_user_id_ignored` only covers today's handler.
- Fix: add this to `test_self_scoped_routes_have_no_permission`:
  ```python
  d = routes[route]
  assert not d.path_params and not d.query_params and not d.body_params, route
  ```
  These lists are flattened across sub-dependencies. `current_admin` contributes only `Request`, the session and the `Security(bearer_scheme)` header, so the assertion holds today. Also check that `_flat_dependant` or `get_flat_dependant` gives the same answer on your FastAPI version.

💭 **note: the exception is still marked "pending owner confirmation"**
- Where: the ADR-0008 heading at `docs/adr/0008-admin-api-authentication.md:129`, and D23 at `docs/identity-architecture.md:279`.
- Meanwhile `CLAUDE.md` R3/R9 and spec §5.3 already state it as accepted.
- The ticket says the owner confirms. Keep the PR description clear that merging it counts as that confirmation, or wait for an explicit OK, so the governing docs don't say different things.

💭 **note: the new row sits in the `/api/v1/auth` endpoint table**
- Where: `docs/identity-architecture.md:140`, between the cookie routes `accounts` and `token`, even though the route is outside `/auth` and is a Bearer route.
- This is a documentation-clarity issue only, not a security one. Consider moving it to a separate "admin self-scoped" row or a note.

💭 **note: roles and permissions are read in two separate queries**
- `principal.permissions` comes from `current_admin`; `effective_roles` is a second query in `service.py` (`self_view`).
- Under READ COMMITTED, a grant that lands between the two reads can make one response inconsistent. The ADR accepts this, and since the data is only a UI hint, that is fine.

### Verified OK

**Authentication**
- The router-level `dependencies=[Depends(current_admin)]` plus `AdminDep` gives full ADR-0008 §1–4 behaviour: header-only Bearer, `aud=admin` (a list audience is rejected), issuer check, per-request reload, and 401 for disabled or unverified users with the same challenge as other admin routes.
- FastAPI caches the dependency, so there is a single `current_admin` evaluation per request.

**No subject selection / IDOR**
- The handler takes only `session`, `principal` and `response`.
- The subject is `principal.user_id` from the verified `sub`. A `user_id` query parameter is ignored, and a test covers it.

**No PII**
- `MePermissions` has `extra="forbid"` and contains only `user_id`, `roles` and `permissions`; there is no email.
- Nothing new is logged.

**Data correctness**
- Permissions are exactly what enforcement uses (`principal.permissions`), sorted, with `admin:*` returned unexpanded.
- Roles come from the same `_active_assignments` filter: app scope only, expired assignments dropped, keys limited to the `admin:` prefix.
- An org-scoped admin assignment is ignored, matching enforcement (tested).

**Caching**
- The handler sets `Cache-Control: no-store` on 200 responses.
- On a 401, the CORS wrapper (`app/core/cors.py:116-117`) adds `no-store`.
- No ETag is set.

**Deny-by-default (`tests/test_route_auth_classes.py:120-128`)**
- The new predicate accepts a route only if it is:
  - in PUBLIC|COOKIE, or
  - in SELF_SCOPED with exactly `(has_admin=True, keys=[])`, or
  - **not** in SELF_SCOPED with `current_admin` and exactly one `require_permission`.
- So a permission-less Bearer route outside SELF_SCOPED still fails. A SELF_SCOPED route without `current_admin`, or with a permission, also fails.
- Membership is by exact (method, path); there are no wildcards.
- `test_self_scoped_allow_list_is_exact` pins the set to one literal, and `test_bearer_route_permission_map` asserts guarded == BEARER ∪ SELF_SCOPED and that the two sets are disjoint.
- Any other route added under the `/me` router without a permission fails the classification test.
- I found no path for another permission-less admin route to slip through.

**CORS**
- `BearerRouteMatcher` sees `current_admin`, so the route uses ADMIN_POLICY: allowed origin echoed, no `Allow-Credentials`, `Vary: Origin`. This is tested.
- `/me` is outside the `/api/v1/auth/` prefix, so it does not overlap with the credentialed identity CORS scope (the scopes are disjoint, which the test asserts).
- The 401 tests also cover the cookie-only case.

**OpenAPI**
- The operation has `bearerAuth` security, a 401 response using the envelope `$ref`, no 403, and no `x-permission`.

**Docs**
- `CLAUDE.md` R3/R9, spec §5.3, ADR-0008 §6 plus the amendment, and `docs/consumers.md` give the exception the same narrow scope.
- The consumer guidance is sound: keep the result in memory only, treat it as a UI hint, and don't decode the token.

**Rules**
- R1 (layering): the router calls the service, and the service calls authz and the repository. R4 is not applicable because the route is read-only. R5/R11: no secrets or tokens are logged.

### Follow-ups (out of scope)
- Bearer admin routes have no per-principal rate limit (the ADR accepts this). A cheap two-query self-read is a mild amplification surface. Consider a shared admin rate limit later.
- If more self-scoped routes are planned, consider a dedicated `self_scoped_admin()` route decorator or marker. That would let the classifier and the input-free check key off one definition instead of a test-only set.

**Verdict: PASS with one should-fix.** No blockers; I recommend adding the input-free assertion before merge. This depends on a green `make check` from the reviewer.

Files reviewed:
- /home/user/org-admin-api/app/modules/admin_auth/me_router.py
- /home/user/org-admin-api/app/modules/admin_auth/service.py
- /home/user/org-admin-api/app/modules/admin_auth/schemas.py
- /home/user/org-admin-api/tests/test_route_auth_classes.py
- /home/user/org-admin-api/tests/test_me_permissions.py
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/identity-architecture.md

## Should-fix re-check (7d182bb)

**Verdict: fixed.** The 🟡 is resolved and no new findings block the merge. `uv run pytest tests/test_route_auth_classes.py -q`: 63 passed.

- `_input_params(dependant)` collects `path_params`, `query_params` and `body_params` and recurses into `dependant.dependencies`, so it reaches router-level and nested dependencies. A `?user_id=` on `get_my_permissions` or on any dependency, a body model or a `{param}` would fail `test_self_scoped_routes_have_no_permission`. No false positive today (`Request`, `Response`, `SessionDep` are special parameters; Bearer comes via `Security(HTTPBearer)`).
- Docs: the `/me/permissions` row now sits among the Admin rows as "Admin (self-scoped)"; earlier 💭 resolved.

**Remaining 💭 (not blocking):** `_input_params` does not look at `header_params` or `cookie_params`, so a self-scoped route could take its subject from a custom header (e.g. `X-User-Id: Header()`) and still pass. Adding `*dependant.header_params, *dependant.cookie_params` closes it; it should still return `[]` for `/me/permissions`.
