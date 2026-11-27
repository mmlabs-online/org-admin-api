# Consult — database-optimizer — USR-26 (2026-11-27)

**Findings**
- Delete guard `role_has_assignments` (repository.py:299-301) = `EXISTS(role_assignments WHERE role_id=?)`, no `expires_at` filter — counts expired, org- and app-scoped rows. Caller service.py:1356 raises bare `ConflictError("Role has assignments")`. Same guard for super-admin designation (service.py:623) and adding admin permissions (service.py:1462).
- `_definitions` (service.py:1189-1213) batches by `[r.id for r in roles]` via `role_permission_rows` (repository.py:333-344) — the pattern to copy; list and detail both go through it.
- `RoleAssignment.role_id` FK to `roles.id`, NO ACTION (models.py:236); `ix_role_assignments_role_id` (models.py:229) — grouped count over ≤ 100 ids uses it; no migration.
- Other "active-only" counts exist (repository.py:604 `_is_active`, :1040) — naive reuse would disagree with the guard.
- Delete concurrency: an insert between guard and delete → FK `IntegrityError` in a savepoint → "changed concurrently" (service.py:1359-1362). Whether delete takes `_locked_app` not checked; permissions path does (service.py:1382).

**Recommendations**
1. `assignment_count` = `COUNT(*)` of all rows for the role (no expiry filter) — `== 0` iff the guard passes; document in the schema `Field(description=...)` and OpenAPI.
2. `RbacRepository.assignment_counts(role_ids) -> dict[UUID, int]` (`{}` for empty): `select(role_id, func.count()).where(role_id.in_(ids)).group_by(role_id)`; `.get(r.id, 0)` in `_definitions` — one extra query per page.
3. On both list and detail.
4. `is_org_owner = r.id == app.org_owner_role_id` next to `is_default` (service.py:1205).
5. Add `reason` to `ConflictError` details at service.py:1346-1362 and the permissions path, keeping existing `field` keys; one constant set referenced in router `responses`.
6. Keep `super_admin_role_designated` (omitted by the ticket) in the documented set.
7. Tests: count with active, expired, org-scoped rows; count 0 → delete succeeds, > 0 → 409 `role_has_assignments`; page of several roles with a query-count assertion.

**Risks**
- Count is a snapshot; a holder added after the read still gets 409 `changed_concurrently` — UI treats it as a hint.
- If the guard later ignores expired rows, count and guard must change together — share one predicate helper.
- Count incl. expired rows may look high next to active-only holder lists (repository.py:625) — UI copy "assignments".

**Open questions for Ming (defaults)**
1. Include expired rows — yes (matches the guard).
2. Add `super_admin_role_designated` to the documented reasons — yes.
3. `assignment_count` on the list too — yes.
