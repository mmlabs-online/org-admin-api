**Findings**

How responses are declared today:
- `require_permission(key)` (`app/modules/admin_auth/deps.py:112-120`) returns `dependencies`, `openapi_extra={"x-permission": key}` and `responses={401, 403}`. The `responses` dict is a shallow copy of `_ERROR_RESPONSES`, so setting new top-level codes on it is safe.
- `app/core/openapi.py:25-43` runs after `get_openapi`. For every response with a code >= 400 it overwrites `content` with the `ErrorEnvelope` `$ref`. It also adds `X-Request-ID` to every response.
- Because of that, route authors only need `{code: {"description": ...}}`. No schema or `model=` is needed, and none should be added.
- The `_with_409` / `_with_404` helpers are copy-pasted in five routers: `rbac/router.py:41-50`, `app_credentials/router.py:21`, `entitlements/router.py:25`, `join_requests/router.py:18` (hard-wired to `admin:roles:assign`) and `organizations/router.py:30` (`_route(key, {code: desc})`, the best-shaped one).
- `users/router.py:49-50,76-81` and `rbac/assignments_router.py:58-59` mutate `require_permission(...)["responses"]` inline instead.
- `tests/test_openapi_contract.py` already has a generic envelope check (`:43`), the role-holders 404 test (`:94`) and an organizations 404/409 test (`:236-245`). ADM-020's organizations work has landed: 404 is on organization GET, PATCH and DELETE, and 409 is on POST and DELETE.

404 is raised but not declared (only `list_role_holders` declares it, plus organizations and user sessions):
- `rbac/router.py`:
  - GET, PATCH and DELETE `/apps/{key}`: `get` at `service.py:363`, plus the update and delete paths.
  - `/apps/{key}/permissions`, `/permissions/{id}` and `/role-definitions[...]`: the app at `:850`, the permission at `:856`, the role at `:1073`/`:1114`.
  - `/roles`, `/roles/{id}` and `/roles/{id}/permissions`.
- `app_credentials/router.py`: POST, GET and DELETE credentials. The app 404 is at `service.py:103` (`_locked_app`); `:186` is another app 404 and `:197` is the credential 404.
- `entitlements/router.py`: all four routes. The org 404 is at `service.py:40` (the list route is called with a required `org_id` query param). The entitlement 404 is at `:46`. The unknown-app 404 is at `:68`, and `details` carries `reason=unknown_app`.
- `join_requests/router.py`: list, approve and reject. Unknown app is `service.py:68,75`; unknown request is `:82,87,136`.
- `rbac/assignments_router.py`: list, POST and DELETE. The service raises "User not found" at `rbac/service.py:1414,1424` and "Assignment not found" at `:1573,1577`.
- `users/router.py`: GET and PATCH `/users/{id}` (`users/service.py:34,52`).

409 is raised but not declared. The AC list is incomplete here: `_with_409` is missing from several routes that can return 409.
- `DELETE /apps/{key}`: `service.py:544-556`, including `app_has_entitlements`.
- `POST /apps/{key}/permissions`: key already taken, `:914,926`. `DELETE .../permissions/{id}`: `:1040,1042`.
- `POST /apps/{key}/roles`: `:1161,1167`. `PATCH` and `DELETE /roles/{id}`: `:1204,1236-1247`.
- `POST /users/{id}/role-assignments`: "Role is already assigned", `:1533,1540,1554`.
- `PATCH/DELETE` entitlements, and `PUT /apps/{key}/manifest` (`rbac/manifest_router.py:18`, not inspected here).
- Several declared 409 descriptions are not exhaustive. Examples: `PATCH /apps/{key}` also has the concurrent-change 409 at `:536`. `PUT .../permissions` includes the `:1312-1326` rules.

`admin:*` needed beyond the declared permission (`require_super` call sites):

