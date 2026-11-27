Status: BLOCKED

> Blocked only on Ming confirming the five defaults under `## Blocking questions for Ming`
> (batch 2026-11-27-1 approval round). The plan below is complete under those defaults: "defaults OK"
> flips this to `READY` with no further planning. Tier: **High** (RBAC, `app/modules/rbac/`), so it
> stops at PLAN for Ming's approval in any case.

# Plan — USR-26: Role definitions — assignment count, org-owner flag, stable reasons on 409 conflicts

## Scope summary
Ticket `work/tickets/USR-26/ticket.md`:

> The admin-pan roles table (PAN-015b) can show holders, pre-disable Delete for a role with holders,
> flag the org-owner role, and show fixed translatable copy for each 409 instead of the raw server message.

> - `RoleDefinitionRead` gains `assignment_count` (on the detail at least; on the list if it costs no
>   N+1). Its definition matches what the delete guard counts, so a role showing 0 can be deleted
>   (USR-16 plan D5 noted the guard counts expired assignments too) — the planner fixes one definition
>   and documents it.
> - `RoleDefinitionRead` gains `is_org_owner` (`apps.org_owner_role_id == id`) next to `is_default`.
> - `DELETE …/roles/{role_id}` 409 responses keep `code: conflict` and add `details: [{"reason": …}]`
>   with `system_role`, `default_role`, `org_owner_role`, `role_has_assignments`, `changed_concurrently`.
> - `PUT …/roles/{role_id}/permissions` 409s likewise: `default_role_admin_permission`,
>   `org_owner_admin_permission`, `default_role_org_permission`, `super_admin_must_keep_wildcard`,
>   `changed_concurrently`.
> - Additive only (R9); OpenAPI documents the reasons. Tests for each reason and both new fields.

Spec reference: §5.3 Authorization (roles staff API); USR-16 follow-ups (`assignment_count` deferred in
its plan D5; `is_org_owner`). `docs/identity-architecture.md:186` (USR-16 row: staff read model of roles,
`Page`). CLAUDE.md R6 (one error shape `{"error": {"code", "message", "details"}}`, 409 for conflicts),
R9 ("No breaking changes inside v1"). `work/decisions.md` has no entry for this ticket.

**Scope corrections (both consultants, verified in code):** the ticket's reason lists are incomplete.
The full set of 409s these two endpoints can emit today (`app/modules/rbac/service.py:1345-1362`,
`:1435-1463`, `:215`) also includes `super_admin_role_designated` (DELETE, already shipped with
`field: role_id`), and on PUT `role_has_org_permissions` and `wildcard_not_allowed` (shipped, with
`field: permission_keys`) and `role_has_assignments` (shipped, **code `role_has_assignments`**, not
`conflict`, `errors.py` `RoleHasAssignmentsError`). This plan documents and tests all of them so the
UI gets a closed set (Q4), keeps every shipped `code`/`field`/`reason` value unchanged (R9, Q3), and
only adds `details` where there are none today.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `assignment_count` on list and detail = `COUNT(*)` of all `role_assignments` rows for the role (active, expired, app- and org-scoped), the same predicate as the delete guard | `tests/test_rbac_role_definitions_api.py › test_role_definitions_assignment_count_counts_all_rows`, `› test_role_definition_detail_assignment_count`, `› test_assignment_count_zero_means_deletable`, `› test_assignment_count_expired_only_blocks_delete` |
| 2 | List stays N+1-free: one extra grouped query per page, constant in page size | `tests/test_rbac_role_definitions_api.py › test_role_definitions_bounded_queries` (extended with assignments on every role) |
| 3 | `is_org_owner` = `apps.org_owner_role_id == id`, next to `is_default` | `tests/test_rbac_role_definitions_api.py › test_role_definitions_is_org_owner` |
| 4 | DELETE 409s: `code: conflict` + `details[0].reason` ∈ {`system_role`, `default_role`, `org_owner_role`, `super_admin_role_designated` (keeps `field: role_id`), `role_has_assignments`, `changed_concurrently`} | One test per reason, listed in `## Tests to write` (DELETE block) |
| 5 | PUT permissions 409s: `details[0].reason` ∈ {`default_role_admin_permission`, `org_owner_admin_permission`, `default_role_org_permission`, `super_admin_must_keep_wildcard`, `role_has_org_permissions`, `wildcard_not_allowed`, `changed_concurrently`} with `code: conflict`; `role_has_assignments` keeps `code: role_has_assignments` and `field: permission_keys` | One test per reason, listed in `## Tests to write` (PUT block) |
| 6 | Additive only (R9): existing fields, codes, `field`/`reason` values unchanged; messages unchanged | Existing tests stay green unmodified except for added assertions; `tests/test_openapi_contract.py › test_role_definitions_operations` (required set = old set + 2) |
| 7 | OpenAPI documents the reasons (409 description lists every reason; new fields carry `Field(description=…)`) | `tests/test_openapi_contract.py › test_role_delete_409_documents_reasons`, `› test_role_permissions_put_409_documents_reasons`, `› test_role_definitions_operations` (descriptions non-empty, "expired" in `assignment_count` description) |
| 8 | `make check` green | `make check` |

