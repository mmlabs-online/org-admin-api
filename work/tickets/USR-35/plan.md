Status: READY

# Plan — USR-35: Users list filters `app` and `role_id`

> **Predicted tier: Medium.** All application changes are in `app/modules/users/**` (default `medium`); tests and
> docs are `low`; nothing matches a `high:` glob of `.github/risk-tiers.yml` (`app/modules/rbac/**`, `app/core/**`,
> `app/db/**`, `migrations/**`, … ). `python3 -I .github/scripts/risk_tier.py --files app/modules/users/router.py
> app/modules/users/service.py app/modules/users/repository.py tests/test_users_filters.py
> tests/test_openapi_contract.py docs/spec-v1.md` could not be run in this planning session (no shell); worked out from
> the globs (precedence high > low > default, the PR takes its highest file) it yields **medium**. The app/role
> existence checks reuse **existing** `RbacRepository` read methods (call only, no edit), and the shared predicate is
> USR-34's `app.modules.users.repository.active_assignment_clause()`, so `app/modules/rbac/**` (High) is not touched.
> Ticket Gate is `plan-approval`, so it stops at PLAN anyway; under `/run-batch --auto-merge` it can merge after Ming's
> "cho merge" for Medium in the batch round. Batch 2026-11-26-2. Written for the **default answers** of "Blocking
> questions for Ming" (Q1-Q6).

## Scope summary
- Ticket Goal: "Staff find the users who hold any role of an app, or one specific role, from the users list. Requested
  by admin-pan for PAN-034 (users filters)."
- Ticket Scope (verbatim):
  - "`GET /api/v1/users?app=<key>`: users with at least one active assignment of any role in that app (app scope or
    any org)."
  - "`GET /api/v1/users?role_id=<uuid>`: users holding that role actively (app scope or any org)."
  - "Combinable with each other, `email`, `status`, `include=access` and pagination; `total` counts distinct users."
  - "Unknown values → 422 `unprocessable` with `details[].reason` `unknown_app` (field `app`), `unknown_role` (field
    `role_id`), `role_not_in_app` (both given, mismatch). Malformed key / UUID → 422 as today. Disabled app and its
    roles are known values."
  - "Proposed default (spec): with `include=access`, `access` still shows all of the user's active access, not only
    the filtered app/role."
- Ticket Out of scope: "Other filters or sort orders; bulk actions (USR-36)."
- Ticket Technical notes: "Use `EXISTS` subqueries (not JOIN) so users are never duplicated; existing indexes
  `ix_role_assignments_role_id` and `uq_roles_app_id_key` suffice, no migration expected."
- Spec §5.15 "Decided by the owner (2026-11-25)" (`docs/spec-v1.md:343`): "**"Access"** = **active** role assignments
  only (app + role + scope); expired ones are excluded." and (:348) "The users list gets **filters by app and by
  role**."
- Spec §5.15 Part 2 (`docs/spec-v1.md:360-366`):
  - :361 "`GET /api/v1/users?app=<key>` — users with at least one active assignment of any role in that app (app scope
    or any org)."
  - :362 "`GET /api/v1/users?role_id=<uuid>` — users holding that role actively (app scope or any org)."
  - :363 "Combinable with each other, with `email`, `status`, `include=access` and pagination; `total` counts distinct
    users."
  - :364 "**Unknown values → 422, not an empty page (owner, 2026-11-26, OQ76)** … `unprocessable` with
    `details[].reason` `unknown_app` (field `app`), `unknown_role` (field `role_id`), or `role_not_in_app` (both given,
    the role belongs to another app). Malformed `app` (fails the app-key grammar) or `role_id` (not a UUID) → 422 as
    today. A disabled app or its roles are known values (no 422)."
  - :365 "Proposed (not answered, PLAN uses it unless the owner says otherwise): with `include=access`, the `access`
    array always shows all of the user's active access, not only the filtered app or role."
  - :366 "**Tests (R8):** each filter alone and combined; expired-only users excluded; org-scope holders included;
    distinct `total`; 422 `unknown_app`, `unknown_role`, `role_not_in_app`; 422 for malformed values; 401/403."
- Spec §8 OQ76 (:552): "answered … 422 (`unknown_app`, `unknown_role`, `role_not_in_app`), not an empty page … Not
  answered (PLAN default): with `include=access` the `access` array is not narrowed by the filters."
