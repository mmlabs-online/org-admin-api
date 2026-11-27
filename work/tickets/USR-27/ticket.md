# USR-27: Role assignments — return role and org names

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api |
| Attempts | 1 |
| Claimed by | — |
| Created | 2026-11-19 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/112 |

## Goal
The admin UI can name a role and its org scope without extra lookups: `AssignmentRead` (`GET /users/{id}/role-assignments`) and `RoleHolderRead` (`GET /apps/{key}/role-definitions/{role_id}/assignments`) carry `role_key` and `org_id` only.

## Spec reference
§5.3 Authorization; §5.8 Admin bullet (staff manage users' roles).

## Acceptance criteria
- [ ] `AssignmentRead` and `RoleHolderRead` gain `role_name` and `org_name` (nullable; null for app-scope assignments), read in the same query (no N+1). Additive (R9).
- [ ] Every endpoint returning those schemas fills them (per-user list, assignment create, role holders).
- [ ] Tests: names present, `org_name` null for app-scope rows; bounded-query test still passes.

## Dependencies
USR-02c, USR-17. Consumer: admin-pan PAN-015c. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-27 (Ming, chat): "duyệt hết; câu 1–24 theo mặc định; cho merge USR-28".
- Plan approved (2026-11-27, batch 2026-11-27-1); all blocking questions answered with the plan defaults.

## Handoff

### 2026-11-27 — claude — DONE (batch 2026-11-27-1, attempt 1)
- Agent calls: 5, tokens ~246k total (api-platform-engineer 38k, planner opus 57k, implementer sonnet 53k, reviewer opus 49k, appsec-engineer 49k).
- Done: `role_name` and `org_name` on `AssignmentRead` / `RoleHolderRead` (per-user list, assign create, role holders) via an outer join in `_assignment_rows` — no extra statements; row tuples → frozen dataclasses, positional reads confined to `_assignment_row`; `OrgMemberRead` and batch results unchanged. Review PASS (4527 passed; ruff/mypy/alembic clean; tier high); appsec PASS.
- Follow-ups: docs (identity-architecture §4, spec §5.15:337) list the new fields and note `org_name` under `admin:users:read`; shared SQL-capture test helper.
- PR: https://github.com/mmlabs-online/org-admin-api/pull/112 (tier high). Next: Ming merges #112 (or "merge cho tôi #112"); then `/run-batch --resume` builds USR-28.
