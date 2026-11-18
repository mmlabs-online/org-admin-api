## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| app/modules/join_requests/models.py | `CANCELLED` status; CHECK text widened (AC 1) |
| migrations/versions/20261118_c7a3e91f5d28_usr15_join_requests_cancelled.py | New revision on head `e2b7a94d1c58`: widen CHECK, data step, lossy downgrade (R2) |
| app/modules/join_requests/repository.py | `cancel_pending_for_user`, `pending_at_least`, `JoinRequestRetentionRepository` (AC 1-3, R1) |
| app/modules/join_requests/schemas.py | List filter gains `cancelled` |
| app/modules/join_requests/retention.py (new) | `JoinRequestRetentionService` (AC 3) |
| app/modules/users/service.py | Cancel + audit rows in `set_status` on disable (AC 1, R4) |
| app/modules/membership/service.py | `get_for_share` in join; pending cap in `_request` (AC 2, 4) |
| app/modules/membership/errors.py | `JoinQueueFullError` (`join_queue_full`) |
| app/modules/auth/router.py | Pass cap to `JoinService`; docstring |
| app/modules/audit/actions.py | Two new actions |
| app/core/config.py | `join_max_pending_per_app`, `join_request_retention_days`, retention >= cooldown validator |
| app/cli.py | `cleanup` also runs join-request retention; docstring line |
| .env.example | Two new keys, no values |
| docs/consumers.md, docs/deploy.md, docs/identity-architecture.md | Per plan |
| tests/test_join_requests_cancelled_migration.py (new), tests/test_join_requests_retention.py (new) | Plan tests |
| tests/test_join_requests_migration.py, test_users_admin.py, test_join_requests_join.py, test_auth_join.py, test_join_requests_api.py, test_cli_cleanup.py, test_audit_coverage.py, test_config.py | Plan tests / updated expectations |

### Tests added / changed
- test_join_requests_cancelled_migration: upgrade data step, lossy downgrade (CHECK rejects `cancelled`)
- test_join_requests_migration::test_constraints: `cancelled` accepted with `decided_at`, rejected without
- test_users_admin: disable cancels pending, rollback with refused disable (strict client), lock order, no-op cancels nothing
- test_join_requests_join: cancel then rejoin, approval lock order, cap (403, replay, after cooldown, pending only, frees slot, log, open ignores cap)
- test_auth_join::test_join_locks_user_for_share
- test_join_requests_api: 409 on cancelled, `?status=cancelled`
- test_join_requests_retention: 8 tests (purge, recent, cooldown guard, pending, cooldown 0, batches + summary audit, dry run, floor)
- test_cli_cleanup: expected output lines; test_audit_coverage: multi-row disable; test_config: two settings tests

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree: ruff "All checks passed!", "317 files already formatted", mypy "Success: no issues found in 147 source files", pytest "2366 passed" (exit 0). The BUG-001 flake failed once in an earlier run and passed in the final run; not modified.
- alembic: single head `c7a3e91f5d28`; `alembic check` "No new upgrade operations detected"; `downgrade -1` then `upgrade head` both ran, check clean again.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Skipped the optional two-connection concurrency test for the user share lock (lock order is covered by statement-capture tests).
- Test-only: logger `disabled` flag must be reset for caplog tests (migrations' `fileConfig`), as in test_admin_auth.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: not measured (no git repo in this directory).
