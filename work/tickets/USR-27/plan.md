Status: READY

# Plan — USR-27: Role assignments — return role and org names

Tier: **High** (every changed product file is under `app/modules/rbac/**`, a High path in
`.claude/loop.config.md` "High paths"; RBAC is also a High review trigger). Stops at PLAN for
Ming's approval; never merged by an agent. No schema change, no auth change, no new endpoint.

## Scope summary
- Ticket goal: "The admin UI can name a role and its org scope without extra lookups:
  `AssignmentRead` (`GET /users/{id}/role-assignments`) and `RoleHolderRead`
  (`GET /apps/{key}/role-definitions/{role_id}/assignments`) carry `role_key` and `org_id` only."
- Ticket AC: "`AssignmentRead` and `RoleHolderRead` gain `role_name` and `org_name` (nullable; null
  for app-scope assignments), read in the same query (no N+1). Additive (R9)." / "Every endpoint
  returning those schemas fills them (per-user list, assignment create, role holders)." / "Tests:
  names present, `org_name` null for app-scope rows; bounded-query test still passes."
- Spec §5.8 Admin bullet (`docs/spec-v1.md:97`): "staff can list, look up, disable/enable users, set
  or clear a user's display name, manage their roles per app and revoke their sessions".
- CLAUDE.md R9: "No breaking changes inside v1." Adding two always-present response fields is
  additive; no request schema changes.

Out of scope (R7): `OrgMemberRead` (already has `role_name`; no `org_name` — Q1),
`AssignmentBatchItem` / `AssignmentBatchRead` (Q2), any other assignment-shaped read model.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `AssignmentRead` gains `role_name: str` and `org_name: str \| None`; `RoleHolderRead` inherits both | `tests/test_openapi_contract.py › test_role_holders_operation` (required set incl. the two fields) and new `› test_assignment_read_component`; `ASSIGNMENT_KEYS` / `KEYS` sets in the two API test files include them |
| 2 | Per-user list fills names; `org_name` null for app scope, org's name for org scope | `tests/test_rbac_assignments_api.py › test_list_happy_path` (exact dict incl. `role_name`, `org_name: None`), new `› test_list_org_scope_names` |
| 3 | Assignment create (POST 201) fills names, app scope and org scope | `tests/test_rbac_assignments_api.py › test_assign_app_scope_201` (`role_name`, `org_name is None`, list == body still holds), `› test_assign_org_scope_201` (`org_name == org.name`, then app-scope `org_name is None`) |
| 4 | Role-holders list fills names | `tests/test_rbac_role_holders_api.py › test_holders_item_shape` (`role_name`, `org_name is None`), new `› test_holders_org_scope_names` |
| 5 | Same query, no N+1 | Existing `tests/test_rbac_role_holders_api.py › test_holders_bounded_queries` and `› test_page_role_holders_two_statements` still pass unchanged; new `tests/test_rbac_assignments_api.py › test_list_bounded_queries` (limit 1 vs limit 6, equal statement count) and `› test_page_assignments_two_statements` (repository: exactly 2 statements, the rows statement contains `LEFT OUTER JOIN organizations`) |
| 6 | Org-members list unaffected after the row refactor | Existing `tests/test_org_members_api.py` passes unchanged, incl. `› test_page_org_members_two_statements` and the item-shape test (`role_name` still present, no `org_name` key) |
| 7 | Revoke and batch unaffected | Existing `tests/test_rbac_assignments_api.py` revoke tests, `tests/test_rbac_assignment_batches*.py`, `tests/test_openapi_contract.py` batch-item test (line ~258) pass unchanged |
| 8 | Green checks | `make check` (ruff, format, `mypy --strict`, pytest) |

