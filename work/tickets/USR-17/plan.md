Status: READY

# Plan — USR-17: Holders of a role — list assignments by role (admin Bearer)

## Spec summary
- spec §5.3: "Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, …"
- spec §5.8 Admin (`docs/spec-v1.md:74`): "staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4)."
- spec §8 Q6 (`docs/spec-v1.md:150`, open): compliance / PII handling. Ticket note: "The response exposes users' emails (PII, §8 Q6) to holders of the chosen permission, same as the per-user list and `GET /users` today."
- `docs/identity-architecture.md` D18 (line 298): apps addressed by `key` under `/api/v1/apps/{key}/…`; `/apps/{key}/role-definitions` (USR-16) is the staff `Page[T]` read of roles, "no new exception". This ticket nests under it.

## Planner decisions (within the ticket's stated latitude)
All four consults agree on D1, D2, D3, D5, D6 and D7. D4 follows the identity and security consults (the API consult leaves it open).

| # | Decision | Reasoning |
|---|---|---|
| D1 | **Path:** `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`, on the Bearer `rbac` router (`app/modules/rbac/router.py`, prefix `/apps`, `dependencies=[Depends(current_admin)]`). | This is the ticket's example path. It nests under USR-16's plural noun, and `assignments` is a plural noun, so **no new R9 exception** is needed. It does not collide with the public catalog `/apps/{key}/roles` or with the `{role_id}` detail route (it is a longer path). `BearerRouteMatcher` classifies it as Bearer, so it gets admin CORS without credentials (ADR-0011). It does not go on `assignments_router` (prefix `/users`). |
| D2 | **Permission:** `admin:users:read`. | Every read that exposes holder PII already uses it: `GET /users`, `GET /users/{id}`, `GET /users/{id}/role-assignments` (`assignments_router.py:28`) and `GET /apps/{key}/join-requests` (`join_requests/router.py:27`). `admin:roles:assign` is a write permission everywhere. Using it as a read gate would hand emails to assign-only staff, which widens PII access, and would push owners to grant write power to support staff (USR-16 D2 rejected the same anti-pattern). `admin:apps:read` would also leak PII to config-readers. No new data is reachable: `admin:users:read` already reaches every holder via `GET /users` + N per-user calls. A narrower `admin:roles:read` would need a seed migration (follow-up). |
| D3 | **No `admin:*` gate on `admin`-app roles.** | ADR-0008 guards G1/G2/G4/G5 cover writes and grants only. USR-16 D3 made the same call, and the per-user list already returns admin-app assignments to any `admin:users:read` holder. Admin-pan's "who holds this role" sheet for staff roles is a core use case. This is an **owner-visible residual risk** (see PR notes) and is locked in by a test, so any future gate is a deliberate change. |
| D4 | **Add `user_status` and `email_verified` to each item** (in addition to the AC's `user_id`, `email`, `display_name`). Names and types match `JoinRequestRead` (`join_requests/schemas.py:16-17`). | `active` only means "not expired" (`repository.py:489`). Disabled or unverified users keep their rows but get no tokens (`repository.py:573-583`). Without these two fields staff would read a disabled holder of `admin:super-admin` as someone with access (consult risks R-b / R-d). Both fields are additive, are already exposed under the same permission by join-requests, and come from the same `users` join at no extra query cost. The AC is a floor ("has the `AssignmentRead` fields plus …"), and this is not a new feature. Fields cannot be removed inside v1 (R9); that is acceptable because these are stable user attributes. |
| D5 | **`active` filter = assignment not expired, database clock**, the same expression as the item field. Omitted → all rows (expired rows shown with `active=false`). Disabled/unverified users are **not** filtered out. | This matches AC3 and the per-user list. The filter and the field share one SQLAlchemy expression, so they cannot disagree. Hiding disabled users would stop staff from finding and revoking their rows. D4 makes their state visible. |
| D6 | **`org_id` filter = exact match only.** An unknown or unrelated org → 200 with an empty page (no existence check). There is no "app-scope only" (`org_id IS NULL`) selector. | This is what AC3 asks for. A sentinel value would break UUID typing and the OpenAPI schema. A later `scope=app|org` param is additive (follow-up). No org lookup means no org-existence oracle and no extra query. |
| D7 | **No new index, no migration.** | `ix_role_assignments_role_id` (`models.py:207`) narrows the query to one role's rows. `org_id` combines with it, or bitmap-ANDs with `ix_role_assignments_org_id`. The `users` join is by PK for ≤ 100 rows. A composite `(role_id, created_at DESC, id)` only pays off at ~10^5 holders on one role and adds write cost. It is a measured follow-up (DB consult rec. 3). |
| D8 | **`Cache-Control: no-store`** set in the handler. | The rows carry emails. This follows the join-requests precedent (`join_requests/router.py:39`) and is defence in depth if the CORS matcher's default `no-store` ever stops applying. |
| D9 | **No audit entry, no rate limit, no email search param.** | R4 covers mutations only (spec OQ4, USR-16 D7). No Bearer admin read is rate-limited. An email query param would put PII into URLs and logs (R11) and is not in the ACs (R7). |
| D10 | **`total` counts assignment rows, not distinct users.** | The same user can hold the role at app scope and in several orgs; each row has its own `id`, `org_id` and `expires_at`. This is stated in the endpoint description. |

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Bearer admin endpoint listing a role's assignments, path consistent with USR-16, permission `admin:users:read` in `x-permission`. | New `BEARER` entry in `tests/test_route_auth_classes.py` → `test_every_route_is_classified`, `test_bearer_route_permission_map`, `test_cors_matcher_agrees_with_bearer_map`, the parametrized 401 sweeps (no token / cookie only / app credential); `tests/test_admin_cors.py::test_no_allow_credentials_on_any_bearer_route` picks it up automatically. `tests/test_openapi_contract.py::test_role_holders_operation` asserts `x-permission == "admin:users:read"`. `test_role_holders_403_without_permission`. |
| 2 | `Page[T]` (`limit` ≤ 100, `offset`), order `created_at DESC, id`; items = `AssignmentRead` fields + `user_id`, `email`, `display_name`, no N+1. | `test_role_holders_item_shape` (exact key set, values), `test_role_holders_paging_and_order` (tie on `created_at` broken by `id`), `test_role_holders_limit_over_100_422`, `test_role_holders_bounded_queries` (equal statement count for 1 vs 6 holders). Code check: `order_by(RoleAssignment.created_at.desc(), RoleAssignment.id)` and one joined select. |
| 3 | Filters `active` (true/false; default includes expired rows with `active=false`) and `org_id`. | `test_role_holders_expired_rows_default`, `test_role_holders_active_filter`, `test_role_holders_active_partition`, `test_role_holders_org_filter`, `test_role_holders_org_filter_unknown_org_empty`, `test_role_holders_filters_combined`, `test_role_holders_bad_query_values_422`. Each asserts `total` matches the filtered items. |
| 4 | 404 for an unknown app, or a role not belonging to that app. | `test_role_holders_404_unknown_app_and_malformed_key`, `test_role_holders_404_unknown_role`, `test_role_holders_404_role_of_other_app` (that role has holders). |
| 5 | Tests: 401, 403, 404 (unknown app, unknown role, role of another app), expired rows, org-scoped rows, each filter, paging. | All tests below; `make check` green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `RoleHolderRead(AssignmentRead)` after `AssignmentRead`. | New response item (AC2, D4). `AssignmentRead` itself is unchanged (it is returned by POST and the per-user list). |
| `app/modules/rbac/repository.py` | Add module-level `_not_expired() -> ColumnElement[bool]`; use it in `_assignment_rows()` (replaces the inline expression at line 489, same SQL). Add `RoleHolderRow` type alias next to `AssignmentRow` (line 36). Add `RbacRepository.page_role_holders(role_id, *, active, org_id, limit, offset) -> tuple[list[RoleHolderRow], int]`. | R1 (SQLAlchemy only here). Sharing the predicate keeps the filter and the `active` field in sync (consult risk R-c). The other inline copies (lines 579, 625, …) are **not** touched (R7; follow-up). |
| `app/modules/rbac/service.py` | Add `RoleService.list_holders(key, role_id, *, active, org_id, limit, offset) -> tuple[list[RoleHolderRead], int]`. | Reuses `RoleService._app` (line 1077) and `_get` (line 1036) for the 404 logic (R1, AC4). |
| `app/modules/rbac/router.py` | Add `GET /{key}/role-definitions/{role_id}/assignments` in the `# --- roles ---` section after `get_role_definition`, with `**require_permission("admin:users:read")`. | AC1. HTTP only (R1). |
| `tests/test_rbac_role_holders_api.py` (new) | API tests listed below. | R8, AC5. |
| `tests/test_route_auth_classes.py` | Add `("GET", f"{V1}/apps/{{key}}/role-definitions/{{role_id}}/assignments"): "admin:users:read"` to `BEARER` (next to lines 96-97). | R3 deny-by-default classification. |
| `tests/test_openapi_contract.py` | Add `test_role_holders_operation`. Add `assert "/api/v1/apps/{key}/role-definitions/{role_id}/assignments" in checked` to `test_every_error_response_references_envelope`. | OpenAPI contract for admin-pan. |
| `docs/identity-architecture.md` | §4 table: add a row after line 177: `Admin: GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` · `Bearer aud=admin + admin:users:read` · "holders of a role (`Page`, rows not distinct users), with holder email, display name, user status and verification; filters `active`, `org_id`; `admin` and disabled apps included; `no-store`; no rate limit" · `USR-17`. Append to D18: "`…/role-definitions/{role_id}/assignments` (USR-17) lists a role's holders; no new exception." | Records D1–D3 where the route table lives. |

No other files. `AssignmentRead`, `_assignment_read`, `page_assignments` (except the shared predicate), the catalog router and the ADM-002b guards must not change in behaviour.

## Tests to write
All tests are in `tests/test_rbac_role_holders_api.py` unless noted. Follow `tests/test_rbac_role_definitions_api.py`: a `rbac_client` fixture with `make_client(admin_permissions=["admin:*"])`, factories from `tests/rbac_factories.py` (`make_app`, `make_role`, `make_user`, `make_org`, `assign`, `super_admin_role`), and a local copy of `_capture_sql`. For expired rows use `assign(..., expires_at=<past>)` (the factory bypasses the API's past-date 422, as `tests/test_rbac_assignments_api.py:163` does). For tied or ordered `created_at`, set `ra.created_at = …` and flush (pattern at `tests/test_rbac_assignments_api.py:122`). `make_user` creates an unverified, active user; set `email_verified_at` / `status` directly where a test needs it.

| Test (file › name) | Covers |
|---|---|
| `test_role_holders_item_shape` | 200 `Page`. Item key set equals exactly `{id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at, user_id, email, display_name, user_status, email_verified}` (no `password_hash` or other user columns). `app == "stoktok"`, `role_key`/`role_id` match the role, `email`/`display_name` match the user, `granted_by` is a UUID (not resolved), `user_status == "active"`, `email_verified` reflects `email_verified_at`. Response header `Cache-Control: no-store`. |
| `test_role_holders_disabled_and_unverified_listed` | A disabled user and an unverified user each holding the role are listed with `active=true`, `user_status == "disabled"` / `email_verified == false` (D4, D5). |
| `test_role_holders_paging_and_order` | 5 rows with distinct `created_at`, plus 2 rows with the **same** `created_at`. `limit=2` across offsets → `total == 7`, concatenation equals the expected order (`created_at DESC`, then `id ASC` for the tie), no duplicates. Response `limit`/`offset` echo the request; default limit 20. |
| `test_role_holders_limit_over_100_422` | `limit=101` → 422 envelope; `offset=-1` → 422. |
| `test_role_holders_expired_rows_default` | No filter: an expired row is present with `active=false`, a non-expired (future `expires_at`) row and a no-expiry row with `active=true`; `total == 3`. |
| `test_role_holders_active_filter` | `active=true` → only the 2 non-expired rows, `total == 2`; `active=false` → only the expired row, `total == 1`. |
| `test_role_holders_active_partition` | For a mix of rows, the ids of `active=true` and `active=false` are disjoint and their union equals the unfiltered ids, and each item's `active` field equals the filter value (filter/field agreement). |
| `test_role_holders_org_filter` | Same role held at app scope (`org_id` null) by U1, in org A by U2, in org B by U3. `org_id=A` → only U2's row, `total == 1`; unfiltered → 3 rows with the right `org_id` values. |
| `test_role_holders_same_user_multiple_rows` | One user holds the role at app scope and in org A → two rows, same `user_id`, distinct `id`/`org_id`; `total == 2` (D10). |
| `test_role_holders_org_filter_unknown_org_empty` | `org_id=<random uuid>` → 200, `items == []`, `total == 0` (D6). |
| `test_role_holders_filters_combined` | `active=true&org_id=A` where org A has one expired and one active row → only the active row; `active=false&org_id=A` → only the expired row. |
| `test_role_holders_bad_query_values_422` | `org_id=not-a-uuid` → 422 envelope; `active=maybe` → 422 envelope. |
| `test_role_holders_bad_role_uuid_422` | `…/role-definitions/not-a-uuid/assignments` → 422 envelope. |
| `test_role_holders_empty_role` | A role with no holders → 200, empty page, `total == 0`. |
| `test_role_holders_only_this_role` | Another role of the same app with holders is not included (filter on `role_id`). |
| `test_role_holders_404_unknown_app_and_malformed_key` | Unknown key → 404 `not_found` envelope; malformed key (`BAD_KEY!`) → 404 (not 422), as USR-16. |
| `test_role_holders_404_unknown_role` | Random role UUID under an existing app → 404, generic message, no email in the body. |
| `test_role_holders_404_role_of_other_app` | A role of app B **that has holders**, requested under app A → 404 (cross-app IDOR). |
| `test_role_holders_admin_app_leaf_permission` | A principal with **only** `admin:users:read` → `GET /apps/admin/role-definitions/{super_admin_id}/assignments` 200; a user assigned `admin:super-admin` in the test appears (locks D3). Do not assert exact totals (seed may hold rows). |
| `test_role_holders_disabled_app_listed` | `make_app(..., status="disabled")` with a role and a holder → 200 with the row. |
| `test_role_holders_403_without_permission` | Principals with only `["admin:apps:read"]` and with only `["admin:roles:assign"]` → 403, `details.required == "admin:users:read"`, on an existing role, an unknown app **and** an unknown role (403 before 404, no existence oracle). `["admin:users:read"]` → 200; `["admin:*"]` → 200. |
| `test_role_holders_401_before_404_and_422` | No token on an unknown app → 401; no token with a malformed `role_id` → 401; no token with `active=maybe` → 401. |
| `test_role_holders_bounded_queries` | Using `_capture_sql`, request a role with 1 holder and a role with 6 holders (in different orgs). The number of SQL statements is equal (no N+1). |
| `tests/test_openapi_contract.py › test_role_holders_operation` | 200 schema `$ref` is the `Page[RoleHolderRead]` component (expected `#/components/schemas/Page_RoleHolderRead_`, as `Page_RoleDefinitionRead_`; the implementer confirms). `x-permission == "admin:users:read"`. `401`, `403`, `404`, `422` reference `ErrorEnvelope`. Query params `active` and `org_id` exist, are optional and have descriptions. `RoleHolderRead.required` covers all 14 fields. |
| `tests/test_route_auth_classes.py` (existing tests, new `BEARER` entry) | Classification, permission map, CORS matcher, 401 sweeps. |

## Approach
1. **Schema** (`schemas.py`). `class RoleHolderRead(AssignmentRead)` adds, in order:
   - `user_id: uuid.UUID`
   - `email: str` (same type as `UserRead.email` and `JoinRequestRead.email`)
   - `display_name: str | None = Field(description="Untrusted user text: render as text only.")` — keep it required in the schema (no default), so use `Field(...)` with no default.
   - `user_status: str = Field(description="Holder's account status (`active` or `disabled`). A disabled holder gets no tokens.")`
   - `email_verified: bool = Field(description="Holder's email is verified. An unverified holder gets no app tokens.")`

   Also redeclare `active: bool = Field(description="Not expired (database clock). Does not reflect user status, email verification or organization status.")` in the subclass (it stays required). Docstring: "One holder row of a role; the same user can appear once per scope (app or org)." Do not modify `AssignmentRead`.
2. **Repository** (`repository.py`). These are the only SQLAlchemy changes (R1).
   - Module-level `def _not_expired() -> ColumnElement[bool]: return or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())`. `_assignment_rows()` uses it (`active = _not_expired()`); the emitted SQL is unchanged.
   - `RoleHolderRow = tuple[RoleAssignment, str, str | None, str, bool, bool]` = (assignment, email, display_name, user status, email verified, active).
   - `page_role_holders(role_id, *, active: bool | None, org_id: uuid.UUID | None, limit, offset)`:
     - `conds = [RoleAssignment.role_id == role_id]`; if `org_id is not None`: `RoleAssignment.org_id == org_id`; if `active is True`: `_not_expired()`; if `active is False`: `not_(_not_expired())`. (The OR is never NULL — `expires_at IS NULL` is true when the column is NULL — so `NOT` is NULL-safe and equals `expires_at IS NOT NULL AND expires_at <= now()`. Add a one-line comment saying so.)
     - Count: `select(func.count()).select_from(RoleAssignment).where(*conds)` — no joins (the FK `ON DELETE CASCADE` on `user_id` guarantees the user exists).
     - Rows: `select(RoleAssignment, User.email, User.display_name, User.status, User.email_verified_at.is_not(None), _not_expired().label("active")).join(User, User.id == RoleAssignment.user_id).where(*conds).order_by(RoleAssignment.created_at.desc(), RoleAssignment.id).limit(limit).offset(offset)`. Inner join (no outer join).
     - Two statements, same transaction, so `now()` is the same for both (transaction timestamp). Return `([tuple(r) …], total or 0)`. Bound parameters only.
3. **Service** (`service.py`). `RoleService.list_holders`:
   - `app = await self._app(key)` (no status or admin filter: disabled and `admin` apps work; unknown or malformed key → 404 "App not found").
   - `role = await self._get(app, role_id)` (app-scoped `get_role`; unknown role or another app's role → 404 "Role not found").
   - `rows, total = await self.repo.page_role_holders(role.id, …)`; build `RoleHolderRead(id=ra.id, app=app.key, role_id=ra.role_id, role_key=role.key, org_id=ra.org_id, granted_by=ra.granted_by, expires_at=ra.expires_at, active=active, created_at=ra.created_at, user_id=ra.user_id, email=email, display_name=display_name, user_status=status, email_verified=verified)`.
   - No lock (read-only), no audit (D9). No email in any error message or log.
4. **Router** (`router.py`), after `get_role_definition`:
   ```python
   @router.get(
       "/{key}/role-definitions/{role_id}/assignments",
       response_model=Page[RoleHolderRead],
       **require_permission("admin:users:read"),
   )
   async def list_role_holders(
       session: SessionDep,
       response: Response,
       key: str,
       role_id: uuid.UUID,
       page: PageDep,
       active: Annotated[bool | None, Query(description=...)] = None,
       org_id: Annotated[uuid.UUID | None, Query(description=...)] = None,
   ) -> Page[RoleHolderRead]:
   ```
   - Docstring (becomes the OpenAPI description): "Holders of one role, newest first. One row per assignment (app scope or one organization), so a user can appear more than once and `total` counts rows. Rows carry holder emails (PII)."
   - `active` description: "Omitted: all rows; expired rows have `active=false`. `true`: not expired; `false`: expired only. Database clock; ignores user status."
   - `org_id` description: "Exact organization. App-scope rows cannot be selected on their own. An unknown org returns an empty page."
   - Set `response.headers["Cache-Control"] = "no-store"  # rows carry emails` (D8). Return `Page(items=…, total=…, limit=page.limit, offset=page.offset)`.
   - Import `Annotated`, `Query`, `Response`, `RoleHolderRead`. No `rate_limit` (D9). Router-level `current_admin` plus `RequirePermission` run before the handler and before query/path validation errors are raised, giving 401 → 403 → 422/404 (security F3, as in USR-16).
5. **Tests** as listed; update `BEARER` and the OpenAPI contract test.
6. **Docs**: the §4 row and the D18 sentence.
7. Run `make check`.

Rules:
- **R1**: router (HTTP + header) → service (404s, assembly) → repository (SQL).
- **R2**: no schema change, no migration (D7).
- **R3**: class 1 Bearer + `admin:users:read`, classified by test.
- **R4**: read-only, no audit.
- **R5/R11**: no config; no emails in logs, error messages or details; `no-store`; no `password_hash` (explicit fields, exact key-set test).
- **R6**: `NotFoundError` and the existing 401/403/422 envelope.
- **R7**: only the listed files. The `_not_expired()` extraction touches `_assignment_rows` only; the other copies are a follow-up.
- **R8**: happy path, 404, 422, 401, 403 tests.
- **R9**: plural noun under D18, `Page[T]` with `limit ≤ 100`, UUID ids, `timestamptz`; additive only.
- **R10**: not affected.

## Specialist input used
- **api-platform-engineer**
  - Adopted: rec. 1 (path, router, `BEARER`, OpenAPI, §4 row), 2 (`admin:users:read`), 3 (error order), 4 (new schema subclassing `AssignmentRead`, flat fields, `AssignmentRead` untouched, `app`/`role_*` kept), 5 (one joined select, role/app from the 404 lookup, separate count, bounded-query test), 6 (filter semantics, expiry only, documented), 7 (exact `org_id`, empty page for unknown org, no sentinel), 8 (`total` counts rows), 9 (order), 10 (OpenAPI descriptions, envelope check), 11 (test list), 12 (in `RoleService`, reusing `_app`/`_get`; no audit or rate limit).
  - OQ1 → expiry only (D5), but with `user_status`/`email_verified` shown (D4). OQ2 → follow-up. OQ3 → answered: `ix_role_assignments_role_id` exists (`models.py:207`). OQ4 → flat.
  - Rec. 6's "write `active=false` explicitly, not `NOT(...)`": adjusted. `NOT` over the shared expression is NULL-safe (the `IS NULL` arm is never NULL), and sharing the expression is what prevents drift. `test_role_holders_active_partition` proves it.
- **identity-access-engineer**
  - Adopted: rec. 1 (permission), 2 (no admin-app gate, locking test), 3 (path, app/role resolution, error order), 4 (separate `RoleHolderRead`, `user_status` + `email_verified` named as in `JoinRequestRead`, `active` and `display_name` descriptions), 5 (shared predicate, filters on count and select, empty page for unknown org), 6 (`no-store`), 7 (no audit/rate limit, generic 404s), 8 (tests: 403 for both neighbouring permissions, 403 before 404, `admin:*`, exact key set, bounded queries). Risk R-e: inner join, covered by the FK cascade and the key-set test.
  - OQ1 → yes (D4). OQ2 (`no-store` on `GET /users` and the per-user list) → follow-up. OQ3 (`admin:roles:read`, admin-app gate) → follow-up / owner note.
- **security-architect**
  - Adopted: rec. 1, 2 (all error-order cases incl. 403 on unknown app and unknown role, 401 with bad UUID / bad query), 3 (explicit fields, `no-store`, `granted_by` stays a UUID, no email search param), 4 (disabled/unverified listed, `user_status` + `email_verified` added), 5 (one joined query, same predicates on count, order, empty page for unknown org), 6, 7.
  - R-a mitigation (`admin:*` for `key == "admin"`) not adopted (D3); surfaced to the owner in the PR. R-c (large roles) → follow-up with EXPLAIN. R-e (XSS) → PR note for admin-pan.
  - Rec. 5's "`RoleAssignment ⨝ Role ⨝ App ⨝ User`" is reduced to `⨝ User`: role and app are already resolved and constant for the page (DB consult rec. 1/2).
- **database-optimizer**
  - Adopted: rec. 1 (new repo method, not changing `page_assignments`), 2 (count on `role_assignments` only, two statements, shared `now()`, no `count() OVER()`), 3 (no index; composite only as a measured follow-up), 4 (single joined select + statement-count test), 6, 7 (explicit `created_at` for ties).
  - Rec. 5's "404 if unknown **or disabled**": rejected, kept consistent with USR-16 (disabled and `admin` apps are listable); `role.app_id != app.id` is handled by the app-scoped `get_role`.
  - Open questions: disabled/unverified users included (D5); no app-scope selector (D6); the suite has `_capture_sql` (copy it); composite index → follow-up, not a ticket now.

## Assumptions
- A1. The seeded `admin` app and `admin:super-admin` role exist in the test DB (as `tests/rbac_factories.py::super_admin_role` relies on).
- A2. `make_app(session, key, status="disabled")` and setting `User.status` / `User.email_verified_at` / `RoleAssignment.created_at` directly on test rows work (`make_app` forwards kwargs; the `created_at` pattern exists in `tests/test_rbac_assignments_api.py:122`).
- A3. FastAPI names the page component `Page_RoleHolderRead_`, as it does `Page_RoleDefinitionRead_`. The implementer uses whatever FastAPI emits.
- A4. `User.status` is stored as the enum's string value (`UserStatus.ACTIVE.value`, `repository.py:581`), so `user_status` is `"active"` / `"disabled"` as in `JoinRequestRead`.
- A5. Expected holder counts per role are tens to low thousands (DB consult). Offset paging and `count(*)` on `ix_role_assignments_role_id` are acceptable at that size.

## Blocking questions
None.

## Owner-visible residual risks (put these in the PR description)
1. **Staff roster in one call.** Any holder of `admin:users:read` can list every holder of `admin:super-admin` and the other admin-app roles, with emails, in one request. The data was already reachable via `GET /users` + per-user calls; this makes it cheaper for a stolen token (12 h TTL). Mitigations in place: short-lived admin tokens, `admin:*` needed to grant/revoke privileged roles (G4), no self-grant. Option if wanted later: require `admin:*` for `key == "admin"`, or introduce `admin:roles:read`.
2. **Bulk PII read is not audited** (spec Q6 open). Same as `GET /users` and join-requests; any audit-on-read decision should cover all three.
3. **Two additive fields beyond the AC** (`user_status`, `email_verified`) are now part of the v1 contract (D4).
4. **admin-pan must render `email` and `display_name` as text**, not HTML (stored XSS from end users into staff sessions otherwise). PAN-015c's responsibility.
5. **Large roles** (e.g. `<app>:member` with ~10^5 holders) are unmeasured; deep offsets and `count(*)` scan the role's rows. Staff-only route, `limit ≤ 100`.

## Split proposal
None. Estimated diff: ~110 lines of source, ~350 lines of tests, no migration, one spec feature.

## Follow-ups (not in this diff)
- `scope=app|org` (or equivalent) filter to select app-scope rows only (additive).
- Composite index `(role_id, created_at DESC, id)` replacing `ix_role_assignments_role_id`, only if `EXPLAIN (ANALYZE, BUFFERS)` on ~200k rows for one role shows p95 > ~100 ms.
- `Cache-Control: no-store` on `GET /users`, `GET /users/{id}` and the per-user assignments list (consistency with join-requests and this route).
- Optional `admin:roles:read` permission (seed migration) or an `admin:*` gate for admin-app holder lists, if the owner wants this sheet narrower than the user directory.
- Use `_not_expired()` in the other inline copies of the expiry predicate (`repository.py` ~579, ~625 and others).
- `user_status` / `email_verified` on the per-user assignments list are not needed (the caller already knows the user); no action.

## Data model / migration
None. Reads `role_assignments` and `users` (plus the existing `apps`/`roles` lookups). Indexes used: `ix_role_assignments_role_id` (role filter), optionally `ix_role_assignments_org_id` (org filter), `users` PK (join). No index change (D7).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}/assignments` | path `key` (app key, any status, `admin` included), `role_id: UUID`; query `active: bool` (optional), `org_id: UUID` (optional), `limit` 1..100 (default 20), `offset` ≥ 0 | `Page[RoleHolderRead]` = `{items, total, limit, offset}`, items ordered `created_at DESC, id ASC`; header `Cache-Control: no-store` | 200; 401 (no or invalid Bearer); 403 (`details.required = "admin:users:read"`); 404 unknown/malformed app key, unknown role, or role of another app; 422 bad `role_id`, `org_id`, `active`, `limit`, `offset` | `admin:users:read` (`x-permission`) |

`RoleHolderRead`:
```json
{
  "id": "uuid",
  "app": "stoktok",
  "role_id": "uuid",
  "role_key": "stoktok:viewer",
  "org_id": null,
  "granted_by": "uuid",
  "expires_at": null,
  "active": true,
  "created_at": "2026-11-18T10:00:00Z",
  "user_id": "uuid",
  "email": "someone@example.com",
  "display_name": "Someone",
  "user_status": "active",
  "email_verified": true
}
```
- `active` = `expires_at IS NULL OR expires_at > now()` (database clock); ignores user, verification and org status.
- Filters combine with AND and apply to `total`. Unknown `org_id` → empty page. `total` counts rows, not distinct users.

No CORS change: Bearer admin CORS from `apps[admin].origins`, without credentials (ADR-0011). No rate limit, no audit.
