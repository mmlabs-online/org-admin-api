Status: READY

# Plan — ADM-023: Declare 404/409 responses and admin:* requirements in OpenAPI

## Spec summary
The ticket covers contract quality for existing endpoints. It adds no new feature scope. The relevant sources:
- Ticket goal: "`/openapi.json` lists every real error response and permission requirement, so admin-pan's generated types match the API." It follows ADM-013 ("OpenAPI contract clients can generate types from").
- Spec §5.1, §5.3, §5.6 and §5.8 define the endpoints whose contract is being completed.
- `docs/identity-architecture.md` §4 (Privileged writes): "Privileged writes need `admin:*` on top of the route permission (ADM-002b, ADR-0008 amendment): anything on the `admin` app, any change across the `is_admin` boundary, granting or revoking a role that is an admin-app role or holds an `is_admin` permission, and the status of a user holding one. Codes: 403 `admin_wildcard_required` …"
- CLAUDE.md R6: "One error shape: `{"error": {"code", "message", "details"}}` … Codes: … 404, 409, 422." R9: "No breaking changes inside v1."

ADM-020 already declares 404/409 for organizations (`app/modules/organizations/router.py`, `tests/test_openapi_contract.py::test_organizations_declared_error_responses`). Per the ticket's dependency note, ADM-023 **skips organizations**.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | 404 is declared on the entitlements, app-credentials and join-requests routes that can return it. Organizations is already done by ADM-020. By default (consult rec. 1) this is widened to every admin route that can raise 404: apps, permissions, role definitions, roles, role assignments and users. | `tests/test_openapi_contract.py::test_admin_declared_error_responses` asserts the exact set of declared `{404, 409}` codes per operation (table below), each with the `ErrorEnvelope` `$ref`. `::test_parameterized_admin_routes_declare_404` is a reverse guard: every operation that has `x-permission` and a `{…}` path param declares 404. |
| 2 | 409 is declared, with the error envelope (R6), on every route that can return it. This includes the AC's named list and the routes the consult found missing: DELETE app, POST/DELETE permission, POST/PATCH/DELETE role, POST role-assignment. | The same table test checks it. The existing `test_every_error_response_references_envelope` keeps checking the envelope `$ref`. |
| 3 | The `admin:*` requirement beyond the declared permission is machine-readable. It is `x-requires-super: "always"` on `POST /apps/{key}/credentials`. It is `x-requires-super: "conditional"` plus an `x-requires-super-when` text on the conditional routes. | `::test_requires_super_extension` asserts that the extension is present exactly on the expected operations with the expected value. It also asserts that it is absent everywhere else, including operations whose `x-permission` is already `admin:*`, and that `x-requires-super-when` appears iff the value is `conditional`. It checks that the 403 description mentions `admin_wildcard_required` on those operations. `::test_require_permission_rejects_bad_arguments` covers the helper's guards. |
| 4 | POST /apps with a taken key returns 409 with a distinct code: `code="app_key_taken"` and `details=[{"field":"key","reason":"app_key_taken"}]`. This is documented in OpenAPI. | Behaviour: `tests/test_rbac_apps_api.py::test_create_app_duplicate_key_409` (both the plain duplicate and the `admin` key) and `::test_create_app_integrity_race_is_409` assert the new code and details. Contract: `tests/test_openapi_contract.py::test_post_apps_409_documents_codes` asserts that the 409 description names `app_key_taken` and `origin_taken`. |
| 5 | A test walks the OpenAPI document and asserts the declared responses above, and the existing tests still pass. | The tests for AC 1–4 above, plus a green `make check`. |

### Expected `{404, 409}` declarations (the table encoded in the test)
Paths are relative to `/api/v1`.

