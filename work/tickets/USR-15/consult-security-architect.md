# consult-security-architect: USR-15 (join requests hardening)

Everything here comes from reading the code. I ran no tests. Files read: `app/modules/membership/service.py`, `app/modules/join_requests/{service,repository,models}.py`, `app/modules/users/{service,repository}.py`, `app/modules/auth/retention.py`, `app/cli.py` (header and command layout), `app/core/{config,errors}.py`, `app/modules/invitations/errors.py`, and the USR-03g `consult-security-architect.md` and `security-review.md`.

## Findings

**F1. The user lock has to cover the approval path too, not only open join (AC 4 is too narrow).**
- `JoinService.join` reads the user with a plain `self.users.get()` at `membership/service.py:125`, before the open/approval branch.
- The join request INSERT takes only an FK `KEY SHARE` on `users`. That does not conflict with the `FOR NO KEY UPDATE` that `set_status` takes. So the FK does not serialize join against disable.
- Race without the lock:
  1. Join reads the user while it is still active.
  2. Disable commits, including its "cancel pending requests" UPDATE.
  3. Join inserts its pending row and commits.
  4. Result: a disabled user has a pending request that the AC 1 cancel never saw.
- Fix: replace that single call with `get_for_share`. It covers both paths.
- Deadlock check:
  - Join locks app X, then the user (share).
  - Disable locks the `admin` app, then the user (no-key-update), then updates join request rows.
  - Join never holds a join request lock before the user lock, and `admin` is refused before `lock_app`, so there is no cycle.
- `get_for_share` already uses `populate_existing` and returns the latest committed version after it waits, so the status check sees the disable.

**F2. Cancel-on-disable must not lock apps.**
- Approve locks app X (`FOR NO KEY UPDATE`), then the user (`FOR SHARE`).
- If disable took `lock_app(X)` after its user lock, the two would deadlock.
- The cancel therefore has to be a single `UPDATE join_requests … WHERE user_id=:u AND status='pending' RETURNING id, app_id`, with app keys read through an unlocked join to `apps`. No `lock_app`.
- Reject holds the request row lock and never waits on the user, so disable just waits for it. No cycle.
- The `_pending` path in approve and reject re-reads with `FOR UPDATE` and `populate_existing`. After a cancel it sees a non-pending row and returns 409 `not_pending`. That is correct with no change.

**F3. There is no status for "cancelled" today.**
- The `status_valid` CHECK allows only pending, approved and rejected.
- Reusing `rejected` would wrongly start the cooldown, because `rejected_within` filters on `status='rejected'`.
- Deleting the rows loses the history (the audit log would be the only record).
- A new `cancelled` value needs a migration that drops and recreates the CHECK. Its `downgrade()` must deal with `cancelled` rows: delete them, documented as lossy, since converting them to `rejected` would create false cooldowns.

**F4. Pending cap: what an attacker gets out of it.**
- The cap does not remove the flood. It moves the damage from staff to legitimate joiners: an attacker with N verified accounts can fill the cap and lock out real users of that app.
- That cost is acceptable only if:
  - the cap is high,
  - staff learn quickly that it was hit,
  - staff can drain the queue (reject, then the cooldown keeps those accounts out; or disable the accounts, which now frees their slots via AC 1).
- The count is exact under the existing app lock, so the cap cannot be overshot.

**F5. Cap as an oracle.**
- A distinct error tells any verified user of that app only one thing: "this app's queue is at or above the cap". It says nothing about other users.
- That is low sensitivity and acceptable. It matches the precedent of `invitation_limit_reached` (403).
- The response must not include the count or the cap value.

**F6. The retention purge has no race risk.** Decided rows never change again; no code path moves a row out of approved, rejected or cancelled. A purge that only targets `status <> 'pending'` cannot conflict with approve, reject or join. The rows hold ids and timestamps only, so purging does not touch PII. The audit log stays the history (`audit-purge` has a 90-day floor).

## Recommendations

**Rec 1 — AC 4, R11.** Change `membership/service.py:125` to `await self.users.get_for_share(account.user_id)`, which covers open and approval. Update the lock-order docstring to "app, then user (share), then insert". Flag to the planner that this deliberately goes past AC 4's wording; F1 is the reason. Tests:
- A disabled user calling join gets `session_invalid`.
- Optionally, a two-session concurrency test: disable holds the user lock and join blocks behind it. Follow the existing lock-test pattern if there is one.

**Rec 2 — AC 1, R4, R2.** In `UserService.set_status`:
- Place it after all guards pass, only on a real transition to `DISABLED`, next to `revoke_all_accounts_of_user`.
- Call a new `JoinRequestRepository.cancel_pending_for_user(user_id, decided_by=actor_user_id(actor))`.
- That method sets `status='cancelled'` and `decided_at=now()` (DB clock), which keeps `decided_iff_not_pending` valid. It sets `decided_by` to the actor.
- It returns `(id, app_key)` per row.
- Write one audit row per cancelled request:
  - action: `app.join_request_cancelled`
  - actor: the staff principal
  - target: `(JOIN_REQUEST, id)`
  - `app_key`: the row's app key
  - changes: `{"status": change("pending","cancelled")}`
  - optionally a fixed `reason: "user_disabled"`
