## Attempt 1 — 2026-11-27
Verdict: PASS

Tier: High (`risk_tier.py --base origin/main` → `high`; all product hunks under `app/modules/rbac/**`). Ming merges.
Reviewed: c6ca37b (diff vs 3c99dd9). HEAD 3c73bb0 adds only board/ticket state (`work/backlog.md`, `work/tickets/USR-27/ticket.md`).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed (exit 0).
- `uv run ruff format --check app tests migrations` — 449 files already formatted.
- `uv run mypy app` — Success: no issues found in 216 source files.
- `uv run pytest -q` (local Postgres, org_admin_test) — 4527 passed, 2 warnings in 776.79s. Matches impl-log (4527 passed).
- `uv run alembic check` (local dev DB `localhost:5432/org_admin`, config default) — No new upgrade operations detected.
- `python3 -I .github/scripts/risk_tier.py --base origin/main` — `high`.

### Acceptance criteria
- [x] AC1 `AssignmentRead` gains `role_name: str`, `org_name: str | None`; `RoleHolderRead` inherits — evidence: `app/modules/rbac/schemas.py:326-330`; `tests/test_openapi_contract.py:127 test_role_holders_operation` (required set +2), `:143 test_assignment_read_component` (11 required keys; `org_name` anyOf string/null). Additive only (R9): no field removed or renamed, no request schema change.
- [x] AC2 Per-user list fills names; `org_name` null for app scope — `service.py:1566-1580` (`_assignment_read`), `repository.py:812-832` (`page_assignments` → `_assignment_row`); tests `tests/test_rbac_assignments_api.py:105 test_list_happy_path` (exact dict incl. `org_name: None`), `:197 test_list_org_scope_names` (role renamed to "Viewer" proves `Role.name` not `Role.key`; org row "Acme", app row null). PASSED.
- [x] AC3 Assign create (POST 201) fills names via the same query — `service.py:1703-1709` re-reads through `get_assignment_row` (`repository.py:834-845`), no extra statement; tests `:262 test_assign_app_scope_201` (`listed == [body]` still holds), `:285 test_assign_org_scope_201` (org and app scope). PASSED.
- [x] AC4 Role holders fill names — `service.py:1583-1590` (`_assignment_read(row.base)`), `repository.py:661-694`; tests `tests/test_rbac_role_holders_api.py:189 test_holders_item_shape`, `:173 test_holders_org_scope_names` (incl. `org_id` filter, `org_name is None` iff `org_id is None`). PASSED.
- [x] AC5 Same query, outer join, no N+1 — `repository.py:640-654` single `select(... Role.name, Organization.name ...)` with `.outerjoin(Organization, Organization.id == RoleAssignment.org_id)` (FK→PK, ≤1:1; app-scope rows kept); count queries unjoined. Tests: `tests/test_rbac_assignments_api.py:215 test_list_bounded_queries` (limit 1 vs 6, equal counts), `:233 test_page_assignments_two_statements` (2 statements, `LEFT OUTER JOIN organizations` in rows statement, none in count), `tests/test_rbac_role_holders_api.py:421`, `:434` unchanged and PASSED.
- [x] AC6 `OrgMemberRead` unchanged — `schemas.py:405` untouched; `_org_member_read` (`service.py:1593-1610`) maps the same output fields, `role_name` now from `base.role_name` (the duplicate `Role.name` extra column dropped, `repository.py:723`). `tests/test_org_members_api.py:119 test_members_item_shape` (exact `KEYS`, no `org_name`) and `:287 test_page_org_members_two_statements` unchanged and PASSED.
- [x] AC7 Revoke and batch unaffected — `revoke` (`service.py:2051-2059`) now uses `row.app_key` / `row.assignment` / `row.role_key`; I checked every consumer of `AssignmentRow`/`RoleHolderRow`/`OrgMemberRow`/`get_assignment_row`/`page_*` (grep over `app`, `tests`): no positional index or tuple unpacking left; the only index reads are in `_assignment_row` (`repository.py:657-659`) and the `_ASSIGNMENT_COLUMNS`-offset extras (`:691-693`, `:730-732`), both matching the select column order (6 base + email, display_name, status). `AssignmentBatchItem` (`schemas.py:362-369`) untouched. Revoke tests (`tests/test_rbac_assignments_api.py:511-553`, incl. last-super-admin 409), batch tests and `tests/test_openapi_contract.py:462` PASSED.
- [x] AC8 Green checks — see above.
- USR-25 / USR-26 behaviour: `tests/test_rbac_self_assignment.py`, `tests/test_rbac_role_definitions_api.py`, `tests/test_rbac_assignment_batches_api.py`, `tests/test_rbac_super_admin_role.py`, `tests/test_admin_guards.py`, `tests/test_audit_denied.py` all pass in the full run; the diff touches neither the USR-25 guards nor USR-26 counts/409 reasons.
- Ming verification after merge: none required (optional: admin-pan PAN-015c renders the names from a real response).

### Deviations (judged)
- `_assignment_row(r: Sequence[Any])` — accepted. It is the single index-reading point, documented as such; SQLAlchemy `Row` is a `Sequence`, and typed `Row[...]` with mixed widths (base vs. `add_columns`) does not type-check cleanly. Type safety lost is limited to one 6-arg constructor call that the API tests cover end to end.
- `revoke` change — accepted, but it is a required adaptation, not a bug fix: on base 3c99dd9 the 4-tuple unpacking `ra, _, role_key, _ = row` was correct for the 4-tuple row; it would have broken with the dataclass, so the change is in plan (plan Approach 5 names `revoke`).

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: ORM stays in the repository, service maps dataclasses to schemas. R2: no schema change, `alembic check` clean. R3/R4/R6: no auth, audit or error change. R7: changed product/test files exactly match the plan's file list; no out-of-plan files. R8: tests per plan. R9: additive response fields. R11: holder query still selects explicit `User` columns.

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡 (💭 `org_name` now visible under `admin:users:read`, accepted).

### Nits (do not block)
- 💭 `tests/test_rbac_assignments_api.py:48-61` duplicates `_capture_sql` from `tests/test_rbac_role_holders_api.py` / `tests/test_org_members_api.py`; a shared helper in `tests/` would remove three copies (plan chose the local copy).
- 💭 impl-log "Deviations" calls the `revoke` change a fix for something "left as a 4-tuple by USR-25/26"; it was correct for the old tuple and only needed adapting.

### Follow-ups (out of scope)
- Consider a shared `capture_sql` test helper (see nit).
- Plan Q1/Q2 defaults kept: no `org_name` on `OrgMemberRead`, no names on batch results; new ticket only if admin-pan asks.