| Method | Path | 404 | 409 | Source of the raise |
|---|---|---|---|---|
| GET | `/apps` | – | – | |
| POST | `/apps` | – | ✓ | `rbac/service.py:381,396` (key), `:384` (origin) |
| GET | `/apps/{key}` | ✓ | – | `:363` |
| PATCH | `/apps/{key}` | ✓ | ✓ | `:241` (`_locked_app`), `:447,514,526,536` |
| DELETE | `/apps/{key}` | ✓ | ✓ | `:241`, `:544-556` |
| GET | `/apps/{key}/permissions` | ✓ | – | `:850` |
| POST | `/apps/{key}/permissions` | ✓ | ✓ | `:241`, `:914,926` |
| PATCH | `/apps/{key}/permissions/{permission_id}` | ✓ | ✓ | `:241,856`, `:980-1009` |
| DELETE | `/apps/{key}/permissions/{permission_id}` | ✓ | ✓ | `:241,856`, `:1040-1047` |
| GET | `/apps/{key}/role-definitions` | ✓ | – | `:1114` |
| GET | `/apps/{key}/role-definitions/{role_id}` | ✓ | – | `:1114,1073` |
| GET | `/apps/{key}/role-definitions/{role_id}/assignments` | ✓ (already) | – | |
| POST | `/apps/{key}/roles` | ✓ | ✓ | `:241`, `:1161,1167` |
| PATCH | `/apps/{key}/roles/{role_id}` | ✓ | ✓ | `:241,1073`, `:1204` |
| DELETE | `/apps/{key}/roles/{role_id}` | ✓ | ✓ | `:241,1073`, `:1236-1247` |
| PUT | `/apps/{key}/roles/{role_id}/permissions` | ✓ | ✓ (already) | `:241,1073`, `:201,1312-1328` |
| POST | `/apps/{key}/credentials` | ✓ | ✓ (already) | `app_credentials/service.py:103` |
| GET | `/apps/{key}/credentials` | ✓ | – | `:186` |
| DELETE | `/apps/{key}/credentials/{credential_id}` | ✓ | – | `:103,197` |
| GET | `/apps/{key}/join-requests` | ✓ | – | `join_requests/service.py:68` |
| POST | `/apps/{key}/join-requests/{request_id}/approve` | ✓ | ✓ (already) | `:75,82,87` |
| POST | `/apps/{key}/join-requests/{request_id}/reject` | ✓ | ✓ (already) | `:75,136` |
| GET | `/organizations/{org_id}/entitlements` | ✓ | – | `entitlements/service.py:40` |
| POST | `/organizations/{org_id}/entitlements` | ✓ | ✓ (already) | `:40,68` (`unknown_app`) |
| PATCH | `/organizations/{org_id}/entitlements/{entitlement_id}` | ✓ | – | `:46` (no 409 path exists) |
| DELETE | `/organizations/{org_id}/entitlements/{entitlement_id}` | ✓ | – | `:46` |
| GET | `/users/{user_id}/role-assignments` | ✓ | – | `rbac/service.py:1414` |
| POST | `/users/{user_id}/role-assignments` | ✓ | ✓ | `:1424`, `:1450,1533,1540,1554` |
| DELETE | `/users/{user_id}/role-assignments/{assignment_id}` | ✓ | ✓ (already) | `:1573,1577`, `:1587,1605` |
| GET | `/users/{user_id}` | ✓ | – | `users/service.py:34` |
| PATCH | `/users/{user_id}` | ✓ | ✓ (already) | `:52`, `:72` |

The user-session routes (404 already declared, `admin:*` declared), organizations (ADM-020), audit and the public or app-credential routes keep their current declarations. The test checks only the rows above, plus the reverse guard.

### Expected `x-requires-super`
| Operation | Value | `x-requires-super-when` (text) |
|---|---|---|
| POST `/apps/{key}/credentials` | `always` | absent |
| PATCH `/apps/{key}` | `conditional` | "Changing the admin app" |
| POST `/apps/{key}/permissions` | `conditional` | "On the admin app, for an is_admin permission, or for an app-scope permission of an org app" |
| PATCH `/apps/{key}/permissions/{permission_id}` | `conditional` | "Changing an admin-app permission, changing is_admin, or changing scope to app on an org app" |
| DELETE `/apps/{key}/permissions/{permission_id}` | `conditional` | "On the admin app" |
| POST `/apps/{key}/roles` | `conditional` | "On the admin app" |
| PATCH `/apps/{key}/roles/{role_id}` | `conditional` | "Renaming an admin-app role" |
| DELETE `/apps/{key}/roles/{role_id}` | `conditional` | "On the admin app" |
| PUT `/apps/{key}/roles/{role_id}/permissions` | `conditional` | "Changing an admin-app role, or adding/removing an is_admin permission" |
| POST `/users/{user_id}/role-assignments` | `conditional` | "Privileged role (admin-app role, or a role holding an is_admin permission)" |
| DELETE `/users/{user_id}/role-assignments/{assignment_id}` | `conditional` | the same as POST |
| PATCH `/users/{user_id}` | `conditional` | "Target user holds a privileged role assignment" |