## Ming verification after merge
None. (admin-pan PAN-015b consumes it; that is the consumer's verification, not this ticket's.)

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/schemas.py` (`RoleDefinitionRead`, lines 245-273) | Add `is_org_owner: bool = Field(description=…)` right after `is_default` (line 253-255) and `assignment_count: int = Field(ge=0, description=…)` after `privileged` | AC 1, 3, 7 | Yes (rbac) |
| `app/modules/rbac/repository.py` (after `role_has_assignments`, lines 299-301) | New `assignment_counts(role_ids: Sequence[UUID]) -> dict[UUID, int]`: `{}` for empty input, else `select(RoleAssignment.role_id, func.count()).where(RoleAssignment.role_id.in_(role_ids)).group_by(RoleAssignment.role_id)`; docstring "same rows as `role_has_assignments` (no expiry filter); keep both in sync" | AC 1, 2 | Yes (rbac) |
| `app/modules/rbac/service.py` | (a) Role reason constants + `_role_conflict` helper near `_role_has_assignments` (line 184-188), which switches to the constant; (b) `_set_role_permissions` line 215 gains `details` with `changed_concurrently`; (c) `_definitions` lines 1189-1213 add `is_org_owner` and `assignment_count`; (d) `delete` lines 1345-1362 and `replace_permissions` lines 1435-1461 raise with reasons | AC 1, 3, 4, 5 | Yes (rbac) — **hotspot**, see Assumptions |
| `app/modules/rbac/router.py` (lines 387-399 DELETE, 406-422 PUT) | 409 description strings built from the constants (pattern: `assignments_router.py:54-56`); import the constants from `service` | AC 7 | Yes (rbac) |
| `tests/test_rbac_role_definitions_api.py` | New tests for both fields; extend `test_role_definitions_bounded_queries` (line 249) | AC 1-3 | No |
| `tests/test_rbac_roles_api.py` | Add `details` assertions to existing 409 tests (lines 262, 278, 288, 416, 438, 476, 504, 537, 547, 584); new DELETE race test | AC 4, 5 | No |
| `tests/test_rbac_super_admin_role.py` | Assert unchanged `details` on lines 597, 605; add DELETE-of-designated-role reason test if line 574 is not the DELETE path | AC 4, 5, 6 | No |
| `tests/test_admin_guards.py` | No change expected — line 438-439 already pins PUT `role_has_assignments` code + details (cited as the AC 5 test) | AC 5, 6 | No |
| `tests/test_openapi_contract.py` | Update required set in `test_role_definitions_operations` (line 103-106); two new 409-description tests (pattern: lines 596-612) | AC 6, 7 | No |
| `docs/consumers.md` (after the USR-24 bullet, lines 102-108) | New bullet "Role definitions (USR-26)": the two fields, count semantics (incl. expired, snapshot, 0 ⇔ deletable), derived fields do not move the ETag, the reason table for both endpoints incl. the `role_has_assignments` code exception | AC 7 (consumer contract) | No |
| `docs/identity-architecture.md` (line 186, USR-16 row) | Append "`assignment_count`, `is_org_owner` (USR-26)" to the description cell | Keep API table current | No |

No other file. `app/modules/rbac/errors`/`app/core/errors.py` are **not** touched (`ConflictError(message, details=…)` already exists).

## Data model / migration
None. `ix_role_assignments_role_id` (`app/modules/rbac/models.py:229`) serves the grouped count over
≤ 100 ids (consult-database-optimizer finding 3). No `alembic check` drift.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/role-definitions` | unchanged | `Page[RoleDefinitionRead]` + `is_org_owner: bool`, `assignment_count: int` | unchanged | `admin:apps:read` (unchanged) |
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}` | unchanged | `RoleDefinitionRead` (same two fields); ETag unchanged (derived fields do not move `updated_at`) | unchanged | `admin:apps:read` |
| DELETE | `/api/v1/apps/{key}/roles/{role_id}` | unchanged | 204 | 409 now always carries `details[0].reason` (table below) | `admin:roles:manage` |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | unchanged | `RoleRead` | 409 now always carries `details[0].reason` (table below) | `admin:roles:manage` |

DELETE 409 (`code: conflict`, check order as today):

| Reason | Condition (service.py) | `details` |
|---|---|---|
| `system_role` | `role.is_system` (1345) | `[{"reason": "system_role"}]` — new |
| `default_role` | `role.id == app.default_role_id` (1347) | `[{"reason": "default_role"}]` — new |
| `org_owner_role` | `role.id == app.org_owner_role_id` (1349) | `[{"reason": "org_owner_role"}]` — new |
| `super_admin_role_designated` | designated super-admin role (1351) | `[{"field": "role_id", "reason": "super_admin_role_designated"}]` — **unchanged** |
| `role_has_assignments` | `repo.role_has_assignments` (1356) | `[{"reason": "role_has_assignments"}]` — new |
| `changed_concurrently` | `IntegrityError` on delete (1361) | `[{"reason": "changed_concurrently"}]` — new |

PUT permissions 409 (check order as today):

| Reason | Code | Condition (service.py) | `details` |
|---|---|---|---|
| `default_role_admin_permission` | `conflict` | 1435 | `[{"reason": …}]` — new |
| `org_owner_admin_permission` | `conflict` | 1437 | `[{"reason": …}]` — new |
| `default_role_org_permission` | `conflict` | 1439-1444 | `[{"reason": …}]` — new |
| `role_has_org_permissions` | `conflict` | 1446 | `[{"field": "permission_keys", "reason": …}]` — unchanged |
| `wildcard_not_allowed` | `conflict` | 1451 | `[{"field": "permission_keys", "reason": …}]` — unchanged |
| `super_admin_must_keep_wildcard` | `conflict` | 1456-1461 | `[{"reason": …}]` — new |
| `role_has_assignments` | **`role_has_assignments`** | 1462 (G3) | `[{"field": "permission_keys", "reason": …}]` — unchanged |
| `changed_concurrently` | `conflict` | 215 (`_set_role_permissions`) | `[{"reason": …}]` — new |

Messages are unchanged everywhere. New reasons carry no `field` (DELETE has no body; the USR-37
pattern `_assignment_conflict` is reason-only) — the UI keys on `reason`, never on `field`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_role_definitions_api.py › test_role_definitions_assignment_count_counts_all_rows` | One role with an active app-scoped, an expired and an org-scoped assignment → list item `assignment_count == 3`; another role with none → `0` |
| `… › test_role_definition_detail_assignment_count` | Detail returns the same count as the list item |
| `… › test_assignment_count_zero_means_deletable` | Role with `assignment_count == 0` → DELETE 204 |
| `… › test_assignment_count_expired_only_blocks_delete` | Role with only expired assignments → `assignment_count == 1`, DELETE 409 `role_has_assignments` (pins "same definition as the guard") |
| `… › test_role_definitions_is_org_owner` | Org app with `org_owner_role_id` set → that role `is_org_owner: true`, others `false`; app without owner role → all `false`; `is_default` independent |
| `… › test_role_definitions_bounded_queries` (extend, line 249) | Every role also has ≥ 1 assignment; statement count equal for `limit=1` and `limit=6` |
| `tests/test_rbac_roles_api.py › test_delete_system_role_409` (extend, 262) | `code == "conflict"`, `details == [{"reason": "system_role"}]` |
| `… › test_delete_default_role_409` (extend, 278) | `details == [{"reason": "default_role"}]` |
| `… › test_delete_org_owner_role_409` (extend, 537) | `details == [{"reason": "org_owner_role"}]` |
| `… › test_delete_role_with_assignments_409` (extend, 288) | `code == "conflict"`, `details == [{"reason": "role_has_assignments"}]` |
| `… › test_delete_role_integrity_race_is_409` (new) | Monkeypatch `RbacRepository.role_has_assignments` → `False` while an assignment exists; FK NO ACTION raises in the savepoint → 409 `conflict`, `details == [{"reason": "changed_concurrently"}]`, role still present, session usable |
| `tests/test_rbac_super_admin_role.py` › DELETE of the designated role (existing at 574 if it is the DELETE path; else new `test_delete_designated_super_admin_role_409`) | `details == [{"field": "role_id", "reason": "super_admin_role_designated"}]` unchanged |
| `tests/test_rbac_roles_api.py › test_put_admin_permission_on_default_role_409` (extend, 438) and `test_default_then_put_admin_permission_409` (476) | `details == [{"reason": "default_role_admin_permission"}]` |
| `… › test_add_admin_permission_to_org_owner_role_409` (extend, 547) | `details == [{"reason": "org_owner_admin_permission"}]` |
| `… › test_put_org_permission_on_org_app_default_role_409` (extend, 584) | `details == [{"reason": "default_role_org_permission"}]` |
| `… › test_put_super_admin_without_wildcard_409` (extend, 416) | `details == [{"reason": "super_admin_must_keep_wildcard"}]` |
| `… › test_put_role_permissions_integrity_race_is_409` (extend, 504) | `details == [{"reason": "changed_concurrently"}]` |
| `tests/test_rbac_super_admin_role.py › test_replace_permissions_designated_org_permission_409` (extend, 597) | exact `details == [{"field": "permission_keys", "reason": "role_has_org_permissions"}]` (unchanged) |
| `… › test_replace_permissions_designated_wildcard_409` (extend, 605) | exact `details == [{"field": "permission_keys", "reason": "wildcard_not_allowed"}]` (unchanged) |
| `tests/test_admin_guards.py` lines 430-439 (existing, unchanged) | PUT `role_has_assignments`: code `role_has_assignments`, `details` unchanged |
| `tests/test_openapi_contract.py › test_role_definitions_operations` (update) | required set += `is_org_owner`, `assignment_count`; both property descriptions non-empty; `"expired"` in `assignment_count` description |
| `… › test_role_delete_409_documents_reasons` (new) | DELETE 409 description contains `conflict` and all 6 DELETE reasons |
| `… › test_role_permissions_put_409_documents_reasons` (new) | PUT 409 description contains `conflict`, `role_has_assignments` (as a code) and all 8 PUT reasons |

