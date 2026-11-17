## USR-03g consult: identity-access-engineer (read-only)

### Findings (current code, 2026-11-17)
1. **The join path is ready to extend.** `app/modules/membership/service.py`:
   - `lock_joinable_app` refuses `admin` with 403 `join_closed` and a missing or disabled app with 403 `origin_not_allowed`.
   - After that it checks the account session (401), an active user (401 `session_invalid`) and a verified email (403 `email_unverified`), then whether the user is already a member (`effective_roles`, so 200 with no write). Only then does it call `join_default_role`.
   - `join_default_role` raises `JoinApprovalUnavailableError` for `approval`, and refuses a NULL, missing or `is_admin` default role with 403 `join_unavailable`.
   - The grant goes through `AssignmentService._grant_locked(..., actor=SYSTEM_JOIN)` plus `AuditAction.APP_JOINED`.
2. **`join_available` is computed in two places with the same `open`-only expression:**
   - `app/modules/tokens/service.py:77-79` (the comment there says "USR-03g makes `approval` true again")
   - `app/modules/organizations/auth_service.py:89-91`

   Both must change together.
3. **Invitations are switched off for `approval` apps.** `InvitationService._unavailable_reason` (`app/modules/invitations/service.py:136`) returns `"join_policy"` unless the app is `open`. So an `org_mode=required` app with `approval` has no invitations at all, and accepting an invitation cannot get around approval today.
4. **The owner role cannot be granted by an approve.** The DB CHECK `ck_apps_default_role_not_org_owner` (plus ADR-0015 §4) means the default role can never be the org owner role, so an approve that grants the default role can never grant owner. `member_is_org_owner` is a manifest/build-time 409 and does not apply to join. On org apps the default role holds only `scope='app'` permissions (ADR-0015 §3). The `open` join path does not re-check this; only the invitation guard does (`role_has_org_permission`).
5. **The ticket and the code no longer agree in three places:**
   - "Behind the admin RBAC flag": `ADMIN_RBAC_API_ENABLED` no longer exists. ADM-002 is live, so these are plain R3 class-1 Bearer routes with `require_permission`.
   - "USR-03b `AuditRecorder`": it is now `AuditLogger` (ADM-004), with `record` and `record_denied`.
   - Name clash: `membership/schemas.py` already defines `JoinRequest`, which is the request body.
6. **Disabling a user does not touch other tables.** `UserService.set_status` locks the `admin` app, then the user row with `FOR UPDATE`, then revokes account sessions and email tokens. Nothing touches join requests.
7. **Disabled app, existing pieces.**
   - `lock_app` + status check gives 403 at join time.
   - App PATCH can switch `join_policy` at any time; the admin app is pinned to `closed`.
8. **There is no admin permission for join requests.** The catalogue in use is `admin:apps:read|write`, `admin:roles:manage`, `admin:roles:assign`, `admin:users:read|write`, `admin:organizations:*` and `admin:audit:read`. Migration head is `f3a8d51c9e27`.

### Recommendations
**R-a. Migration (AC1)**
- `join_requests` columns: `id uuid`, `app_id → apps ON DELETE CASCADE`, `user_id → users ON DELETE CASCADE`, `status`, `created_at`, `decided_at NULL`, `decided_by → users ON DELETE SET NULL`.
- Add the ticket's CHECK `(status='pending') = (decided_at IS NULL)`.
- Allow `status IN ('pending','approved','rejected','cancelled')`. Adding `cancelled` now avoids a CHECK migration later (see R-f).
- Partial unique index `(app_id, user_id) WHERE status='pending'`.
- Index `(app_id, status, created_at, id)` for the staff list, and `(user_id)`.
- Name the ORM class `AppJoinRequest` (or rename the body schema) to avoid the clash in Finding 5. Import it in `app/db/models.py` (R2).

