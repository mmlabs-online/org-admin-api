Status: READY

# Plan — USR-17: Holders of a role — list assignments by role (admin Bearer)

## Spec summary
- §5.3: "Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, except self-scoped reads of the caller's own authorization (`GET /api/v1/me/permissions`, ADR-0008)."
- §5.8 Admin: "staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4)."
- §8 Q6 (open): PII handling. The ticket notes the owner position: "The response exposes users' emails (PII, §8 Q6) to holders of the chosen permission, same as the per-user list and `GET /users` today."
- CLAUDE.md R9 / D18: apps addressed by `key` in `/api/v1/apps/{key}/…`; `role-definitions` is the USR-16 staff read noun, "no new exception". This endpoint is a plain `Page[T]` list under it, so it needs no new R9 exception.

Ticket wording correction (record for the orchestrator; the planner edits only this file): AC3 says the `active` filter works "like the per-user list". The per-user list (`GET /users/{id}/role-assignments`) has **no** `active` filter; it only has `app` and returns every row with a computed `active` flag. USR-17 adds a new filter; what it mirrors is the per-user list's *default behaviour* (all rows, expired ones labelled `active=false`) and its *definition* of `active` (not expired, DB clock).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Bearer admin endpoint `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`, permission `admin:users:read`, declared in `x-permission` | `tests/test_route_auth_classes.py` BEARER map row passes; `tests/test_openapi_contract.py › test_role_holders_operation` asserts `x-permission == "admin:users:read"`; `test_holders_403_*` tests |
| 2 | `Page[T]` (`limit` ≤ 100, `offset`), order `created_at DESC, id`; items = `AssignmentRead` fields + `user_id`, `email`, `display_name` (+ `user_status`, D3), no N+1 | `test_holders_item_shape` (exact key set), `test_holders_order_and_id_tiebreak`, `test_holders_paging`, `test_holders_limit_over_100_is_422`, `test_holders_bounded_queries`, `test_page_role_holders_two_statements` |
| 3 | Filters `active` (true/false; default = all rows, expired marked `active=false`) and `org_id` | `test_holders_expired_default_and_active_filters`, `test_holders_active_boundary`, `test_holders_org_filter`, `test_holders_unknown_org_is_empty_page`, `test_holders_filters_combine_and_total` |
| 4 | 404 for unknown app or role not of that app | `test_holders_404_unknown_app`, `test_holders_404_unknown_role`, `test_holders_404_other_app_role_same_body` |
| 5 | Tests: 401, 403, 404s, expired rows, org-scoped rows, each filter, paging | The test list below; `make check` green |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `RoleHolderRead(AssignmentRead)` with `user_id: uuid.UUID`, `email: str`, `display_name: str \| None`, `user_status: UserStatus`; field descriptions (see API contract) | AC2 response type; explicit field list (security rec. 3) |
| `app/modules/rbac/repository.py` | Add static `_is_active()` (the single `or_(expires_at IS NULL, expires_at > now())`), make `_assignment_rows()` use it (identical SQL), add `RoleHolderRow` type alias and `page_role_holders(role_id, *, active, org_id, limit, offset)` | AC2/AC3; one shared `active` expression for label and filter; no N+1 |
| `app/modules/rbac/service.py` | Add `RoleService.list_holders(key, role_id, *, active, org_id, limit, offset)` and module function `_role_holder_read(row)` (reuses `_assignment_read`) | R1 business layer; reuses `_app` / `_get` 404s |
| `app/modules/rbac/router.py` | Add `GET /{key}/role-definitions/{role_id}/assignments` with `require_permission("admin:users:read")`, `active`/`org_id` query params, `response.headers["Cache-Control"] = "no-store"  # rows carry emails` | AC1; R3 class 1; security rec. 4 |
| `tests/test_rbac_role_holders_api.py` (new) | API + repository tests listed below | AC5, R8 |
| `tests/test_route_auth_classes.py` | Add BEARER row `("GET", f"{V1}/apps/{{key}}/role-definitions/{{role_id}}/assignments"): "admin:users:read"` | R3 route-class guard (no `_fill` change: `{key}`, `{role_id}` already handled) |
| `tests/test_openapi_contract.py` | Add `test_role_holders_operation` (200 ref `Page_RoleHolderRead_`, `x-permission`, 401/403/404 envelope, `RoleHolderRead.required` set) | AC1/AC2 contract |
| `docs/identity-architecture.md` | §4 table: new row for `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` (Bearer `aud=admin` + `admin:users:read`, admin and disabled apps included, no `admin:*` gate, `total` counts assignments, USR-17) | Security rec. 1 / API rec. 2: auth class table must list every endpoint |