No pure-logic module: the count is a SQL aggregate and the reasons are constants at existing raise
sites, so API tests are the right level (they also exercise the real FK and the real error envelope).

## Approach
1. **Schema** (`schemas.py`): add `is_org_owner` after `is_default` with description
   "This role is the app's org owner role (apps.org_owner_role_id)."; add `assignment_count: int =
   Field(ge=0, description=…)`: "Number of role_assignments rows for this role, including expired and
   org-scoped ones — the rows that block DELETE. 0 means DELETE will not fail with
   `role_has_assignments`. A snapshot: a concurrent grant can still produce 409. Does not move
   `updated_at`/ETag." Both required (no defaults) — `RoleDefinitionRead` is only built in `_definitions`.
2. **Repository**: `assignment_counts` as above (one GROUP BY, short-circuit on empty). Its docstring
   and `role_has_assignments`'s docstring point at each other ("same predicate; change together").
3. **Service constants** (directly after `_role_has_assignments`, ~line 189), typed `Final`:
   `REASON_SYSTEM_ROLE`, `REASON_DEFAULT_ROLE`, `REASON_ORG_OWNER_ROLE`,
   `REASON_SUPER_ADMIN_ROLE_DESIGNATED`, `REASON_ROLE_HAS_ASSIGNMENTS`,
   `REASON_DEFAULT_ROLE_ADMIN_PERMISSION`, `REASON_ORG_OWNER_ADMIN_PERMISSION`,
   `REASON_DEFAULT_ROLE_ORG_PERMISSION`, `REASON_ROLE_HAS_ORG_PERMISSIONS`,
   `REASON_WILDCARD_NOT_ALLOWED`, `REASON_SUPER_ADMIN_MUST_KEEP_WILDCARD`; plus two tuples
   `ROLE_DELETE_CONFLICT_REASONS` / `ROLE_PERMISSIONS_CONFLICT_REASONS` (router docs + tests iterate them).
   Reuse USR-37's `REASON_CHANGED_CONCURRENTLY` (line 1487) — do **not** move or duplicate it (it is
   referenced inside function bodies, so definition order is irrelevant at runtime; for the tuples,
   which are module-level, define them after line 1487, i.e. next to the USR-37 block, to avoid a
   NameError). `_role_has_assignments` uses `REASON_ROLE_HAS_ASSIGNMENTS` (value unchanged, also used
   by line 1096). Add `_role_conflict(message: str, reason: str) -> ConflictError` returning
   `details=[{"reason": reason}]` — do not rename `_assignment_conflict` (USR-25/USR-27 hotspot).
