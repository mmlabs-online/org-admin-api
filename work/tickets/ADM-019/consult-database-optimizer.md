Findings (ADM-019 DB consult, read-only)

Existing code
- PATCH path: `RbacService._update` (app/modules/rbac/service.py ~404) starts with `_locked_app` (`lock_app`, row lock on the app). The new guard fits right after the `changes` dict is built, e.g. `if changes.get("org_mode") == "none" and app.org_mode == "required"`. It raises ConflictError and returns before `repo.save()`. Audit is written by the caller only after a successful `_update` (lines ~395-401), so a refusal leaves no audit row, which matches the AC.
- Helpers already exist in app/modules/rbac/repository.py (~109-117): `app_has_entitlements(app_id)` and `app_has_assignments(app_id)`. They are used by `delete`.
  - `app_has_entitlements` is `EXISTS(org_entitlements WHERE app_id=…)` and is served by `ix_org_entitlements_app_id`. It counts all rows, including inactive/expired ones. That is correct, because the rows still point at an org-mode app.
  - `app_has_assignments` counts ALL assignments for the app, including app-scoped ones (org_id NULL). It cannot be reused as-is. Reusing it would wrongly block apps with plain app-level roles.
- `role_assignments` has indexes on `role_id` and on `org_id`, and FK roles.app_id → roles has an index via the role join. `roles.app_id` is FK-backed (check for an index on roles(app_id), or the unique (id, app_id) / (app_id, key) constraint, which would serve the lookup). A new query is therefore cheap: the join goes roles (by app_id) → role_assignments (ix_role_assignments_role_id).
- Invitations (`org_invitations`) have `app_id`, `role_id` and `org_id`. The only app-related index is `ix_org_invitations_role_id_app_id`, and the partial unique `uq_org_invitations_pending` leads with org_id, so neither is a good lookup by app_id alone. Join requests are app-scoped (no org_id), so they are irrelevant.

Recommendations
1. Add `RbacRepository.app_has_org_assignments(app_id)`:
   `SELECT EXISTS (SELECT 1 FROM role_assignments ra JOIN roles r ON r.id = ra.role_id WHERE r.app_id = :app_id AND ra.org_id IS NOT NULL)`.
   Expired assignments should count (no `expires_at` filter). Rows stay in the table, are still listed and reachable, and expiry is lazy, so ignoring them would orphan data and make the guard time-dependent. Same reasoning as `delete`, which counts all.
2. Reuse `app_has_entitlements` for the entitlement blocker. Call the entitlement check first, then the assignment check. Raise one 409 with `details=[{"field":"org_mode","reason":"org_mode_in_use"}]`. If you want to tell the two apart, you could add a second detail entry or an extra detail field, but this is optional (see open question 2).
3. Run the guard only on the required to none transition. It must not trigger on a no-op (`changes` is empty), on none to required, or on a PATCH that does not touch org_mode. This also keeps the existing admin-app check unaffected.
4. Order: put the guard after validation that gives 404/422, but before the `org_owner_role_id` check at 461? When org_mode becomes none and org_owner_role_id is still set, the existing check returns 422 `org_mode_required`. Decide which error wins. Suggestion: in-use 409 first, since it is the more specific business conflict. Either way add a test.
5. Pending org invitations: do not block on them. Accepting an invitation re-checks `org_mode` (invitations/service.py ~134, 161) and returns the silent/refused path, so a pending invite just fails harmlessly after the flip. Accepted invitations already produced role_assignments, which are counted by the assignment query. If the owner wants them blocked, it needs an index on org_invitations(app_id) (a migration), which I do not recommend for a P3.
6. No migration is needed. `alembic check` stays clean. Existing indexes cover the two EXISTS queries.
7. Tests (R8): entitlement-only blocker; org-assignment-only blocker (include an expired org assignment); app-scoped assignment only (allowed); nothing exists (allowed, with an audit row); none to required (unaffected); refused attempt leaves the audit count unchanged and the app unchanged; 404 and 401/403 as already covered.

Risks
- Race (real gap): the entitlement `grant` (app/modules/entitlements/service.py:60-99) reads the app with `get_app_by_key` and NO `lock_app`. A concurrent grant can read `org_mode=required`, then commit an entitlement after the PATCH's EXISTS check, leaving an entitlement on a `none` app. Role assignments are safe: `assign`, invitations, join requests, membership and org auth_service all go through `lock_app`, so they serialize against the PATCH. Fix (small): make `EntitlementService.grant` use `lock_app` (via `_locked_app`/`rbac.lock_app`) when reading the app, so the org_mode check and the insert happen under the same lock. The org-row lock ordering (org lock before app lock, or the reverse) needs a check against other paths. Entitlement update/revoke touch existing rows only and do not need the lock. The fix is in scope as it is needed for the guard to be sound. If the planner prefers to stay strictly in scope, record it as a follow-up and keep the residual race documented.
- READ COMMITTED: with the app row lock held, the EXISTS runs after the lock is acquired, so it sees every commit that happened before the lock was granted. This is correct for all paths that lock.
- Expired assignments counting means an app can stay stuck on `required` until the rows are deleted or revoked. That is the intended trade-off, and there is a revoke path. Mention it in the 409 message.
- `app_has_assignments` is used by `delete`. Do not change its semantics when adding the org variant.

Open questions
1. Do expired org assignments block? (My recommendation: yes. Confirm with the owner/planner.)
2. Should the 409 distinguish the two blockers (e.g. `org_mode_in_use` plus a field naming entitlements vs assignments), or one generic reason? The AC says "e.g. org_mode_in_use".
3. Is the `EntitlementService.grant` lock fix in scope for ADM-019, or a follow-up?
4. Confirm that blocking on pending invitations is out of scope (no index and no AC for it).

Files: /home/user/org-admin-api/app/modules/rbac/service.py, /home/user/org-admin-api/app/modules/rbac/repository.py, /home/user/org-admin-api/app/modules/entitlements/service.py, /home/user/org-admin-api/app/modules/invitations/models.py, /home/user/org-admin-api/app/modules/rbac/models.py