No other files. No migration file, no `app/db/models.py` change.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_role_holders_api.py › test_holders_401_without_token` | 401 before anything else |
| `… › test_holders_403_without_users_read` | token with unrelated permission → 403, `details.required == "admin:users:read"` (403 before 404: use a random role id) |
| `… › test_holders_403_with_apps_read_only` | `admin:apps:read` alone → 403 (can see the role, not its holders) |
| `… › test_holders_403_with_roles_assign_only` | write permission does not grant this read |
| `… › test_holders_admin_star_passes` | `admin:*` → 200 |
| `… › test_holders_404_unknown_app` | 404, message "App not found" |
| `… › test_holders_404_unknown_role` | 404, "Role not found" |
| `… › test_holders_404_other_app_role_same_body` | role id of app B under app A → 404 whose JSON body equals the unknown-role 404 body (whole JSON compared) |
| `… › test_holders_item_shape` | exact key set `{id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at, user_id, email, display_name, user_status}`; values match the seeded user/assignment; no `password_hash` |
| `… › test_holders_cache_control_no_store` | `Cache-Control: no-store` on 200 |
| `… › test_holders_expired_default_and_active_filters` | one expired + one live row: default returns both (expired `active=false`); `active=true` → only live; `active=false` → only expired; `total` matches each |
| `… › test_holders_active_boundary` | `expires_at` NULL → true, future → true, past → false |
| `… › test_holders_org_filter` | app-scope row + org A row + org B row: no filter → 3; `org_id=A` → only org A row; `org_id` row's `org_id` echoed |
| `… › test_holders_unknown_org_is_empty_page` | random `org_id` → 200, `items == []`, `total == 0` (no org lookup) |
| `… › test_holders_filters_combine_and_total` | `active` + `org_id` together; `total` equals the filtered count, not the page size |
| `… › test_holders_same_user_multiple_rows` | one user with app-scope + org row for the same role → 2 items, same `user_id`; `total == 2` (documents assignments-not-users) |
| `… › test_holders_order_and_id_tiebreak` | rows with distinct `created_at` come DESC; two rows with forced equal `created_at` are ordered by `id` ASC |
| `… › test_holders_paging` | 3 rows, `limit=2&offset=0` / `offset=2`: no overlap, union = all, `total == 3`, `limit`/`offset` echoed |
| `… › test_holders_limit_over_100_is_422` | `limit=101` → 422 envelope |
| `… › test_holders_invalid_params_422` | non-UUID `role_id`, non-UUID `org_id`, `active=maybe` → 422 |
| `… › test_holders_only_this_role` | assignments of another role of the same app are not listed |
| `… › test_holders_disabled_user_listed` | user with `status=disabled` is listed, `user_status == "disabled"`, `active` still reflects expiry only |
| `… › test_holders_admin_app_super_admin_listed` | principal with only `admin:users:read` lists `admin:super-admin` holders (deliberate decision D2) |
| `… › test_holders_disabled_app_listed` | app with `status=disabled` → 200 |
| `… › test_holders_bounded_queries` | `_capture_sql` (copy of the USR-16 helper): statement count with 1 holder == with 6 holders (`limit` 1 vs 6) |
| `… › test_page_role_holders_two_statements` | call `RbacRepository.page_role_holders` directly under `_capture_sql`: exactly 2 statements (count + rows) |
| `tests/test_openapi_contract.py › test_role_holders_operation` | 200 schema `$ref` `Page_RoleHolderRead_`, `x-permission`, 401/403/404 envelope refs, `RoleHolderRead.required` set |
| `tests/test_route_auth_classes.py` (existing tests, new BEARER row) | route is Bearer + declares `admin:users:read`; no route left unclassified |

Use `tests/rbac_factories.py` (`make_app`, `make_role`, `make_user`, `make_org`, `assign`, `super_admin_role`); set `expires_at` / `created_at` / `users.status` with direct updates in the test where the factory has no parameter.

## Approach
1. **Repository** (`RbacRepository`, R1: SQLAlchemy only).
   - `@staticmethod _is_active() -> ColumnElement[bool]`: returns `or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())`. `_assignment_rows()` uses it for its label (generated SQL unchanged; existing per-user tests must stay green). Other inline copies elsewhere are left alone (R7; follow-up).
   - `RoleHolderRow = tuple[RoleAssignment, str, str, bool, str, str | None, str]` (assignment, app key, role key, active, email, display_name, user status).
   - `page_role_holders(role_id, *, active: bool | None, org_id: uuid.UUID | None, limit, offset) -> tuple[list[RoleHolderRow], int]`:
     - filters list: `RoleAssignment.role_id == role_id`; if `active is True` → `_is_active()`; if `active is False` → `not_(_is_active())`; if `org_id is not None` → `RoleAssignment.org_id == org_id`.
     - count: `select(func.count()).select_from(RoleAssignment).where(*filters)` (no joins: role already validated, `user_id`/`role_id` FKs NOT NULL, so the count cannot drift from the rows).
     - rows: `_assignment_rows().add_columns(User.email, User.display_name, User.status).join(User, User.id == RoleAssignment.user_id).where(*filters).order_by(RoleAssignment.created_at.desc(), RoleAssignment.id).limit().offset()`. Explicit `User` columns only, never the `User` entity (security R-c: `password_hash` can never reach the item). `user_id` comes from `RoleAssignment.user_id`.
     - Exactly 2 statements. Same `func.now()` (transaction timestamp) in both, so label and filter agree.
2. **Service** (`RoleService.list_holders`, R1): `app = await self._app(key)` (by key, no status/admin filter: admin and disabled apps allowed, unknown → `NotFoundError("App not found")`), `role = await self._get(app, role_id)` (unknown or another app's role → the same `NotFoundError("Role not found")`, identical body), then `repo.page_role_holders(role.id, …)`, map rows via `_role_holder_read(row)` = `RoleHolderRead(**_assignment_read(row[:4]).model_dump(), user_id=ra.user_id, email=…, display_name=…, user_status=…)`. No audit (R4: reads are not audited), no logging of rows (R11).
3. **Router** (`rbac/router.py`, R1 HTTP only, R3 class 1): `@router.get("/{key}/role-definitions/{role_id}/assignments", response_model=Page[RoleHolderRead], **require_permission("admin:users:read"))`; params `session, response: Response, key: str, role_id: uuid.UUID, page: PageDep, active: Annotated[bool | None, Query(description=…)] = None, org_id: Annotated[uuid.UUID | None, Query(description=…)] = None`; set `response.headers["Cache-Control"] = "no-store"  # rows carry emails`; return `Page(...)`. Route is declared after `get_role_definition` (no path clash: extra segment). `require_permission` runs as a dependency, so 401/403 precede any 404 (no role-id probing, security F6).
4. **Schema**: `RoleHolderRead(AssignmentRead)` appends fields; `AssignmentRead` itself is untouched (R9 additive). `user_status: UserStatus` (import from `app.modules.users.models`). Field descriptions: `active` = "not expired (database clock); ignores user, org and app status — not 'effective in tokens'"; endpoint description: "`total` counts assignments, not distinct users: a user can appear once per org and once at app scope".
5. **Tests + docs** as listed. `make check` green (R8).

