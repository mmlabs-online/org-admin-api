# Consult — api-platform-engineer — USR-27 (2026-11-27)

**Findings**
- `Role.name` exists (String(255), NOT NULL, `rbac/models.py:185`); `Organization.name` NOT NULL (`organizations/models.py:33`); neither read for assignments today.
- `AssignmentRead` (`rbac/schemas.py:309`: `role_key`, `org_id`); `RoleHolderRead(AssignmentRead)` (`schemas.py:361`) inherits — one edit covers both. `AssignmentBatchItem` (`schemas.py:346`) holds only `assignment_id` — no change.
- Builders: `RbacRepository._assignment_rows()` (`repository.py:607`) selects `(RoleAssignment, App.key, Role.key, active)`; `page_assignments` (:759) → `GET /users/{id}/role-assignments`; `get_assignment_row` (:781) → POST create response (`service.py:1643`); `page_role_holders` (:614) → holders list; mappers `_assignment_read`, `_role_holder_read` (`service.py:1508`, `1523`). Aliases `AssignmentRow = tuple[RoleAssignment, str, str, bool]`, `RoleHolderRow` (`repository.py:82-84`).
- `page_org_members` (:671) already `.add_columns(Role.name, ...)` and exposes `role_name` (`OrgMemberRead`, `service.py:1545`) — reuse the name.
- The create response already re-reads through the joined query — no extra query.
- Query-count tests: `tests/test_auth_orgs_super_admin.py:286-330`, `tests/test_org_members_api.py:287`.

**Recommendations**
1. `role_name: str` and `org_name: str | None` on `AssignmentRead` (always present; `org_name` null for app scope).
2. `_assignment_rows()` selects `Role.name`, `Organization.name` with `.outerjoin(Organization, Organization.id == RoleAssignment.org_id)` (inner join would drop app-scope rows); widen `AssignmentRow` to 6 and update unpacking in `page_assignments`, `get_assignment_row`, `_assignment_read`.
3. `page_role_holders` index-based `r[4..6]` shifts by 2 — prefer named access; `page_org_members` also uses `_assignment_rows()` — fix its indexes and drop its duplicate `Role.name`.
4. Count queries need no Organization join (1:1 on PK).
5. Tests: per-user list names + null `org_name` for app scope; POST create org-scoped and app-scope; holders list; count-constant test for the per-user list (limit 1 vs 50); keep `test_holder_query_count_constant`, `test_page_org_members_two_statements`.
6. OpenAPI `Field(description=...)`; update any contract test; additive (R9), no ADR.

**Risks**
- Tuple index shifts (3) — main regression risk; `mypy --strict` catches most.
- `org_name` is current, not a snapshot.
- Suspended/renamed orgs returned as-is.

**Open questions for Ming (defaults)**
- `org_name` on `OrgMemberRead` — no (R7).
- Names on USR-36 batch results — no.
