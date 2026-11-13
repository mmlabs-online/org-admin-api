Status: READY

# Plan — USR-04d: Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert

Estimated size: about 290 production lines (incl. a ~30-line migration), about 600 test lines, about 90 doc lines. One migration (index only). Under the ~400-line production budget; no split.

## Spec summary

§5.8 (docs/spec-v1.md:68):

> **Email flows:** email verification and password reset by one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3).

ADR-0007, Consequences:

> Retention cleanup of `email_send_log` and used tokens is a follow-up (USR-04d).

USR-04b security review (`work/tickets/USR-04b/security-review.md`):

> **S1** … lock with `FOR NO KEY UPDATE`, i.e. `.with_for_update(key_share=True)` in `get_for_update` …
> **S2** … give each purpose a ceiling, e.g. verify ≤ 80 % and reset ≤ 50 %, so each always has a floor.
> Follow-up 3: USR-04d retention cleanup must keep `email_tokens` rows for at least 24 h, because the cap now counts tokens.

CLAUDE.md R1 (layering), R2 (migration with downgrade), R3 (resend is cookie class: `mm_device` + Origin + `X-MM-App`), R5, R6, R8, R11 (no addresses, tokens or emails in logs or CLI output).

## Acceptance criteria → verification

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | **S1:** `UserRepository.get_for_update` locks `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`). Reset vs a concurrent resend, and verify vs a concurrent resend, finish without a deadlock (no 500). | `test_email_lock_concurrency.py › test_resend_vs_reset_no_deadlock`, `› test_resend_vs_verify_no_deadlock`; `› test_control_plain_for_update_deadlocks[reset]` / `[verify]` prove the same interleave deadlocks with the old `FOR UPDATE`. impl-log records running the two main tests once with `.with_for_update()` restored and seeing them fail (500 / `DeadlockDetectedError`). |
| 2 | Login still waits on a reset's user lock and reads the new hash (no regression of USR-04b AC 14). | `test_login_concurrency.py › test_login_blocked_by_reset_lock_reads_new_password` (now holds `FOR NO KEY UPDATE`) passes. |
| 3 | **S2:** reset may use at most 50 % of `EMAIL_DAILY_CAP` (at least 1), verify at most 80 % (at least 1); the total cap still binds. A purpose-share refusal logs `reason=purpose_cap`, a total-cap refusal `reason=daily_cap`. | `test_email_throttle.py › test_cap_refusal[...]` (table below); `test_password_reset_api.py › test_forgot_purpose_cap_logs_and_verify_still_sends`. |
| 4 | Every capped send logs one `email_not_sent reason=<daily_cap\|purpose_cap> purpose=<verify\|reset>` line (sign-up, resend, forgot), no address. | `test_email_verification_api.py › test_daily_cap_resend_429_and_signup_still_201` (unchanged assertion still holds), `› test_resend_cap_logs_reason`; AC 3 test for forgot. |
| 5 | Route-level `AppContextDep` on `POST /auth/verify-email/resend`, declared before `DeviceDep`: a bad app context answers 403 before any device lookup or cookie rotation, even when `get_link_origin` is overridden. | `test_email_verification_api.py › test_resend_app_context_checked_before_device`: `get_link_origin` overridden, rotation-due cookie, no `X-MM-App` → 403 `app_header_required`, no `Set-Cookie`, device `rotated_at` unchanged, no send. |
| 6 | `EmailDispatcher.aclose()` is wired into the app lifespan. On close it drains the queue for at most `DRAIN_SECONDS` (5 s), then cancels the worker and logs `email_dropped reason=shutdown count=N` when N > 0, and always `email_dispatcher_closed`. `enqueue` after close drops with `email_dropped reason=shutdown`. No address in logs. | `test_email_dispatcher.py › test_aclose_drains_queued_mail`, `› test_aclose_timeout_drops_and_logs_count`, `› test_enqueue_after_close_dropped`, `› test_aclose_idempotent`, `› test_app_lifespan_closes_dispatcher`. |
| 7 | Migration adds `ix_email_tokens_created_at` with a working `downgrade()`; model declares it; `alembic check` clean. | `test_email_schema.py › test_email_tokens_created_at_index`; `uv run alembic downgrade -1 && uv run alembic upgrade head`; `uv run alembic check` (CI). The session fixture already runs `downgrade base` → `upgrade head`. |
| 8 | `python -m app.cli cleanup [--dry-run]` deletes `email_send_log` rows older than 48 h and `email_tokens` rows older than 7 d that are used or expired, in batches (`DELETE … IN (SELECT … LIMIT n FOR UPDATE SKIP LOCKED)`, commit per batch, `lock_timeout` 2 s). Recent rows (incl. a used token 25 h old, an old unexpired unused token) survive. | `test_cli_cleanup.py › test_cleanup_deletes_old_rows_keeps_recent`, `› test_cleanup_batches`, `test_email_lock_concurrency.py › test_purge_skips_locked_rows`. |
| 9 | Minimum cutoffs enforced in code: token purge refuses an age < 24 h, send-log purge refuses an age < 24 h (ValueError). | `test_cli_cleanup.py › test_purge_refuses_age_below_floor`. |
| 10 | `--dry-run` prints fixed-format counts and deletes nothing; output never contains addresses, DSN or tracebacks; failures print one fixed line and exit 1; bad arguments exit 64 without echoing them; `DB_ECHO` refused. | `test_cli_cleanup.py › test_cleanup_dry_run_deletes_nothing`, `› test_cleanup_failure_exit_1_fixed_line`, `› test_cleanup_usage_error_64`, `› test_cleanup_refuses_db_echo`. |
| 11 | Runbook in `docs/deploy.md`: alerts on `email_not_sent reason=daily_cap` / `purpose_cap`, `email_quota_exceeded`, `email_auth_failed` (plus `email_dropped`), with how to check and what to do; retention cleanup via the ops workflow; shutdown drain note. ADR-0007 amended. | Doc review. |
| 12 | `make check` green. | `make check`. |