4. **`_definitions`** (1189-1213): call `counts = await self.repo.assignment_counts([r.id for r in
   roles])` once, beside `role_permission_rows`; set `is_org_owner=r.id == app.org_owner_role_id` and
   `assignment_count=counts.get(r.id, 0)`. List and detail both go through it → one extra query each.
5. **DELETE** (1345-1362): replace each bare `ConflictError(msg)` with `_role_conflict(msg, REASON_…)`;
   the designated-role raise keeps its literal `details` but uses the constant for the reason value.
   Message strings unchanged. Guard predicate and check order unchanged.
6. **PUT** (1435-1461): same for the four bare raises; the two shipped ones switch to constants with
   identical `field`/`reason`. Line 215 in `_set_role_permissions` becomes `_role_conflict("Role
   permissions changed concurrently", REASON_CHANGED_CONCURRENTLY)` — this helper is also used by the
   default-role builder (line 336), which therefore gains the same reason on its race 409: additive,
   same meaning, acceptable under R9.
7. **Router**: `_ROLE_DELETE_409 = f"code=conflict; details[0].reason: {', '.join(...)}"` and
   `_ROLE_PERMISSIONS_409` likewise plus "or code=role_has_assignments (details[0].reason
   role_has_assignments) when an is_admin permission is added to a role with assignments"; use them in
   the `errors={409: …}` maps at lines 394 and 414.
