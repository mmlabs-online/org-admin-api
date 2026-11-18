Status: READY

# Plan — ADM-019: Guard org_mode required → none while org data exists

## Spec summary
- Ticket goal: "Changing an app from `org_mode=required` to `none` must not silently orphan org-scoped data." Owner, 2026-11-17: "block with 409 (option chosen)."
- Spec §5.6: "Which org can use which product, with plan and limits; enable/disable per org." The ADM-006 note says: "Only non-admin, active, org_mode=required apps can be granted."
- Spec §5.3: an app has "an `org_mode` (`none` | `required`; calechip = `required`)". Users get roles per app, "optionally limited to one organization".
- ADR-0013 §4 sets the precedent: refuse, do not cascade. "admin delete of an org with any assignment answers 409 `org_has_assignments`. Cascading would drop grants without per-row audit."
- R6 (409 `conflict`, one error shape), R4 (audit only for applied mutations; refused 409s write no row), R9 (no breaking change: existing reasons keep their names).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `PATCH /api/v1/apps/{key}` that sets `org_mode` from `required` to `none` returns 409 `conflict` with details `[{"field":"org_mode","reason":"org_mode_in_use"}]` when the app has org entitlements or org-scoped role assignments. | `test_patch_org_mode_none_with_entitlement_409`, `test_patch_org_mode_none_with_org_assignment_409`, `test_patch_org_mode_none_with_expired_org_assignment_409`. Each asserts the status, code, exact details, and that `org_mode` is still `required`. |
| 2 | No change when nothing org-scoped exists, and the reverse change (none → required) is unaffected. | `test_patch_org_mode_none_without_org_data_ok` (200, audited), `test_patch_org_mode_none_with_only_app_scope_assignments_ok`, `test_patch_org_mode_required_with_org_data_unaffected`, `test_patch_org_mode_same_value_with_org_data_ok`, and the existing `test_patch_org_mode_none_while_owner_set_422` still passes. |
| 3 | A refused attempt writes no audit mutation row. Tests cover both blockers and the allowed case. | The 409 tests assert zero `app.updated` rows. The allowed-case test asserts exactly one `app.updated` row with `changes == {"org_mode": {"from": "required", "to": "none"}}`. |
| 4 | (Soundness of AC1, from the DB consult.) An entitlement grant cannot slip in between the guard's EXISTS check and the commit. | `EntitlementService.grant` takes `lock_app` before it checks `org_mode`. `test_grant_locks_app_row` spies on `RbacRepository.lock_app` and asserts it is called with the app key. The existing `test_ungrantable_app_409` still passes for all 3 reasons. |
| 5 | `make check` is green. | `make check`. `alembic check` is clean because there is no schema change. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/repository.py` | Add `app_has_org_assignments(app_id) -> bool`: `EXISTS` over `RoleAssignment` joined to `Role` with `Role.app_id == app_id` and `RoleAssignment.org_id IS NOT NULL`. There is no `expires_at` filter. Do not change `app_has_assignments`, which `delete` uses. | AC1 assignment blocker. |
| `app/modules/rbac/service.py` | In `AppService._update`, add the guard (Approach step 2). | AC1–3. |
| `app/modules/rbac/router.py` | Line 86: change the `_with_409` text to "Admin app invariant, origin already taken, or org_mode in use". | OpenAPI documents the new 409. |
| `app/modules/entitlements/service.py` | `grant`: refuse `ADMIN_APP_KEY` before locking, then read the app with `self.rbac.lock_app(data.app_key)` instead of `get_app_by_key`. Add a docstring line on the lock. | AC4. Closes the race found by the DB consult. |
| `tests/test_rbac_apps_api.py` | Add the tests listed below. | R8. |
| `tests/test_entitlements_api.py` | Add `test_grant_locks_app_row`. | R8, AC4. |
| `docs/spec-v1.md` | §5.6: append a dated note (ADM-019). | Spec is the source of truth. |
| `docs/adr/0013-org-scoped-identity.md` | Consequences: add one bullet about the new 409. | Same refuse-not-cascade principle as §4. |
| `docs/adr/0015-org-roles-claim-and-permission-scope.md` | Consequences: add one clause next to the "none to required flip" line about the reverse flip (ADM-019). | Keeps the flip rules together. |
| `docs/identity-architecture.md` | Line 176 (admin `/api/v1/apps` row): append "PATCH `org_mode` required→none: 409 `org_mode_in_use` while org entitlements or org-scoped assignments exist (ADM-019)". | Admin API table. |
| `docs/consumers.md` | Line ~418: one sentence saying a flip back to `none` is refused while org data exists. | Integrators and the admin UI need to know about the 409. |

No other files. `docs/deploy.md` is unchanged: the CLI never changes `org_mode` of an existing app (line 725).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| test_rbac_apps_api.py › `test_patch_org_mode_none_with_entitlement_409` | `_org_app`, `make_org`, plus an `OrgEntitlement` row (disabled, `enabled=False`, so inactive rows also block). PATCH `{"org_mode":"none"}` returns 409 `conflict` with details `[{"field":"org_mode","reason":"org_mode_in_use"}]`. The app row is still `required`, and there are 0 `app.updated` audit rows. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_with_org_assignment_409` | `assign(..., org_id=org.id)` on an app role, with no entitlement. Same assertions as above. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_with_expired_org_assignment_409` | Same as above, with `expires_at` in the past. Expired rows still block. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_with_only_app_scope_assignments_ok` | An app-scope assignment (`org_id=None`) returns 200 and `org_mode == "none"`. Pins that `app_has_assignments` is not reused. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_without_org_data_ok` | Returns 200 with exactly one `app.updated` audit row, `changes == {"org_mode": {"from": "required", "to": "none"}}`. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_and_clear_owner_without_org_data_ok` | Owner role set, then `{"org_mode":"none","org_owner_role_id":null}` returns 200. |
| test_rbac_apps_api.py › `test_patch_org_mode_none_owner_set_and_org_data_422_wins` | Owner role set and an org entitlement present. PATCH `{"org_mode":"none"}` returns **422** `org_mode_required`, not 409. Then `{"org_mode":"none","org_owner_role_id":null}` returns **409** `org_mode_in_use`, and the owner role is still set (no partial write). |
| test_rbac_apps_api.py › `test_patch_org_mode_required_with_org_data_unaffected` | A `none` app holding an orphan org-scoped assignment (the factory bypasses the service) can be patched to `required`: 200. |
| test_rbac_apps_api.py › `test_patch_org_mode_same_value_with_org_data_ok` | A `required` app with an entitlement, PATCH `{"org_mode":"required"}` returns 200 with no audit row. A PATCH of another field (`name`) on the same app also returns 200. |
| test_entitlements_api.py › `test_grant_locks_app_row` | Monkeypatch-wrap `RbacRepository.lock_app` and record the keys. A grant returns 201 and the recorded keys equal `[app.key]`. A grant for `admin` returns 409 `admin_app` and records no `lock_app("admin")` call. |

