**Findings**

- **Where the change goes.** `RbacService._update` in `/home/user/org-admin-api/app/modules/rbac/service.py` (lines ~404-505) handles `PATCH /apps/{key}`. `changes` is computed first, so it already holds the real `org_mode` transition. A same-value PATCH produces no `changes`, hits the `if not changes: return` at line 494, and writes nothing. Idempotency is therefore already correct as long as the new guard only fires when `changes.get("org_mode") == "none"` and `app.org_mode == "required"`.
- **Existing org_mode checks in the update path.**
  - Admin app with `org_mode` other than `none`: 409 `ConflictError`, no details (line 421).
  - Owner role set while the resulting mode is not `required`: 422 `UnprocessableError`, `details=[{"field":"org_owner_role_id","reason":"org_mode_required"}]` (lines 444-465). The test `test_patch_org_mode_none_while_owner_set_422` (tests/test_rbac_apps_api.py:598) pins it and shows the workaround `{"org_mode":"none","org_owner_role_id":null}`, which must keep working.
  - Default role holding `scope=org` permissions on a switch to `required`: 422 `role_has_org_permissions` (ADR-0015). This only applies to none to required, so it is unaffected.
- **Org owner role is not a separate blocker.** The 422 above already refuses it unless the same request clears it. DB constraint `org_owner_requires_org_mode` backs this. Do not add it to the 409 blockers.
- **Existing 409 shape.** `ConflictError` has code `conflict`. Refusals use `details=[{"field": ..., "reason": ...}]`, with no values. Entitlements and app_credentials use field `app_key`/`key`, with reasons such as `org_mode_none` and `app_has_entitlements`.
- **Auditing.** `ConflictError` is not in `DENIED_ERRORS`, so a refused 409 writes no audit row. This matches the ticket and needs no extra work.
- **Blockers that exist in the data model.**
  - Org entitlements: `OrgEntitlement.app_id`. The helper `repo.app_has_entitlements` already exists (`app/modules/rbac/repository.py:115`).
  - Org-scoped assignments: `RoleAssignment.org_id IS NOT NULL` joined to `Role.app_id`. No helper exists. The existing `app_has_assignments` also counts app-scope rows, so do not reuse it.
  - Invitations: `org_invitations` carries `app_id`, `role_id` and `expires_at`. This is a third candidate blocker.
  - Organizations: the table is platform-wide with no `app_id` (D10/D15, OQ1). "Organizations created for the app" cannot be detected and is not a blocker.

**Recommendations**

1. **Placement (AC1).** Put the guard after the admin-app and role validations and before the origin check (G6) and the write. The 422 validation errors and the admin 409 then win over it. Do the check only when `changes.get("org_mode") == "none"`. The `_locked_app` row lock already serialises PATCH against PATCH.
2. **Error shape.** `ConflictError("App has organization data; clear it before switching org_mode to none", details=[{"field": "org_mode", "reason": "org_mode_in_use"}])`. A single reason, with no counts or values.
3. **Which blockers.**
   - Required by the AC: org entitlements (reuse `app_has_entitlements`) and org-scoped assignments. For the latter, add `repo.app_has_org_assignments(app_id)`: `exists` over `RoleAssignment` joined to `Role` where `Role.app_id == app_id` and `RoleAssignment.org_id IS NOT NULL`.
   - Count expired assignments too (do not filter on `expires_at`). The rows still exist and reappear if `org_mode` flips back.
   - Pending invitations: add them only if an accepted/pending status column makes that clean. Otherwise list them as a follow-up (R7). Accepted invitations already produce assignments.
4. **Writer locking.** Check whether the entitlement and assignment writers lock the app row (`_locked_app` or `FOR SHARE`). If not, there is a small race with the new guard, so log it as a follow-up.
5. **Docs.**
   - Router: change the `_with_409` text at `app/modules/rbac/router.py:86` to mention "org_mode in use".
   - ADR: add a note to ADR-0015 (line ~54 discusses the flip) or ADR-0013.
   - `docs/identity-architecture.md`: the admin API table.
   - `docs/consumers.md`: line ~418.
   - `docs/spec-v1.md`: §5.6 note.
   - `docs/deploy.md` needs no change, because the CLI never changes `org_mode` of an existing app.
   - Use the reason string `org_mode_in_use` consistently everywhere.
6. **Tests** in `/home/user/org-admin-api/tests/test_rbac_apps_api.py`, reusing `_org_app`, `make_app`, `make_role` and the entitlements helpers.
   - Entitlement blocker: 409, `details[0].reason == "org_mode_in_use"`, `org_mode` still `required`, no `app.updated` audit row.
   - Org-scoped assignment blocker: same assertions.
   - Only app-scope assignments and no entitlements: 200, one audit row.
   - `{"org_mode":"none","org_owner_role_id":null}` with no org data: 200.
   - none to required with org data around: still 200.
   - `required` to `required` with org data: 200, no audit row.
   - Owner role still set and no org data: still 422 `org_mode_required`.
   - Both org data and an owner role set, PATCH of only `org_mode=none`: 422, not 409. State this ordering in the plan.

**Risks**

- Reason-string drift: delete uses `app_has_entitlements` and entitlements uses `org_mode_none`. Use `org_mode_in_use` for the new case and do not rename the existing reasons (R9, no breaking changes inside v1).
- The 409 message should point at clearing org data, not only the owner role. An integrator may otherwise retry with `org_owner_role_id: null` and hit the same 409.
- The guard is only as strong as the writers' locking (recommendation 4). An assignment or entitlement created after the check passes would still be orphaned.
- `role_assignments.org_id` has `ondelete=RESTRICT` to organizations, so rows linger after org suspension. They block intentionally until revoked.

**Open questions**

1. Should pending invitations (`org_invitations` rows for the app) block, or is that a follow-up?
2. Should disabled or suspended entitlements block? I assume yes, since the rows exist and the data would be orphaned.
3. Is there an admin way to clear an app's org data (revoke every entitlement and org assignment)? If not, `required` to `none` is effectively one-way once orgs exist. Raise this with the owner as a follow-up and do not widen this ticket.
