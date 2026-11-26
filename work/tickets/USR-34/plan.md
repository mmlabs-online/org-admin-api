Status: READY

# Plan — USR-34: Users list — opt-in active-access summary (`include=access`)

> **Predicted tier: Medium.** Every file is in `app/modules/users/**` (default `medium`) or in `tests/**` / `docs/**`
> (`low`); none matches a `high:` glob of `.github/risk-tiers.yml` / "High paths" in `.claude/loop.config.md`.
> `python3 -I .github/scripts/risk_tier.py --files …` could not be run here (no shell in this planning session); the
> tier is worked out from the globs (precedence high > low > default, the PR takes its highest file). The access read
> and the shared predicate live in `app/modules/users/repository.py`, **not** in `app/modules/rbac/**` (High), so
> `/run-batch --auto-merge` can merge it after Ming's "cho merge" for Medium in the batch round. Ticket Gate is
> `plan-approval`, so it stops at PLAN anyway. Batch 2026-11-26-2; USR-35 builds on it (shared predicate, below).
> Written for the **default answers** of "Blocking questions for Ming" (Q1-Q6).

## Scope summary
- Ticket Goal: "Staff see each user's current access (active roles per app, with scope) directly in the users list,
  without one request per row. Requested by admin-pan for PAN-032 (access column)."
- Ticket Scope: "`GET /api/v1/users?include=access` (permission `admin:users:read`, unchanged). Without `include` the
  response is unchanged." / "Each item gains `access: [{app, app_status, roles: [{assignment_id, role_id, role_key,
  org_id | null, expires_at | null}]}]` and `access_truncated`; active assignments only (`expires_at IS NULL OR >
  now()`), grouped by app key; assignments in disabled apps included with `app_status`." / "Cap: at most 20
  assignments per user, ordered by app key, role key, app scope before org scope; `access_truncated: true` when the
  user has more." / "One extra query per page for all users of the page (no N+1)." / "Proposed defaults (spec, not
  answered): user with none → `access: []`; `include` accepts only `access`, anything else → 422."
- Ticket Out of scope: "Role or org names (USR-27); filters (USR-35); any write."
- Spec §5.15 "Decided by the owner (2026-11-25)" (spec-v1.md:343): "**"Access"** = **active** role assignments only
  (app + role + scope); expired ones are excluded."
- Spec §5.15 Part 1 (spec-v1.md:352-358):
  - :352 "`GET /api/v1/users?include=access` — opt-in; without it the response is unchanged. Permission:
    `admin:users:read`, as for the list and for reading a user's assignments (no extra permission)."
  - :353 "Additive (R9); whether the fields are absent or `null` without `include=access` is PLAN's. Proposed (not
    answered): a user with no active assignment gets `access: []`; `include` accepts only `access`, anything else →
    422."
  - :354 "**Assignments in disabled apps are included** with `app_status` (`active` | `disabled`) … **No role or org
    names** — app key and `role_key` are enough (names overlap USR-27)." (OQ75, spec :551)
  - :355 "**Per-user cap (owner, 2026-11-26, OQ74):** at most **20** assignments per user in `access`, ordered by app
    key, role key, then app scope before org scope; `access_truncated: true` when the user has more." (OQ74, :550)
  - :356 "**No N+1:** one extra query per page (all users of the page at once), never one per row; reads must meet §6
    (p95 < 200 ms)."
  - :357 "Read-only: no audit row (R4 covers mutations only)."
  - :358 "**Tests (R8):** with and without `include=access`; expired assignments excluded; app-scope and org-scope
    rows; disabled app with `app_status`; user with none; cap of 20 and `access_truncated`; 401/403; query count does
    not grow with page size; 422 for an unknown `include` value."
- Spec §5.15 Part 2 (:363, :365) for the USR-35 hand-over only: filters "Combinable with … `include=access`"; "with
  `include=access`, the `access` array always shows all of the user's active access, not only the filtered app or
  role." (USR-35 implements; this plan only makes the access read filter-independent.)
- `work/decisions.md`: nothing on §5.15 / the users list.
- CLAUDE.md R1 (router HTTP only; service groups; repository SQL), R3 class 1 (Bearer, `admin:users:read`; no new
  route), R4 (read-only → no audit), R6 (422 through the standard envelope), R7, R8, R9 (additive, `Page[T]`, `limit`
  ≤ 100, no breaking change), R11 (no PII in logs; nothing logged).