Existing tests that must stay green unchanged: `test_patch_org_mode_none_while_owner_set_422`, `test_admin_app_locked_fields_409`, `test_ungrantable_app_409`, `test_unknown_org_404`, `test_unknown_app_key_404`, `test_duplicate_grant_integrity_409`. 401/403 on PATCH are already covered and the auth class is unchanged.

For the entitlement row in the rbac tests, insert `OrgEntitlement(org_id=..., app_id=..., plan="pro", enabled=False)` directly through `db_session` with a small local helper. Do not add a new shared factory.

## Approach
1. **Repository (R1):** add `RbacRepository.app_has_org_assignments` next to `app_has_assignments`, using the same `select(exists().where(...))` style plus `RoleAssignment.org_id.is_not(None)`.
2. **Guard in `AppService._update` (R1, R6).** Place it after the `role_has_org_permissions` check (line ~484) and before the `"origins" in changes` block (line ~486). It must come after every 422 check, so the owner-role 422 `org_mode_required` wins. It must also come before the write.
   ```python
   if changes.get("org_mode") == OrgMode.NONE.value and app.org_mode == OrgMode.REQUIRED.value:
       if await self.repo.app_has_entitlements(app.id) or await self.repo.app_has_org_assignments(app.id):
           raise ConflictError(
               "App has organization data (entitlements or org role assignments); "
               "revoke them before switching org_mode to none",
               details=[{"field": "org_mode", "reason": "org_mode_in_use"}],
           )
   ```
   The guard runs only on a real required→none change, so no-op PATCHes and none→required are untouched. The admin app is always `none`, so the guard never fires there. The app row is already locked by `_locked_app`.
