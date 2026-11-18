## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (`service postgresql start`, then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): exit 0. ruff "All checks passed!"; ruff format "317 files already formatted"; mypy "Success: no issues found in 147 source files"; pytest "2366 passed, 1 warning in 241.51s". First run; the BUG-001 flake did not occur, so no rerun was needed. This matches impl-log.md (2366 passed).
- alembic: `alembic heads` gives a single head, `c7a3e91f5d28`. `alembic upgrade head && alembic check` gives "No new upgrade operations detected." The downgrade and re-upgrade round trip runs in `tests/test_join_requests_cancelled_migration.py` (passed).

### Acceptance criteria
- [x] AC1: disabling cancels pending requests in the same transaction, with the same lock order as `set_status`. Evidence: `app/modules/users/service.py:86-97` runs inside the existing `DISABLED` branch, after the `admin` app lock and the user `FOR NO KEY UPDATE`, in the same session and with no commit before it. Tests: `test_users_admin.py::test_disable_cancels_pending_join_requests` (2 pending rows across apps become `cancelled` with `decided_by` = staff; approved, rejected and other users' rows are unchanged), `::test_disable_cancel_rolls_back_with_disable` (strict client, 403, row stays pending, no cancel audit), `::test_disable_cancel_lock_order` (exactly one app lock, then the user lock, then the `UPDATE join_requests`), `::test_disable_noop_cancels_nothing`, `test_join_requests_join.py::test_join_after_cancel_202_new_request` (a cancel starts no cooldown), and `test_join_requests_api.py::test_approve_cancelled_409_not_pending`. All passed.
  - Deadlock analysis: approve locks app, then user (share), then request; disable locks `admin` app, then user (no-key-update), then request rows; reject locks app, then request, and never waits on the user; join locks app, then user (share), and never locks `admin`. The lock graph has no cycle.
- [x] AC2: the per-app cap comes from env and join answers a defined error. Evidence: `app/core/config.py:70` (`ge=1`), `.env.example`, and `membership/service.py:170-177`, which runs under the app lock after the idempotent replay and cooldown checks. The error is `JoinQueueFullError` with 403 `join_queue_full` and no details. Tests: `test_join_cap_reached_403` (no row, no audit, a log line with ids only), `_replay_same_request_202`, `_after_cooldown_check`, `_counts_pending_only`, `_frees_slot_after_reject`, `_open_ignores_cap`, and `test_config.py::test_join_max_pending_per_app_floor`. All passed.
- [x] AC3: retention. Evidence: `join_requests/repository.py` `JoinRequestRetentionRepository` has a 1-day floor, `SKIP LOCKED` batches, and `lock_timeout`. The predicate is non-pending AND older than the retention age AND NOT a rejection inside the cooldown, which keeps a superset of "latest rejection per (app, user) while the cooldown applies". `config.py:203` checks that retention covers the cooldown. The CLI is wired in `app/cli.py:686-699`. Tests: `test_join_requests_retention.py` (8 tests), `test_cli_cleanup.py` output lines, and `test_config.py::test_join_request_retention_must_cover_cooldown`. All passed.
- [x] AC4 (widened by the plan to both join paths, which I accept as the condition that makes AC1 true): `membership/service.py:131` uses `get_for_share` at the single shared read before the policy branch. Tests: `test_auth_join.py::test_join_locks_user_for_share` (open: app lock, then user `FOR SHARE`, then insert into role_assignments) and `test_join_requests_join.py::test_join_approval_lock_order`. Both passed.
- [x] AC5: tests for each criterion, plus audit rows wherever state changes. One `app.join_request_cancelled` row per request with the staff actor, `app_key` set, and no email (`test_disable_cancels_pending_join_requests`, `test_audit_coverage.py::test_user_disable_with_pending_requests_writes_multiple_rows`). One `join_request_retention.purged` summary per deleting batch with actor `system:cli` (`test_batches_write_one_summary_each`). A cap refusal writes no audit row.
- [x] Migration (R2): `migrations/versions/20261118_c7a3e91f5d28_...` widens the CHECK using `op.f`, runs the data step, and has a working downgrade that is lossy and documented as such. The model CHECK text is identical to the migration's. The CHECK is covered by `test_join_requests_migration.py::test_constraints` and the new migration tests.

### Issues
None.

### Scope
Every changed file is in the plan's file list, plus the workflow files (`work/backlog.md`, the ticket folder). There are no out-of-plan changes. The optional concurrency test was skipped, and impl-log.md records this as the plan allows.

### Rules R1–R11
No violations found. R1: SQL is only in the repositories, and the services raise `AppError` subclasses. R3: no new endpoint. R4: the cancel audit rows are written in the disable transaction. R5: two env keys are listed in `.env.example` without values. R6: the error envelope is unchanged and the 403 subclass is used. R9: the `cancelled` filter value is additive. R11: logs and audit rows contain ids only.

### Nits (do not block)
- `docs/deploy.md:898` still says "Both ages have a 24 h floor in code". There are now three rules, and the join-requests age has a 1-day floor in code plus a settings validator. This sentence could be reworded.
- `test_disable_cancel_rolls_back_with_disable` uses the self-disable 403, which returns before the cancel runs. It therefore proves "no cancel on refusal" more than it proves atomicity. Atomicity is clear from the structure (one session, one commit in `get_session`), and the plan prescribed this test.
- The `cleanup` CLI test only asserts `join_requests: deleted 0`. Non-zero counts are covered at the service level.

### Follow-ups (out of scope)
- As listed in plan.md: cancel pending requests when an app is disabled or leaves `approval`; expiry of stale pending requests, bulk reject, and a per-account join rate limit; a pending-queue metric or alert on `join_queue_full`; a scheduled `cleanup` run.
- Owner decisions 1-8 in plan.md ("Needs owner's decision") still need the owner's confirmation at merge.
