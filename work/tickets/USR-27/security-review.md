# Security review — USR-27 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only — the reviewer ran the suite).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit c6ca37b (diff `3c99dd9..c6ca37b`): `rbac/repository.py` (`_assignment_rows`, `_assignment_row`, `page_role_holders`, `page_org_members`, `page_assignments`, `get_assignment_row`), `rbac/service.py` (`_assignment_read`, `_role_holder_read`, `_org_member_read`, `assign` ~:1703, `revoke` :2048-2100), `rbac/schemas.py:323-330`, routers `assignments_router.py`, `rbac/router.py:328`, `organizations/router.py:175`, `organizations/app_router.py`.

## Findings
- 🟢 **Join keys correct:** `repository.py:653` `.outerjoin(Organization, Organization.id == RoleAssignment.org_id)` on the PK via FK `fk_role_assignments_org_id_organizations` (`models.py:237-242`) — at most one org per assignment, no duplication; `total` counts (no join) still match; `org_name` NULL exactly when `org_id IS NULL`; row filters unchanged; `get_assignment_row` still scoped to `user_id` (:834-845).
- 🟢 **Authorization unchanged:** `admin:users:read` on list and holders; `_ASSIGN_KWARGS`, `_REVOKE_KWARGS` unchanged; no new endpoint; R3 classes unchanged.
- 🟢 **`revoke` guard path unchanged** (`service.py:2051-2075`): attribute access maps to the same columns; sequence read → `_locked_app` → re-read with `populate_existing` → `_guard_privileged` (USR-25 rules) → last-super-admin check; audit payloads identical; no `FOR UPDATE` with the outer join.
- 🟢 **Org-member outputs unchanged:** staff `GET /organizations/{id}/members` (`OrgMemberRead`) same field set (`role_name` from the base row, `org_name` not serialized); app-credential `GET /apps/{key}/orgs/{org_id}/members` (`AppOrgMemberRow`/`AppOrgMemberRead`, `repository.py:764-808`) untouched; no cookie-class use.
- 🟢 **No sensitive columns:** only `Role.name`, `Organization.name` added; `User` columns explicit (no `password_hash`, R11); nothing new logged (R5).
- 🟢 **Positional indexing confined** to `_assignment_row()` / `_ASSIGNMENT_COLUMNS = 6`; extra-column offsets match the `select` order.
- 💭 `GET /users/{id}/role-assignments` and role holders (`admin:users:read`) now return `org_name`, previously readable only with `admin:organizations:read` — low-sensitivity, within the AC, owner-approved plan; accepted.

## Follow-ups (out of scope, R7)
- Docs: `docs/identity-architecture.md` §4 rows and `docs/spec-v1.md:337` — list `role_name` / `org_name` in `AssignmentRead`, note `org_name` visible under `admin:users:read`.