No other operation carries the extension. The implementer may rephrase the texts. The test checks only that the text is present and non-empty, not its wording.

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/admin_auth/deps.py` | Extend `require_permission` with keyword-only `errors: Mapping[int, str] \| None = None`, `requires_super: Literal["always", "conditional"] \| None = None` and `super_when: str \| None = None`. Build fresh dicts on every call. Add the constants `REQUIRES_SUPER_KEY = "x-requires-super"` and `REQUIRES_SUPER_WHEN_KEY = "x-requires-super-when"`. | AC1–3: one backward-compatible way to declare errors and the `admin:*` requirement. Existing callers are unchanged. |
| `app/core/errors.py` | Add `class AppKeyTakenError(ConflictError)` with `code = "app_key_taken"`. Its `__init__` fixes the message "App key is already taken" and `details=[{"field": "key", "reason": "app_key_taken"}]`. | AC4 |
| `app/modules/rbac/service.py` | Raise `AppKeyTakenError()` at `:381` and `:396` in place of the bare `ConflictError`. | AC4 |
| `app/modules/rbac/router.py` | Delete the local `_with_409`/`_with_404` and use `require_permission(..., errors=..., requires_super=..., super_when=...)` per the tables. Widen the existing 409 descriptions so they name all causes and the `details[0].reason`/code values where stable. | AC1–4 |
| `app/modules/rbac/assignments_router.py` | Replace the inline `_REVOKE` mutation and declare 404/409/`x-requires-super` per the tables. | AC1–3 |
| `app/modules/app_credentials/router.py` | Delete `_with_409`. Add 404 on all three routes and `requires_super="always"` on POST. Drop "also needs admin:*" from the 409 description, since it was a 403 condition in a 409 text. | AC1, AC3 |
| `app/modules/entitlements/router.py` | Delete `_with_409` and add 404 on all four routes. Update the POST 404 description to mention `details[0].reason=unknown_app`. | AC1 |
| `app/modules/join_requests/router.py` | Delete `_with_409` and add 404 on all three routes. | AC1 |
| `app/modules/users/router.py` | `GET /users/{user_id}` gets 404. `PATCH /users/{user_id}` gets 404, 409 and conditional super, which replaces the inline `_WRITE` mutation. `_sessions_route` changes to pass `errors={404: ...}` through the helper. Its output stays identical. | AC1–3 |
| `tests/test_openapi_contract.py` | Add the tests listed below. | AC1–5 |
| `tests/test_rbac_apps_api.py` | Change the expectations at `:210` and `:226` to `app_key_taken` plus details. Also assert the code for the `admin` key at `:211-212`. | AC4 |
| `docs/identity-architecture.md` | In the "Privileged writes" paragraph (around `:192-198`), change `409 conflict/origin_taken` to also list 409 `app_key_taken`. Add one sentence: "OpenAPI marks these operations with `x-requires-super` (`always` / `conditional` + `x-requires-super-when`)." | Keeps the design doc truthful about codes (R6), with a minimal edit. |

Not touched: `app/modules/organizations/router.py` (ADM-020 already declares 404/409), `app/modules/audit/router.py`, `app/modules/rbac/manifest_router.py`, `app/modules/rbac/catalog_router.py` and `app/core/openapi.py`. The post-processor already attaches the envelope to every code >= 400.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_openapi_contract.py › test_admin_declared_error_responses` | AC1, AC2. For each `(method, path)` row of the 404/409 table: `{"404","409"} & set(op["responses"]) == expected`, and each declared one has `content.application/json.schema == {"$ref": REF}`. |
| `tests/test_openapi_contract.py › test_parameterized_admin_routes_declare_404` | AC1 reverse guard. Every operation with `"x-permission"` and `"{"` in its path has `"404"`. Today this also holds for organizations, audit `/{audit_log_id}` (`audit/router.py:78`) and the user sessions, so no exceptions are needed. |
| `tests/test_openapi_contract.py › test_requires_super_extension` | AC3. Builds `{(method, path): op.get("x-requires-super")}` over all operations and compares it to the expected table, with `None` elsewhere. `x-requires-super-when` is a non-empty string iff the value is `conditional`. No operation has both `x-permission == "admin:*"` and `x-requires-super`. On flagged operations, `"admin_wildcard_required"` appears in the 403 description. |
| `tests/test_openapi_contract.py › test_post_apps_409_documents_codes` | AC4 contract. The POST `/api/v1/apps` 409 description contains `app_key_taken` and `origin_taken`. |
| `tests/test_openapi_contract.py › test_require_permission_rejects_bad_arguments` | Helper guards (pure unit test, no DB). It covers `errors={401: ...}` and `errors={403: ...}`, a non-4xx code, `requires_super="conditional"` without `super_when`, `super_when` without `conditional`, and `requires_super` with `key="admin:*"`. Each one raises `ValueError`. Two calls return distinct `responses` dicts, and mutating one does not affect the other or `_ERROR_RESPONSES`. |
| `tests/test_rbac_apps_api.py › test_create_app_duplicate_key_409` (updated) | AC4 behaviour. It checks a plain duplicate and the `admin` key. Both give 409 with `code == "app_key_taken"` and `details == [{"field": "key", "reason": "app_key_taken"}]`. |
| `tests/test_rbac_apps_api.py › test_create_app_integrity_race_is_409` (updated) | AC4 on the race path (`:396`). |
| Existing `test_schema_generation_does_not_mutate_shared_dicts`, `test_every_error_response_references_envelope`, `test_route_auth_classes.py::test_openapi_bearer_scheme`, CLI register tests | Must stay green. For the CLI, `AppKeyTakenError` is a `ConflictError` subclass and `app_key_taken` is not in `cli._REASONS`, so `cli.py:554` still maps it to `MSG_CONFLICT`. |