## Acceptance criteria → verification
New tests go into `tests/test_users_access.py` (new) unless another file is named.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | `include=access` returns `access` and `access_truncated` per item with the shape above | `test_include_access_shape` (one user, one app-scope assignment with `expires_at` in +30 days → item keys = `USER_READ_KEYS ∪ {access, access_truncated}`; `access == [{"app", "app_status": "active", "roles": [{"assignment_id", "role_id", "role_key", "org_id": None, "expires_at": <iso>}]}]`, exact key sets at every level, nullable keys present) |
| 1b | Without `include` the response matches today's `UserRead` page | `test_without_include_is_unchanged` (same fixture as 1a: body equals `Page[UserRead](…).model_dump(mode="json")` built from the same rows, item key **order** equals `list(UserRead.model_fields)`, `"access"` not in `resp.text`, no statement mentions `role_assignments`); existing `tests/test_users_admin.py` › `test_user_read_has_exact_keys` stays green **unchanged**; `test_get_and_patch_never_carry_access` (`GET /users/{id}` and `PATCH` keys = `USER_READ_KEYS` for a user with assignments) |
| 1c | A user with no active assignment | `test_user_without_access_gets_empty_list` (`access == []`, `access_truncated is False`; also a user with only expired rows) |
| 2a | Expired assignments are excluded | `test_expired_assignments_excluded` (one expired -1 day, one active +30 days, one without expiry → only the two active, in order) |
| 2b | App-scope and org-scope rows both appear | `test_app_and_org_scope_rows` (same role at app scope and in org O, another role in org O → three roles in one app group, app scope first, `org_id` set on the org rows) |
| 2c | Disabled-app assignments appear with `app_status: disabled` | `test_disabled_app_listed_with_status` (`make_app(…, status="disabled")` → group present, `app_status == "disabled"`; active app group `"active"`) |
| 3a | > 20 active assignments → exactly 20 in spec order, `access_truncated: true` | `test_cap_truncates_at_20_in_order` (22 active across 3 apps, plus 3 expired that must not count → 20 roles; flattened `(app, role_key, org_id is None)` list equals the expected sorted first 20; `access_truncated is True`; the last app group may be partial) |
| 3b | Boundary: exactly 20 → not truncated; 21 → truncated | `test_cap_boundary` (parametrized 20 → 20 rows, `False`; 21 → 20 rows, `True`) |
| 3c | The cap is per user, not per page | `test_cap_is_per_user` (user A with 25, user B with 2 on the same page → A 20 + `True`, B 2 + `False`) |
| 3d | Order: app key, role key, app scope before org scope (byte order) | `test_access_order` (apps `zeta`, `alpha`, `a-c` and `ab` (Q4: byte order → `a-c` before `ab`), roles inserted out of order, same role app + two orgs → app scope first, then org rows by `org_id`) |
| 4 | Query count does not grow with page size | `test_query_count_constant` (`before_cursor_execute` listener, pattern `tests/test_users_admin.py:424-436`; 5 users with 3 assignments each; `include=access` at `limit=1` and `limit=50` → equal statement counts; exactly one statement contains `FROM role_assignments` in each run); `test_query_count_without_include` (no statement contains `role_assignments`) |
| 5a | Unknown `include` value → 422 | `test_include_rejects_bad_values` parametrized `include=bogus`, `include=`, `include=ACCESS`, `include=access,bogus` → 422 `validation_error`, `details` points at `include` (query); `test_include_repeated_is_idempotent` (`include=access&include=access` → 200, same body as once) |
| 5b | No token → 401 | `test_include_access_requires_token` (`client` fixture, `GET /api/v1/users?include=access` → 401 envelope) |
| 5c | Token without `admin:users:read` → 403 | `test_include_access_requires_users_read` (`make_client(admin_permissions=["admin:orgs:read"])` → 403; and `["admin:users:read"]` alone → 200 with `access`) |
| 5d | Pagination unchanged with `include` | `test_include_access_pagination` (`limit=2&offset=2` → same ids/order/`total` as without `include`; empty page past the end → `items: []`, no access statement) |
| 6 | OpenAPI documents the new parameter and fields (additive) | `tests/test_openapi_contract.py` › `test_users_list_operation` (parameter `include` in query, array of enum `["access"]`, not required; 200 schema `anyOf` of `#/components/schemas/Page_UserWithAccessRead_` and `#/components/schemas/Page_UserRead_`; `UserRead` component `required` unchanged (7 keys); `UserWithAccessRead.required ⊇ {access, access_truncated}`; `AccessRole.required == {assignment_id, role_id, role_key, org_id, expires_at}`; `AppAccess.required == {app, app_status, roles}`; `x-permission == "admin:users:read"`); existing `test_every_error_response_references_envelope` green |
| — | Everything | `make check` green; `uv run alembic check` not needed (no model change) but harmless |

