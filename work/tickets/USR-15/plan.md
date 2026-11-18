Status: READY

# Plan — USR-15: Join requests hardening

## Spec summary
Ticket scope comes from the USR-03g follow-ups (spec §5.3, `work/tickets/USR-03g/plan.md` "Follow-ups", lines 322-330):

> - Cancel pending requests when a user is disabled, adding a `cancelled` status (identity R-f).
> - Retention purge of decided `join_requests` in the `cleanup` CLI, keeping the newest rejection per (app, user) for at least the cooldown.
> - Pending cap per app (security R4 backstop).
> - The open join reads the user without a lock (`membership/service.py:94`); use `get_for_share` (security F5).

Spec §5.3 (Membership): "each app has a `join_policy` (`open` | `approval` | `closed`; …) … Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data."
Spec §5.8: "Users: … status (`active` / `disabled`) …"; joining follows the app's `join_policy` (D8–D9).

Ticket acceptance criteria (verbatim):
1. Disabling a user cancels their pending join requests (in the same transaction as the disable, lock order as `set_status`)
2. A per-app cap on pending requests (from env) → join answers a defined error when the cap is reached
3. Retention: cleanup CLI purges decided requests older than a configurable age, keeping the latest rejection per (app, user) while the cooldown applies
4. Open join (`join_policy=open`) reads the user with `get_for_share` (lock) like approve does
5. Tests for each, plus audit rows where state changes

