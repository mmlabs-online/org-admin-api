# Review — USR-04d

## Attempt 1 — 2026-11-13
Verdict: PASS

Diff reviewed: `git diff cd266d9 HEAD` (commit 83052b9; unmerged USR-07 / USR-04b work below cd266d9 excluded). Working tree clean.

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0. `ruff check`: All checks passed!; `ruff format --check`: 328 files already formatted; `mypy app`: Success: no issues found in 79 source files; `pytest`: **988 passed in 35.72s** (matches impl-log's 988).
- Migration against `org_admin_test`: `alembic upgrade head` (at head) → `alembic downgrade -1` (4d4b5d67dc44 → 91a78d0496f9, `current` = 91a78d0496f9) → `alembic upgrade head` (91a78d0496f9 → 4d4b5d67dc44) → `alembic check`: "No new upgrade operations detected." `alembic heads`: single head 4d4b5d67dc44.

### Acceptance criteria
- [x] **AC 1 (S1)** `get_for_update` uses `FOR NO KEY UPDATE` — evidence: `app/modules/users/repository.py:31` `.with_for_update(key_share=True)`; only callers are verify (`email_verification.py:102`) and reset (`password_reset.py:100`), which update non-key columns only, so no lock upgrade. `test_email_lock_concurrency.py › test_resend_vs_reset_no_deadlock` ([202, 204], user verified, password changed, old verify token used) and `› test_resend_vs_verify_no_deadlock` ([202, 422], new verify token live) pass; `› test_control_plain_for_update_deadlocks[reset|verify]` pass (500 with the old lock), so the tests really reach the cycle. Interleave is deterministic (patched `issue`, `_wait_for_lock_waiter`), every wait bounded.
- [x] **AC 2** login still waits on reset's lock — `test_login_concurrency.py › test_login_blocked_by_reset_lock_reads_new_password` now holds `FOR NO KEY UPDATE` and passes (`FOR NO KEY UPDATE` conflicts with login's `FOR SHARE`).
- [x] **AC 3 (S2)** reset ≤ 50 %, verify ≤ 80 %, total binds, reasons — `email_throttle.py:12,32-49`; `test_email_throttle.py › test_cap_refusal` (17 rows incl. floors at cap 1 / 5 and "reset at ceiling cannot take verify's floor"); `test_password_reset_api.py › test_forgot_purpose_cap_logs_and_verify_still_sends`. Logic checked by hand: verify keeps ≥ 50 %, reset ≥ 20 %.
- [x] **AC 4** every capped send logs `email_not_sent reason=… purpose=…`, no address — sign-up `hooks.py:52-53`, resend `email_verification.py:127-133`, forgot `password_reset.py:70-72`; `test_email_verification_api.py › test_resend_cap_logs_reason`, `› test_daily_cap_resend_429_and_signup_still_201`, the AC 3 test, and the updated `test_verify_share_exhausted_reset_still_sent` (deviation justified in impl-log).
- [x] **AC 5** route-level `AppContextDep` before `DeviceDep` on resend — `router.py:230`; `test_email_verification_api.py › test_resend_app_context_checked_before_device` (link origin overridden, rotation-due cookie → 403 `app_header_required`, no `Set-Cookie`, `rotated_at` unchanged, no send/no send-log row). Impl-log records the test failing (202) with the dependency removed.
- [x] **AC 6** dispatcher shutdown wired into lifespan — `main.py:30-35,47`; `dispatcher.py:56-87` (closed flag, bounded drain, count-only drop log, idempotent, residual queue drained so `join()` cannot hang); `test_email_dispatcher.py › test_aclose_drains_queued_mail`, `› test_aclose_timeout_drops_and_logs_count` (count=3, no address/link/token), `› test_enqueue_after_close_dropped`, `› test_aclose_idempotent`, `› test_app_lifespan_closes_dispatcher`.
- [x] **AC 7** index + reversible migration, model declared, no drift — `models.py:59`; `migrations/versions/20261113_4d4b5d67dc44_add_email_tokens_created_at_index.py` (down_revision 91a78d0496f9, working `downgrade()`); `test_email_schema.py › test_email_tokens_created_at_index`; round-trip and `alembic check` above.
- [x] **AC 8** retention CLI, batched `SKIP LOCKED` deletes, commit per batch, `lock_timeout` 2 s — `repository.py:243-314`, `retention.py`, `cli.py:148-162,355-388`; `test_cli_cleanup.py › test_cleanup_deletes_old_rows_keeps_recent` (49 h log deleted / 47 h kept; used 8 d + expired 8 d deleted; used 25 h + unexpired 8 d kept), `› test_cleanup_batches` ([2, 2, 1]), `test_email_lock_concurrency.py › test_purge_skips_locked_rows`.
- [x] **AC 9** 24 h floors in code — `repository.py:260-263`; `test_cli_cleanup.py › test_purge_refuses_age_below_floor` (all four methods; 24 h itself allowed).
- [x] **AC 10** fixed output, dry run, exit 1 / 64, `DB_ECHO` refused, no leaks — `test_cli_cleanup.py › test_cleanup_dry_run_deletes_nothing`, `› test_cleanup_failure_exit_1_fixed_line` (DSN-bearing `OperationalError` → fixed line, no `postgresql`/`secret`/`@`), `› test_cleanup_usage_error_64` (4 params, nothing echoed), `› test_cleanup_refuses_db_echo`.
- [x] **AC 11** runbook + ADR amended — `docs/deploy.md:162-182` (events, check command, alert rule; `daily_cap`, `purpose_cap`, `email_quota_exceeded`, `email_auth_failed`, `email_dropped`), `docs/deploy.md:408-451` (retention, ops workflow, fallback, exit codes), drain note `docs/deploy.md:106-108`, setup step 6; ADR-0007 amendments in decisions 4 and 5 and Consequences.
- [x] **AC 12** `make check` green — see Checks.

