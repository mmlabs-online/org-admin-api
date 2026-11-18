## Consult: identity-access-engineer, USR-15 (join requests hardening)

### Findings
1. **No disable hook exists.** `UserService.set_status` (`app/modules/users/service.py:41-92`) takes `lock_app("admin")`, then the user row `FOR NO KEY UPDATE` (`get_for_update`), then revokes account sessions and email tokens. It never touches `join_requests`. When a user is disabled, their pending rows stay in the staff queue. Approve then refuses them with 409 `user_disabled`, but the rows stay pending forever.
2. **The status set is closed.** The model CHECK `status_valid` is `status IN ('pending','approved','rejected')` (`join_requests/models.py:28`). The USR-03g plan dropped `cancelled` (plan.md:252), so adding it needs a CHECK migration. `decided_iff_not_pending` already works for any non-pending status that sets `decided_at`.
3. **The cooldown keys on `status='rejected'` only.** It is `rejected_within` (`join_requests/repository.py:24-36`). `decided_by` is not considered. `decided_by` is already NULL legitimately when the deciding staff user is deleted (`ON DELETE SET NULL`).
4. **Join reads the user without a lock** (`membership/service.py:125`, `self.users.get`). This applies to both the `open` and the `approval` (`_request`) paths. Join holds only the target app lock, and `set_status` never takes that lock. So this race is possible today and after USR-15:
   - Join reads the user as active.
   - `set_status` disables the user and cancels pending requests (finds none), then commits.
   - Join inserts a pending row (or, for `open`, grants a role) for a now-disabled user.

   Locking `get_for_share` only on the open path, as the AC says, leaves the approval path open to this race, which defeats AC1.
5. **Lock graph** (ADR-0003 §10: app, then user, then request):
   - approve: app → user `FOR SHARE` → request `FOR UPDATE`.
   - reject: app → request.
   - join (proposed): app → user `FOR SHARE` → insert.
   - set_status (proposed): admin app → user `FOR NO KEY UPDATE` → request rows.

   Join and approve/reject never lock the `admin` app (both refuse that key before locking). Reject holds no user lock. So adding the cancel to set_status creates no cycle. The account-session read in join is unlocked, and set_status updates only `account_sessions`, not device rows, so the `get_for_share` docstring's warning about device rows does not apply.
6. **Precedent for caps:** `invitation_max_pending_per_org` (`config.py:76`, `ge=1`) raises `InvitationLimitReachedError` (403 `invitation_limit_reached`), and `OrgLimitReachedError` is 403 `org_limit_reached`. Every join refusal is a 403 `PermissionDeniedError` subclass.
7. **Staff list filter:** `JoinRequestStatusFilter = Literal["pending","approved","rejected"]` (`join_requests/schemas.py:7`).

### Recommendations
**R1. Add a new `cancelled` status (AC1). Do not reuse `rejected` with `decided_by` NULL.**
- Reusing `rejected` would start a 7-day cooldown, so a user who was re-enabled could not re-request. That violates the brief.
- It would also mix with real rejections in the staff `?status=rejected` list and in the audit trail.
- `decided_by IS NULL` is already ambiguous (a deleted decider), so it cannot mark a cancel.
- Changes:
  - Add `JoinRequestStatus.CANCELLED`.
  - CHECK becomes `status IN ('pending', 'approved', 'rejected', 'cancelled')`. Keep the model and migration text byte-identical, because Alembic does not compare CHECKs. Update the constraint test.
  - Add `cancelled` to `JoinRequestStatusFilter`. This is additive, so it is OK under R9.
  - `rejected_within` stays `status='rejected'`, so a cancel never starts a cooldown. Add a test: disable → enable → join returns 202 with a new request.
- Migration (R2):
  - `upgrade`: drop and recreate `ck_join_requests_status_valid`. Add a data step: `UPDATE join_requests SET status='cancelled', decided_at=now() WHERE status='pending' AND user_id IN (SELECT id FROM users WHERE status='disabled')`. This clears rows left over from USR-03g and from the race in Finding 4.
  - `downgrade`: delete `cancelled` rows before restoring the 3-value CHECK. Do not map them to `rejected`, which would create fake cooldowns. Document the downgrade as lossy.

**R2. Cancel inside `set_status(DISABLED)` (AC1, R4).**
- Add the cancel after `user.status` is flushed, next to the session and email-token revokes, in the same transaction.
- Use the lock order already held: admin app → user → requests.
- Repository: `cancel_pending_for_user(user_id) -> list[tuple[request_id, app_id]]` via `UPDATE … SET status='cancelled', decided_at=now(), decided_by=NULL WHERE user_id=:u AND status='pending' RETURNING id, app_id`.
  - Use `synchronize_session=False`, as `revoke_all_accounts_of_user` does.
  - Leave `decided_by` NULL; the audit row names the actor.
- Audit: write one `app.join_request_cancelled` row per cancelled request.
  - Target `join_request`, actor = the staff principal, `app_key`, `changes={"status": change("pending","cancelled")}`.
  - Add the action to `AuditAction` and to any catalogue or docs test that lists the actions.
  - The number of rows is bounded by the number of apps.
  - Optionally add `cancelled_join_requests: <n>` to the `user.disabled` row.