## Ming verification after merge
None required. Optional: admin-pan PAN-015c renders the names from a real response.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/schemas.py` (`AssignmentRead`, :309-318) | Add `role_name: str = Field(description="Role display name, current value.")` and `org_name: str \| None = Field(description="Name of the org scope, current value; null for an app-scope assignment.")` after `role_key` / `org_id` | AC 1 | **High** |
| `app/modules/rbac/repository.py` (:81-86 row aliases; :606-612 `_assignment_rows`; :614-644 `page_role_holders`; :646-681 `page_org_members`; :759-779 `page_assignments`; :781-792 `get_assignment_row`) | Replace the three tuple aliases with frozen dataclasses (named access); widen `_assignment_rows()` with `Role.name`, `Organization.name` and an outer join; one helper maps the six base columns; drop the duplicate `Role.name` in `page_org_members` | AC 2-6, no N+1 | **High** |
| `app/modules/rbac/service.py` (:46 import; :1508-1550 `_assignment_read`, `_role_holder_read`, `_org_member_read`; :1976-1983 `revoke`) | Read rows by attribute; fill `role_name`, `org_name`; `_org_member_read` takes `role_name` from the base row; `revoke` uses `row.app_key` / `row.assignment` / `row.role_key` | AC 2-4, 6, 7 | **High** |
| `tests/test_rbac_assignments_api.py` | Extend `ASSIGNMENT_KEYS`, happy-path dict, assign tests; new org-scope list test, bounded-query test, repository two-statement test (local `_capture_sql` helper copied from `test_rbac_role_holders_api.py:54`) | AC 2, 3, 5 | no |
| `tests/test_rbac_role_holders_api.py` | Extend `KEYS`, `test_holders_item_shape`; new `test_holders_org_scope_names` | AC 1, 4 | no |
| `tests/test_openapi_contract.py` (:118-122) | Add `role_name`, `org_name` to the `RoleHolderRead` required set; new `test_assignment_read_component` | AC 1 | no |

No other file. `tests/test_org_members_api.py` and `tests/test_auth_orgs_super_admin.py` must pass
**unchanged** (the latter's query-count tests cover `/auth/orgs`, which does not use
`_assignment_rows`; they are listed in the consult but are not affected).

## Data model / migration
None. `Role.name` (`String(255)`, NOT NULL, `rbac/models.py:185`) and `Organization.name` (NOT NULL,
`organizations/models.py:33`) already exist. `RoleAssignment.org_id` → `organizations.id` is a FK to
the PK, so the outer join is at most 1:1 and needs no new index.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users/{user_id}/role-assignments` | unchanged | `Page[AssignmentRead]`, each item + `role_name`, `org_name` | unchanged (200, 401, 403, 404, 422) | `admin:users:read` (unchanged) |
| POST | `/api/v1/users/{user_id}/role-assignments` | unchanged | `AssignmentRead` + `role_name`, `org_name` | unchanged (201, …) | unchanged (`_ASSIGN_KWARGS`) |
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}/assignments` | unchanged | `Page[RoleHolderRead]`, each item + `role_name`, `org_name` | unchanged | `admin:users:read` (unchanged) |

Both new keys are always present. `org_name` is `null` iff `org_id` is `null`. Names are the
**current** values at read time, not a snapshot at grant time; a suspended org is returned with its
name as-is. Batch route `POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches`: response
unchanged (Q2). `GET /api/v1/orgs/{id}/members` (`OrgMemberRead`): response unchanged (Q1).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_assignments_api.py › ASSIGNMENT_KEYS` (+ `role_name`, `org_name`) | Shape of every list/create item (AC 1-3) |
| `tests/test_rbac_assignments_api.py › test_list_happy_path` (dict gains `"role_name": "stoktok:editor"`, `"org_name": None`; `make_role` sets `name=key`) | Names on per-user list, app scope null (AC 2) |
| `tests/test_rbac_assignments_api.py › test_list_org_scope_names` (new; one org-scoped row on an org named e.g. "Acme" and one app-scope row; role renamed to a distinct name, e.g. `role.name = "Viewer"`, so `role_name != role_key` is proven) | `org_name == "Acme"` for org row, `None` for app row; `role_name == "Viewer"` (AC 2) |
| `tests/test_rbac_assignments_api.py › test_assign_app_scope_201` (+ `role_name == "stoktok:viewer"`, `org_name is None`; `listed == [body]` kept) | Create response = list item (AC 3) |
| `tests/test_rbac_assignments_api.py › test_assign_org_scope_201` (+ `org_name == org.name` for the org POST, `org_name is None` for the app-scope POST) | Create, org and app scope (AC 3) |
| `tests/test_rbac_assignments_api.py › test_list_bounded_queries` (new; user with 6 assignments across ≥2 orgs and app scope; `limit=1` vs `limit=6`, equal statement counts) | Count-constant, no N+1 (AC 5) |
| `tests/test_rbac_assignments_api.py › test_page_assignments_two_statements` (new; repository call, `len(statements) == 2`, rows statement contains `LEFT OUTER JOIN organizations`, count statement does not reference `organizations`) | Same query, outer join, count unjoined (AC 5) |
| `tests/test_rbac_role_holders_api.py › KEYS` (+ `role_name`, `org_name`) and `› test_holders_item_shape` (+ `role_name == "stoktok:r0"`, `org_name is None`) | Holders shape (AC 1, 4) |
| `tests/test_rbac_role_holders_api.py › test_holders_org_scope_names` (new; one org-scoped and one app-scope holder; filter `org_id` once to show the name survives the filter) | `org_name` set / null on holders (AC 4) |
| `tests/test_rbac_role_holders_api.py › test_holders_bounded_queries`, `› test_page_role_holders_two_statements` (unchanged, must pass) | Count-constant (AC 5) |
| `tests/test_org_members_api.py` (unchanged, must pass, incl. `test_page_org_members_two_statements`) | Refactor regression (AC 6) |
| `tests/test_openapi_contract.py › test_role_holders_operation` (required set + 2) and `› test_assignment_read_component` (new; `AssignmentRead.required` = the 11 keys; `org_name` is nullable `anyOf [string, null]`) | Contract (AC 1) |