Ticket criteria map 1:1 onto the plan's ACs (dispatcher → 6; retention + index → 7–10; runbook → 11; `AppContextDep` → 5; S1 → 1–2; S2 + retention ≥ 24 h → 3, 9; tests → each).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: CLI → `EmailRetentionService` → `EmailRetentionRepository`; no ORM in router/CLI; services raise `AppError` subclasses (`DailyCapReachedError`).
- R2: one hand-reviewed migration with downgrade; model updated; round-trip verified.
- R3: resend stays cookie class with the app context resolved first; no new endpoints.
- R5: no new env keys; ratios are documented code constants (ADR-0007 amendment), `EMAIL_DAILY_CAP` stays the env knob.
- R6: 429 bodies unchanged.
- R7: every changed file is in the plan's file list; deviations (one existing assertion updated, `noqa: ASYNC109`, single `email_dispatcher_closed`) are justified in impl-log.
- R11: logs carry event, reason, purpose, user id and counts only; CLI prints counts or fixed lines; tests assert no address/link/token/DSN.

### Nits (do not block)
- 💭 `dispatcher.py:76-87`: if the worker were already `done()` with items still queued, the residual drain loop drops them without counting them in `email_dropped … count=N`. The worker cannot die today (it catches `Exception`), so unreachable in practice; counting `qsize()` before the residual loop in that branch would make the log exact.
- 💭 impl-log reports "327 files already formatted"; reviewer run reports 328. Result identical (green); likely counted before the final file. No action.
- 💭 `retention.py:36-61`: the targets tuple's inline type annotation is verbose; a `NamedTuple` would read better. Style only.

### Follow-ups (out of scope)
- Deployment repo: add `cleanup` to `ops-org-admin-api.yml` (plan §6); until then use the documented shell fallback.
- Plan A5 list: `login_failures` cleanup; device/account session cleanup; scheduled cleanup job (owner decision); log-based alerting for the runbook events; `stop_grace_period` / `--timeout-graceful-shutdown` tuning; `TokenRepository.invalidate` touching old expired rows.