## Approach
1. **Helper (`deps.py`).** Inside `require_permission`:
   - Validate `key` as today.
   - Build `responses = {c: dict(d) for c, d in _ERROR_RESPONSES.items()}`. This is a fresh copy per call, so the shared dicts stay untouched.
   - For each `errors` item: raise `ValueError` if the code is not `400 <= code < 500` or is 401/403. Otherwise set `responses[code] = {"description": text}`.
   - If `requires_super` is set:
     - Raise `ValueError` if `key == "admin:*"`, or if `(requires_super == "conditional") != (super_when is not None)`.
     - Set `openapi_extra[REQUIRES_SUPER_KEY] = requires_super`, and `openapi_extra[REQUIRES_SUPER_WHEN_KEY] = super_when` when the value is conditional.
     - Set the 403 description to `"Missing permission (permission_denied), or admin:* required (admin_wildcard_required)"`.
   - Enforcement does not change: the services still call `require_super`.
   - Update the module docstring with one line about `errors` and `requires_super`.
2. **Error class (`errors.py`).** Add `AppKeyTakenError` next to `LastSuperAdminError`/`OrgHas*Error`, as described in the files table. Do not add it to `DENIED_ERRORS`, because it is not a guard refusal.
3. **Service (`rbac/service.py`).** Swap the two raises at `:381` and `:396`. Keep `from exc` on the race path.
4. **Routers.** Rewrite each decorator per the two tables:
   - Delete the local `_with_*` helpers and the inline `responses` mutations.
   - Use short module-level description constants where the same text repeats, e.g. `_APP_404 = "Unknown app"` or `"Unknown app, or role not of that app"`.
   - 409 descriptions name the causes and any stable code/reason. For example, POST `/apps` becomes "Key taken (code=app_key_taken) or origin taken (details[0].reason=origin_taken)", and DELETE `/apps/{key}` becomes "admin app, app has role assignments, or app has entitlements (details[0].reason=app_has_entitlements)".
   - Keep `x-permission` values unchanged.
5. **Tests**, then the doc line, then `make check`.

Rules:
- **R1:** Only routers change OpenAPI metadata. The service still raises an `AppError` subclass, and no `HTTPException` is used.
- **R3:** Every route keeps exactly one auth class and its `x-permission`. The extension is additive metadata. The manifest route (class 4) gets no `x-permission`/`x-requires-super`.
- **R6:** The envelope is unchanged. The new code goes through the existing handler, and `core/openapi.py` attaches the `$ref`.
- **R7:** Organizations and audit routers stay untouched, and the manifest audit is a follow-up.
- **R8:** The tests above, plus a green `make check`.
- **R9:** The `code` value change for a taken app key is the only client-visible change. See the owner's decision items.
- **R2, R4, R5, R11:** Not affected (no schema, no new mutation, no config, no credentials).

Estimated diff is about 250–320 lines, most of it decorator rewrites and the table-driven test. That fits one cycle.