| Route | Declared | When `admin:*` is needed | Where |
|---|---|---|---|
| `POST /apps/{key}/credentials` | `admin:apps:write` | always | `app_credentials/service.py:109` |
| `PATCH /apps/{key}` | `admin:apps:write` | only for the `admin` app, and only if the PATCH changes something | `rbac/service.py:439-440` |
| `POST /apps/{key}/permissions` | `admin:roles:manage` | admin app, `is_admin`, or app-scope perm on an org app | `:902-912` |
| `PATCH` permission | `admin:roles:manage` | admin app (if changes), `is_admin` change, or scope change to app on an org app | `:958-962` |
| `DELETE` permission | `admin:roles:manage` | admin app | `:1028` |
| `POST`, `PATCH` (rename) and `DELETE` role | `admin:roles:manage` | admin app | `:1154,1188,1223` |
| `PUT /roles/{id}/permissions` | `admin:roles:manage` | admin app, or any `is_admin` permission added or removed | `:1292-1295` |
| `POST` and `DELETE /users/{id}/role-assignments` | `admin:roles:assign` | privileged role (admin app, or holds an `is_admin` permission), and nobody changes their own admin roles | `:1560-1564` |
| `PATCH /users/{id}` | `admin:users:write` | target holds a privileged assignment | `users/service.py:62` |
| `DELETE /users/{id}` and `/sessions...` (3 routes) | `admin:*` | declared | `users/router.py:77` |
| session GET and DELETE on a privileged user | `admin:*` | already declared as `admin:*` | `sessions/staff_service.py:55,82` |

- The router docstrings (`rbac/router.py:3-4`, `assignments_router.py:3-4`) mention this only in prose.
- Only `POST /apps/{key}/credentials` mentions it, in a 409 description string (`app_credentials/router.py:33`).
- `POST /apps` (`service.py:367`) and `DELETE /apps/{key}` do not need it. `DELETE` has no `require_super` at all.
- The 403 code is `admin_wildcard_required` with `details={"required": "admin:*"}` (`errors.py:63-69`). 403 is already in every `require_permission` response set.

POST /apps with a taken key:
- `rbac/service.py:380-381` and `:395-396` (the race path) raise a bare `ConflictError("App key is already taken")`. The code is the default `conflict` and `details` is `None`.
- Origin conflicts already carry `details=[{"field":"origins","reason":"origin_taken"}]` (`:142-146`), still under code `conflict`.
- The convention elsewhere is a stable `code` plus a `details[0]` of `{field, reason}`: entitlements `service.py:27-28`, join requests `:34-35`, and credentials `:90-91`.
- Existing tests pin `code == "conflict"` for the taken key: `tests/test_rbac_apps_api.py:210,226`. The `admin` key is a taken key too (`:211-212`).
- The organization slug conflict at `organizations/service.py:52,56` has the same shape, bare `conflict`, and ADM-020 documented it as "code conflict".

**Recommendations**

1. **404 (AC1).** Declare 404 on every route above, not only the four named families. Admin-pan generates types from the whole document, and the same 404 is raised on apps, roles, permissions, assignments and users. Descriptions should say what is unknown, in the style of `rbac/router.py:261`.
2. **Shared helper.** Add one helper in `app/modules/admin_auth/deps.py`:
   - `require_permission(key, *, errors: Mapping[int, str] | None = None, super_required: str | bool = False)`. It would merge `{code: {"description": d}}` into `responses` and set `x-requires-super`.
   - It would replace the five local helpers and the inline mutations. This is the least-diff way to make declaring errors the default. It touches many routers (R7), so scope it as a mechanical refactor.
   - `test_schema_generation_does_not_mutate_shared_dicts` (`:159`) must stay green. Build fresh dicts per call.