**R-b. Join on an `approval` app (AC2)**
- Keep the current order unchanged: app lock, then `admin` → `join_closed`, then disabled/unknown → `origin_not_allowed`, then the session/active check (401), then `email_unverified`, then the existing member → 200 `member`.
- Then validate the default role *at request time* with the same checks as `open`: refuse NULL, missing or `is_admin` with 403 `join_unavailable`, and on `org_mode=required` apps also refuse a role holding `scope='org'` permissions. Don't queue requests that can never be approved.
- Refactor `join_default_role` into `usable_default_role(app)`, without the policy branch, so both policies share it.
- Then:
  - if the user rejected inside the cooldown → 403 `join_rejected` (R-e);
  - if a pending request exists → 202 with the same `request_id`;
  - otherwise insert → 202 `{"status":"pending","request_id"}`.
- Extend `JoinResponse.status` to `Literal["member","pending"]` with an optional `request_id`. Document both 200 and 202 in OpenAPI and update the 403 table in `docs/consumers.md:351`.
- `org_mode=required` gets no special handling: approval grants app scope only (`org_id NULL`), as `open` does. The user then creates an org (only if the default role holds `<app>:org:create`) or, later, is invited (see Q3).

**R-c. `/token` and `/orgs` `join_available` (AC2)**
- Put this in one helper, `join_available(app, user)`, used by both call sites:
  - `open` → usable default role;
  - `approval` → usable default role and no rejection inside the cooldown (pending → true, because join returns 202 idempotently);
  - `closed` → false.
- Optionally add `details.join_status: "pending"` so apps can show "waiting for approval" without calling join. It is additive, so no break in v1 (R9). The extra query runs only on the non-member path.

**R-d. Approve and reject (AC3)**
Approve takes locks in this order: `lock_app(key)` → user row `FOR SHARE` → request row `FOR UPDATE`. This matches `set_status` (user before request) and avoids a deadlock with R-f. Then:
1. Return 404 if the request is unknown or `request.app_id != app.id`. Never resolve an id across apps.
2. Return 409 if the request is not pending.
3. Return 409 `conflict` with `details.reason` when:
   - the app is not active (`app_disabled`);
   - `join_policy != 'approval'` (`join_policy_changed`; staff then reject or the user re-joins under the new policy);
   - the user is not active (`user_disabled`);
   - the email is unverified (`email_unverified`);
   - the default role is NULL or unusable (`default_role_unset` / `default_role_unusable`).

   Each refusal leaves the request pending.
4. **NULL default role:** refuse (409); don't let the approve body pick a role. Staff who want a different role use `POST /users/{id}/role-assignments`.
5. **Already a member** (staff granted a role directly): mark the request `approved` and grant nothing (`effective_roles` non-empty). Don't surface `_grant_locked`'s "already assigned" 409.
6. Otherwise call `_grant_locked(app, role, user_id, None, None, actor=<staff principal>)`, so `granted_by` = the approver and `_guard_privileged` still applies. Set `decided_by` and `decided_at = now()`.
7. Audit in the same transaction (R4):
   - `app.join_request_approved` (target `join_request`, actor staff), plus `app.joined` (target `role_assignment`, `changes.role_key`);
   - `app.join_requested` (actor `EndUser`) when the request is created;
   - `app.join_request_rejected`.

   Add `AuditTarget.JOIN_REQUEST`.

Reject also takes `lock_app` → request `FOR UPDATE`, sets `decided_by`/`decided_at`, and returns 409 for a non-pending request and 404 for an unknown one. Return the updated request as 200, not 204: it is a state transition, not a delete.

**R-e. Re-request after reject**
- Use a cooldown from env, `JOIN_REJECT_COOLDOWN_SECONDS` (R5; default 7 days, matching the USR-03b security-architect consult). Inside it, join → 403 `join_rejected`. You may add `details.retry_after`; it is the user's own decision, so this leaks nothing.
- After the cooldown a new pending row is allowed (the partial unique index permits it).
- No permanent deny-list in this ticket (USR-03b F6); it stays a follow-up unless the owner asks for it.
- Retention: when the cleanup CLI later purges decided rows, keep them at least as long as the cooldown.

**R-f. User disabled**
- In `set_status(DISABLED)`, after the user `FOR UPDATE`, run `UPDATE join_requests SET status='cancelled', decided_at=now(), decided_by=NULL WHERE user_id=? AND status='pending'`. Include the count in the `user.disabled` audit facts.
- Also cancel pending requests on re-enable? Not needed. Re-enable does not revive them; the user re-requests.
- The approve-time active-user re-check with the user `FOR SHARE` stays as the guarantee against the race.
- Do the same for pending requests when an app is disabled or leaves `approval`? Optional. The approve-time 409 is enough for v1.

