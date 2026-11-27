# Consult — api-platform-engineer — USR-26 (2026-11-27)

**Findings**
- `RoleDefinitionRead` (schemas.py:245-273): `is_default`, `is_super_admin`, `privileged`; no `assignment_count` / `is_org_owner`. Built in `_definitions` (service.py:1189-1213) for list and detail; already batches permissions via `role_permission_rows`.
- Delete guard `role_has_assignments` (repository.py:299-301) = `EXISTS(role_id == id)`, no `expires_at` filter (models.py:248); same predicate drives PUT G3 (service.py:1462) and designation (service.py:623).
- DELETE 409s (service.py:1345-1362), in order: system role (no details); default role (no details); org owner role (no details); designated super-admin role (`[{"field":"role_id","reason":"super_admin_role_designated"}]`); `Role has assignments` (no details); `IntegrityError` → `Role was changed concurrently` (1362).
- PUT permissions 409s (service.py:1435-1463, plus 215): default role + admin permission (1436); org-owner + admin permission (1438); org-mode default role + org permission (1444); designated role + org permission (1447, `role_has_org_permissions`, field `permission_keys`); designated role + wildcard (1452, `wildcard_not_allowed`); `admin:super-admin must keep admin:*` (1461); role has assignments when an admin permission is added (1463, `_role_has_assignments` service.py:184 → `RoleHasAssignmentsError` code **`role_has_assignments`**, not `conflict`, errors.py:111, details `{field, reason}`); `IntegrityError` → `Role permissions changed concurrently` (215).
- The ticket's PUT list misses `role_has_org_permissions`, `wildcard_not_allowed`, `role_has_assignments`; DELETE list misses `super_admin_role_designated`.
- USR-37 pattern: `REASON_*` constants (service.py:1486-1487), `_assignment_conflict(message, reason)` (1490) → `details=[{"reason": r}]`; USR-38 and G3 details carry `field` too.
- Router docs for these 409s are free text (router.py:394, 414); assignments_router.py:54-56 interpolates constants.

**Recommendations**
1. `assignment_count` = all rows incl. expired (matches the guard: 0 = deletable); document in the Field description; don't change the guard.
2. `RbacRepository.assignment_counts(role_ids) -> dict` (one GROUP BY), called in `_definitions`; `assignment_count: int`, `is_org_owner: bool` with `Field` descriptions.
3. `REASON_*` constant set next to USR-37's; generic `_role_conflict(message, reason)` (or rename `_assignment_conflict`); code stays `conflict`.
4. DELETE reasons: `system_role`, `default_role`, `org_owner_role`, `super_admin_role_designated` (keep its shipped `field`), `role_has_assignments`, `changed_concurrently`.
5. PUT reasons: `default_role_admin_permission`, `org_owner_admin_permission`, `default_role_org_permission`, `super_admin_must_keep_wildcard`, `role_has_org_permissions` (existing), `wildcard_not_allowed` (existing), `role_has_assignments` (existing), `changed_concurrently`.
6. Keep existing `field` / `reason` values; only add details where none.
7. Router `errors={409: …}` text built from the constants; tests per reason, both fields on list and detail, expired + active assignments, zero-count role deletable.

**Risks**
- PUT `role_has_assignments` has `code=role_has_assignments` — contradicts "keep `code: conflict`"; changing it breaks R9 — leave it.
- Some reasons only via races or rare setups (`changed_concurrently` on DELETE; `default_role_org_permission` needs org_mode=required).
- Count is point-in-time.
- Expired-only roles show non-zero count and aren't deletable — may look stale.
- Undocumented reasons become a stable contract — tests must pin them.

**Open questions for Ming (defaults)**
1. Count includes expired rows — yes (optional `active_assignment_count` later).
2. Change the guard to ignore expired rows — no (R7).
3. PUT `role_has_assignments` → code `conflict` — no, keep and document.
4. Add the three missing PUT reasons and `super_admin_role_designated` to scope — yes.
5. `assignment_count` on list responses — yes (batched).