3. **409 (AC2).** Add 409 to the routes listed under Findings and widen the existing 409 descriptions. Put machine-readable reasons in the description as `details[0].reason` values, as is already done for entitlements. The envelope is attached by `core/openapi.py`, so no new schema work is needed.
4. **`admin:*` (AC3).** Use the AC's `x-requires-super`. Prefer an object or enum over a bare boolean, for example `x-requires-super: true` plus a `description` string such as `"always"` / `"admin app or is_admin permission"`. A boolean forces admin-pan to render "needs admin:*" on routes where it is only conditional (admin app only, privileged role only). This matters because `admin:roles:manage` holders can legitimately call `POST /roles` on non-admin apps.
   - Suggested split: `x-requires-super: true` for `POST /credentials` and for the `admin:*`-declared user-session routes and `DELETE /users/{id}`. The user routes already declare `admin:*` as `x-permission`, so skip `x-requires-super` there. Use `"conditional"` (or a second extension, `x-requires-super-when`) for the rest.
   - Also have the extension appear in the 403 response description.
   - Do not change the 403 code or shape (R6, R9).
5. **Taken key (AC4).** Add `AppKeyTakenError(ConflictError)` with `code = "app_key_taken"` and `details=[{"field":"key","reason":"app_key_taken"}]` in `app/core/errors.py`. Raise it at `rbac/service.py:381` and `:396`. This follows `LastSuperAdminError` / `OrgHasAssignmentsError` at `errors.py:84-107`.
   - This is a deliberate change of a 409 `code`: `conflict` becomes `app_key_taken`. The status is unchanged. The statement "no breaking changes in v1" (R9) is satisfied only if no client branches on `code == "conflict"` for this route. The ticket's requester (admin-pan) asked for it, so it is defensible, but record it in the PR.
   - Keep the `details[0]` form so the UI can map `field=key`.
   - Update the two tests at `tests/test_rbac_apps_api.py:210,226`.
   - Document the codes in the `POST /apps` 409 description (`app_key_taken`, `origin_taken`).
   - Optional: do the same for the org slug later (`slug_taken`). Out of scope here (R7); list it as a follow-up.
6. **Contract test (AC5).** Add a table-driven test to `tests/test_openapi_contract.py`, in the same style as `test_organizations_declared_error_responses` (`:236`):
   - A list of `(method, path, {404?, 409?}, requires_super?)` tuples asserts each declared response references `REF`.
   - Also assert that `x-requires-super` is present exactly on the expected operations, and absent elsewhere.
   - Add a reverse guard: every operation using `x-permission` with a `{key}`/`{*_id}` path param has a 404. This catches future routes.
   - Add one real-behaviour probe for `app_key_taken`, as in `tests/test_admin_guards.py`.

**Risks**
- Over-declaring is cheap but wrong declarations mislead generated types. Only declare codes the service can raise (for example, list routes with a required `org_id` raise 404 for an unknown org).
- Renaming the `code` for a taken key is a client-visible behaviour change inside v1.
- The `PUT /apps/{key}/manifest` route (app credential, R3 class 4) is out of the Bearer scheme. It should be audited for 404/409 declarations but must not get `x-permission` or `x-requires-super`.
- Merge conflict risk with ADM-020: organizations is already done, so ADM-023 must skip it, but the new helper should either leave `organizations/router.py:30` alone or replace it carefully.
- The 409 text in `app_credentials/router.py:33` ("also needs admin:*") conflates a 403 condition with a 409 description. Remove it once `x-requires-super` is added.

**Open questions**
1. Is `app_key_taken` acceptable as a code change from `conflict` (admin-pan's request), or should `conflict` stay and only `details[0].reason` be added? The ticket says "reason/code (e.g.)", so either works. I recommend both code and reason.
2. Should the AC be widened to all routes with 404/409 (my recommendation), or kept to the four named families?
3. Should `x-requires-super` be a bare boolean (as the ticket literally says) or a conditional variant? What does admin-pan need?
4. Is the `PUT /apps/{key}/manifest` route in scope? It is not named in the AC.

Key files: `app/modules/admin_auth/deps.py`, `app/core/openapi.py`, `app/core/errors.py`, `app/modules/rbac/router.py`, `app/modules/rbac/assignments_router.py`, `app/modules/rbac/service.py`, `app/modules/app_credentials/router.py`, `tests/test_openapi_contract.py`.