Rules: R1 (layering above), R2 (no schema change → no migration; `alembic check` unaffected), R3 (class 1, `x-permission`, BEARER map), R4 (read, no audit), R5/R11 (no secrets; emails never logged; `no-store`), R6 (404/422/401/403 via existing errors), R7 (only listed files), R8 (tests above), R9 (`Page[T]`, `limit` ≤ 100, UUID ids, `/apps/{key}` per D18, additive schema), R10 (n/a).

### Decisions
- **D1 — Path** `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments` (all four consults). Matches USR-16 shape; no new R9 exception.
- **D2 — Permission `admin:users:read`, no `admin:*` gate for admin-app or privileged roles** (all four consults). Same PII population as `GET /users` and the per-user list; `admin:apps:read` would leak PII to a config-read permission; `admin:roles:assign` is a write permission. **Accepted risk for the owner (security R-a):** any `admin:users:read` holder can pull the full list of super-admin / staff emails in one call (`/apps/admin/role-definitions/{super-admin id}/assignments`). This is not new data (reachable today by crawling `GET /users` + per-user lists) but is now one targeted request — a phishing target list if a staff token is stolen. Mitigations in place: 12 h token TTL (USR-13), Bearer-only admin auth, every privileged write needs `admin:*`. Tightening (gate privileged roles on `admin:*`) would be a new rule and should also cover the per-user list; listed under "Needs owner's decision".
- **D3 — Add `user_status` to items: YES** (identity rec. 4, security rec. 3 optional, DB open question). Additive, read from the same join (no extra query), already visible to the same permission via `UserRead`; without it an access review shows disabled accounts as live holders of privileged roles. `email_verified_at` and `org_status` are **not** added (follow-ups).
- **D4 — `scope=app|org` filter: NOT added; follow-up.** AC3 asks only for `active` and `org_id`; admin-pan has not asked for app-scope-only. Adding it later is additive. `org_id` means "rows in that org only"; app-scope rows are visible unfiltered (they show `org_id: null`). No sentinel values on `org_id`.
- **D5 — `total` counts assignments, not distinct users** (identity risk, DB risk). One user can hold the role at app scope and in several orgs; disabled users are included. Documented in the OpenAPI description and the §4 row; admin-pan must not label it "members" (that is `member_count`, OQ5).
- **D6 — Unknown `org_id` → empty page**, no org lookup (DB, identity, security); same on `org_mode=none` apps (no 422 revealing the mode).