## Files to create / modify

| File | Change | Required because |
|---|---|---|
| `app/modules/users/repository.py` | `get_for_update`: `.with_for_update(key_share=True)`; docstring: "`FOR NO KEY UPDATE`: conflicts with `FOR SHARE` (login, forgot) and other updates; does not block FK `KEY SHARE` (S1)". | AC 1 |
| `app/modules/auth/email_throttle.py` | Add `RESET_CAP_PERCENT = 50`; replace `cap_allows` with `cap_refusal(purpose, *, total_24h, verify_24h, daily_cap) -> str \| None` returning `None`, `"daily_cap"` or `"purpose_cap"`. Docstring states the guaranteed floors (verify ≥ 50 %, reset ≥ 20 %). | AC 3 |
| `app/modules/auth/email_verification.py` | `daily_cap_allows` → `daily_cap_refusal(...) -> str \| None`; `DailyCapReachedError.__init__(retry_after, reason)` stores `self.reason`; `prepare` raises it with the reason; `resend` catches `DailyCapReachedError`, logs `email_not_sent reason=%s purpose=verify user_id=%s`, re-raises. | AC 3, 4 |
| `app/modules/auth/password_reset.py` | Use `daily_cap_refusal`; log `email_not_sent reason=%s purpose=reset`. | AC 3, 4 |
| `app/modules/auth/hooks.py` | Log `exc.reason` for `DailyCapReachedError` (else `limited`). | AC 4 |
| `app/modules/auth/router.py` | `resend_verification`: add `_app: AppContextDep` right after `_cookie`, before `body`, `link_origin` and `device`; docstring notes the order. | AC 5 |
| `app/modules/email/dispatcher.py` | `DRAIN_SECONDS = 5.0`; `_closed` flag, `_in_flight` counter; `enqueue` after close drops with log; `aclose(timeout=DRAIN_SECONDS)` drains/cancels/logs (Approach §4). Module docstring updated. | AC 6 |
| `app/main.py` | `lifespan` context manager that closes `app.state.email_dispatcher` on shutdown; `FastAPI(..., lifespan=_lifespan)`. | AC 6 |
| `app/modules/auth/models.py` | `Index("ix_email_tokens_created_at", "created_at")` in `EmailToken.__table_args__`. | AC 7 |
| `migrations/versions/20261113_<rev>_add_email_tokens_created_at_index.py` | New, `down_revision = "91a78d0496f9"`; `op.create_index` / `op.drop_index`. | AC 7, R2 |
| `app/modules/auth/repository.py` | New `EmailRetentionRepository` (lock timeout, count/purge for both tables, floors, commit). | AC 8, 9, R1 |
| `app/modules/auth/retention.py` | New `EmailRetentionService` + `PurgeCount`: retention constants, targets table, batch loop, dry run. | AC 8, R1 |
| `app/cli.py` | `cleanup [--dry-run]` subcommand: parser, fixed messages, own session scope without `begin()`, output, exit codes; module docstring. | AC 8, 10 |
| `Makefile` | `cleanup:` target (`uv run python -m app.cli cleanup $(ARGS)`), like `register-app`. | Local use, consistent with existing CLI targets |
| `docs/deploy.md` | Email alerts runbook; "Email retention cleanup (USR-04d)" section; One-time setup step 6 command list gets `cleanup`; shutdown drain note in the Email section. | AC 11 |
| `docs/adr/0007-email-verification-and-delivery.md` | "Amended by USR-04d" notes: reset ≤ 50 % / `purpose_cap`; drain on shutdown; retention replaces the follow-up line. | AC 11 (decision record stays true) |
| `tests/test_email_lock_concurrency.py` | New (committed data, real `get_session`). | AC 1, 8 |
| `tests/test_login_concurrency.py` | AC 14 test: raw SQL `FOR UPDATE` → `FOR NO KEY UPDATE`, docstring updated. | AC 2 (mirror production lock) |
| `tests/test_email_throttle.py` | Re-parametrize for `cap_refusal`. | AC 3 |
| `tests/test_password_reset_api.py` | Purpose-cap test. | AC 3, 4 |
| `tests/test_email_verification_api.py` | Resend cap log test; route-level app-context test. | AC 4, 5 |
| `tests/test_email_dispatcher.py` | Shutdown/lifespan tests. | AC 6 |
| `tests/test_email_schema.py` | Index test. | AC 7 |
| `tests/test_cli_cleanup.py` | New CLI + retention tests. | AC 8–10 |

