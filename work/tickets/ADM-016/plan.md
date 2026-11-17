Status: READY

# Plan — ADM-016: `member_count` on the staff apps list and app detail

## Spec summary
`docs/spec-v1.md` §5.9 "Admin UI read models":

> **Per-app member count**: the "Members" column of the Apps screen. It is an additive `member_count` field on the existing staff apps list and app detail (no new endpoint, no breaking change in v1). Definition (owner, 2026-11-17, OQ5): a member is a distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff).

> Permission (owner, 2026-11-17, OQ4): ... `member_count` uses the existing `admin:apps:read`. Read-only, so no audit entry (R4 covers mutations only).

The ticket notes record the same OQ4/OQ5 answers. ADM-015 already ticked OQ4/OQ5/OQ6 in spec §8. This ticket does not tick them again.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/apps` and `GET /api/v1/apps/{key}` include an additive `member_count` field (R9: no breaking change in v1) | `tests/test_rbac_apps_api.py` › `test_app_read_has_exact_keys` (updated: `APP_READ_KEYS` gains `member_count`, and every other key is unchanged), `test_list_apps_member_count`, `test_get_app_member_count`. The diff shows only an added field on `AppRead`, with no rename, removal or type change |
| 2 | The count follows the OQ5 definition | The router or service calls only `RbacRepository.member_counts` (grep: no second member query in the diff). `test_member_count_app_and_org_scope`, `test_member_count_excludes_expired`, `test_member_count_disabled_excluded_unverified_counted`, `test_member_count_admin_app_counts_staff`, `test_member_count_matches_dashboard` |
| 3 | No N+1 on the list | `test_list_apps_query_count`: the statement count for `GET /api/v1/apps` is the same with 1 extra app and with 5 extra apps (exactly 3: total, page, member counts), and exactly one statement references `role_assignments` |
| 4 | Tests: app-scope and org-scope assignments; expired excluded; disabled/unverified per OQ5 | the tests in AC 2 (API level, through the real endpoints) |
| 5 | Existing app API tests still pass | `make check` is green. The only edit to existing tests is the `APP_READ_KEYS` constant (see Files) |
| 6 | Permission stays `admin:apps:read` (OQ4) | `test_member_count_visible_with_apps_read_only`: a `make_client(admin_permissions=["admin:apps:read"])` call to list and detail returns 200 with `member_count`. `tests/test_route_auth_classes.py` is unchanged and stays green (no route or permission change) |

R8 notes: no new endpoint. The 404, 422, 409, 401 and 403 cases for these routes already exist (`test_get_app_unknown_404`, `test_patch_unknown_app_404`, validation tests, route-class sweeps) and must stay green.

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Split the `AppRead` fields into a base `AppFields(BaseModel)` (`from_attributes=True`, the 11 current fields unchanged). `AppRead(AppFields)` adds `member_count: int = Field(ge=0, description=…)` (required, no default) | additive field on the shared read model. The base class lets the router validate from the ORM object and then add the count |
| `app/modules/rbac/service.py` | `AppService`: add `async def member_counts(self, apps: Sequence[App]) -> dict[uuid.UUID, int]`, which returns `await self.repo.member_counts([a.id for a in apps])`. Leave `list_apps`, `get`, `create`, `update` and `register` signatures unchanged | R1: the router calls the service, not the repository. Reuses the single OQ5 function. The CLI callers (`register`, `set_admin_origins`) are untouched |
| `app/modules/rbac/router.py` | Add a helper `_app_read(app, member_count) -> AppRead` (`AppRead(**AppFields.model_validate(app).model_dump(), member_count=member_count)`). `list_apps`: one `member_counts(items)` call per page, then `counts.get(a.id, 0)`. `get_app`, `register_app` and `update_app`: `member_counts([app])` after the service call | AC 1–3. One batched call per list page, so no N+1 |
| `tests/test_rbac_apps_api.py` | Add `"member_count"` to `APP_READ_KEYS`, and add the new tests below | AC 1–6. The exact-keys test covers list, detail, create and PATCH |

No other files. There is no migration, no model change, no new permission, no route-class map change and no repository change (`RbacRepository.member_counts` from ADM-015 is used as is). `app/modules/stats/*` is not touched. Docs are not changed: spec §5.9 already describes the field. See owner item 3 for a possible §4 note.

## Tests to write
All tests go in `tests/test_rbac_apps_api.py`. They use the `rbac_client` fixture (`admin:*`) unless noted, and the factories `make_app`, `make_role`, `make_org`, `assign` (`tests/rbac_factories.py`), `make_login_user(status=…, verified=…)` (`tests/auth_factories.py`) and `make_staff` (`tests/admin_auth_factories.py`), as `tests/test_rbac_member_counts.py` does.

| Test (file › name) | Covers |
|---|---|
| `test_app_read_has_exact_keys` (existing; constant updated) | `member_count` is present on POST 201, list items, GET detail and PATCH 200. No other key is added or removed |
| `test_list_apps_member_count` | Apps `ma-one` (1 member), `ma-two` (2 members) and `ma-zero` (no assignments). The list shows 1, 2 and 0. Also check a page (`limit=1, offset=…`) that returns only `ma-zero`: count 0, which proves the default-0 path and the batch scoped to page ids |
| `test_get_app_member_count` | Detail of an app with 2 members returns `member_count == 2`. Detail of an app with none returns 0 |
| `test_member_count_app_and_org_scope` | One user holding an app-scope assignment, plus the same role in two orgs, plus a second role on the same app, counts as 1. A second user with an org-scope assignment only makes it 2 (list and detail agree) |
| `test_member_count_excludes_expired` | An assignment with `expires_at` in the past is not counted. One with `expires_at` in the future is counted |
| `test_member_count_disabled_excluded_unverified_counted` | `make_login_user(status="disabled")` is not counted. `make_login_user(verified=False)` (active) is counted |
| `test_member_count_admin_app_counts_staff` | Read `GET /apps/admin` `member_count` as the baseline. Then `make_staff(db_session, "admin:apps:read")`, and the count is baseline + 1 |
| `test_member_count_after_create_and_patch` | POST of a new app returns `member_count == 0`. Assign a user to its member role, PATCH `name`, and the response shows `member_count == 1` |
| `test_member_count_matches_dashboard` | Seed apps with mixed members (app scope, org scope, expired, disabled user, unverified user, an app with 0). Call `GET /api/v1/apps?limit=100` and `GET /api/v1/stats/dashboard` (client with `["admin:*"]`). For every app, `{key: member_count}` from the list equals `{app_key: member_count}` from `members_per_app`, and the key sets are equal. With the migration-seeded `admin` app plus seeded apps, the total stays ≤ 100, so one page covers it |
| `test_member_count_visible_with_apps_read_only` | `make_client(admin_permissions=["admin:apps:read"])`: list and detail return 200 with `member_count` (OQ4: no extra permission needed) |
| `test_list_apps_query_count` | Same pattern as `tests/test_stats_api.py` › `_statements` (a `before_cursor_execute` listener on the session's engine, and `make_client(admin_permissions=["admin:apps:read"])`). Make 1 extra app with a member and count the statements. Then make 4 more apps, each with a member, and count again. Assert `few == many == 3` and that exactly one statement contains `role_assignments`. If the auth override adds statements, adjust the constant but keep `few == many` and the single `role_assignments` statement |
| `test_get_app_query_count` | Detail runs exactly 2 statements (app by key, member counts) |

## Approach
1. **Schema** (`schemas.py`): rename the body of `AppRead` to `class AppFields(BaseModel)` with the same `model_config` and fields. Add `class AppRead(AppFields): member_count: int = Field(ge=0, description="Distinct active users with at least one non-expired role assignment on the app (app scope or any org); unverified email counts; `admin` = staff (OQ5).")`. Keep the field required, with no default, so a missing count is a bug that fails validation and is never shown as a silent 0. `AppFields` is internal (it is not a `response_model`), so OpenAPI keeps the component name `AppRead` and only gains a property.
2. **Service** (`AppService.member_counts`): a pass-through to the shared ADM-015 function. Do not copy the query and do not add filters, because the OQ5 definition must live in one place (`RbacRepository.member_counts` docstring). The repository returns `{}` without a query for an empty list, so an empty page (`offset` past the end) costs nothing extra.
3. **Router**:
   - `list_apps`: `svc = AppService(session)`, `items, total = await svc.list_apps(...)`, `counts = await svc.member_counts(items)`, then `items=[_app_read(a, counts.get(a.id, 0)) for a in items]`. This is one query per page, whatever the page size.
   - `get_app`: `app = await svc.get(key)` (404 unchanged), then `_app_read(app, (await svc.member_counts([app])).get(app.id, 0))`.
   - `register_app` / `update_app`: the same after the service call, inside the same request transaction (the count is read after the mutation and its audit row; it writes nothing). The 4xx paths raise before the count is read, so they are unchanged.
   - R1: the router only calls service methods and Pydantic. It passes the ORM instance to `model_validate`, which is the existing pattern on these routes.
4. **Tests** as listed. Run `make check` and `alembic check` (no drift expected).

Rules: R1 (router → service → repository; the count stays in the repository), R2 (no schema change, so no migration; `alembic check` stays clean), R3 (no route or permission change; still `admin:apps:read` on reads, `admin:apps:write` on POST/PATCH), R4 (read-only addition; mutation audit is unchanged, and no new audit row comes from the count), R5/R11 (no config or secrets), R6 (no new errors), R7 (only the four files), R8 (tests above plus a green `make check`), R9 (additive field only; `Page[T]` and the paths are unchanged), R10 (no change).

## Specialist input used
No new consults. The ADM-015 consults cover this field (orchestrator note in the ticket):
- **database-optimizer** rec. 5 (adopted): "ADM-016 passes the page's app ids, which gives one batched query per list page and no N+1. For app detail, pass `[app.id]`". Return 0 for apps absent from the result. The existing `ix_role_assignments_role_id` is enough, so there is no new index. Risk note (adopted): the helper exposes only a number under `admin:apps:read`.
- **api-platform-engineer** rec. 7 (adopted): one shared function, and "a test asserting the stats `members_per_app` equals the ADM-016 `member_count` for the same data, once ADM-016 lands". That test is `test_member_count_matches_dashboard`.
- ADM-015 `plan.md`: `member_counts(app_ids: Sequence[UUID] | None) -> dict[UUID, int]` is the single home of OQ5. Its own tests (`tests/test_rbac_member_counts.py`) stay. This ticket adds API-level tests on top of them.

## Assumptions
- Members through a suspended org's assignment still count, and so do members of a disabled app. This is inherited from the shared function (ADM-015 owner item 7). It keeps the column equal to the dashboard.
- The count is read at request time and is not cached. At admin load, one extra grouped query per page stays well inside §6 p95 < 200 ms (DB consult: the tables are small, and the filter uses the role_id index).
- For the list, the count is taken right after the page query in the same transaction (READ COMMITTED). The two can differ by a concurrent change. That is acceptable for a display column, as with the dashboard ("approximate, not a snapshot").
- The CLI (`register-app`, `set-admin-origins`) prints its own output and does not use `AppRead`, so it is unchanged.

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **`member_count` also appears on the `POST /api/v1/apps` (201) and `PATCH /api/v1/apps/{key}` (200) responses**. This is the default. Reason: all four routes return the same `AppRead` model, and the existing contract test (`test_app_read_has_exact_keys`) requires identical keys on all of them. One read model keeps the admin-pan cache consistent after an edit (the PATCH response replaces the list row without dropping the column). The cost is one extra small query per create or PATCH. The spec names only list and detail. The alternative is a separate `AppDetailRead` used only on GET, which would make POST/PATCH return a different shape from GET. That is also additive, but less consistent. Either choice is non-breaking for v1 clients.
2. **The field is required (`int`, never null)**: every response that carries it computes it. The default is not `int | None`.
3. **Docs**: no change to `docs/identity-architecture.md` §4 (the apps rows describe auth, not fields). If the owner wants the field listed there, it is a one-line follow-up.

## Blocking questions
None.

## Split proposal
None. This is one small, additive feature: about 30 lines of production code and about 200 lines of tests.

## Data model / migration
None. There is no schema change, no new index (DB consult: `ix_role_assignments_role_id` serves `Role.app_id IN (…)` through the join) and no data migration (no new permission, per OQ4).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps` | `limit` ≤ 100, `offset` (unchanged) | `Page[AppRead]`; each item gains `member_count: int` (≥ 0) | 200, 401, 403, 422 (unchanged) | `admin:apps:read` (unchanged) |
| GET | `/api/v1/apps/{key}` | none | `AppRead` + `member_count` | 200, 401, 403, 404 (unchanged) | `admin:apps:read` (unchanged) |
| POST | `/api/v1/apps` | `AppCreate` (unchanged) | `AppRead` + `member_count` (0 for a new app) | 201, 401, 403, 409, 422 (unchanged) | `admin:apps:write` (unchanged) |
| PATCH | `/api/v1/apps/{key}` | `AppUpdate` (unchanged) | `AppRead` + `member_count` | 200, 401, 403, 404, 409, 422 (unchanged) | `admin:apps:write` (unchanged) |

`DELETE /api/v1/apps/{key}` (204, no body) is unchanged. `member_count` follows the OQ5 definition and is computed by `RbacRepository.member_counts`, the same function behind the dashboard's `members_per_app`.