3. **Audit (R4):** `ConflictError` is not in `DENIED_ERRORS`, and the caller audits only after `_update` returns. A refusal therefore writes no row and needs no code. The tests pin this.
4. **Race fix in `EntitlementService.grant`.** **Decision: fix it in this ticket.**
   - **Why it is needed:** without the fix, the guard is unsound. A grant reads `org_mode=required` unlocked, the PATCH runs its EXISTS check and commits `none`, and then the grant commits an entitlement on a `none` app. Every assignment writer already uses `lock_app` (rbac assign, invitations accept/create, join requests, membership, org auth_service), so entitlements are the only gap.
   - **Lock order is safe.** No code path locks `organizations` rows explicitly. The repo-wide order is app (`FOR NO KEY UPDATE`) first, then org via a FK `KEY SHARE` (invitations/service.py:295, `_grant_locked`). After the fix, grant does an unlocked `orgs.get` (no row lock), then `lock_app`, then the insert, which takes `KEY SHARE` on org and app. Our own `FOR NO KEY UPDATE` is compatible with `KEY SHARE`, so the order is app → org, the same as everywhere else, and there is no new deadlock edge.
   - **Admin app:** refuse `data.app_key == ADMIN_APP_KEY` with `_refusal(..., "admin_app")` *before* locking. This keeps the admin-lock ordering note at rbac/service.py:1408 intact. `admin` always exists, so the 404/409 outcome is unchanged.
   - **Scope:** update and revoke touch existing rows only and need no lock (DB consult).
   - **Size:** about 5 changed lines.
5. **Docs:** apply the edits listed in the files table. Use the reason string `org_mode_in_use` everywhere. Existing reasons (`org_mode_none`, `app_has_entitlements`) are not renamed (R9).
6. **No migration (R2).** Existing indexes cover both EXISTS queries (`ix_org_entitlements_app_id`; roles by app → `ix_role_assignments_role_id`).

Rules: R1 (repo/service split), R3 (unchanged: Bearer `admin:apps:write`, and `admin:entitlements:write` for grant), R4, R6, R7 (follow-ups below, not in the diff), R8, R9. Estimated diff: about 60 lines of code and 150 lines of tests and docs.

## Specialist input used
- api-platform-engineer:
  - Adopted: placement after the 422s, the single reason `org_mode_in_use`, a message that points at clearing org data, no audit work, the docs list, and the test list including the 422-over-409 ordering.
  - Adopted: "org owner role is not a separate blocker", because the 422 already covers it.
- database-optimizer:
  - Adopted: the new `app_has_org_assignments` with expired rows included, reuse of `app_has_entitlements` (counts disabled rows), no migration, and pending invitations not blocking (accept re-checks `org_mode`).
  - **Rejected:** its suggestion that the 409 should win over the 422. The caller and the API consult agree the 422 wins: it is a validation of the request itself, and the existing test shows the documented workaround.
  - Adopted: the grant race fix, in scope, with the lock-order check done above.

## Assumptions
- Disabled entitlements, entitlements of suspended orgs, and expired org assignments all block, because the rows exist and would be orphaned (both consults).
- One generic reason, with no per-blocker detail. The AC says "details reason e.g. `org_mode_in_use`".
- `require_super` for the admin app is unaffected, because the admin app's `org_mode` is always `none`.
- Rows that are already orphaned (a `none` app with org data from before this ticket) are not detected or cleaned up here.

## Needs owner's decision (non-blocking; defaults applied)
1. Pending org invitations do not block. Default: no, and a follow-up is recorded. Accept already refuses on `none` apps, and blocking would need an `org_invitations(app_id)` index (a migration).
2. The 409 does not say which blocker it found. Default: one reason, `org_mode_in_use`.
3. Expired org assignments block. Default: yes.
4. The grant lock fix ships inside ADM-019. Default: yes, because the guard is unsound without it.

## Follow-ups (not in this diff, R7)
- Pending invitations as a blocker, or cancelling them on the flip.
- An admin way to clear an app's org data in bulk. Without it, required→none means revoking each entitlement and org assignment one by one.
- A one-off query or report for `none` apps that already hold org data from before ADM-019.

## Blocking questions
None.

## Split proposal
None.

## Data model / migration
None.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| PATCH | `/api/v1/apps/{key}` | `AppUpdate` (unchanged) | `AppRead` (unchanged) | 200, 401, 403, 404, 422, **409 added: `conflict` with details `[{"field":"org_mode","reason":"org_mode_in_use"}]`** | `admin:apps:write` (unchanged) |
| POST | `/api/v1/organizations/{org_id}/entitlements` | unchanged | unchanged | unchanged (now serialised against app PATCH by the app row lock) | `admin:entitlements:write` (unchanged) |