**Scope clarification (AC 4, required for AC 1 correctness).** AC 4 names only the `open` path. All four consults (identity F4, security F1, database §2, api 6) show that the `approval` path has the same unlocked read (`membership/service.py:125`, before the policy branch). Without the lock this race is possible: join reads the user as active → disable commits and cancels nothing → join inserts a pending row for a now-disabled user. AC 1 would then look done but not be. This plan therefore takes `get_for_share` at the single shared read, which covers **both** `open` and `approval`. This goes past AC 4's wording; it does not add a feature. The reviewer should accept it as the condition that makes AC 1 true.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | Disable cancels every pending request of the user, across apps; nothing else changes | `tests/test_users_admin.py::test_disable_cancels_pending_join_requests`: 2 pending (2 apps) + 1 approved + 1 rejected + another user's pending → after PATCH `disabled`, the 2 rows are `cancelled`, `decided_at` set, `decided_by` = staff id; the other 3 rows are unchanged |
| 1b | Same transaction as the disable | `test_disable_cancel_rolls_back_with_disable`: last-super-admin refusal (409) or self-disable (403) leaves the pending rows `pending` and writes no `app.join_request_cancelled` row |
| 1c | Lock order as `set_status` (admin app → user → requests), no per-app lock | `test_disable_cancel_lock_order` (statement capture, as `test_approve_lock_order`): index of `FROM apps … FOR NO KEY UPDATE` < `FROM users … FOR NO KEY UPDATE` < `UPDATE join_requests`; exactly one `FROM apps … FOR NO KEY UPDATE` statement |
| 1d | No-op and re-enable do not touch requests; cancel never starts a cooldown | `test_disable_noop_cancels_nothing`; `tests/test_join_requests_join.py::test_join_after_cancel_202_new_request`: disable → enable → join answers 202 with a **new** `request_id` |
| 1e | Approve/reject of a cancelled request answers 409 `not_pending` | `tests/test_join_requests_api.py::test_approve_cancelled_409_not_pending` |
| 2a | Cap from env; join answers 403 `join_queue_full` at the cap | `tests/test_join_requests_join.py::test_join_cap_reached_403` with `make_client(join_max_pending_per_app=2)`: 2 pending of other users → 403, `error.code == "join_queue_full"`, `details` has no count or cap, no row and no audit row written |
| 2b | Idempotent replay and order | `test_join_cap_replay_same_request_202` (at the cap, an existing requester gets 202 with the same id); `test_join_cap_after_cooldown_check` (inside the cooldown and at the cap → `join_rejected`, not `join_queue_full`) |
| 2c | Only `pending` counts; cap is per app | `test_join_cap_counts_pending_only` (approved/rejected/cancelled rows of the same app and pending rows of another app do not count → 202); `test_join_cap_frees_slot_after_reject` |
| 2d | Cap hit is logged with ids only | `test_join_cap_logs_warning` (caplog: `join_queue_full app=<key> user_id=<uuid> cap=2`, no `@`) |
| 2e | Setting validation | `tests/test_config.py::test_join_max_pending_per_app_floor` (`0` → ValidationError) |
| 3a | Cleanup purges decided rows older than `JOIN_REQUEST_RETENTION_DAYS` | `tests/test_join_requests_retention.py::test_purges_old_decided_rows` (old approved, old cancelled, old rejected outside cooldown → deleted) |
| 3b | Keeps rejections while the cooldown applies; never purges pending | `test_keeps_rejection_inside_cooldown` (service called directly with retention 1 d < cooldown 7 d, a rejection decided 3 d ago → kept; an approval decided 3 d ago → deleted); `test_never_purges_pending` (pending created 2 years ago → kept); `test_cooldown_zero_keeps_nothing_extra` |
| 3c | Retention ≥ cooldown enforced in settings | `tests/test_config.py::test_join_request_retention_must_cover_cooldown` (`JOIN_REQUEST_RETENTION_DAYS=1`, `JOIN_REJECT_COOLDOWN_SECONDS=604800` → ValidationError; 7 d and 604800 → OK) |
| 3d | Batches, one summary audit row per deleting batch, dry run writes nothing | `test_batches_write_one_summary_each` (batch_size 2, 5 rows → 3 rows of `join_request_retention.purged`, `changes == {"join_requests": {"from": None, "to": n}}`, actor `system:cli`); `test_dry_run_counts_only` (count returned, rows and audit count unchanged) |
| 3e | CLI output | `tests/test_cli_cleanup.py`: updated `DELETED` / `WOULD_DELETE` lines include `join_requests: deleted <n> (decided, older than 180 d)` |
| 4 | Join reads the user with `get_for_share` before any write, for both policies | `tests/test_auth_join.py::test_join_locks_user_for_share` (open: app lock < `FROM users … FOR SHARE` < `INSERT INTO role_assignments`); `tests/test_join_requests_join.py::test_join_approval_lock_order` (approval: app lock < user `FOR SHARE` < `INSERT INTO join_requests`) |
| 5 | Audit rows where state changes | `test_disable_cancels_pending_join_requests` also asserts 1 `user.disabled` + 2 `app.join_request_cancelled` rows (target `join_request`, `app_key`, `changes == {"status": {"from": "pending", "to": "cancelled"}}`, actor = staff, no email); `tests/test_audit_coverage.py::test_user_disable_with_pending_requests_writes_multiple_rows`; retention summary rows (3d); cap refusal writes none (2a) |
| — | Migration | `tests/test_join_requests_cancelled_migration.py` (see Tests); `alembic check` clean in `make check` |
| — | All | `make check` green |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/join_requests/models.py` | `JoinRequestStatus.CANCELLED = "cancelled"`; CHECK text `status IN ('pending', 'approved', 'rejected', 'cancelled')` (byte-identical to the migration) | AC 1 |
| `migrations/versions/20261118_<rev>_usr15_join_requests_cancelled.py` | New revision, `down_revision = "e2b7a94d1c58"` (current head); widen CHECK + data step; lossy downgrade | AC 1, R2 |
| `app/modules/join_requests/repository.py` | `cancel_pending_for_user`, `pending_at_least`; new `JoinRequestRetentionRepository` (count/purge/lock timeout/commit) | AC 1, 2, 3 (R1: SQL only here) |
| `app/modules/join_requests/schemas.py` | `JoinRequestStatusFilter` gains `"cancelled"` (additive) | AC 1: staff can list cancelled rows |
| `app/modules/join_requests/retention.py` (new) | `JoinRequestRetentionService.run(dry_run)` returning `PurgeCount` | AC 3 |
| `app/modules/users/service.py` | In `set_status`, on a real transition to `DISABLED`: cancel + one audit row per cancelled request | AC 1, R4 |
| `app/modules/membership/service.py` | `self.users.get` → `self.users.get_for_share` in `join`; `JoinService(..., max_pending_per_app)`; cap check in `_request`; warning log; docstring lock order | AC 2, AC 4 |
| `app/modules/membership/errors.py` | `JoinQueueFullError(PermissionDeniedError)`, code `join_queue_full` | AC 2, R6 |
| `app/modules/auth/router.py` | Pass `max_pending_per_app=settings.join_max_pending_per_app` to `JoinService`; extend the join docstring with `join_queue_full` | AC 2 |
| `app/modules/audit/actions.py` | `APP_JOIN_REQUEST_CANCELLED = "app.join_request_cancelled"`, `JOIN_REQUEST_RETENTION_PURGED = "join_request_retention.purged"` | AC 5, R4 |
| `app/core/config.py` | `join_max_pending_per_app`, `join_request_retention_days`, validator retention ≥ cooldown | AC 2, 3, R5 |
| `app/cli.py` | `_cleanup` runs the join-request retention after the email one; module docstring and `CLEANUP_USAGE` unchanged except the docstring line | AC 3 |
| `.env.example` | `JOIN_MAX_PENDING_PER_APP=`, `JOIN_REQUEST_RETENTION_DAYS=` with comment lines | R5 |
| `docs/consumers.md` | Join section: cap bullet, `403 join_queue_full` table row, "handle unknown 403 codes" note | AC 2 (consumer contract) |
| `docs/deploy.md` | "Email retention cleanup (USR-04d)" → "Retention cleanup (USR-04d, USR-15)": join_requests rule, env var, output line | AC 3 (operator doc) |
| `docs/identity-architecture.md` | Data model status set (line ~116), join paragraph (~70: cap + cancel on disable), staff list filter (~155), audit catalogue (~223: `app.join_request_cancelled`, `join_request_retention.purged`) | Keep the architecture doc true |
| `tests/test_join_requests_cancelled_migration.py` (new) | Migration tests | R2 |
| `tests/test_join_requests_migration.py` | `test_constraints`: assert `cancelled` (with `decided_at`) is accepted, `cancelled` without `decided_at` rejected; `bogus` still rejected | CHECK drift is invisible to `alembic check` |
| `tests/test_users_admin.py` | Disable/cancel tests | AC 1, 5 |
| `tests/test_join_requests_join.py` | Cap tests, cancel → rejoin, approval lock order | AC 1, 2, 4 |
| `tests/test_auth_join.py` | Open-join user lock test | AC 4 |
| `tests/test_join_requests_api.py` | 409 on cancelled; list `?status=cancelled` | AC 1 |
| `tests/test_join_requests_retention.py` (new) | Retention service tests | AC 3 |
| `tests/test_cli_cleanup.py` | Expected output lines include `join_requests` | AC 3 |
| `tests/test_audit_coverage.py` | Multi-row disable test + comment on the `_user_status` scenario | AC 5 |
| `tests/test_config.py` | Two settings tests | AC 2, 3 |

Not touched: `tests/test_cli_audit_purge.py` (its flake is BUG-001), `app/modules/auth/provisioning.py` (CLI `create-account --join` creates a new user, never reads an existing one, so no extra lock is needed), `join_available` (see Approach 4), ADRs.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_join_requests_cancelled_migration.py › test_upgrade_cancels_pending_of_disabled_users` | Downgrade to `e2b7a94d1c58`, seed a disabled user with a pending row and an active user with a pending row (committed engine, as in `test_join_requests_migration`), upgrade → only the disabled user's row is `cancelled` with `decided_at` set, `decided_by` NULL; clean up seeded rows in `finally` |
| `… › test_downgrade_deletes_cancelled_rows` | At head, seed one `cancelled` and one `rejected` row; downgrade → `cancelled` row gone, `rejected` kept, inserting `cancelled` now violates the CHECK; upgrade back to head in `finally` |
| `test_join_requests_migration.py › test_constraints` (edit) | `cancelled` + `decided_at` accepted; `cancelled` without `decided_at` rejected |
| `test_users_admin.py › test_disable_cancels_pending_join_requests` | AC 1a + audit rows (AC 5) |
| `… › test_disable_cancel_rolls_back_with_disable` | AC 1b (use the self-disable 403 path: denied row only, no cancel) |
| `… › test_disable_cancel_lock_order` | AC 1c |
| `… › test_disable_noop_cancels_nothing` | PATCH `disabled` on an already-disabled user with a (seeded) pending row → row stays pending, no cancel audit row; PATCH `active` cancels nothing |
| `test_join_requests_api.py › test_approve_cancelled_409_not_pending` | AC 1e (also reject → 409) |
| `… › test_list_status_cancelled` | `?status=cancelled` returns the cancelled row; `?status=bogus` still 422 |
| `test_join_requests_join.py › test_join_after_cancel_202_new_request` | AC 1d |
| `… › test_join_approval_lock_order` | AC 4 (approval path) |
| `… › test_join_cap_reached_403` | AC 2a |
| `… › test_join_cap_replay_same_request_202` | AC 2b |
| `… › test_join_cap_after_cooldown_check` | AC 2b ordering |
| `… › test_join_cap_counts_pending_only` | AC 2c |
| `… › test_join_cap_frees_slot_after_reject` | AC 2c |
| `… › test_join_cap_logs_warning` | AC 2d |
| `… › test_join_open_ignores_cap` | `open` app with cap 1 and 1 pending row (seeded) → 200 (cap applies to the request queue only) |
| `test_auth_join.py › test_join_locks_user_for_share` | AC 4 (open path) |
| `test_join_requests_retention.py › test_purges_old_decided_rows` | AC 3a |
| `… › test_keeps_rejection_inside_cooldown` | AC 3b |
| `… › test_never_purges_pending` | AC 3b |
| `… › test_cooldown_zero_keeps_nothing_extra` | cooldown 0: an old rejected row is purged |
| `… › test_keeps_recent_decided_rows` | decided 10 d ago with 180 d retention → kept |
| `… › test_batches_write_one_summary_each` | AC 3d |
| `… › test_dry_run_counts_only` | AC 3d |
| `… › test_retention_floor` | repository refuses a retention below 1 day (`ValueError`) |
| `test_cli_cleanup.py` (edit existing output tests) | AC 3e; seed one old decided join request where the expected lines are asserted, or assert `join_requests: deleted 0 (…)` |
| `test_audit_coverage.py › test_user_disable_with_pending_requests_writes_multiple_rows` | AC 5: PATCH disable on a user with 2 pending → `before + 3` rows, actions `{user.disabled, app.join_request_cancelled×2}`, all `outcome=success`, actor = staff, no `@` in changes |
| `test_config.py › test_join_max_pending_per_app_floor` | AC 2e |
| `test_config.py › test_join_request_retention_must_cover_cooldown` | AC 3c |