## Specialist input used
- `consult-api-platform-engineer.md`: adopted path, permission, `RoleHolderRead(AssignmentRead)`, reuse `_app`/`_get`, `active: bool | None`, shared predicate, admin app allowed, tests incl. 403 with `admin:apps:read`. Its "no `user_status`" suggestion overridden by D3 (identity + security lean yes; additive and cheap). Its `org_id`-only stance adopted (D4).
- `consult-database-optimizer.md`: adopted 2-statement design, join-free count, `not_()` of the non-NULL predicate, DB clock, no migration (existing `ix_role_assignments_role_id` suffices), tie-on-id test, total = filtered count, user may repeat. Deferred: `(role_id, created_at DESC, id)` index (follow-up at ~10^5 holders).
- `consult-identity-access-engineer.md`: adopted `active` = not expired only (consistent with per-user list), ticket wording correction, `user_status` (D3), disabled holders listed, tests (disabled user, other-app 404, 401<403<404, `limit=101`). Rejected for now: `scope` filter (D4) and `email_verified_at` (follow-ups); `org_status` rejected as advised.
- `consult-security-architect.md`: adopted `admin:users:read` with no `admin:*` gate and recorded R-a (D2), identical 404 bodies, explicit column select + exact key set test, explicit `Cache-Control: no-store` + test, no audit, no row logging, 403 tests for `admin:apps:read` / `admin:roles:assign` only, `admin:*` passes, super-admin holders listed. R-d (explicit `no-store` on `GET /users` etc.) → follow-up, not in this diff (R7).

## Assumptions
- `tests/rbac_factories.assign` creates a `RoleAssignment` for (user, role, org, expires_at); where a parameter is missing, the test updates the row directly.
- `make_client(admin_permissions=[...])` (conftest) issues a token with exactly those permissions, as used by USR-16 tests.
- `RoleHolderRead` subclasses `AssignmentRead`; Pydantic v2 orders inherited fields first, so the JSON field order is AssignmentRead fields then the four new ones.
- Disabled users are listed (no status filter), consistent with the per-user list.

### Needs owner's decision (non-blocking; plan proceeds with the stated default)
1. Accept R-a (D2): privileged/admin-app role holders listed under `admin:users:read` without `admin:*`. Default: accept.
2. `user_status` in items (D3). Default: included.
3. `scope=app|org` filter (D4). Default: not in this ticket; follow-up if admin-pan PAN-015c needs app-scope-only.
4. `total` semantics (D5). Default: counts assignments; documented.
5. Correct the ticket AC3 text "like the per-user list" (that list has no `active` filter).

### Follow-ups (not in this diff, R7)
- `scope=app|org` filter (422 when combined with `org_id`).
- `email_verified_at` (and possibly `org_status`) on holder items.
- `(role_id, created_at DESC, id)` index if a role ever has ~10^5 holders.
- Replace the remaining inline copies of the `active` predicate in `repository.py` with `_is_active()`.
- Explicit `Cache-Control: no-store` on `GET /users`, `GET /users/{id}`, `GET /users/{id}/role-assignments` (security R-d).

## Blocking questions
None

## Split proposal
None (estimated ~300 changed lines, mostly tests; one endpoint).

## Data model / migration
None. Uses existing `role_assignments` (indexed on `role_id`, `org_id`), `roles`, `apps`, `users`. No new model, no Alembic revision; `alembic check` must stay clean.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}/assignments` | path `key` (app key, any status, incl. `admin`), `role_id` (UUID); query `limit` (≤ 100), `offset`, `active` (bool, optional; omitted = all rows), `org_id` (UUID, optional; equality, unknown → empty page) | 200 `Page[RoleHolderRead]`, order `created_at DESC, id`; item = `id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at, user_id, email, display_name, user_status`; header `Cache-Control: no-store`; `total` = matching assignments (not distinct users) | 200, 401, 403, 404 (unknown app: "App not found"; unknown role or role of another app: identical "Role not found"), 422 | `admin:users:read` (Bearer `aud=admin`, R3 class 1) |