8. **Docs**: `docs/consumers.md` bullet and `docs/identity-architecture.md:186` cell.
9. `make check`.

Rules: R1 (count in repository, flags/reasons in service, docs-only in router); R2 (no schema change);
R3/R4 unchanged (same permissions; DELETE/PUT audit paths untouched — 409s write no audit, as today);
R6 (envelope via `ConflictError`, code `conflict`; the one shipped exception keeps its code); R7
(guard semantics and `_assignment_conflict` untouched); R8 (tests per reason and field; `make check`);
R9 (additive fields and `details`, no renamed codes/values).

Estimated diff: ~70 lines app code, ~200 lines tests, ~20 lines docs — one cycle, no split.

## Specialist input used
- consult-database-optimizer 1, 2, 3, 4 (count = all rows, `assignment_counts` GROUP BY, on list and detail, `is_org_owner` next to `is_default`) — adopted.
- consult-database-optimizer 5, 6 (reasons keep `field`; add `super_admin_role_designated`) — adopted.
- consult-database-optimizer 7 (tests: active/expired/org-scoped, 0 → 204, query count) — adopted.
- consult-database-optimizer risk "share one predicate helper" — adopted in a lighter form: cross-referencing docstrings plus `test_assignment_count_expired_only_blocks_delete`, which fails if either side diverges. A shared SQL helper for an `EXISTS` and a `GROUP BY` would add indirection for one `where` clause.
- consult-api-platform-engineer 1, 2, 4, 5, 6, 7 — adopted (full reason sets, constants in router docs, tests per reason).
- consult-api-platform-engineer 3 (generic helper or rename `_assignment_conflict`) — adopted as a **new** `_role_conflict`; renaming rejected to avoid churn in USR-37 code that USR-25/USR-27 are editing in parallel.
- consult-api-platform-engineer risks (PUT `role_has_assignments` code; `changed_concurrently` only via race; snapshot count; expired-only roles show non-zero) — all reflected (Q3, monkeypatched race test, doc copy, Q1).