- The row count is bounded at one pending request per app per user.
- Register `set_status` as a multi-row action in `tests/test_audit_coverage.py`.
- Never put the email in these rows.
- No-op disables, and disables that fail a guard (self-disable, last super-admin), cancel nothing.
- Re-enabling does not restore cancelled requests; the user files a new one. Document this.
- Add `cancelled` to the list `status` filter enum and to `docs/consumers.md`. The response enum gains a value, which is additive; the consumer is staff UI only.
- Confirm `rejected_within` still filters only on `rejected`, and add a test: cancel, then immediate re-join returns 202.

**Rec 3 — AC 2, R5, R6.** The pending cap:
- Setting: `join_pending_cap_per_app: int = Field(default=1000, ge=1)` in `config.py`, plus the key in `.env.example`.
- Check order inside `_request`, all under the app lock:
  1. member → 200
  2. usable default role
  3. existing pending → 202 with the same id (an existing requester is never refused)
  4. cooldown → `join_rejected`
  5. cap → refuse
- Use a bounded query on the existing `ix_join_requests_app_id_status_created_at_id`, for example `SELECT 1 … WHERE app_id=:a AND status='pending' OFFSET cap-1 LIMIT 1`, rather than an unbounded `count(*)`.
- Error: a new `JoinQueueFullError(PermissionDeniedError)`, 403, code `join_queue_full`, generic message ("Joining this app is not available right now, try again later"), `details` with no count and no cap. Not 429: it is not the caller's rate, and `Retry-After` would be made up.
- Leave `/token join_available` as it is. That avoids a count on every `/token`, and the cap is meant to be transient. Document that join can still answer `join_queue_full` while `join_available=true`.
- Tests:
  - at the cap, a new user gets 403 `join_queue_full`
  - at the cap, an existing pending user still gets 202
  - after one reject (or one disable-cancel), a new user gets 202

**Rec 4 — cap-hit visibility.**
- Write a log line, not an audit row: `logger.warning("join_queue_full app=%s cap=%s user_id=%s", …)`, with ids only (R11).
- No audit row: there is no state change, and refused requests would let an attacker grow the audit table.
- Log volume is bounded by the per-IP join rate limit.
- Real alerting (a log-based alert, or a pending count on `GET /stats/dashboard`) is a follow-up; see the open questions.

**Rec 5 — AC 3, R5.** Retention:
- Setting: `join_request_retention_days` (for example default 180, `ge=30`).
- Add it to the existing `cleanup` command as a third target in the same batch, commit and summary pattern as `EmailRetentionService` (or a sibling service). `--dry-run` counts only.
- One summary audit row per deleting batch: `join_requests.purged`, `SYSTEM_CLI`, counts only.
- Set `lock_timeout` before each batch.
- Purge predicate (DB clock):
  ```
  status <> 'pending'
  AND decided_at < now() - :retention
  AND NOT (status = 'rejected' AND decided_at > now() - :cooldown)
  ```
  The cooldown only needs some rejection inside the window, so this keeps the right rows without a "latest per (app, user)" window function.
- Simplest option: a settings validator that enforces `retention_seconds >= join_reject_cooldown_seconds`. The last clause then becomes a guard that can never trigger; keep it anyway as defense in depth.
- Never purge `pending` rows. Expiring stale pending requests is a separate follow-up.
- Tests:
  - an old approved row is deleted
  - an old cancelled row is deleted
  - a rejection inside the cooldown is kept even when older than the retention
  - pending rows are kept
  - dry-run deletes nothing
  - one summary audit row per batch

**Rec 6 — R8.** Each AC needs a regression test. Audit rows are needed only where state changes: cancel (per row) and purge (summary). The cap refusal has none.

## Risks

- **The cap becomes a DoS lever** (F4). With the default 1000 and sign-up friction (Turnstile, email verification, per-IP limits) the bar is high but not hard. Mitigation depends on staff noticing; without alerting (Rec 4) a full queue can go unseen.
- **No bulk reject.** Draining a flooded queue one request at a time is slow. Disabling the attacker's accounts is the fastest drain after AC 1. Bulk reject would be a follow-up.
- **Raising the cooldown after a purge.** Rejections already purged under the shorter window cannot block. This is acceptable; document it.
- **The `cancelled` migration.** The downgrade has to delete `cancelled` rows before it can restore the old CHECK, so it loses data; document it as lossy. `alembic check` will not catch CHECK drift, so the CHECK text in the model must match the migration exactly, and the existing constraint tests need a `cancelled` case.
- **The rate limiter is in-process and per IP** (ADR-0002). The cap and the cooldown remain the real controls behind several replicas or rotating IPs.

## Open questions (owner)

1. Cap value and response: is 403 `join_queue_full` with a default cap of 1000 acceptable? Or reuse the generic `join_unavailable`? That hides the oracle but confuses consumers with the misconfiguration case.
2. Alerting: is a WARNING log enough for v1, or should the pending count go on `/stats/dashboard` (D26) or a log-based alert as a follow-up?
3. Cancellation: is a new `cancelled` status acceptable? The alternative, deleting pending rows on disable, leaves the audit log as the only trace. And should re-enabling a user restore anything? (Recommended: no.)
4. Retention: default number of days, and should the settings validator enforce `retention >= cooldown`?
5. AC 4 scope: is it OK to extend the user lock to the approval path, as F1 requires?

Follow-ups (outside the ticket, R7): bulk reject; expiry of stale pending requests; the deny/ban list (USR-03b F6); a pending count on the dashboard.
