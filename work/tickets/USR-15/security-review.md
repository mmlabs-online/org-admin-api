## Attempt 1

## Security review: USR-15 (join requests hardening)

I read the diff `origin/main...HEAD` (commit 53cec16) together with the plan and the consults. I traced every path that takes the user, app or join-request locks. As instructed, I did not run `make check` or pytest.

### Findings

No 🔴 blockers.

**🟡 should-fix**

None in the code itself. There is one operational item that needs a check in the deploy repo before merge:

- **The cleanup run must use the same cooldown setting as the API.** Files: `docs/deploy.md:885-930`, `app/cli.py:691-699`.
  - **Risk:** retention is only safe if the `cleanup` container's `JOIN_REJECT_COOLDOWN_SECONDS` matches the API's. Example: the API runs with a 365-day cooldown and 365-day retention, but `cleanup` runs without those vars. It would then use the defaults (180 d retention, 7 d cooldown), pass validation, and delete rejections the API still enforces. That lets a rejected user ask again early.
  - The documented command uses the `migrate` service with `--env-file release.env`. That is probably the same env as the API, but nothing in this repo checks it.
  - **Fix:** confirm in `mmlabs-online/deployment` that the `migrate`/ops service gets `JOIN_REJECT_COOLDOWN_SECONDS` and `JOIN_REQUEST_RETENTION_DAYS` from the same source as `api`. Add one sentence to `docs/deploy.md`: "cleanup must run with the API's `JOIN_*` env; a different cooldown can purge live rejections."

**💭 notes**

1. **Lock order: no deadlock cycle found.**
   - `set_status` (`app/modules/users/service.py:49-50, 86-97`) takes the admin app (NO KEY UPDATE), then the user (NO KEY UPDATE), then the user's pending request rows (the UPDATE).
   - approve (`join_requests/service.py:73,84-85`) takes app X, then the user (SHARE), then the request (FOR UPDATE). reject takes app X, then the request, and never waits on the user. join (`membership/service.py:123,131`) takes app X, then the user (SHARE), then the INSERT.
   - **Disable vs approve:** both lock the user before the request, so no cycle. Approve that gets in after the disable re-reads the row with `populate_existing` and answers 409 `not_pending`.
   - **Disable vs reject:** reject holds only the request row. Its `decided_by` FK takes KEY SHARE on the staff row, which does not conflict with NO KEY UPDATE. So reject finishes and the disable UPDATE then continues.
   - **join and admin lock:** join refuses `admin` before `lock_app`, so it never holds the admin lock.
   - **Other user-lock holders:** I checked verify, password reset and staff sessions (`email_verification.py:121`, `password_reset.py:104`, `staff_service.py:74`). None of them takes an app lock after the user lock, so the new user SHARE in join adds no cycle.
   - **Cleanup:** it uses SKIP LOCKED plus `lock_timeout`, and deleting referencing rows takes no lock on `users`.

2. **The join vs disable race is closed, but only under READ COMMITTED.** `membership/service.py:131` covers both policies.
   - If join commits first, `set_status` waits on the user row. Its UPDATE then starts a new statement snapshot, sees the new pending row and cancels it.
   - If the disable commits first, join's `FOR SHARE` returns the disabled row (fresh via `populate_existing`) and answers 401.
   - This depends on the per-statement snapshot of READ COMMITTED, which is the default; I found no isolation override in `app/`. Under REPEATABLE READ the UPDATE would miss the row.
   - **Fix (optional):** add one line to the `set_status` docstring: "relies on READ COMMITTED: the cancel UPDATE must see a pending row committed while we waited on the user lock."
   - The two-connection concurrency test was skipped (impl-log:41). The statement-capture tests prove the order, not the wait. Worth a follow-up.

