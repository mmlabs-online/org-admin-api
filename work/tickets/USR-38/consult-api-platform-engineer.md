# Consult — api-platform-engineer — USR-38 (2026-11-26)

**Findings**
- Precedent: `org_owner_role_id` in PATCH /apps/{key}. `AppUpdate` (app/modules/rbac/schemas.py:58-81) allows null only for `default_role_id` and `org_owner_role_id`; `AppFields` (:84-97) exposes both; service validates in the PATCH path (rbac/service.py:471-501) with 422 reasons `unknown_role`, `role_has_admin_permissions`, `org_mode_required`, `same_as_default_role` (`details[{field, reason}]`); `org_mode_in_use` is 409 (:513-525).
- `RoleDefinitionRead` (schemas.py:232-256) has `is_default` and `privileged`, set in `_definitions` (service.py:1094-1117); `privileged` = `role_is_privileged(app_key, holds_admin)` (:1065), mirroring `_guard_privileged` (:1614-1623), which only checks `role_has_admin_permission` — a designated role is not covered today.
- `_grant_locked` (:1557-1612) already checks `expires_at` and `org_id` via `_assignment_unprocessable(msg, field, reason)`. `revoke` calls `_guard_privileged` too (:1637).
- 409 = current state conflicts, 422 = bad input (`org_mode_in_use`, admin app invariant).
- Manifest: `org_mode` of an existing app already immutable through the manifest (service.py:636-641); the manifest only adds permissions and writes `origins` (:577, :788).
- `_ensure_member_role` refuses 409 `member_is_org_owner` (:314-319); same pattern needed for the designated role.

**Recommendations**
1. Field on PATCH /api/v1/apps/{key} (`super_admin_role_id`, nullable set/clear), not a new route (matches `org_owner_role_id`, additive R9, Bearer). Explicit `require_super(actor)` whenever the field is present, even for a no-op (today `require_super` is reached only for the admin app).
2. `super_admin_role_id: uuid | None` in `AppFields` (detail and list); add it to the null-allowed set in `AppUpdate`.
3. 422 reasons (`field = super_admin_role_id`): `unknown_role` (incl. role of another app), `org_mode_required`, `admin_app_not_allowed`, `same_as_default_role`, `same_as_org_owner_role`, `role_has_org_permissions` (only if Ming picks the architect option, OQ80/OQ88).
4. 409 reasons: `designated_role_in_use` (role delete/rebuild), `super_admin_role_designated` (`org_mode` leaves `required`), `designated_role_protected` (manifest, role permission replace, rename/delete); `member_is_super_admin` alongside `member_is_org_owner`.
5. Grant refusals (422 via `_assignment_unprocessable`): `org_id_not_allowed`; `recipient_not_staff` (no active `admin:super-admin`; plain read under the app lock, no `admin` lock); `designated_role_bulk_forbidden` on the bulk route at request level; self-assignment reuses `SelfAssignmentForbiddenError`; non-`admin:*` actor 403 via `require_super`.
6. `is_super_admin: bool` on `RoleDefinitionRead`; `privileged = true` for the designated role; pass `app.super_admin_role_id` into `role_is_privileged` and `_guard_privileged` (update both together).
7. No change to the public role catalog (`GET /apps/{key}/roles`); the designation is a staff-only fact.
8. Audit via the existing `app.updated` diff (R4); grant audited as `role_assignment.granted`; no new action by default.
9. OpenAPI: document the new reasons in route `responses` text (as `entitlements/router.py:65`); widening `privileged` is a semantic widening — note it.

**Risks**
- `privileged` changes meaning (only adds roles — safe for UIs).
- 409/422 split easy to get wrong.
- A role with org-scope permissions leaks through the `roles` union (ADR-0015) — OQ80.
- Manifest or bulk paths that skip `_guard_privileged` bypass the designation — test each path.

**Open questions for Ming (defaults)**
- OQ80/OQ88 designated role may hold `scope = org` permissions? — no (`role_has_org_permissions`); consumers grant from the opt-in claim.
- OQ81 lesser staff roles as recipients? — no, only active `admin:super-admin`.
- OQ82 lifecycle — no cascade revoke, USR-39 re-checks at mint.
- OQ83 `expires_at` — optional.
- OQ90 — P2, simap first, designation per app.
- Separate audit action for set/clear? — no, `app.updated` diff.