- Spec §5.15 :336 (current contract): "`GET /api/v1/users` (`admin:users:read`): filters `email` … and `status`;
  `Page[UserRead]` … newest first (`created_at` desc, `id`)." — order and permission unchanged.
- `work/decisions.md`: nothing on §5.15 / the users list.
- CLAUDE.md: R1 (router HTTP only; existence checks + 422 in the service; SQL in the repository), R3 class 1 (Bearer,
  `admin:users:read`; no new route), R4 (read-only → no audit), R6 (422 `unprocessable` / `validation_error` through
  the standard envelope), R7, R8 (happy path, validation, 401/403; 404 n/a for a list), R9 (additive query params,
  `Page[T]`, no breaking change), R11 (nothing logged).

## Acceptance criteria → verification
New tests go into `tests/test_users_filters.py` (new) unless another file is named.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | `app` alone returns only users with an active assignment of any role of that app; org-scope holders included | `test_app_filter_matches_app_and_org_scope` (app A with roles `a:viewer`, `a:editor`; app B with `b:viewer`; U1 app-scope `a:viewer`, U2 org-scope `a:editor` in org O, U3 only `b:viewer`, U4 none → `app=A` items = {U1, U2}, `total == 2`) |
| 1b | `role_id` alone returns only active holders of that role, app and org scope | `test_role_filter_matches_app_and_org_scope` (same fixture: `role_id=a:viewer.id` → {U1}; add U5 org-scope `a:viewer` → {U1, U5}; `role_id=a:editor.id` → {U2}) |
| 1c | Both together | `test_app_and_role_filter_together` (`app=A&role_id=a:viewer.id` → same ids and total as `role_id` alone) |
| 1d | Expired-only users are excluded; one active row is enough | `test_expired_only_excluded` (parametrized over `app` and `role_id`: U6 only expired (−1 day) `a:viewer` → absent; U7 expired `a:viewer` in org O + active `a:viewer` at app scope → present; U8 `expires_at` +30 days → present) |
| 1e | Disabled app and its roles are known values | `test_disabled_app_accepted` (`make_app(…, status="disabled")`, role R, U holds R → `app=<key>` 200 with U; `role_id=R.id` 200 with U; `app=<key>&role_id=R.id` 200) |
| 2a | `total` counts each user once with several matching assignments | `test_total_is_distinct` (U holds `a:viewer` at app scope, `a:viewer` in orgs O1 and O2, and `a:editor` at app scope → `app=A`: `total == 1`, `len(items) == 1`; `role_id=a:viewer.id`: `total == 1`, one item) |
| 2b | Combines with `email` | `test_filters_combine_with_email` (two holders with emails `…-alpha@…` / `…-beta@…` plus a non-holder `…-alpha2@…` → `app=A&email=alpha` → only the holder `alpha`, `total == 1`) |
| 2c | Combines with `status` | `test_filters_combine_with_status` (one active and one disabled holder → `status=disabled&app=A` → only the disabled one; `status=active&role_id=…` → only the active one) |
| 2d | Combines with pagination (limit, offset, past the end), order unchanged | `test_filters_paginate` (3 holders with distinct `created_at`, 1 non-holder newer than all → `app=A&limit=2&offset=0` → newest two holders in `created_at desc, id` order, `total == 3`; `offset=2` → the third, `total == 3`; `offset=10` → `items == []`, `total == 3`) |
| 2e | Combines with `include=access`; `access` is not narrowed by the filter (Q4) | `test_filters_with_include_access_not_narrowed` (U holds `a:viewer` and `b:viewer` → `app=A&include=access` → U's `access` has groups for **both** A and B; `role_id=a:viewer.id&include=access` → both groups, all roles; non-holder absent) |
| 3a | 422 `unknown_app` | `test_unknown_app_422` (`app=no-such-app` → 422, `error.code == "unprocessable"`, `error.details == [{"field": "app", "reason": "unknown_app"}]`) |
| 3b | 422 `unknown_role` | `test_unknown_role_422` (`role_id=<uuid4()>` → 422 `unprocessable`, `details == [{"field": "role_id", "reason": "unknown_role"}]`; also with a known `app` and an unknown role → `unknown_role`) |
| 3c | 422 `role_not_in_app` | `test_role_not_in_app_422` (`app=A&role_id=b:viewer.id` → 422 `unprocessable`, `details == [{"field": "role_id", "reason": "role_not_in_app"}]`) |
| 3d | Both unknown → `unknown_app` first (Q1) | `test_unknown_app_wins_over_unknown_role` (`app=no-such-app&role_id=<uuid4()>` → `details == [{"field": "app", "reason": "unknown_app"}]`, exactly one item); and unknown app + known role → `unknown_app` |
| 3e | 422 shape identical to the existing `unprocessable` errors | covered by the exact `details ==` asserts of 3a-3d (list of `{"field", "reason"}` only, same shape as `rbac/service.py:1479-1480` `_assignment_unprocessable`); `error.message` is a fixed string, never the submitted value (`"no-such-app" not in resp.text`) |
| 3f | Malformed values → 422 `validation_error` as today; empty strings too (Q2) | `test_malformed_filters_422` parametrized `{"app": ""}`, `{"app": "Bad!"}`, `{"app": "a"}` (too short), `{"app": "a" * 33}`, `{"app": "9app"}`, `{"role_id": ""}`, `{"role_id": "not-a-uuid"}`, `{"role_id": "123"}` → 422, `error.code == "validation_error"`, `details[0]["loc"] == ["query", <name>]` |
| 4a | 401 without token | `test_filters_require_token` (`client` fixture, `GET /api/v1/users?app=<known key>` → 401 envelope) |
| 4b | 403 without `admin:users:read` | `test_filters_require_users_read` (`make_client(admin_permissions=["admin:orgs:read"])` → 403 `permission_denied`; `["admin:users:read"]` alone → 200) |
| 5 | OpenAPI documents both parameters and the 422 reasons | `tests/test_openapi_contract.py` › `test_users_list_filter_params` (on `paths["/api/v1/users"]["get"]`: `app` in query, not required, `_flat(schema)["pattern"] == APP_KEY_RE`, description mentions "key", "disabled" and `unknown_app`; `role_id` in query, not required, `_flat(schema)["format"] == "uuid"`, description mentions `unknown_role` and `role_not_in_app`; `responses["422"]` schema `== {"$ref": REF}` and its description contains `validation_error`, `unprocessable`, `unknown_app`, `unknown_role`, `role_not_in_app`); existing `test_every_error_response_references_envelope` and USR-34's `test_users_list_operation` stay green |
| — | Existing behaviour unchanged | `tests/test_users_admin.py` (list order, email/status filters, `test_list_rejects_bad_query`) and `tests/test_users_access.py` (USR-34) green **unchanged** |
| — | Everything | `make check` green; no model change, so `alembic check` is unaffected |

404: not applicable (list route, no path id). Conflict: not applicable (read-only).

## Ming verification after merge
- After deploy: admin-pan PAN-034 filters by app and by role on the production users list; a typo in the app key
  shows the 422 message, not an empty table. Optional: p95 of `GET /api/v1/users?app=…` under 200 ms on the production
  dashboard (§6).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/users/repository.py` | `UserRepository.list` (today :77-93, after USR-34 moved by its additions): new keyword params `app_id: uuid.UUID \| None = None`, `role_id: uuid.UUID \| None = None`; each adds one correlated `EXISTS` to the shared `conditions` list (so count and page stay consistent), using USR-34's `active_assignment_clause()` from the same module. Uses the `Role`, `RoleAssignment` imports USR-34 already added | AC1, AC2; R1 (SQL only here) | no |
| `app/modules/users/service.py` | `UserService.list` (:41-44): new keyword params `app_key: str \| None = None`, `role_id: uuid.UUID \| None = None`; existence checks through the **existing** `RbacRepository.get_app_by_key` (`rbac/repository.py:108-109`) and `RbacRepository.role_with_app` (`rbac/repository.py:575-582`) — both already-imported class, call only; raises `UnprocessableError` (`app/core/errors.py:45`, import only) with reasons `unknown_app` / `unknown_role` / `role_not_in_app`; passes ids to the repository. New private helper `_filter_unprocessable(message, field, reason)` | AC1, AC3; R1 (business rule in the service), R6 | no |
| `app/modules/users/router.py` | `list_users` (:26-41, as extended by USR-34): new query params `app: Annotated[str \| None, Query(pattern=APP_KEY_RE, description=…)] = None` and `role_id: Annotated[uuid.UUID \| None, Query(description=…)] = None`; pass them to the service; `require_permission("admin:users:read", errors={422: …})` documents the reasons. Import `APP_KEY_RE` from `app.modules.rbac.models` (import only) | AC3f, AC5 | no |
| `tests/test_users_filters.py` (new) | All tests of the table except the OpenAPI one | AC1-AC4 | no |
| `tests/test_openapi_contract.py` | New `test_users_list_filter_params` (uses the file's `_schema`, `_param`, `_flat`, `REF`) | AC5 | no |
| `docs/spec-v1.md` | §5.15 Part 2 :365 and §8 OQ76 :552: only after Ming answers Q4 in the batch round, replace "Proposed (not answered …)" / "Not answered (PLAN default)" by the dated answer ("owner, batch 2026-11-26-2, USR-35"); and add one line under :364 recording Q1/Q2 (precedence, empty strings). If Ming does not answer, leave the spec untouched | Spec stays the source of truth | no |

Not changed: `app/modules/rbac/**` (High: `get_app_by_key`, `role_with_app`, `APP_KEY_RE` are used as they are; the
predicate placement there proposed by the db consult is rejected, see Specialist input), `app/modules/users/schemas.py`
(no response change), `app/core/**` (`UnprocessableError` imported only), `app/api/deps.py`, `app/db/**`, migrations,
`tests/test_route_auth_classes.py` (no new route, class unchanged), `tests/test_users_admin.py`,
`tests/test_users_access.py` (unchanged, must stay green).

## Data model / migration
None. Reads only:
- role filter: `role_assignments.role_id = :rid` → `ix_role_assignments_role_id`; correlation on `user_id`.
- app filter: `roles.app_id = :app_id` → `uq_roles_app_id_key` (leading `app_id`), then `role_assignments.role_id IN
  (those roles)` → `ix_role_assignments_role_id` (`rbac/models.py:222-231`, db consult findings).
The implementer records `EXPLAIN (ANALYZE, BUFFERS)` of the count query for `app=` and `role_id=` on the test DB
(~1 000 users, ~3 000 assignments over 3 apps) in impl-log; an index is a follow-up only if a sequential scan of
`role_assignments` shows up (api consult risk 2; db consult rec 6).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users` | unchanged `email` (1-254), `status`, `include` (USR-34), `limit` ≤ 100, `offset`; **new** `app` (query, optional, app key, `APP_KEY_RE` `^[a-z][a-z0-9-]{1,31}$`) and `role_id` (query, optional, UUID) | unchanged: `Page[UserRead]`, or `Page[UserWithAccessRead]` with `include=access` (USR-34); items only users with ≥ 1 active matching assignment; `total` = distinct users; order `created_at desc, id` | 200; 401; 403; 422 `validation_error` (malformed `app` / `role_id`, empty strings, as for bad `status` / `limit`); 422 `unprocessable` with `details == [{"field": "app", "reason": "unknown_app"}]`, `[{"field": "role_id", "reason": "unknown_role"}]` or `[{"field": "role_id", "reason": "role_not_in_app"}]` (exactly one item) | `admin:users:read` (unchanged, R3 class 1) |

Parameter descriptions (OpenAPI):
- `app`: "App key. Only users with at least one active (not expired) assignment of any role of this app, at app scope
  or in any organization. Disabled apps are accepted. Unknown key → 422 `unprocessable`, reason `unknown_app`."
- `role_id`: "Role id. Only users holding this role actively, at app scope or in any organization. Unknown → 422
  `unprocessable`, reason `unknown_role`; with `app`, a role of another app → reason `role_not_in_app`."
- 422 response: "Validation failed (code=validation_error: malformed query value such as an `app` key that fails the
  key grammar or a `role_id` that is not a UUID) or refused filter value (code=unprocessable, details[].reason:
  unknown_app, unknown_role, role_not_in_app)."
- Route description gains: "`include=access` is never narrowed by `app` / `role_id`: it lists all of the user's
  active access." (USR-34 already says "`include` is not narrowed by other filters"; keep one sentence, not two.)

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_users_filters.py` › `test_app_filter_matches_app_and_org_scope` | 1a |
| › `test_role_filter_matches_app_and_org_scope` | 1b |
| › `test_app_and_role_filter_together` | 1c |
| › `test_expired_only_excluded` (param `app` / `role_id`) | 1d |
| › `test_disabled_app_accepted` | 1e, Q3 |
| › `test_total_is_distinct` | 2a |
| › `test_filters_combine_with_email` | 2b |
| › `test_filters_combine_with_status` | 2c |
| › `test_filters_paginate` | 2d |
| › `test_filters_with_include_access_not_narrowed` | 2e, Q4 |
| › `test_unknown_app_422` | 3a, 3e |
| › `test_unknown_role_422` | 3b, 3e |
| › `test_role_not_in_app_422` | 3c, 3e |
| › `test_unknown_app_wins_over_unknown_role` | 3d, Q1 |
| › `test_malformed_filters_422` (param, 8 cases incl. empty strings) | 3f, Q2 |
| › `test_filters_require_token` | 4a |
| › `test_filters_require_users_read` | 4b |
| `tests/test_openapi_contract.py` › `test_users_list_filter_params` | 5 |

Fixtures / conventions (module-local in the new file): `users_client` as in `tests/test_users_admin.py:40-43`
(`make_client(admin_permissions=["admin:*"])`); users via `make_user` or a local `_user(db, email, status=…,
created_at=…)` copied from `tests/test_users_admin.py:46-62`; `make_app`, `make_org`, `make_role`, `assign` from
`tests/rbac_factories.py` (app keys must match `APP_KEY_RE`, e.g. `flt-a`; role keys `ROLE_KEY_RE`, e.g.
`flt-a:viewer`). Seeded rows (e.g. `admin:super-admin` holders) may exist: assert on sets of the test's own user ids
and on `total` only where the filter is on a test-created app/role (seeded users hold no role of it). Expiry values
far from the clock (−1 day / +30 days): `func.now()` is the test transaction start. Error calls last in a test (an
error response may roll the shared transaction back). 401/403 tests send **well-formed** params (dependency auth runs
before the service; well-formed values keep the expected status unambiguous).

## Approach
1. **Precondition:** USR-34 is merged on `main` (`active_assignment_clause()`, `include` param, `Role` /
   `RoleAssignment` imports in `users/repository.py`). Branch `ticket/USR-35` from that `main`. If the function is
   missing or renamed, stop and record in impl-log (do not define a second predicate).
2. **Repository** (`UserRepository.list`, R1):
   ```python
   async def list(self, *, email, status, app_id: uuid.UUID | None = None,
                  role_id: uuid.UUID | None = None, limit, offset) -> tuple[list[User], int]:
       ...
       if role_id is not None:
           conditions.append(
               select(RoleAssignment.id)
               .where(RoleAssignment.user_id == User.id, RoleAssignment.role_id == role_id,
                      active_assignment_clause())
               .correlate(User).exists()
           )
       if app_id is not None:
           conditions.append(
               select(RoleAssignment.id)
               .join(Role, Role.id == RoleAssignment.role_id)
               .where(RoleAssignment.user_id == User.id, Role.app_id == app_id,
                      active_assignment_clause())
               .correlate(User).exists()
           )
   ```
   `EXISTS` (not JOIN) so a user with several matching rows is counted and listed once — no `DISTINCT`/`GROUP BY`;
   the same `conditions` feed the `count(*)` and the page select, so `total` matches the page. Explicit
   `.correlate(User)` (db consult risk). No app-status or user-status condition (spec :364; `status` stays the user
   filter it is today).
3. **Service** (`UserService.list`, R1, R6):
   ```python
   async def list(self, *, email, status, app_key: str | None = None,
                  role_id: uuid.UUID | None = None, limit, offset) -> tuple[list[User], int]:
       app_id = None
       if app_key is not None:
           app = await RbacRepository(self.repo.session).get_app_by_key(app_key)
           if app is None:
               raise _filter_unprocessable("Unknown app", "app", "unknown_app")
           app_id = app.id
       if role_id is not None:
           found = await RbacRepository(self.repo.session).role_with_app(role_id)
           if found is None:
               raise _filter_unprocessable("Unknown role", "role_id", "unknown_role")
           if app_id is not None and found[0].app_id != app_id:
               raise _filter_unprocessable("Role does not belong to this app", "role_id", "role_not_in_app")
           app_id = None  # the role already pins the app; one EXISTS is enough (db consult rec 2)
       return await self.repo.list(email=email, status=status, app_id=app_id, role_id=role_id, ...)
   ```
   `_filter_unprocessable(message, field, reason) -> UnprocessableError` returns
   `UnprocessableError(message, details=[{"field": field, "reason": reason}])` — byte-identical shape to
   `rbac/service.py:1479-1480` (private there, so not imported). App check first → both unknown gives `unknown_app`
   (Q1). No status check on the app (Q3). No lock (read-only), no audit (R4), no logging (R11). Messages are fixed
   strings, never the submitted key.
4. **Router** (`list_users`): add `app` and `role_id` after `status`, pass `app_key=app, role_id=role_id` to the
   service; add `errors={422: <text of API contract>}` to `require_permission("admin:users:read", …)` (the core
   post-processor replaces only generic 422 descriptions, `app/core/openapi.py:39`, and attaches the envelope to every
   4xx). The USR-34 `include` path is untouched: `access_summaries` receives only the page's user ids, so the filter
   cannot narrow `access` (Q4). Empty `app=` fails the pattern; empty `role_id=` fails UUID parsing → both 422
   `validation_error` (Q2). If `test_malformed_filters_422` shows FastAPI treating `role_id=` as absent, record it in
   impl-log and stop for the reviewer (do not add custom parsing outside the plan).
5. **Spec** (`docs/spec-v1.md`): only per Ming's answers (see Files).
6. `make check`; EXPLAIN output in impl-log.

Rules: R1 (SQL in repository; existence/422 in service via existing RbacRepository reads; router HTTP only), R3 (class
and permission unchanged), R4 (no mutation), R6 (standard `validation_error` / `unprocessable` envelope), R7 (no
other filters, no rbac refactor), R8 (happy, combinations, 422s, 401/403), R9 (additive optional params, `Page[T]`,
`/api/v1/users`), R11 (nothing logged; message carries no input).

Estimate: ~60 changed lines of application code, ~260 lines of tests, ~4 lines of spec; one review cycle.

## Specialist input used
- **api-platform-engineer:** rec 1 `app` as key with `Query(pattern=APP_KEY_RE)` (precedent
  `rbac/assignments_router.py:37`), `role_id: uuid.UUID | None` — adopted; rec 2 existence checks in `UserService.list`
  through `RbacRepository` — adopted (existing `get_app_by_key`, `role_with_app`; no rbac edit); rec 3 order
  `unknown_app` → `unknown_role` → `role_not_in_app`, one 422 not aggregated — adopted (Q1); rec 4 EXISTS with a shared
  predicate in the users repository — adopted, the predicate is USR-34's `active_assignment_clause()`; rec 5 resolve
  key → id in the service, repository compares ids — adopted; rec 6 OpenAPI 422 with reasons and param descriptions —
  adopted (AC5); rec 7 `include=access` not narrowed — adopted (Q4); rec 8 separate PR when USR-34 supplies the helper
  — adopted. Risk "details shape must match" → exact `details ==` asserts (3e). Risk "possible scan" → EXPLAIN in
  impl-log.
- **database-optimizer:** rec 1 app EXISTS with `JOIN roles` and resolved `app_id` — adopted; rec 2 with both filters
  only the role EXISTS after validating `role.app_id == app.id` — adopted; rec 3 validation in the service, disabled
  apps accepted, `unknown_app` first — adopted; rec 4 / Q3 predicate next to `RoleAssignment` in
  `app/modules/rbac/models.py` — **rejected**: High path, would make USR-35 (and USR-34) High and never auto-merged;
  USR-34's plan already placed it in `users/repository.py` (its Q7); rec 5 / Q2 bundle with USR-34 — **rejected** by
  default (USR-34 plan Q6: separate sequential PRs); rec 6 no migration — adopted, EXPLAIN recorded; rec 7
  `include=access` independent — adopted; rec 8 tests list — adopted in full. Risk "correlate explicitly" →
  `.correlate(User)` in step 2.
- **Lookup choice (asked by the orchestrator):** existing `RbacRepository` read methods, **not** a new users-repository
  query on `App`/`Role`. Why: both methods already exist with exactly the needed semantics (`get_app_by_key` has no
  status filter → disabled apps are known; `role_with_app` is the same lookup the single-assignment POST uses for
  `unknown_role`, `rbac/service.py:1589-1591`), `UserService` already instantiates `RbacRepository`
  (`users/service.py:66`, `:94`), and calling them edits no rbac file, so the tier stays Medium without duplicating
  lookup SQL. USR-18's apps list filters (`RbacRepository.list_apps`, `rbac/repository.py:145-173`) filter enum
  columns and do no existence lookup, so they offer no closer precedent.

## Blocking questions
None block planning; the decisions below have defaults and this plan is written for them. They go to Ming in the
batch approval round (section "Blocking questions for Ming").

## Split proposal
None. ~60 app lines + ~260 test lines; one implement/review cycle.

## Blocking questions for Ming
1. **Both unknown (or unknown app + any role) → which reason?** *Default: `unknown_app` (field `app`) first, one
   detail only; the role is not checked when the app is unknown* (both consults). Alternative: report both details in
   one 422.
2. **Empty strings (`app=`, `role_id=`)?** *Default: malformed → 422 `validation_error` (as `email=` today,
   `tests/test_users_admin.py:155`), not "no filter".* Alternative: treat an empty value as absent.
3. **Disabled apps?** *Default: accepted — a disabled app and its roles are known values; their active holders are
   listed* (spec :364 already rules out a 422; this confirms they are listed, with no app-status filter).
   Alternative: accept the key but return an empty page for a disabled app (not recommended: it hides real access).
4. **`include=access` with filters.** *Default (spec :365 proposal): `access` always shows all of the user's active
   access, not narrowed by `app` / `role_id`.* Alternative: narrow `access` to the filtered app/role. If accepted, the
   spec line :365 and OQ76 :552 are marked answered in this PR.
5. **422 `details` shape.** *Default: identical to the existing `unprocessable` errors — `details` is a list with
   exactly one `{"field", "reason"}` object, no submitted value; `message` a fixed English string* (api consult risk:
   admin-pan display). Alternative: none proposed.
6. **The `admin` app as a filter value.** *Default: `app=admin` and admin-app roles are accepted like any app (spec
   does not exclude it; `admin:users:read` already exposes admin-app assignments via `GET
   /users/{id}/role-assignments`, spec :337).* Alternative: 422 for `app=admin` (would be new scope).

## Assumptions
- **Builds on USR-34** (`work/tickets/USR-34/plan.md`, READY; built and merged before USR-35 in batch 2026-11-26-2):
  USR-35 imports/uses its shared predicate `app.modules.users.repository.active_assignment_clause()`
  (`() -> ColumnElement[bool]`, uncorrelated, `expires_at IS NULL OR expires_at > now()`) and defines no other; it
  also relies on USR-34's `include` parameter, `UserWithAccessRead` union response and `access_summaries(user_ids)`
  (page ids only), and on the `App`/`Role`/`RoleAssignment` imports USR-34 adds to `users/repository.py`. If the
  reviewer of USR-34 renamed the predicate, this plan follows the merged name (orchestrator aligns at the gate).
- `func.now()` is the transaction start; the existence checks, count and page run in one transaction.
- Race with a concurrent app/role delete between the existence check and the list is harmless (empty page; db consult
  risk 3); no lock is taken.
- `status` keeps meaning the **user** status; there is no app-status filter.
- Existence checks add one or two indexed primary-key / unique lookups only when a filter is present; without filters
  the request runs exactly the USR-34 statements (USR-34's query-count tests stay green).
- **Hotspots:** `app/modules/users/{repository,service,router}.py` and `tests/test_openapi_contract.py` are edited by
  USR-34 (same batch, merged first → no rebase expected; if the batch stacks branches instead, rebase on
  `ticket/USR-34`). USR-36 / USR-37 (PLANNING, same batch) edit `docs/spec-v1.md` §5.15 other parts and possibly
  `tests/test_openapi_contract.py` → docs/test-only rebase possible. No `IN_PROGRESS` / `REVIEW` / `DONE` ticket edits
  these files (`work/backlog.md`: only ADM-007 `IN_PROGRESS`, unrelated; ADM-001 / USR-08 `DONE`, unrelated).
- `tests/test_route_auth_classes.py` needs no change (no new route).
- `risk_tier.py` not run (no shell); tier reasoned from `.github/risk-tiers.yml` → **Medium**. It would become High
  only if the db consult's rbac-models placement were chosen.