**R-g. Admin permissions (R3)**
- `GET /api/v1/apps/{key}/join-requests` (`Page[T]`, filter `status`, default `pending`) → `admin:users:read`, because it exposes user ids and emails. The response carries `id, user_id, email, status, created_at, decided_at, decided_by`.
- `POST …/{id}/approve|reject` → `admin:roles:assign`. Approve is a role grant, the same permission as `POST /users/{id}/role-assignments`.
- No new permission keys, so no seed migration. `admin:*` is still needed through `_guard_privileged` if a default role ever holds `is_admin`, which is already refused earlier.
- Add the routes to `tests/test_route_auth_classes.py`.

**R-h. Tests (R8)**
- Each 409 reason in R-d.
- Approve after the user is disabled, after the email lost verification, after the default role was set to NULL, and after the policy flipped.
- Cross-app request id → 404.
- Join repeated while pending → same `request_id`.
- Join inside and after the cooldown.
- `join_available` for pending, rejected-in-cooldown and NULL default.
- Disable cancels pending requests.
- Approve when already a member.
- An `org_mode=required` app: approve grants app scope only, no `org` claim, and `/orgs` shows no org.
- 401/403 for every staff route.

### Risks
- **Deadlock** if approve locks the request before the user while disable locks the user and then updates requests. Fix with the lock order in R-d.
- **Silent widening on org apps** if staff attach `scope='org'` permissions to the default role through a path ADR-0015 doesn't cover. The request-time and approve-time `role_has_org_permission` check closes this.
- **Policy drift:** pending requests stay after the app moves to `open` or `closed`. Approving them would bypass the new policy, hence the `join_policy_changed` 409.
- **No user notification:** apps learn about an approval only when they retry `/token`. Without guidance in `docs/consumers.md` they may poll join in a loop, so tell them to poll `/token` and rely on the `rate_limit("join")`.
- **Size:** migration, model, repository, service, staff router, the `/token` + `/orgs` helper, `set_status`, docs and tests probably come to about 500 lines. Consider deferring the R-f disable hook and the `join_status` detail if it grows.

### Open questions (owner)
1. Cooldown length after a reject (proposed: 7 days, from env). Should staff also be able to ban permanently (`reject {block:true}`, the F6 deny-list), or is that a follow-up?
2. What approve grants: confirm "the app's default role at approval time, app scope, refuse if NULL" (proposed). The alternative is an optional `role_key` in the approve body.
3. Org apps with `approval`: should invitations keep being refused (current guard), or should accepting an invitation count as approval? Today an `approval` org app can only get members through staff.
4. Four-eyes: may staff approve their own join request? Currently allowed, consistent with `_guard_privileged`, which blocks self-assignment only on the `admin` app.
5. Should pending requests expire after N days, purged by the cleanup CLI, or stay pending forever?
6. Should the stale ticket wording be corrected: "admin RBAC flag" and "AuditRecorder" (spec/ticket conflict, flagged per CLAUDE.md)?

Files referenced:
- /home/user/org-admin-api/app/modules/membership/service.py
- /home/user/org-admin-api/app/modules/membership/schemas.py
- /home/user/org-admin-api/app/modules/membership/errors.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/organizations/auth_service.py
- /home/user/org-admin-api/app/modules/invitations/service.py
- /home/user/org-admin-api/app/modules/users/service.py
- /home/user/org-admin-api/app/modules/rbac/service.py (`_grant_locked` line 1380, `_guard_privileged` line 1437)
- /home/user/org-admin-api/app/modules/rbac/models.py
- /home/user/org-admin-api/app/modules/rbac/assignments_router.py
- /home/user/org-admin-api/app/modules/audit/actions.py
- /home/user/org-admin-api/docs/identity-architecture.md (§2 Join, §3 `join_requests`)
- /home/user/org-admin-api/docs/adr/0015-org-roles-claim-and-permission-scope.md
- /home/user/org-admin-api/docs/consumers.md (line 351)