## Specialist input used
From `consult-api-platform-engineer.md`:
- **Adopted:** rec. 1, widening 404 to every route that raises it. The caller also directed this default.
- **Adopted:** rec. 2, the shared helper. It is narrowed to a backward-compatible keyword extension of `require_permission`, applied only to the routers in scope. Organizations and audit keep their local helpers to avoid conflicts with ADM-020 (least-risk).
- **Adopted:** rec. 3, adding 409 to the missing routes and widening descriptions. One correction: the consult listed "PATCH/DELETE entitlements" as able to return 409, but `entitlements/service.py:101-145` has no 409 path, so they get 404 only. The consult's "`DELETE /users/{id}`" route does not exist; the user routes are GET/PATCH `/users/{id}` and the session routes.
- **Adopted:** rec. 4, the always vs conditional distinction, the 403 description mention, and skipping `admin:*`-declared routes. I chose a string enum (`"always"`/`"conditional"`) plus `x-requires-super-when`, not a boolean or an object. Generators handle a single-typed string extension cleanly, and it distinguishes the two cases as admin-pan needs.
- **Adopted:** rec. 5, `AppKeyTakenError` with code and reason `app_key_taken`. The org slug (`slug_taken`) is a follow-up.
- **Adopted:** rec. 6, the table test, the exact-presence super test and the reverse 404 guard. For the behaviour probe, I update the existing `test_rbac_apps_api.py` tests rather than adding one to `test_admin_guards.py`, which avoids a duplicate test.
- **Adopted (risk):** the manifest route is out of scope and becomes a follow-up. The note about the 409 text in `app_credentials/router.py:33` is handled.

## Assumptions
- `x-requires-super` is a string, `"always"` or `"conditional"`. Conditional operations add a human-readable `x-requires-super-when`. The ticket's `true` was given only as an example ("e.g.").
- Routes whose `x-permission` is already `admin:*` (the user-session routes) do not get `x-requires-super`, because it would be redundant. The helper refuses that combination.
- The PATCH/DELETE entitlement routes declare 404 only, because no 409 path exists in the service.
- `PUT /apps/{key}/manifest` (R3 class 4) is not named in the AC and is out of scope. Its 404 (`manifest.py:109`) is a follow-up.
- The 404 for an unknown `app_key` in the entitlement grant body is reported as 404. This is today's behaviour, and the description only documents it.

### Needs owner's decision (defaults applied; non-blocking)
1. **The taken-key code changes from `conflict` to `app_key_taken`.** It stays 409, and details `[{"field":"key","reason":"app_key_taken"}]` are added. This is a client-visible change inside v1 (R9). The default applied follows admin-pan's request and the consult. The fallback, if the owner objects, is to keep `code="conflict"` and add only the `details` reason: a one-line change in `AppKeyTakenError`. The PR must call this out.
2. **404 is widened beyond the four named families** to apps, permissions, roles, role definitions, role assignments and users. Default: widen, as the caller directed. It is cheap, and every declaration is backed by a cited raise site.
3. **`x-requires-super` shape:** `"always" | "conditional"` plus `x-requires-super-when`, not a boolean. Admin-pan should confirm that this is what it wants to consume.

## Blocking questions
None

## Split proposal
None. The estimated diff is about 250–320 lines in one coherent contract change.

Follow-ups, not in this diff (R7):
- Organization slug conflict → `slug_taken` code/reason, for symmetry with `app_key_taken`.
- Audit the `PUT /apps/{key}/manifest` 404 declaration (`manifest.py:109`).
- Migrate the `organizations/router.py` `_route` and `audit/router.py` `_read_route` local helpers to `require_permission(errors=...)`.
- Possibly list `app_key_taken` among the examples in the `ErrorBody.code` description.

## Data model / migration
None

## API contract
No new endpoints. Changes to existing ones:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/apps` | unchanged | unchanged | 409 for a taken key: `code` changes from `conflict` to `app_key_taken`, `details=[{"field":"key","reason":"app_key_taken"}]`. Origin conflict is unchanged (`conflict` / `origin_taken`). | `admin:apps:write` (unchanged) |
| (see tables above) | apps, permissions, role-definitions, roles, credentials, join-requests, entitlements, role-assignments, users | unchanged | unchanged | OpenAPI only: 404/409 declared per the table, with the `ErrorEnvelope` schema. Runtime is unchanged. | unchanged `x-permission`. The `x-requires-super` (`always`/`conditional`) and `x-requires-super-when` extensions are added per the table. |