404: not applicable (list route, no path id). Conflict: not applicable (read-only). R8 is met by 401/403/422 + happy
path.

## Ming verification after merge
- After deploy: admin-pan PAN-032 shows the access chips for a page of 100 users, "+N more" for a user with more than
  20 active assignments, and "app disabled" for a disabled app; optional: p95 of `GET /api/v1/users?include=access`
  under 200 ms on the production dashboard (§6).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/users/repository.py` | New module-level `active_assignment_clause()` (shared with USR-35); `AccessRow` `NamedTuple`; new `UserRepository.list_access(user_ids, *, per_user)` (one windowed query); new imports of `App`, `Role`, `RoleAssignment` from `app.modules.rbac.models` (import only, no edit there) | AC2-4; R1 (SQL only here) | no |
| `app/modules/users/schemas.py` | `UserInclude` StrEnum (`ACCESS = "access"`); `AccessRole`, `AppAccess`, `UserWithAccessRead(UserRead)`; `UserRead` **unchanged** | AC1, AC6; R9 additive | no |
| `app/modules/users/service.py` | `ACCESS_CAP = 20`; new `UserService.access_summaries(user_ids) -> dict[uuid.UUID, tuple[list[AppAccess], bool]]` (cap+1 probe, truncate, group by app); `list()` unchanged | AC1-3; R1 (grouping/cap rule in the service) | no |
| `app/modules/users/router.py` | `list_users` (:26-41): `include` query param, union `response_model`, docstring/description; builds `UserWithAccessRead` only with `include=access` | AC1, AC5, AC6 | no |
| `tests/test_users_access.py` (new) | All tests of the table except the OpenAPI one | AC1-5 | no |
| `tests/test_openapi_contract.py` | `test_users_list_operation` (new function) | AC6 | no |
| `docs/spec-v1.md` | §5.15 Part 1 (:353): record the PLAN decisions (absent without `include`; repeatable `include`, only `access`; empty list for none; byte order; partial last group), dated 2026-11-26, "USR-34" | Spec stays the source of truth (it says "PLAN's") | no |

Not changed: `app/modules/rbac/**` (High; `RbacRepository._is_active` at `rbac/repository.py:597-599` and the inline
copies stay; dedupe is a follow-up), `app/core/**`, `app/api/deps.py`, `app/db/**`, migrations,
`tests/test_route_auth_classes.py` (no new route, class unchanged), `tests/test_users_admin.py` (unchanged, must stay
green).

## Data model / migration
None. Reads `role_assignments` (`uq_role_assignments_user_role_org`, leading `user_id`, `rbac/models.py:222-228`),
joins `roles` and `apps` by primary key. At `limit=100` at most 100 × 21 = 2100 rows. The implementer records
`EXPLAIN (ANALYZE, BUFFERS)` of the access query on the test DB (100 users × 25 assignments) in impl-log; an index is a
follow-up only if it is not an index scan on `uq_role_assignments_user_role_org` (db consult rec 8).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users` | unchanged `email` (1-254), `status`, `limit` ≤ 100, `offset`; **new** `include` (query, repeatable, values: `access`; optional) | Without `include`: `Page[UserRead]`, byte-for-byte as today. With `include=access`: `Page[UserWithAccessRead]` = every `UserRead` field + `access: AppAccess[]` + `access_truncated: bool` (both always present, never `null`) | 200; 401; 403; 422 `validation_error` (bad `include`, as for bad `status`/`limit`) | `admin:users:read` (unchanged, R3 class 1) |

Schemas (always-present keys; nullable keys emitted as `null`):
- `AccessRole {assignment_id: uuid, role_id: uuid, role_key: str, org_id: uuid | null, expires_at: datetime | null}`
- `AppAccess {app: str (app key), app_status: "active" | "disabled" (AppStatus), roles: AccessRole[]}`
- Order inside `access`: apps by key; within an app, roles by `role_key`, app scope (`org_id: null`) before org
  scope, then `org_id`, then `assignment_id`; string order is byte order (`COLLATE "C"`). At most 20 roles in total per
  user; `access_truncated: true` when the user has more active assignments; the last app group can be partial.
- The route description says this, plus "the full list: `GET /users/{id}/role-assignments`" and "`include` is not
  narrowed by other filters".

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_users_access.py` › `test_include_access_shape` | 1a |
| › `test_without_include_is_unchanged`, `test_get_and_patch_never_carry_access` | 1b |
| › `test_user_without_access_gets_empty_list` | 1c |
| › `test_expired_assignments_excluded` | 2a |
| › `test_app_and_org_scope_rows` | 2b |
| › `test_disabled_app_listed_with_status` | 2c |
| › `test_cap_truncates_at_20_in_order`, `test_cap_boundary` (param 20/21), `test_cap_is_per_user` | 3a-3c |
| › `test_access_order` | 3d, Q4 |
| › `test_query_count_constant`, `test_query_count_without_include` | 4 (no N+1) |
| › `test_include_rejects_bad_values` (param), `test_include_repeated_is_idempotent` | 5a |
| › `test_include_access_requires_token`, `test_include_access_requires_users_read` | 5b, 5c |
| › `test_include_access_pagination` | 5d |
| `tests/test_openapi_contract.py` › `test_users_list_operation` | 6 |

Fixtures (module-local in the new file): `users_client` as in `tests/test_users_admin.py:40-43`
(`make_client(admin_permissions=["admin:*"])`); `make_user`, `make_app`, `make_org`, `make_role`, `assign` from
`tests/rbac_factories.py` (role keys must match `ROLE_KEY_RE`, e.g. `alpha:viewer`; app keys `APP_KEY_RE`). Expiry
values far from the clock (±1 day / +30 days): `func.now()` is the test transaction start. Users are found by id in
`items` (seeded rows may exist); for order-sensitive list tests filter with `email=` on a unique fragment. Error calls
last in a test (an error response rolls the shared transaction back).

## Approach
1. **Shared predicate** (`users/repository.py`, module level, above the class):
   ```python
   def active_assignment_clause() -> ColumnElement[bool]:
       """`role_assignments` row is active: not expired by the database clock (spec §5.15).

       Shared by the users list access summary (USR-34) and its filters (USR-35). Same rule as
       `RbacRepository._is_active` (rbac/repository.py:597-599). Uncorrelated: callers add their own
       `RoleAssignment.user_id == …` condition."""
       return or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())
   ```
   **Contract for USR-35:** `from app.modules.users.repository import active_assignment_clause` (or a direct call
   inside `UserRepository`), signature `() -> ColumnElement[bool]`, name fixed. USR-35 uses it in its correlated
   `EXISTS` (`.correlate(User)`).
2. **Access read** (`UserRepository.list_access`, R1):
   ```python
   class AccessRow(NamedTuple):
       user_id: uuid.UUID; assignment_id: uuid.UUID; role_id: uuid.UUID; role_key: str
       org_id: uuid.UUID | None; expires_at: datetime | None; app_key: str; app_status: str

   async def list_access(self, user_ids: Sequence[uuid.UUID], *, per_user: int) -> list[AccessRow]:
   ```
   One statement: inner select `RoleAssignment.user_id, .id, .role_id, .org_id, .expires_at, Role.key, App.key,
   App.status` + `func.row_number().over(partition_by=RoleAssignment.user_id, order_by=(App.key.collate("C"),
   Role.key.collate("C"), RoleAssignment.org_id.is_not(None), RoleAssignment.org_id, RoleAssignment.id))`, `JOIN roles
   ON roles.id = role_assignments.role_id JOIN apps ON apps.id = roles.app_id`, `WHERE role_assignments.user_id IN
   (:ids) AND active_assignment_clause()`; outer `WHERE rn <= :per_user ORDER BY user_id, rn`. No app-status filter
   (OQ75), no user-status filter. Empty `user_ids` → return `[]` without a query.
3. **Service** (`UserService.access_summaries(user_ids)`): call `list_access(user_ids, per_user=ACCESS_CAP + 1)`;
   group rows by `user_id` (rows arrive in final order); per user `truncated = len(rows) > ACCESS_CAP`, keep the first
   `ACCESS_CAP`; group consecutive rows by `app_key` into `AppAccess(app, app_status, roles=[AccessRole(…)])`. Return
   an entry for **every** requested id (`([], False)` when none), so the router has no branching on data. No audit,
   no logging (R4, R11).
4. **Schemas:** `class UserInclude(StrEnum): ACCESS = "access"`; `AccessRole`, `AppAccess` (`app_status: AppStatus`
   from `app.modules.rbac.models`), `class UserWithAccessRead(UserRead): access: list[AppAccess];
   access_truncated: bool` (required, no defaults, with `Field(description=…)`). `UserRead` is not touched, so
   `GET /users/{id}` and `PATCH` cannot leak `access` (api consult risk 1).
5. **Router** (`list_users`): new parameter `include: Annotated[list[UserInclude] | None, Query(description=…)] =
   None` (FastAPI reads a repeated query key into the list; an unknown or empty value fails enum validation → the
   standard 422 `validation_error` envelope). `response_model=Page[UserWithAccessRead] | Page[UserRead]` (superset
   first, so union validation picks it when `access` is present and falls back to `Page[UserRead]` when it is absent;
   precedent `auth/router.py:259`), return annotation the same union. Without `include`: exactly today's code path
   (`UserRead.model_validate`). With `UserInclude.ACCESS in include`: `summaries = await service.access_summaries([u.id
   for u in items])`, items `UserWithAccessRead(**UserRead.model_validate(u).model_dump(), access=…,
   access_truncated=…)`. Never `response_model_exclude_none` (would drop `display_name: null`, `expires_at: null`).
   If `test_without_include_is_unchanged` or `test_include_access_shape` shows the union picking the wrong member,
   the fallback is Q3's alternative (one model, optional fields, `response_model_exclude_unset=True`), recorded in
   impl-log; the two tests must pass unchanged either way.
6. **Query count:** with `include=access` the request runs count + page + access (auth is overridden in tests); the
   test compares `limit=1` and `limit=50`, not an absolute number.
7. **Spec** §5.15 :353: replace "whether the fields are absent or `null` … is PLAN's. Proposed (not answered): …" by
   the dated decisions (Q1-Q5 answers).
8. `make check`; EXPLAIN in impl-log.

Rules: R1 (SQL in repository, cap/grouping in service, router HTTP only), R3 (class and permission unchanged), R4 (no
mutation), R6 (standard 422), R8 (happy, 401/403/422, pagination), R9 (additive, opt-in, `Page[T]`), R11 (nothing
logged).

## Specialist input used
- **api-platform-engineer:** rec 1 separate subclass + `UserRead` untouched + union `response_model` — adopted (name
  `UserWithAccessRead` from the db consult); `response_model_exclude_unset` — **not adopted** (not needed: the no-include
  path builds plain `UserRead`; exclude_unset would make output depend on how models were built); rec 2 absent without
  `include` — adopted (Q1); rec 3 `list[IncludeField]` repeatable, unknown/empty → 422, duplicates idempotent —
  adopted (Q2), as `list[UserInclude] | None = None` (no mutable default); rec 4 schemas — adopted, `AppStatus`
  reused; rec 5 order + cap+1 + group after cap, partial groups documented — adopted (Q4); rec 6 one shared predicate,
  batch read by page ids, `[]`/`false` for none, R1 — adopted, placed in the users repository (non-High); rec 7
  additive — adopted; rec 8 no new permission — adopted; rec 9 query counter at two page sizes + no-`access` snapshot
  — adopted (sizes 1 and 50). Risk "two page types in generated SDKs" → Assumptions (tell admin-pan).
- **database-optimizer:** rec 1 three queries, access only with `include` — adopted; rec 2 CTE/subquery with
  `row_number()` partitioned per user, `rn <= 21` — adopted (`org_id`, `id` tiebreak); rec 3 truncation in Python, no
  `count(*) OVER` — adopted; rec 4 shared helper, `func.now()` only — adopted as `active_assignment_clause()`; rec 5
  access read in `users/repository.py` → off the rbac High path — adopted; rec 6 `Literal["access"]` single value —
  **not adopted** in favour of the api consult's repeatable enum (Q2); separate response model — adopted; rec 7
  query-count test — adopted; rec 8 no index — adopted, EXPLAIN in impl-log.
- **USR-35 consults:** database-optimizer rec 4 / Q3 (predicate in `rbac/models.py`, High) — **not adopted**: the api
  consult's alternative (users repository, rbac is High) keeps both tickets Medium; db rec 5 / Q2 (bundle USR-34 +
  USR-35) — **not adopted** by default (Q6; api consult rec 8 allows separate PRs when USR-34 supplies the helper);
  both consults' "`include=access` not narrowed by filters" — the access read here takes only page ids, so it holds.

## Blocking questions
None block planning; the decisions below have defaults and this plan is written for them. They go to Ming in the
batch approval round (section "Blocking questions for Ming").

## Split proposal
None. Estimate: ~110 changed lines of application code, ~320 lines of tests, ~6 lines of spec; one review cycle.

## Blocking questions for Ming
1. **Without `include`: absent or `null`?** *Default: absent* — the response is byte-for-byte today's `Page[UserRead]`
   (both consults). Alternative: always present, `null` without `include` (changes every list response).
2. **`include` shape.** *Default: repeatable list (`include=access`, `include=access&include=x` later), only `access`
   valid now; unknown, empty (`include=`) or comma-joined (`include=access,x`) → 422 `validation_error`; duplicates
   idempotent* (api consult). Alternative: a single `Literal["access"]` value (db consult) or comma-separated values.
3. **Response model.** *Default: separate `UserWithAccessRead(UserRead)` with required `access` / `access_truncated`,
   route `response_model=Page[UserWithAccessRead] | Page[UserRead]` (OpenAPI `anyOf`; admin-pan's generated client sees
   two page types)*. Alternative: one `Page[UserWithAccessRead]` with optional fields and
   `response_model_exclude_unset=True` (one type, but the fields look optional in OpenAPI).
4. **Order and cap.** *Default: `app` key, then `role_key`, then app scope before org scope, then `org_id`, then
   `assignment_id`; string order is byte order (`COLLATE "C"`, so `a-c` sorts before `ab`, independent of the database
   locale); cap 20 per user over the flattened list, `access_truncated` = the user has a 21st active row; the last app
   group may be partial (no group-level rounding).* Alternative: database default collation; or cap whole app groups.
5. **User with no active assignment.** *Default (spec proposal): `access: []`, `access_truncated: false`.*
6. **Bundle with USR-35?** *Default: no — separate PRs, sequential: USR-34 first (ships `active_assignment_clause`),
   USR-35 branches from `main` after USR-34 merges (or stacks on `ticket/USR-34` if the batch runs them back to back)
   and only adds filters.* Alternative: one PR for both (db consult USR-35), larger diff, one review.
7. **Shared predicate placement / High path.** *Default: `app.modules.users.repository.active_assignment_clause()`
   (non-High), duplicating the one-line rule of `RbacRepository._is_active`; moving rbac's inline copies onto it is a
   separate High follow-up.* Alternative: put it in `app/modules/rbac/models.py` (db consult USR-35) — makes USR-34
   and USR-35 High, never auto-merged.

## Assumptions
- **Shared predicate contract (USR-35 relies on it):** module `app/modules/users/repository.py`, name
  `active_assignment_clause`, signature `def active_assignment_clause() -> ColumnElement[bool]`, body
  `or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())`, uncorrelated. If the reviewer
  asks for a rename, USR-35's plan must follow; the orchestrator aligns both plans at the gate.
- `func.now()` is the transaction start time (both consults); the list's queries share one transaction, so all
  rows of one response use the same instant.
- The `admin` app's assignments (e.g. `admin:super-admin`) are shown like any app's: spec §5.15 says "active
  assignments only" with no app exclusion, and `admin:users:read` already exposes them through
  `GET /users/{id}/role-assignments` (spec :337).
- Disabled users' access is shown (no user-status filter); a disabled role assignment concept does not exist.
- The union response model gives admin-pan's generated SDK two page types: follow-up note for admin-pan (PAN-032),
  not a diff in this repo.
- Importing `App`, `Role`, `RoleAssignment` from `app.modules.rbac.models` into the users repository creates no cycle
  (`rbac/models.py` imports only `app.db.base`, :29) and does not touch a High path (import only).
- **Hotspots:** USR-35 (same batch, PLANNING) edits the same `users/repository.py`, `service.py`, `router.py` and adds
  tests next to these → expect USR-35 to rebase on USR-34 (sequential by default, Q6). USR-36 / USR-37 (PLANNING) edit
  `docs/spec-v1.md` §5.15 other parts → docs-only rebase possible. No `IN_PROGRESS` / `REVIEW` / `DONE` ticket edits
  these files (`work/backlog.md`: only ADM-007 `IN_PROGRESS`, unrelated).
- `tests/test_route_auth_classes.py` needs no change (no new route; the parameter does not change the class).
- `risk_tier.py` not run (no shell); tier reasoned from `.github/risk-tiers.yml`: files `app/modules/users/*.py` →
  `medium` (default), `tests/**`, `docs/**` → `low` ⇒ **Medium**. It would become High only under Q7's alternative.