The `email_dispatcher` fixture in `tests/conftest.py` may pass `aclose(timeout=0)` if a test is measurably slowed by the drain; record it in the impl-log if changed.

## Tests to write

| Test (file › name) | Covers |
|---|---|
| `test_email_lock_concurrency.py › test_resend_vs_reset_no_deadlock` | Interleave below; resend 202, reset 204; user verified, password changed; old verify token used. |
| `test_email_lock_concurrency.py › test_resend_vs_verify_no_deadlock` | Resend 202, verify 422 `token_invalid` (token superseded), user still unverified, the new verify token is unused and unexpired. |
| `test_email_lock_concurrency.py › test_control_plain_for_update_deadlocks[reset\|verify]` | Same interleave with `get_for_update` monkeypatched to the old plain `with_for_update()`: one of the two responses is 500 (client with `raise_app_exceptions=False`). Proves the test reaches the S1 cycle. |
| `test_email_lock_concurrency.py › test_purge_skips_locked_rows` | Committed old send-log row locked `FOR UPDATE` in another session: purge returns 0 within `TIMEOUT` (no wait); after release, purge returns 1. |
| `test_login_concurrency.py › test_login_blocked_by_reset_lock_reads_new_password` | Updated to `FOR NO KEY UPDATE`; still 401 for the old password. |
| `test_email_throttle.py › test_cap_refusal` | Table: `("reset",149,0,300)→None`; `("reset",150,0,300)→purpose_cap`; `("verify",240,240,300)→purpose_cap`; `("verify",299,149,300)→None` (reset at its 150 ceiling cannot take verify's floor); `("reset",300,0,300)→daily_cap`; `("verify",300,0,300)→daily_cap`; cap 1: `("reset",0,0,1)→None`, `("verify",1,0,1)→daily_cap`; cap 5: `("reset",2,0,5)→purpose_cap` (share max(1,2)=2), `("verify",3,3,5)→None`. |
| `test_password_reset_api.py › test_forgot_purpose_cap_logs_and_verify_still_sends` | `email_daily_cap=2`: forgot A sends; forgot B → 202, no mail, log `email_not_sent reason=purpose_cap purpose=reset`, no address in log; then a sign-up still sends its verification mail. |
| `test_email_verification_api.py › test_resend_cap_logs_reason` | `email_daily_cap=1` after one sign-up: resend 429 `Retry-After: 3600`, log `email_not_sent reason=daily_cap purpose=verify user_id=<id>`, no address. |
| `test_email_verification_api.py › test_resend_app_context_checked_before_device` | AC 5. |
| `test_email_dispatcher.py › test_aclose_drains_queued_mail` | 3 queued, fast sender: all 3 sent, no `email_dropped`, `email_dispatcher_closed` logged. |
| `test_email_dispatcher.py › test_aclose_timeout_drops_and_logs_count` | Sender blocks on an `asyncio.Event`; 3 queued; `aclose(timeout=0.05)` returns within `TIMEOUT`; log `email_dropped reason=shutdown count=3` (1 in flight + 2 queued); no address/link in `caplog.text`. |
| `test_email_dispatcher.py › test_enqueue_after_close_dropped` | After `aclose`, `enqueue` logs `email_dropped reason=shutdown purpose=… user_id=…`, sender not called, no worker started. |
| `test_email_dispatcher.py › test_aclose_idempotent` | Two `aclose` calls; also `aclose` with no worker ever started. |
| `test_email_dispatcher.py › test_app_lifespan_closes_dispatcher` | `create_app()`; replace `app.state.email_dispatcher` with a fake-sender dispatcher; enqueue inside `app.router.lifespan_context(app)`; after exit the mail was sent and a later `enqueue` logs `reason=shutdown`. (`ASGITransport` does not run the lifespan.) |
| `test_email_schema.py › test_email_tokens_created_at_index` | `pg_indexes` has `ix_email_tokens_created_at` on `email_tokens(created_at)`. |
| `test_cli_cleanup.py › test_cleanup_deletes_old_rows_keeps_recent` | Seeded via SQL relative to `now()`: send log 49 h (deleted), 47 h (kept); tokens: used 8 d (deleted), unused+expired 8 d (deleted), used 25 h (kept), unused 8 d with `expires_at` 30 d after creation (kept: neither used nor expired). Exact stdout lines (counts include only these rows; the test DB transaction holds nothing else). Exit 0. |
| `test_cli_cleanup.py › test_cleanup_dry_run_deletes_nothing` | Same seed; `--dry-run` prints `would delete` lines + the dry-run line; row counts unchanged. |
| `test_cli_cleanup.py › test_cleanup_batches` | `EmailRetentionService(session, batch_size=2)` with 5 old send-log rows: all deleted, result 5 (3 batches). |
| `test_cli_cleanup.py › test_purge_refuses_age_below_floor` | `purge_tokens(timedelta(hours=23), 10)` and `purge_send_log(timedelta(hours=23), 10)` raise `ValueError`; same for the count methods. |
| `test_cli_cleanup.py › test_cleanup_failure_exit_1_fixed_line` | Scope that raises (e.g. `OperationalError` carrying a DSN-like string): exit 1, stderr exactly `MSG_CLEANUP_FAILED`, no `Traceback`/`postgresql` (reuse `_assert_no_leak` pattern). |
| `test_cli_cleanup.py › test_cleanup_usage_error_64` | `cleanup --older-than 1h` → exit 64, `CLEANUP_USAGE`, the argument not echoed. |
| `test_cli_cleanup.py › test_cleanup_refuses_db_echo` | `Settings(db_echo=True)` → exit 1, `MSG_DB_ECHO`, scope never entered (`_never`). |

## Approach

### 1. S1 — lock mode (R1, R11)

1. `get_for_update`: `.with_for_update(key_share=True)` (same as `RbacRepository.get_app_for_update`). Callers stay `PasswordResetService.reset` and `EmailVerificationService.verify`; both later update only `password_hash` / `email_verified_at` (non-key), so the lock is never upgraded. `FOR NO KEY UPDATE` still conflicts with login's/forgot's `FOR SHARE` and with itself, so the USR-04b login guarantee and reset/verify serialization hold; it no longer blocks the FK `KEY SHARE` that resend's `email_tokens` INSERT takes, which removes the cycle.
2. **Concurrency tests** (`tests/test_email_lock_concurrency.py`, committed data like `test_login_concurrency.py`; import `TIMEOUT`, `_bounded`, `_wait_for_lock_waiter` from `tests.test_login_concurrency`). Fixture `email_race`: commit an app (`make_login_app`), an **unverified** user (`make_login_user(..., verified=False)`), a device + account (`make_device`), and two tokens with known raw values: a live verify token X (`expires_at = now() + 24 h`) and a live reset token R (`now() + 30 min`), stored as `email_token_hash(raw)`. App = `create_app()` with fake Turnstile and a fake-sender dispatcher override. Cleanup deletes `email_send_log` rows for the user's email HMAC, account/device sessions, the user (tokens cascade) and the app.
3. **Deterministic interleave** — monkeypatch `EmailTokenRepository.issue`:

   ```
   async def issue(self, user_id, purpose, token_hash, ttl):
       if purpose == VERIFY and not paused.is_set():
           await self.invalidate(user_id, purpose)   # resend now holds X's row lock
           paused.set()
           await release.wait()
       await real_issue(self, user_id, purpose, token_hash, ttl)   # INSERT -> FK KEY SHARE on users
   ```

   - Start resend (cookie + `X-MM-App` + Origin); `await _bounded(paused.wait())`.
   - Start the other request: reset with R (strong new password) or verify with X. It takes the user lock (`get_for_update`), then blocks on X's row (reset: `invalidate(VERIFY)`; verify: `consume(X)`). `await _bounded(_wait_for_lock_waiter())`; assert the task is not done.
   - `release.set()`; gather both, bounded.
   - Expected with `FOR NO KEY UPDATE`: resend's INSERT proceeds and commits (202). Reset: its `invalidate(VERIFY)` re-checks X (now used) and finishes → 204. Verify: `consume(X)` re-checks, 0 rows, user unverified → 422 `token_invalid`.
   - Note (READ COMMITTED): reset's `invalidate(VERIFY)` statement started before resend's INSERT committed, so it does **not** see the new verify token; that token stays unused. That is harmless: the user is now verified and `verify` is idempotent for a verified user. Do not assert the new token is superseded (see Specialist input).
   - Control: monkeypatch `UserRepository.get_for_update` to the old `with_for_update()` variant; same steps; `deadlock_timeout` (1 s default) fires; assert `500 in statuses` using `ASGITransport(app, raise_app_exceptions=False)`.
   - Do not wrap in a retry; every wait is bounded by `TIMEOUT`.

### 2. S2 — per-purpose ceilings (R5 note)

- `cap_refusal`: `share(p) = max(1, daily_cap * p // 100)`; if `total_24h >= daily_cap` → `"daily_cap"`; verify: `verify_24h >= share(VERIFY_CAP_PERCENT)` → `"purpose_cap"`; reset: `total_24h - verify_24h >= share(RESET_CAP_PERCENT)` → `"purpose_cap"`; else `None`. Count stays under the global advisory lock (unchanged).
- **Constants, not env** (decision). They are safety invariants of the split, in the same module as `VERIFY_CAP_PERCENT`, `DAILY_LIMIT`, `MIN_INTERVAL` (all constants today). The operator knob is `EMAIL_DAILY_CAP` (env, R5). Env percentages could be set so a purpose's floor is 0 (e.g. reset 100 %), which reopens S2. R5 governs deployment config and secrets; this is policy in code. Recorded in the ADR-0007 amendment.
- Logging: `email_not_sent reason=<daily_cap|purpose_cap> purpose=<verify|reset>` (+ `user_id` where known). Resend did not log before; it now logs before the 429 so the runbook sees every capped send. Response bodies are unchanged (still `rate_limited`, `Retry-After: 3600`; the reason is not exposed).

### 3. Resend route-level app context (R3 class 2)

Add `_app: AppContextDep` after `_cookie`, before `body`/`link_origin`/`device`. FastAPI caches it per request, so it runs once. Today the check already runs first only because `get_link_origin` depends on it; the explicit dependency keeps the 403 before `get_device` (which can rotate and commit the cookie) even if `get_link_origin` is overridden or changed — same pattern as signup/forgot (USR-04b).

### 4. Dispatcher shutdown + lifespan

- `enqueue`: if `self._closed`: `_log(WARNING, "email_dropped reason=shutdown", email)`; return (no worker started).
- `_run`: set `self._in_flight = 1` after `get()`, `0` in `finally`.
- `aclose(self, timeout: float = DRAIN_SECONDS)`: idempotent.
  1. Set `_closed = True`; take and clear `_worker`.
  2. If a live worker exists: `await asyncio.wait_for(self._queue.join(), timeout)`, suppressing `TimeoutError`.
  3. `dropped = self._queue.qsize() + self._in_flight`; cancel and gather the worker.
  4. Drain leftovers with `get_nowait()` + `task_done()`, so `join()` cannot hang later.
  5. If `dropped`: `logger.warning("email_dropped reason=shutdown count=%d", dropped)`. Always `logger.info("email_dispatcher_closed")`. Counts only, no addresses (R11).
- `DRAIN_SECONDS = 5.0`: below Docker's default 10 s stop grace. Uvicorn runs the lifespan shutdown after in-flight requests (and their background tasks, i.e. the enqueues) finish. A message cancelled mid-send may already have been accepted by Gmail; the user resends either way (documented, accepted).
- `main.py`:

  ```
  @asynccontextmanager
  async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
      yield
      dispatcher: EmailDispatcher = app.state.email_dispatcher
      await dispatcher.aclose()
  ```

  Reads `app.state` at shutdown, so tests can swap the dispatcher.

### 5. Retention (R1, R2, R11)

- **Migration**: `op.create_index("ix_email_tokens_created_at", "email_tokens", ["created_at"])`; downgrade `op.drop_index(...)`. Plain `CREATE INDEX`: the table is small (bounded by the cap). It also serves `count_issued_24h` (USR-04b N3).
- **`EmailRetentionRepository`** (`app/modules/auth/repository.py`):
  - `TOKEN_MIN_AGE = SEND_LOG_MIN_AGE = timedelta(hours=24)`. Each method raises `ValueError` if `older_than` is below its floor, because the daily cap counts tokens of the last 24 h and the per-email window counts send-log rows of the last 24 h (S2 follow-up 3).
  - `set_lock_timeout()`: `SET LOCAL lock_timeout = '2s'`.
  - `count_send_log(older_than) -> int` / `purge_send_log(older_than, batch) -> int`.
  - `count_tokens(older_than) -> int` / `purge_tokens(older_than, batch) -> int`.
  - `commit()` (precedent: `LoginFailureRepository.commit`).
  - Purge shape (token predicate shown; the send log uses `sent_at < now() - older_than` only):

    ```
    DELETE FROM email_tokens WHERE id IN (
      SELECT id FROM email_tokens
      WHERE created_at < now() - :older_than
        AND (used_at IS NOT NULL OR expires_at <= now())
      ORDER BY created_at LIMIT :batch
      FOR UPDATE SKIP LOCKED)
    ```

    In SQLAlchemy: `select(EmailToken.id)….limit(batch).with_for_update(skip_locked=True).scalar_subquery()` inside `delete(EmailToken).where(EmailToken.id.in_(...))`, `synchronize_session=False`; return `result.rowcount`. The `used/expired` predicate is literal to the AC and stays correct if a TTL ever exceeds 7 d. Times come from the DB clock.
- **`EmailRetentionService`** (`app/modules/auth/retention.py`):
  - `TOKEN_RETENTION = timedelta(days=7)`, `SEND_LOG_RETENTION = timedelta(hours=48)`, `BATCH_SIZE = 1000`.
  - `PurgeCount(table: str, rows: int, retention: str)`.
  - `run(*, dry_run: bool) -> list[PurgeCount]` iterates a targets tuple `(table, retention, label, count_fn, purge_fn)`: send log first, then tokens. New targets (login_failures, sessions) plug in as rows.
  - Dry run: count only, no writes.
  - Real run: loop { `set_lock_timeout()`; `n = purge(...)`; `commit()`; total += n; stop when `n < batch_size` }.
  - No locks on `users`, so no interaction with S1.
- **CLI** (`app/cli.py`): `amain` dispatches `argv[:1] == ["cleanup"]` → `_cleanup(argv[1:], settings=…, session_scope=…)`.
  - Parser: `_Parser(prog="python -m app.cli cleanup", allow_abbrev=False, add_help=False)`, only `--dry-run`. Then `_load_settings`, which refuses `DB_ECHO`.
  - Default scope `_cli_cleanup_scope(settings)`: the same engine options as `_cli_session_scope` (NullPool, `hide_parameters=True`) but **without** `session.begin()`, because the service commits per batch. Committing inside `session.begin()` would leave the context manager on a closed transaction. Tests inject `_scope(db_session)`; savepoint join mode makes `commit()` release a savepoint.
  - Fixed output, one line per table, then for dry run the last line:

    ```
    email_send_log: deleted <n> (older than 48 h)
    email_tokens: deleted <n> (used or expired, older than 7 d)
    ```

    Dry run uses `would delete` instead of `deleted`, then `dry-run: nothing was deleted`.
  - Constants:
    - `CLEANUP_USAGE = "usage: python -m app.cli cleanup [--dry-run]"`, exit 64.
    - `MSG_CLEANUP_FAILED = "error: cleanup failed; batches already committed stay deleted, run again"`, exit 1, never `str(exc)`.
    - `MSG_CONFIG` and `MSG_DB_ECHO` are reused.

### 6. Scheduling — ops workflow (no edits to the deployment repo in this ticket)

- Cleanup runs through the existing `ops-org-admin-api` workflow (manual, owner-approved `org-admin-api-ops` environment). The recommended cadence is weekly, documented in `docs/deploy.md`. Volume is bounded by the cap (≤ 300 tokens/day), so weekly is ample.
- **Deploy-side change for the orchestrator** (`mmlabs-online/deployment`, separate PR, after a release containing USR-04d is deployed):
  1. `.github/workflows/ops-org-admin-api.yml`: add `cleanup` to the `command` choice input's `options`.
  2. In the step that builds the CLI arguments: `cleanup` → `cleanup`, plus `--dry-run` when the existing `dry_run` boolean input is true (default on, as for register-app). Ignore `email`/`key`/`name`/`origins`/`permissions`/`join_policy`/`org_mode` for this command.
  3. Run it the same way as the other commands: `docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env run --rm --no-deps migrate /app/.venv/bin/python -m app.cli cleanup [--dry-run]`. It uses the same `org-admin-api-ops` environment approval and writes its output to the run summary.
  4. Update the README/command list of that workflow.
- **Owner decision (not blocking, not planned):** a scheduled run. This would be a separate workflow on `schedule:` that can run only `cleanup` (no inputs), in its own environment without a required reviewer, because the ops environment's approval gate would make a cron run wait forever. Listed as a follow-up.

### 7. Docs

- `docs/deploy.md`:
  - **"### Email alerts (runbook)"** under the Email section, as a table: event → meaning → check → action.
    - `email_not_sent reason=daily_cap` (our total cap): find the source (sign-up spike, forgot flood), consider `EMAIL_DAILY_CAP`.
    - `email_not_sent reason=purpose_cap purpose=reset|verify` (one purpose hit its share; `purpose=reset` repeatedly is a likely forgot abuse): check per-IP 429s and Turnstile.
    - `email_quota_exceeded` (Gmail 550 5.4.5, 1 h pause): see "Gmail limits".
    - `email_auth_failed` (App Password revoked or wrong, 1 h pause): rotate per "Gmail limits".
    - `email_dropped reason=paused|queue_full|shutdown`.
    - Check command: `docker compose -p org-admin-api logs --since 24h org-admin-api | grep -E 'email_(not_sent|quota_exceeded|auth_failed|dropped)'`.
    - Alert rule: any `email_quota_exceeded` or `email_auth_failed` → act the same day; `daily_cap`/`purpose_cap` more than a few per day → investigate.
    - Wiring these patterns into a log-alerting tool is a follow-up (no log aggregation exists today).
  - **"## Email retention cleanup (USR-04d)"**:
    - What is deleted and why the floors exist.
    - Ops workflow steps (command `cleanup`, `dry_run` first) once the deployment PR lands, and the fallback shell command (same pattern as grant-super-admin).
    - Output, and exit codes 0 / 1 / 64; a re-run after a failure is safe.
    - Weekly cadence; the scheduled-run owner decision.
  - One-time setup step 6: the workflow's commands are `register-app`, `grant-super-admin`, `cleanup`.
  - Email section: "On a clean stop the API drains queued mail for up to 5 s, then logs `email_dropped reason=shutdown count=N`."
- ADR-0007: add an "*Amended by USR-04d (2026-11-13)*" paragraph covering:
  - the reset ≤ 50 % ceiling (constants), giving verify ≥ 50 % and reset ≥ 20 %;
  - the `purpose_cap` vs `daily_cap` reasons;
  - the 5 s drain;
  - retention (48 h / 7 d, 24 h floors).

  Replace the Consequences line "Retention cleanup … is a follow-up (USR-04d)" with a pointer to `docs/deploy.md`.

### Rules

- R1: CLI → service → repository.
- R2: one hand-reviewed migration with downgrade, model updated.
- R3: resend stays cookie class with the app context first.
- R4: n/a (no admin mutation).
- R5: no new env keys.
- R6: unchanged envelopes.
- R7: login_failures/session cleanup are follow-ups.
- R8: tests above.
- R11: logs and CLI output carry counts, event names and user ids only.

## Specialist input used

| Source | Item | Decision |
|---|---|---|
| identity-access-engineer | S1 one-line `key_share=True`, fix docstrings | Adopted. |
| identity-access-engineer | Deterministic interleave by patching `issue`, reuse `_bounded` / `_wait_for_lock_waiter` | Adopted; added permanent control tests with the old lock instead of a one-off manual check only. |
| identity-access-engineer | Resend vs reset: "new verify token superseded afterwards" | **Rejected.** Under READ COMMITTED, reset's `invalidate(VERIFY)` began before resend's INSERT committed, so it cannot see the new row; the test asserts the user is verified and the request outcomes instead. Harmless (verify is idempotent for verified users). |
| identity-access-engineer | `RESET_CAP_PERCENT = 50`, floors, `purpose_cap` reason | Adopted; constants (open question answered in Approach §2). |
| identity-access-engineer | Route-level `AppContextDep` before `DeviceDep`, no-`Set-Cookie` test | Adopted. |
| identity-access-engineer | `closed` flag, bounded ~5 s drain, count-only log, lifespan test via `lifespan_context` | Adopted; 5 s vs 10 s Docker grace (open question answered). |
| database-optimizer | One index `ix_email_tokens_created_at`, `down_revision 91a78d0496f9`, plain CREATE INDEX | Adopted. |
| database-optimizer | Batched `DELETE … IN (SELECT … LIMIT … FOR UPDATE SKIP LOCKED)`, commit per batch, `lock_timeout 2s`, DB clock, floors | Adopted; kept the literal used-or-expired predicate as defence in depth. |
| database-optimizer | CLI `cleanup [--dry-run]`, fixed output, exit 1 | Adopted. |
| database-optimizer | Daily cron in its own environment | Not planned: owner decision (scheduled run bypasses the ops approval gate); documented as an option and follow-up. |
| database-optimizer | Test "25 h-old used token survives" | Adopted (AC 8 test). |
| database-optimizer / both | login_failures and session cleanup | Follow-ups (R7). |

## Assumptions

- **A1** Deleting tokens older than 7 d means a very old verify link of an already-verified user answers 422 instead of the idempotent 204. Acceptable: such links expired after 24 h anyway.
- **A2** Tests run serially against the test DB (as `test_login_concurrency.py` already assumes); `_wait_for_lock_waiter` counts any lock waiter in the database.
- **A3** The test Postgres uses the default `deadlock_timeout` (1 s), so the control tests finish well inside `TIMEOUT` (10 s).
- **A4** The orchestrator makes the deployment-repo change (Approach §6); until it lands, cleanup runs via the documented shell fallback.
- **A5** Follow-ups for the backlog (not in this diff):
  1. `login_failures` cleanup (rows with `last_failed_at` older than 24 h and not blocked; ADR-0002).
  2. Device/account session cleanup (needs a decision: `account_sessions.id` is the app-held `account_id`, ADR-0004).
  3. Scheduled cleanup job (owner decision).
  4. Log-based alerting for the runbook events.
  5. Optional `stop_grace_period` / `--timeout-graceful-shutdown` tuning in the deploy compose.
  6. `TokenRepository.invalidate` touching old expired rows (database-optimizer note; harmless with SKIP LOCKED).

## Blocking questions

None

## Split proposal

None

## Data model / migration

- Table `email_tokens`: new index `ix_email_tokens_created_at` on (`created_at`), non-unique, non-partial. Declared in `EmailToken.__table_args__`.
- Migration `migrations/versions/20261113_<rev>_add_email_tokens_created_at_index.py`, generated with `make migration m="add email_tokens created_at index"` and hand-edited:
  - `down_revision = "91a78d0496f9"`.
  - `upgrade()`: `op.create_index("ix_email_tokens_created_at", "email_tokens", ["created_at"], unique=False)`.
  - `downgrade()`: `op.drop_index("ix_email_tokens_created_at", table_name="email_tokens")`.
  - Docstring: expand-only, small table, supports the cap count and retention.
- No column or constraint changes. `alembic check` must report no drift.

## API contract

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/verify-email/resend` | unchanged (`account_id`; cookie `mm_device`, `Origin`, `X-MM-App`) | unchanged (202, empty) | unchanged: 202, 401, 403, 409, 422, 429. The 403 for app context is now guaranteed before device resolution/rotation. | Cookie class (R3 class 2); no admin permission |

No other endpoint changes. The 429 bodies for capped resend are unchanged (the cap reason only goes to logs).