## Split proposal
None.

## Blocking questions for Ming
1. **Does `assignment_count` include expired assignments?** It must equal what blocks DELETE, and the
   guard counts every row. *Default: yes — `COUNT(*)` of all rows (active, expired, org-scoped); a
   role with only expired grants shows e.g. 2 and is not deletable. An `active_assignment_count` can
   be a later ticket.*
2. **Change the delete guard to ignore expired rows instead?** That is a behaviour change the ticket
   did not ask for, and the FK (`NO ACTION`) would still block deleting a role with expired rows.
   *Default: no — guard unchanged (R7).*
3. **PUT `role_has_assignments` (G3) keeps `code: role_has_assignments`** (shipped since USR-02d,
   pinned by `tests/test_admin_guards.py:438`), although the ticket says 409s "keep `code: conflict`".
   Changing it would break v1 clients (R9). *Default: keep the code; document it as the one exception
   in OpenAPI and `docs/consumers.md`.*
4. **Add the reasons the ticket missed to the documented, tested contract:** DELETE
   `super_admin_role_designated`; PUT `role_has_org_permissions`, `wildcard_not_allowed`,
   `role_has_assignments`. All already ship in responses today. *Default: yes.*
5. **`assignment_count` on the list too, not only the detail?** Costs one grouped query per page,
   constant in page size. *Default: yes, on both.*

## Assumptions
- **Hotspot `app/modules/rbac/service.py`:** USR-25 (`self_assignment_forbidden`, assignment write
  paths around lines 1600-1860) and USR-27 (role/org names on assignment reads, `_assignment_read` ~1508)
  are planned in the same batch and also edit this file. USR-26's hunks are at ~184-217, 1189-1213,
  1326-1463 and do not touch the USR-37 block (1486-1496) except reading `REASON_CHANGED_CONCURRENTLY`;
  the module-level reason tuples go immediately after line 1487 — the one spot that may conflict
  textually with USR-25 if it adds a reason constant there. Whichever merges second rebases; expect a
  trivial conflict at most. `router.py`/`schemas.py` may also be touched by USR-27 (different models
  and routes).
- New reasons carry no `field` key (matching USR-37's reason-only details); shipped `field` keys stay.
- `is_org_owner` is `false` on apps with no org owner role (`org_owner_role_id IS NULL`), including
  non-org apps and `admin`.
- `assignment_count`, `is_org_owner` are derived fields: like `is_default`/`privileged` (USR-24 note in
  `docs/consumers.md:105-107`) they do not move `updated_at`, so the detail ETag does not change when
  only they change. Documented, not changed.
- The default-role builder's race 409 (`_set_role_permissions`, used at line 336) gains
  `details[0].reason = changed_concurrently` as a side effect; additive and same meaning.
- No new endpoint, so no new auth-class registry entry (`tests/test_route_auth_classes.py`) and no
  new audit actions.
- No spec text change is needed (§5.3 already covers the roles staff API); if Ming wants a spec
  changelog line like USR-24's (`docs/spec-v1.md:59`), that goes through the product-manager, not this diff.
