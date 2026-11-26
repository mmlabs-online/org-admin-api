# Consult — database-optimizer — USR-30 (2026-11-21)

**USR-30 consult (db): atomic org-role removal and the last-owner guard**

**Findings**
- `lock_app` is `FOR NO KEY UPDATE` on the `apps` row (`app/modules/rbac/repository.py:81-93`). That mode conflicts with itself, so it serializes every holder per app. Only FK `KEY SHARE` checks pass through.
- Every writer of `role_assignments` I found takes it first (ADR-0003 §9/§10):
  - staff role grant: `_grant_locked` runs under `_locked_app` (`rbac/service.py:1567`, `1631`);
  - invitation create (`invitations/service.py:119`), `org create` (`organizations/auth_service.py:85`), join requests (`join_requests/service.py:73`), `membership/service.py:52`;
  - manifest sync (`rbac/manifest.py:107`).
- The mutual removal race is therefore closed by `lock_app(app.key)` as the first statement. Both requests queue on the app row. The second one re-reads the owner count after the first commits, sees one owner left, and gets 409.
- There is no existing "last owner" helper for org owners. The only analogue is the last super-admin check: `is_counting_super_admin` and `count_other_active_super_admins` (`rbac/service.py:1638-1644`). I would mirror that shape, not invent a new lock.
- The unique constraint `uq_role_assignments_user_role_org` (`rbac/models.py:200`, nulls not distinct) plus the indexes on `role_id` and `org_id` (`rbac/models.py:207-208`) make both queries cheap: filter on `org_id` and `role_id IN (app's roles)`.
- Expired rows are already handled the same way everywhere: `expires_at IS NULL OR expires_at > db_now()` (`rbac/service.py:1542`, `1589`). Use `repo.db_now()`, not Python time.

**Recommendations**
1. **Lock: `lock_app` only.**
   - Use `_lock_active_app`-style: take `lock_app(calling_app.key)` first, then read the user (the app lock comes before users, per ADR-0003 §10).
   - Add no `FOR UPDATE` on the owner rows. It is redundant while all writers hold the app lock, and it adds a lock-order surface.
   - Add no advisory lock: it would be a second mechanism that other writers do not know about.
   - Pin this invariant in a comment and in the plan: any future `role_assignments` writer must take `lock_app`.
   - One exception to verify: a user hard-delete cascades `role_assignments` (`ondelete=CASCADE`, `rbac/models.py:212`). If a delete path exists that does not take the app lock, it can bypass the guard. I did not find one in `app/modules/users`, but the plan should confirm.
2. **Lock order:** `lock_app` → read caller/target users → delete → audit. This is the same as invitations and `org create`, so there is no new deadlock edge. Do not take `lock_app("admin")` in the same request.
3. **Delete in one statement** (new `rbac/repository` method), roughly:
   ```
   DELETE FROM role_assignments ra USING roles r
   WHERE ra.role_id = r.id AND r.app_id = :app AND ra.user_id = :target
     AND ra.org_id = :org AND (ra.expires_at IS NULL OR ra.expires_at > now())
   RETURNING r.key, ra.role_id
   ```
   - `RETURNING` gives the role keys for the audit row. An empty result means 404, with no existence leak.
   - Because the statement runs under the app lock, there is no read-then-delete race. Run the guard first.
4. **Last-owner guard** (after the lock, before the delete):
   - Only if the target's returned or pre-read role ids include `app.org_owner_role_id`, count the other active holders of that role in the org: `org_id = :org AND role_id = owner AND user_id <> target AND (expires_at IS NULL OR expires_at > now())`. If 0, raise 409 `last_org_owner`.
   - Treat expired owner rows as non-owners. This is consistent with `/auth/token` and with the super-admin counting rule.
5. **Expired assignments:**
   - Delete only non-expired rows, as the ticket says. Expired rows grant nothing, so leaving them is harmless. They are replaced in place on re-grant (`rbac/service.py:1591`), and keeping them avoids touching audit or ETags for dead rows.
   - Corollary: a target with only expired roles gets 404. That is consistent.
   - Optional: delete expired rows of the same app and org as well, as cleanup. It changes the "removed role keys" audit fact, so I would not do it.
6. **Audit:** `record` in the same transaction with `app_key`, `org_id`, target user, and role keys plus a count in `facts`. Denied attempts (self-removal, last owner, missing permission) use `record_denied` as in `revoke` (`rbac/service.py:1645-1656`). Check that `last_org_owner` is a member of `DENIED_ERRORS`, or add it.
7. **No migration** unless a seed is needed. `<app>:org:remove-member` arrives through the manifest as `scope = 'org'`. Confirm `<app>:org:invite` is not seeded by migration. If it is, a seed for `remove-member` would need a migration (High path).
8. **USR-29 `updated_since`:**
   - A hard delete leaves no row, so an incremental sync cannot see the removal. A member whose last org roles vanish disappears from the member list with no `updated_at` bump.
   - Options: (a) document that apps must periodically do a full read; (b) the member read returns `updated_since` over `role_assignments.created_at` only, with an explicit caveat; (c) add a tombstone or `removed_at`, which needs a migration.
   - Default: (a), a documented limitation in `docs/consumers.md`, with no schema change.

**Risks**
- Future writers that skip `lock_app` silently break the guard (mitigate with the comment and a test).
- Owner role holders can include a user who is also the actor in another org. The guard is per `(app, org)`, so this is fine.
- A staff revoke of the last owner via the admin API is not covered by this guard. That is acceptable (staff action) but should be stated in the ADR.
- 12 h token lag is documented, and USR-29 member reads may disagree with live tokens for up to that long.

**Open questions for Ming (defaults)**
1. Lock mechanism: `lock_app` only? Default yes.
2. Expired rows: leave them? Default yes.
3. Removal visibility to `updated_since` (USR-29): document the full-resync limitation, with no tombstone? Default yes.
4. Should staff revoke of the last org owner also be guarded? Default no, out of scope (R7), logged as a follow-up.
5. Success response: 204. Default 204.

Key files:
- app/modules/rbac/repository.py
- app/modules/rbac/service.py
- app/modules/rbac/models.py
- app/modules/invitations/service.py
- app/modules/organizations/auth_service.py