## Approach
1. **Schema** (`schemas.py:309`): add the two fields with `Field(description=…)`. `RoleHolderRead`
   inherits; it redeclares only `active`, so field order stays base-first. No default values: both
   keys are required in OpenAPI and always serialized.
2. **Rows as dataclasses (named access)** in `repository.py`, replacing lines 81-86, next to the
   existing `AppOrgMemberRow` precedent:
   ```python
   @dataclass(frozen=True)
   class AssignmentRow:
       assignment: RoleAssignment
       app_key: str
       role_key: str
       role_name: str
       org_name: str | None  # None for app scope
       active: bool  # database clock

   @dataclass(frozen=True)
   class RoleHolderRow:
       base: AssignmentRow
       email: str
       display_name: str | None
       user_status: str

   @dataclass(frozen=True)
   class OrgMemberRow:
       base: AssignmentRow
       email: str
       display_name: str | None
       user_status: str
   ```
3. **`_assignment_rows()`** (:607): return type
   `Select[RoleAssignment, str, str, str, str | None, bool]`;
   `select(RoleAssignment, App.key, Role.key, Role.name, Organization.name, active)`
   `.join(Role, …).join(App, …).outerjoin(Organization, Organization.id == RoleAssignment.org_id)`.
   An inner join would drop app-scope rows. `Organization` is already imported (used at :693).
   Add `_ASSIGNMENT_COLUMNS = 6` and one static helper `_assignment_row(r) -> AssignmentRow` that is
   the **only** place reading indexes 0-5. Extra columns are read as
   `r[_ASSIGNMENT_COLUMNS + i]`, so any future widening changes one constant, not every caller.
4. **Callers**:
   - `page_assignments` / `get_assignment_row`: return `_assignment_row(r)`. Count query unchanged
     (no Organization join: the join is 1:1 and outer, it cannot change the count).
   - `page_role_holders`: `.add_columns(User.email, User.display_name, User.status)` unchanged;
     build `RoleHolderRow(base=_assignment_row(r), email=r[n], display_name=r[n+1],
     user_status=r[n+2])` with `n = _ASSIGNMENT_COLUMNS`. Count unchanged.
   - `page_org_members`: drop `Role.name` from `.add_columns` (now in the base); build
     `OrgMemberRow` the same way. Its rows statement gains the Organization outer join (harmless,
     1:1; statement count stays 2). Count unchanged.
   - `get_assignment_row` keeps `populate_existing=True`.
