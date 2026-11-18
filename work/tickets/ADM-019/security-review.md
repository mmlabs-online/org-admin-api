## Attempt 1

## Security review: ADM-019 (diff `origin/main...HEAD`, code and git only; `make check` not run, as instructed)

No blockers and nothing to fix before merge. The guard holds, the new lock adds no deadlock risk, and the 409 leaks nothing.

### Findings

**Can the guard be bypassed?** No.
- `PATCH /apps/{key}` is the only path that can change `org_mode` on an existing app.
  - The manifest PUT refuses any change with 409 `org_mode_change` (`app/modules/rbac/service.py:609-615`).
  - The admin-origins path writes origins only.
- The guard (`app/modules/rbac/service.py:486-498`) runs while `_update` holds `_locked_app` (`:408`). That lock uses `FOR NO KEY UPDATE` with `populate_existing`, so the guard compares against fresh state.
- Every writer of org-scoped data now takes the app lock first:
  - **Org assignments:** all go through `_grant_locked`, which refuses `org_id` on a `none` app (`service.py:1463-1467`). Every caller locks the app before calling it: `assign` (`:1380`), membership (`membership/service.py:52`), invitations (`invitations/service.py:119`), join requests (`join_requests/service.py:73`), org auth (`organizations/auth_service.py:85`) and provisioning (`lock_joinable_app`, org_id=None).
  - **Entitlements:** `grant` now calls `lock_app` (`app/modules/entitlements/service.py:66`) before its `org_mode` check (`:73`), which closes the check-then-commit race.
  - `update` and `revoke` touch existing rows only, so they need no lock.

**Is the admin key refused before the lock?** Yes, at `entitlements/service.py:64-65`, before `lock_app`. `app_key` must match `APP_KEY_RE`, so a case variant cannot slip past this check and lock the admin row. `test_grant_locks_app_row` asserts that no `lock_app("admin")` call happens.

**Lock order and deadlock.** No new edge. Grant now does:
1. an unlocked `orgs.get`;
2. `lock_app` (`FOR NO KEY UPDATE`);
3. the INSERT, whose foreign-key checks take `KEY SHARE` on the org and app rows. `KEY SHARE` is compatible with our own `FOR NO KEY UPDATE`.

That is app before org, the same order used everywhere else (`invitations/service.py:295`). Org delete does not lock app rows, so no cycle can form.

**Does the 409 leak anything?** No. The message is fixed text, with one generic reason, `org_mode_in_use`. It has no counts, org ids or user ids. Only a caller who already holds `admin:apps:write` can see it.

**Audit on refusal.** Writing no audit row is acceptable. `ConflictError` is raised before any write, and the audit call runs only after `_update` returns. This matches R4 and the refuse-not-cascade precedent in ADR-0013 §4. The tests assert 0 `app.updated` rows.

**Validation order.** Correct. The guard runs after every 422 check, so the owner-role 422 `org_mode_required` still takes priority, and before the write, so a refusal leaves no partial state. The only checks after it are the origins checks. The one that returns 422 applies only to the admin app, where this guard never fires, so a 409 cannot hide a 422.

**💭 note 1: rows that existed before ADM-019 are not covered.** A `none` app that already holds org entitlements or org assignments is not detected or cleaned up. This is already listed as a plan follow-up and needs no change in this diff.

**💭 note 2: pending invitations do not block the switch.** This is safe because accepting an invitation goes through `_grant_locked`, which refuses `org_id` on a `none` app under the app lock. It is already a plan follow-up.

### Follow-ups (out of scope)
- A one-off report of `none` apps that already hold org data.
- An admin action to clear an app's org data in bulk before switching `org_mode` to `none`.

**Verdict: PASS (security). No 🔴 and no 🟡 findings.**

Files reviewed:
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/entitlements/service.py
- /home/user/org-admin-api/work/tickets/ADM-019/plan.md