- Do not cancel on the early return when the status is unchanged, or on the denied path. Re-enable restores nothing.
- `app_key` needs a lookup by id; `RETURNING` plus one `SELECT key FROM apps WHERE id IN …` is enough. Do not call `lock_app` per app: that would invert the order (user before app) and could deadlock with approve.

**R3. Lock the user `FOR SHARE` in join for both policies (AC4).**
- In `JoinService.join`, replace `self.users.get(account.user_id)` with `self.users.get_for_share(...)`. It runs after `lock_joinable_app`, before the active/verified checks, and before the policy branch, so the `open` grant and the `approval` insert are both covered.
- This is the lock that makes AC1 airtight. Either disable waits for join to commit and then cancels the new row, or join waits and then sees `disabled` (401 `session_invalid`).
- Also check the CLI `create-account --join` path, which shares `lock_joinable_app`. Lock it the same way if it re-reads a user that already exists.
- Test: interleave with two sessions, or at minimum assert that the lock is taken (as USR-03g tested concurrent approves).

**R4. Pending cap per app (AC2).**
- Env setting `join_max_pending_per_app: int = Field(default=…, ge=1)`, plus a key in `.env.example` (R5). Mirror the invitation cap and do not allow 0 as "unlimited". Suggested default: 500. The owner picks the value.
- Error: a new `JoinRequestLimitReachedError(PermissionDeniedError)` with code `join_request_limit_reached` (403). It matches the `invitation_limit_reached` and `org_limit_reached` family and R6, and reveals no queue size or other users.
- Order in `_request`:
  1. usable default role
  2. existing pending → 202 with the same id, so the idempotent replay is never blocked by the cap
  3. cooldown → `join_rejected`
  4. cap check `count(*) WHERE app_id=? AND status='pending'`, which the existing `(app_id, status, created_at, id)` index serves
  5. insert

  The app lock serialises the count and the insert, so the cap is exact.
- Decide whether `join_available` (`/token` `not_member.details`) should return false when the cap is full. Recommendation: leave it unchanged and document that join may answer `join_request_limit_reached`. This avoids an extra count query on every non-member `/token`.
- Document the new 403 code in `docs/consumers.md` and in the join route's OpenAPI responses.
- Tests:
  - cap reached → 403;
  - replay while at the cap → 202 with the same id;
  - approved, rejected or cancelled rows do not count;
  - after a reject frees a slot, another user can join.

**R5. Retention (AC3), with these points for whoever owns it.**
- Purge `approved`, `rejected` and `cancelled` rows older than `JOIN_REQUEST_RETENTION_DAYS` (env).
- Never purge the newest `rejected` row per (app, user) while `decided_at > now() - join_reject_cooldown_seconds`. Simpler, and sufficient: enforce retention ≥ cooldown in settings validation (a `model_validator`), then exempt only that row.
- Follow `auth/retention.py`: batched deletes, one commit per batch, one summary audit row per deleting batch, counts only.
- Never purge `pending` rows.

### Risks
- If the AC4 lock is applied only to `open`, the approval path still lets a pending request appear for a disabled user. The disable hook then looks done but is not.
- Taking `lock_app` per target app inside set_status inverts ADR-0003 §10 (user before app) and can deadlock with approve, which locks app then user. Do not do it. The user-row lock is enough.
- The migration's data step and the downgrade's delete are both lossy and irreversible. State this in the docstring.
- Reusing `rejected` instead of adding `cancelled` would make re-enabled users silently hit `join_rejected` for 7 days. That is hard to diagnose from the client, because the error reveals nothing.
- Under a cap, one abusive app origin or a sign-up farm can fill the queue and block real users (a DoS on onboarding). The cap bounds storage, not abuse. The per-account join rate limit and the deny-list remain follow-ups.

### Open questions (owner)
1. The cap value per app (I propose 500), and whether `join_available` should reflect a full queue (I propose no).
2. One summary audit row on `user.disabled` with a count, or one `app.join_request_cancelled` row per request (I propose per-request rows plus the count)?
3. Should pending requests also be cancelled when an app is disabled or leaves `approval`? Not in the AC. I suggest keeping it a follow-up, since approve already returns 409 for those.
4. Retention age for decided rows (the USR-03g database consult suggested 1 year), and should it be at least the cooldown, enforced by settings validation?
5. Should the scope widen so the AC4 lock covers the approval path as well (recommended), not just `open`? This is a wording fix to the ticket AC.

Relevant files:
- /home/user/org-admin-api/app/modules/users/service.py
- /home/user/org-admin-api/app/modules/membership/service.py
- /home/user/org-admin-api/app/modules/membership/errors.py
- /home/user/org-admin-api/app/modules/join_requests/{models,repository,service,schemas,router}.py
- /home/user/org-admin-api/app/modules/users/repository.py
- /home/user/org-admin-api/app/modules/auth/retention.py
- /home/user/org-admin-api/app/core/config.py
- /home/user/org-admin-api/work/tickets/USR-03g/{plan,security-review,consult-identity-access-engineer}.md