5. **Service** (`service.py`): `_assignment_read(row)` reads attributes and fills `role_name`,
   `org_name`; `_role_holder_read` calls `_assignment_read(row.base)` instead of rebuilding a tuple;
   `_org_member_read` uses `row.base.role_name`, output fields unchanged; `revoke` (:1979, :1983)
   uses `row.app_key`, `row.assignment`, `row.role_key`. `assign` (:1643) already re-reads through
   `get_assignment_row` — no extra statement.
6. Update/add tests per the table; `make check`.

Rules: R1 (ORM stays in the repository; service maps rows to schemas), R2 (no schema change → no
migration; `alembic check` unaffected), R3 (auth classes and permissions unchanged), R4 (read-only
change; `assign` audit untouched), R6 (no new error), R7 (OrgMemberRead/batch excluded), R8 (tests
above; no new endpoint), R9 (additive fields only), R11 (no credentials touched; holder query still
selects explicit `User` columns, never the entity).

Size estimate: ~60 lines product code, ~120 lines tests. One cycle.

## Specialist input used
`consult-api-platform-engineer.md`:
- Rec 1 (`role_name: str`, `org_name: str | None`, always present) — adopted.
- Rec 2 (outer join in `_assignment_rows`, widen `AssignmentRow`) — adopted, with dataclasses
  instead of a wider tuple (task brief: prefer named access).
- Rec 3 (index shifts in `page_role_holders` / `page_org_members`; drop duplicate `Role.name`) —
  adopted via the single `_assignment_row` helper; also covers `revoke` (`service.py:1979-1983`),
  which the consult did not list.
- Rec 4 (count queries without Organization join) — adopted.
- Rec 5 (tests incl. per-user count-constant test) — adopted; the consult's reference to
  `tests/test_auth_orgs_super_admin.py:286-330` is not relevant (that covers `/auth/orgs`), the
  holder bounded test lives at `tests/test_rbac_role_holders_api.py:401`.
- Rec 6 (`Field(description=…)`, contract test, no ADR) — adopted.
- Risks (current-name not snapshot; suspended orgs as-is) — documented in the API contract.
- Open questions — carried as Q1 and Q2 below with the consult's defaults.

## Blocking questions
None blocking — both have defaults that keep the plan as written; listed under
`## Blocking questions for Ming` for confirmation at the High-path approval.

## Split proposal
None.

## Blocking questions for Ming
1. Add `org_name` to `OrgMemberRead` (`GET /api/v1/orgs/{id}/members`) too? The org is the path
   parameter there, so the name is redundant. **Default: no** (R7; a separate ticket if admin-pan
   asks).
2. Add `role_name` / `org_name` to the USR-36 batch results (`AssignmentBatchItem`)? The batch is
   one role and one org for every item, which the caller already knows. **Default: no** (batch
   results unchanged).

## Assumptions
- **Hotspot (batch 2026-11-27-1):** USR-25 (`self_assignment_forbidden` scope; areas auth,
  security) and USR-26 (role-definition assignment count, org-owner flag, 409 reasons; areas api,
  db) are planned in parallel and also edit `app/modules/rbac/` — likely `service.py` (USR-25:
  `assign` / batch guards near :1615-1650; USR-26: 409 reasons), `repository.py` (USR-26: counts on
  role definitions) and `schemas.py` (USR-26: `RoleDefinitionRead`). USR-27's hunks are
  `repository.py:81-86, 606-792`, `schemas.py:309-318`, `service.py:46, 1508-1550, 1976-1983`, and
  the test files above. Expect a rebase on whichever merges second; conflicts should be textual,
  not semantic, unless USR-25 also changes `revoke` or adds a caller of `get_assignment_row` /
  `AssignmentRow` — then that caller must use the named fields.
- `make_role` sets `name=key` (`tests/rbac_factories.py:46`), so existing fixtures yield
  `role_name == role_key`; new tests rename one role to prove the column is `Role.name`.
- No other module imports `AssignmentRow`, `RoleHolderRow` or `OrgMemberRow` (grep: only
  `rbac/service.py:46`); changing them from tuples to dataclasses is internal.
- Names are current values; no denormalisation into `role_assignments`.
- admin-pan (PAN-015c) tolerates the new keys (additive).