3. **The cap is exact and does not leak.** `membership/service.py:170-177`, `join_requests/repository.py:40-52`.
   - Pending rows are inserted only in `_request`, under the app `FOR NO KEY UPDATE`. Cancel and decide only lower the count and need no app lock. So there are never more than N pending rows.
   - Check order: existing pending (replay) → cooldown → cap. A caller only learns about their own state, plus a boolean "queue full" for an app they are already signed into with a verified email.
   - The error has the same message as `join_rejected`, no `details` and no count or cap. The warning log carries the app key, user id and cap only.
   - Residual risk: anyone with about N verified accounts can fill the queue and block real users. Staff can recover by disabling those accounts, which now cancels their requests. A per-account join rate limit and bulk reject are already listed as follow-ups.

4. **Cooldown cannot be bypassed by cancel or purge.** `join_requests/repository.py:149-161`.
   - Cancel only touches `status='pending'` rows (`:54-79`), so it never overwrites a rejection. `rejected_within` still reads only `rejected`, so a cancel never starts a cooldown.
   - The purge predicate keeps every `rejected` row with `decided_at > now() - cooldown`. That uses the same strict `>` as `rejected_within`, with no boundary gap, and time only moves forward, so a row purged now can never fall back inside the cooldown.
   - The config validator (`config.py:202-208`) means this clause never triggers in a valid config, and the tests cover it directly.

5. **Pending rows are never purged, by two separate guards.** The predicate has `status <> 'pending'`, and `decided_at < …` is NULL for pending rows because of `ck_join_requests_decided_iff_not_pending`. Under READ COMMITTED, `FOR UPDATE SKIP LOCKED` re-checks the predicate on locked rows, and a decided row cannot become pending again.

6. **Audit content holds ids only.**
   - Cancel rows: target `join_request` id, `app_key`, `{"status": pending→cancelled}`, the staff actor, and `decided_by = actor_user_id(actor)` (None for system actors). They are written in the disable transaction, before `user.disabled`.
   - Purge rows: `{"join_requests": count}`, actor `system:cli`, one per deleting batch. A dry run writes nothing.
   - No email anywhere in either.

7. **Migration** (`migrations/versions/20261118_c7a3e91f5d28_usr15_join_requests_cancelled.py:27-47`).
   - The CHECK is widened before the data step. The data step sets `decided_at = now()` and leaves `decided_by` NULL, which satisfies `decided_iff_not_pending`. No audit rows, as documented.
   - The downgrade deletes `cancelled` rows before narrowing the CHECK. It is lossy, but the right call: mapping them to `rejected` would create false cooldowns. Existing `app.join_request_cancelled` audit rows will point at deleted ids, which is acceptable.
   - The CHECK rebuild takes ACCESS EXCLUSIVE plus a full scan. That is fine on this small table.

8. **Settings have no upper bound.** `app/core/config.py:69-71`.
   - Neither `join_request_retention_days` nor `join_reject_cooldown_seconds` has an upper bound. A huge value overflows `timedelta` or the Postgres timestamp range.
   - The CLI then exits with the fixed `MSG_CLEANUP_FAILED` line (no leak), but cleanup stops working.
   - **Fix (optional):** `join_request_retention_days: int = Field(default=180, ge=1, le=3650)`, matching the `audit-purge` range.
   - The validator message contains no values. A bad config fails both API startup and the CLI. That is intended, but it is a deploy-time behaviour change if production already sets a cooldown over 180 days; check this together with the 🟡 item.

9. **Log hygiene is clean.** The only new log line is `join_queue_full app=%s user_id=%s cap=%s`. The CLI never prints `str(exc)`.

### Follow-ups (out of scope)
- A two-connection concurrency test: a blocker holds the user `FOR NO KEY UPDATE`, and join with `lock_timeout` must time out.
- A deploy-repo check that the ops/`migrate` service and `api` share the `JOIN_*` env.
- `le` bounds on the retention and cooldown settings.
- Per-account join rate limit and bulk reject, to limit queue-filling (already listed in the plan).

**Verdict: PASS (security).** No blockers. One 🟡 deploy-env check to confirm before the first production `cleanup` run.