Optional (implementer's call, record in impl-log if skipped): a two-connection concurrency test in the style of `tests/test_email_lock_concurrency.py`: a blocker connection holds the user `FOR NO KEY UPDATE`; join with `lock_timeout` must time out (proves it waits on the user lock).

## Approach
1. **Model + migration (R2).** Add `CANCELLED`; update the model CHECK text. Migration `upgrade()`:
   - `op.drop_constraint(op.f("ck_join_requests_status_valid"), "join_requests", type_="check")`, then `op.create_check_constraint(op.f("ck_join_requests_status_valid"), "join_requests", "status IN ('pending', 'approved', 'rejected', 'cancelled')")`. Use `op.f` on both, as in `a9d4c27e6b13`, so the naming convention does not double-prefix.
   - Data step: `UPDATE join_requests SET status = 'cancelled', decided_at = now() WHERE status = 'pending' AND user_id IN (SELECT id FROM users WHERE status = 'disabled')`. No audit rows (a deploy step, not an admin action); say so in the docstring.
   - `downgrade()`: `DELETE FROM join_requests WHERE status = 'cancelled'`, then drop and recreate the 3-value CHECK. Docstring: "Lossy: cancelled rows are deleted (mapping them to `rejected` would create false cooldowns)."
   - Do not touch the indexes (partial-index `postgresql_where` text must stay byte-identical).
2. **Cancel on disable (AC 1, R1, R4).** `JoinRequestRepository.cancel_pending_for_user(user_id, decided_by) -> list[tuple[uuid.UUID, str]]`:
   - `UPDATE join_requests SET status='cancelled', decided_at=now(), decided_by=:actor WHERE user_id=:u AND status='pending' RETURNING id, app_id`, with `synchronize_session=False`.
   - Then one unlocked `SELECT id, key FROM apps WHERE id IN (…)`; return `(request_id, app_key)` sorted by `app_key`, `request_id` (deterministic audit order).
   - In `UserService.set_status`, inside the existing `if status == UserStatus.DISABLED:` block after `invalidate_all`: call it with `actor_user_id(actor)`, then `audit.record(APP_JOIN_REQUEST_CANCELLED, actor=actor, ctx=ctx, target=(AuditTarget.JOIN_REQUEST, id), app_key=key, changes={"status": change("pending", "cancelled")})` per row. The `user.disabled` row stays last.
   - No `lock_app` for target apps: that would put user before app and deadlock with approve (app → user). The user `FOR NO KEY UPDATE` already held serialises against join (after step 4) and approve (`FOR SHARE`); reject holds only the request row and never waits on the user, so the UPDATE just waits for it. No cycle (identity F5, security F2).
   - The early return (unchanged status) and the denied path stay before the cancel, so they cancel nothing. Re-enable restores nothing.
   - `rejected_within` stays `status='rejected'`: cancels never start a cooldown.
   - Update the `set_status` docstring: lock order admin app → user → the user's join requests.
3. **User lock in join (AC 4, R11).** In `JoinService.join`, replace `self.users.get(account.user_id)` with `self.users.get_for_share(account.user_id)`. It runs after `lock_joinable_app` and before the policy branch, so both `open` and `approval` are covered. Lock order: app → user (share) → insert. `admin` is refused before `lock_app`, so join never holds the `admin` lock that `set_status` takes first. Update the module/class docstring.
4. **Pending cap (AC 2, R5, R6).**
   - Config: `join_max_pending_per_app: int = Field(default=1000, ge=1)`.
   - `JoinService.__init__(session, *, reject_cooldown_seconds, max_pending_per_app)`; the only constructor call is `app/modules/auth/router.py:274`.
   - `JoinRequestRepository.pending_at_least(app_id, n) -> bool`: `SELECT id … WHERE app_id=:a AND status='pending' ORDER BY created_at, id OFFSET n-1 LIMIT 1` (bounded; served by `ix_join_requests_app_id_status_created_at_id`).
   - Order in `_request`: `usable_default_role` → existing pending → 202 same id → cooldown → `join_rejected` → **cap** → insert. All under the app lock, so count-then-insert is exact.
   - On cap: `logger.warning("join_queue_full app=%s user_id=%s cap=%s", app.key, user_id, cap)` and raise `JoinQueueFullError("Joining this app is not available right now")`, no `details`. No audit row (no state change; refusals must not grow the audit table).
   - `join_available` is unchanged: no count on every non-member `/token`, the cap is meant to be transient, and the authoritative check is the locked one in `_request`. Document that join can answer `join_queue_full` while `join_available` is true.
   - The join route's `responses` already declare 403 with the error envelope; only the docstring changes.
5. **Retention (AC 3).**
   - Config: `join_request_retention_days: int = Field(default=180, ge=1)`, plus `@model_validator` `_join_retention_covers_cooldown`: `join_request_retention_days * 86400 >= join_reject_cooldown_seconds`, else `ValueError("JOIN_REQUEST_RETENTION_DAYS must be at least JOIN_REJECT_COOLDOWN_SECONDS")` (no values in the message).
   - `JoinRequestRetentionRepository` in `join_requests/repository.py`, same shape as `EmailRetentionRepository`: `set_lock_timeout()` (`SET LOCAL lock_timeout = '2s'`), `count(retention, cooldown_seconds)`, `purge(retention, cooldown_seconds, batch)`, `commit()`. A 1-day floor check raising `ValueError`.
   - Predicate (DB clock): `status <> 'pending' AND decided_at < now() - :retention AND NOT (:cooldown > 0 AND status = 'rejected' AND decided_at > now() - :cooldown)`. Purge: `DELETE … WHERE id IN (SELECT id … ORDER BY decided_at, id LIMIT :batch FOR UPDATE SKIP LOCKED)`, `synchronize_session=False`.
     - This keeps every rejection inside the cooldown, which is a superset of "the latest rejection per (app, user)" and exactly what `rejected_within` reads. With the settings validator this clause cannot trigger in a valid config; it is defense in depth, tested by calling the service with retention < cooldown.
   - `JoinRequestRetentionService(session, *, retention: timedelta, cooldown_seconds: int, batch_size=1000).run(dry_run) -> list[PurgeCount]` (reuse `PurgeCount` from `app.modules.auth.retention`). Loop as in `EmailRetentionService`: per batch `set_lock_timeout` → purge → if deleted > 0 write `JOIN_REQUEST_RETENTION_PURGED` (`SYSTEM_CLI`, `AuditContext.NONE`, `changes={"join_requests": change(None, deleted)}`) → commit. Dry run: count only, no writes, no audit. The key `join_requests` does not match `_FORBIDDEN_KEY`, so `audit/service.py` needs no change.
   - Label: `f"decided, older than {days} d"`.
   - `app/cli.py::_cleanup`: after `EmailRetentionService(...).run`, in the same scope, run `JoinRequestRetentionService(session, retention=timedelta(days=settings.join_request_retention_days), cooldown_seconds=settings.join_reject_cooldown_seconds).run(dry_run=dry_run)` and print its rows with the existing format. Flags unchanged (`--dry-run` only; the ops workflow needs no change). Errors keep the fixed `MSG_CLEANUP_FAILED` line.
6. **Docs + env.**
   - `.env.example`:
     - `# Pending join requests per approval app (default 1000, min 1); join answers 403 join_queue_full at the cap`
     - `JOIN_MAX_PENDING_PER_APP=`
     - `# Days to keep decided join requests before cleanup deletes them (default 180; must cover JOIN_REJECT_COOLDOWN_SECONDS)`
     - `JOIN_REQUEST_RETENTION_DAYS=`
   - `docs/consumers.md`: add the cap bullet and the `| 403 join_queue_full | The app's request queue is full; try later, contact staff if it persists. No Retry-After. |` row, plus "Treat unknown 403 codes as 'cannot join now'".
   - `docs/deploy.md`: rename the section, add the join_requests rule (decided rows older than `JOIN_REQUEST_RETENTION_DAYS`, rejections inside the cooldown kept, pending never deleted, one `join_request_retention.purged` summary per batch), and the output line.
   - `docs/identity-architecture.md`: the four spots listed above.

Rules: R1 (SQL only in repositories; services raise `AppError` subclasses), R2 (step 1), R3 (no new endpoint; join stays cookie class, staff list stays Bearer `admin:users:read`), R4 (cancel rows in the disable transaction; purge summaries per batch), R5 (two env keys in `config.py` + `.env.example`), R6 (403 `PermissionDeniedError` subclass, envelope unchanged), R7 (follow-ups below), R8 (tests above), R9 (additive enum value on the list filter; no breaking change), R11 (ids only in logs and audit; no email; error reveals no count).

## Specialist input used
- **identity-access-engineer**: R1 (`cancelled` status, CHECK migration, lossy downgrade, data step) adopted. R2 (cancel in `set_status`, no `lock_app` per app, per-request audit rows) adopted. R3 (lock for both policies) adopted. R4 order (pending → cooldown → cap) adopted. R5 retention adopted. Rejected: cap default 500 (1000 chosen, see below); `decided_by` NULL (actor chosen); optional `cancelled_join_requests` count on the `user.disabled` row (per-request rows are enough; keeps the `user.disabled` shape stable).
- **security-architect**: F1/Rec 1 (lock both paths) adopted. F2 (no app locks in disable) adopted. Rec 2 adopted, including `decided_by` = actor. Rec 3 adopted: 403 `join_queue_full`, default 1000, `ge=1`, bounded OFFSET query, no details. Rec 4 (warning log, no audit row) adopted. Rec 5 predicate adopted, with `ge=1` instead of `ge=30` because the validator already ties retention to the cooldown. Default 180 d adopted.
- **database-optimizer**: §1 migration mechanics (`op.f` names, downgrade delete, constraint test) adopted. §2 set-based UPDATE + RETURNING adopted. §4 batching, `lock_timeout`, new audit action adopted. Rejected: 409 for the cap (R6 refusals of a caller are 403, consistent with `invitation_limit_reached` / `org_limit_reached`; 409 is used for staff-side state conflicts); `join_available` turning false on a full queue (extra count on every `/token`, leaks queue state); `NOT EXISTS` latest-rejection subquery (the simpler cooldown-window clause keeps a superset of what the cooldown reads); 90 d default. Answered database Q7: `audit_log.action` is `Text` with only a format CHECK `action ~ '^[a-z_]+(\.[a-z_]+)+$' AND length(action) <= 64` (`app/modules/audit/models.py:34`). Both new action names match it, so no audit migration is needed.
- **api-platform-engineer**: 403 with a new code and no `Retry-After` adopted. `join_available` unchanged adopted. Retention option (a) (env age, `cleanup` keeps only `--dry-run`) adopted. Docs list adopted. Rejected: `ge=0` / 0 = unlimited (mirror `invitation_max_pending_per_org`, `ge=1`; an unlimited queue is what the ticket removes); locking every affected app in disable (inverts ADR-0003 §10 order and can deadlock with approve; the other three consults agree).

## Assumptions
- `make_client(**overrides)` accepts any `Settings` field (as `join_reject_cooldown_seconds=0` in `test_join_after_cooldown_new_request`).
- `actor_user_id(actor)` returns the staff user id for a Bearer principal (as approve/reject use it).
- Cancelling covers pending requests on every app, including disabled apps or apps that left `approval` (database Q5): it is "all pending of that user".
- Raising the cooldown after a purge cannot revive purged rejections. Accepted and documented in `docs/deploy.md`.
- Production has few or no pending rows of disabled users (USR-03g just shipped), so the data step and CHECK rebuild (ACCESS EXCLUSIVE, small table) are cheap.
- Rough size: about 280–320 changed production lines (migration ~60, retention service + repository ~110, the rest ~130), plus docs and ~450 test lines. This fits one cycle.

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **Cap error**: 403 `join_queue_full` (join_* family, same status as `invitation_limit_reached`), message "Joining this app is not available right now", no details, no `Retry-After`. Alternatives: `join_request_limit_reached`, or the generic `join_unavailable`.
2. **Cap value**: `JOIN_MAX_PENDING_PER_APP` default **1000**, `ge=1` (0 is not "unlimited").
3. **`join_available` ignores the cap** (stays true while the queue is full).
4. **`decided_by` on cancelled rows = the staff user who disabled the account** (NULL only for rows cancelled by the migration data step).
5. **Retention default 180 days**, `ge=1`, validated ≥ the cooldown at settings load (a bad config fails startup, not just the CLI).
6. **Audit**: one `app.join_request_cancelled` row per request (bounded by apps), no count on `user.disabled`; one `join_request_retention.purged` summary per deleting batch; cap hits are a WARNING log, not audit.
7. **Migration data step** cancels pending rows of already-disabled users with no audit row; the downgrade deletes `cancelled` rows (lossy).
8. **AC 4 widened** to the approval path (see Spec summary).

## Blocking questions
None

## Split proposal
None. Estimated production code is well under ~500 lines. If the implementer finds it much larger, the fallback split is: slice 1 = AC 1 + AC 4 + AC 2 (migration, cancel, lock, cap); slice 2 = AC 3 (retention + CLI + deploy.md).

## Data model / migration
Table `join_requests` (no new columns or indexes):
- `status` CHECK `ck_join_requests_status_valid`: `status IN ('pending', 'approved', 'rejected', 'cancelled')` (was 3 values). The model `CheckConstraint` text must be byte-identical.
- `ck_join_requests_decided_iff_not_pending` unchanged: `cancelled` rows always have `decided_at`.
- `decided_by` (existing, nullable, `ON DELETE SET NULL`): the disabling staff user for cancels.

Revision `usr15_join_requests_cancelled`, `down_revision = "e2b7a94d1c58"`.
- Upgrade: drop + recreate the CHECK; data step (pending rows of disabled users → `cancelled`, `decided_at = now()`).
- Downgrade: `DELETE FROM join_requests WHERE status = 'cancelled'`; drop + recreate the 3-value CHECK. Lossy, documented.
- `alembic check` does not compare CHECKs: guarded by `test_constraints` and the new migration tests.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/apps/{key}/join` (changed) | `{"account_id"}` (unchanged) | unchanged | adds **403 `join_queue_full`** (approval apps, new request while the app has ≥ `JOIN_MAX_PENDING_PER_APP` pending; no details); a disabled user racing a disable now gets 401 `session_invalid` | cookie class (R3 2), unchanged |
| GET | `/api/v1/apps/{key}/join-requests?status=` (changed) | `status` ∈ `pending` (default) \| `approved` \| `rejected` \| **`cancelled`** | `Page[JoinRequestRead]`; `status` may be `cancelled` | 200, 401, 403, 404, 422 (unchanged) | Bearer `admin:users:read` (unchanged) |
| PATCH | `/api/v1/users/{user_id}` (behaviour) | unchanged | unchanged | unchanged; disabling also cancels the user's pending join requests | Bearer (unchanged) |
| POST | `/api/v1/apps/{key}/join-requests/{id}/approve` \| `/reject` (behaviour) | unchanged | unchanged | a cancelled request → 409 `not_pending` (existing reason) | unchanged |

CLI (not HTTP): `python -m app.cli cleanup [--dry-run]` also prints `join_requests: deleted|would delete <n> (decided, older than <N> d)`.

## Follow-ups (out of scope, R7)
- Cancel pending requests when an app is disabled or leaves `approval` (approve already answers 409).
- Expiry of stale pending requests; bulk reject; per-account join rate limit / deny list (USR-03b F6).
- Pending-queue count on `GET /api/v1/stats/dashboard` or a log-based alert on `join_queue_full`.
- Scheduled `cleanup` run (existing open owner decision in `docs/deploy.md`).
