# Security review — USR-04d

## Attempt 1

**Verdict: no 🔴 blockers and no 🟡 should-fix findings.** There are 4 💭 notes and 3 follow-ups. All five security goals hold: the deadlock fix, the cap split, the cleanup floors, no data leaks in CLI output or logs, and input validation in the ops workflow.

I reviewed `git diff cd266d9 HEAD` and the deployment branch `org-admin-api-ops-cleanup` (mmlabs-online/deployment#58). I ran `uv run pytest tests/test_cli_cleanup.py tests/test_email_throttle.py -q`: 36 passed. I did not run the full suite.

### 🟢 Verified

- **Lock semantics** (`app/modules/users/repository.py:21-33`)
  - `FOR NO KEY UPDATE` (`key_share=True`) conflicts with `FOR SHARE`. Login (`app/modules/auth/service.py:162`) and forgot (`app/modules/auth/password_reset.py:76`) use `FOR SHARE`, so they still serialize with reset and verify. The test `test_login_blocked_by_reset_lock_reads_new_password` still covers the login-vs-reset ordering.
  - Reset vs verify: both take the same `NO KEY UPDATE` lock on the user row before touching tokens (`password_reset.py:100`, `email_verification.py:102`), so they still serialize.
  - The deadlock is gone. Resend holds `email_tokens` row locks (from `invalidate`), then its INSERT takes the FK `KEY SHARE` on the user row, which `NO KEY UPDATE` does not block.
  - The "never upgraded" claim holds. Reset and verify only write `password_hash` and `email_verified_at`. The only unique key is `email` (`users/models.py:22`), and they never change it, so the lock never escalates to `FOR UPDATE`.
  - `tests/test_email_lock_concurrency.py:152,169,192` covers resend vs reset and resend vs verify. It also has a control test proving that plain `FOR UPDATE` deadlocks.
- **Cap math** (`app/modules/auth/email_throttle.py:32-49`)
  - The total cap is checked first. Each share has a floor of `max(1, …)`, and `email_daily_cap` has `ge=1` (`app/core/config.py:84`).
  - Reset usage is computed as `total - verify`. That is exact, because the CHECK constraint allows only the purposes `verify` and `reset` (`app/modules/auth/models.py:50`).
  - Guaranteed shares: verify keeps at least cap − ⌊cap/2⌋ (≥ 50 %); reset keeps at least cap − ⌊0.8·cap⌋ (≥ 20 %, and ≥ 1 once cap ≥ 2). Neither purpose can starve the other.
  - The check and the count still run under the global advisory lock (`email_verification.py:58-60`), so there is no race that bypasses the cap.
  - Forgot stays silent on `purpose_cap` (`password_reset.py:70-73`), so there is no account enumeration. The resend 429 body does not reveal `reason` (it is in logs only).
- **Retention** (`app/modules/auth/repository.py:243-314`, `app/modules/auth/retention.py`)
  - Tokens are deleted only when `created_at < now() - 7 d` AND (`used_at IS NOT NULL` OR `expires_at <= now()`). Unused, unexpired tokens are never deleted. The longest TTL is 24 h, so any unused token older than 7 days has already expired.
  - The 24 h floors are enforced in the repository (`_check`, lines 260-262) and tested (`tests/test_cli_cleanup.py:168`). Ages use the DB clock, the same clock as `count_issued_24h`, so tokens inside the 24 h cap window can never be deleted.
  - The send log is kept 48 h, more than the 24 h per-email window.
  - Batches use `SKIP LOCKED`, `SET LOCAL lock_timeout = '2s'` and one commit per batch, and take no lock on `users`.
- **CLI output** (`app/cli.py:355-388`)
  - It prints only table names, integer counts and fixed labels.
  - Every exception becomes one fixed line, with no `str(exc)`. The engine uses `hide_parameters=True`, and `db_echo` is refused.
  - The `--dry-run` path only counts and writes nothing.
- **Dispatcher shutdown** (`app/modules/email/dispatcher.py:68-87`, `app/main.py:30-35`)
  - Shutdown drains the queue for at most 5 s, then drops what is left. The log line carries only a count (`email_dropped reason=shutdown count=N`).
  - After close, each dropped message is logged with purpose and user id only (R11 is respected).
  - Leftover queue items are cleared so `join()` cannot hang, and calling `aclose()` twice is safe.
  - Dropped mail is safe: the token stays unused in the DB until its TTL, and the user presses resend.
- **Route `AppContextDep`** (`app/modules/auth/router.py:230`)
  - It is declared before `DeviceDep`, so a bad `X-MM-App` or `Origin` gets a 403 before any device lookup or cookie rotation.
  - FastAPI caches the dependency, so `get_link_origin` reuses the same result.
- **Ops workflow** (`deployment/.github/workflows/ops-org-admin-api.yml`, cleanup branch)
  - It rejects any non-empty key, name, origins, permissions or email, and any join_policy or org_mode other than `keep`.
  - The only argument it can add is the fixed `--dry-run`. Arguments go through a NUL-separated file, and inputs are never interpolated into `run:`.
  - `dry_run` defaults to true. The `org-admin-api-ops` environment approval gate and the `main`-only guard still apply.
  - The `[ "$DRY_RUN" = true ] && add --dry-run` line under `bash -e` follows the same pattern already used for register-app and is safe.
- **Migration**: the index migration only adds `ix_email_tokens_created_at` and has a working `downgrade()`.

### 💭 Notes (no change required)

1. **The shutdown "dropped" count can be wrong for one message** (`app/modules/email/dispatcher.py:78-80`). `SmtpSender.send` uses `asyncio.to_thread` (`app/modules/email/sender.py:102`). Cancelling the worker does not stop that thread, so the in-flight message may still be sent even though it is counted as dropped. This does not affect security. A short docstring note is enough if you want the log to be exact.
2. **The 5 s drain is not bounded end to end.** uvicorn runs with no `--timeout-graceful-shutdown` (`Dockerfile:18`), and compose sets no `stop_grace_period`. Lifespan shutdown starts only after open requests finish. If that takes more than about 5 s, Docker's 10 s SIGKILL can arrive before the `email_dropped` line is written, and mail is lost without a log. That outcome is still safe (resend), but the alert signal disappears. See follow-up 1.
3. **`EmailRetentionService` with `batch_size=0` would loop forever** (`app/modules/auth/retention.py:66-72`): `deleted < 0` is never true. The CLI cannot reach this because `BATCH_SIZE` is a constant. Optional: add a guard in `__init__`.
4. **With `EMAIL_DAILY_CAP=1`, both shares are 1**, so whichever purpose sends first takes the whole day. Only that degenerate setting is affected; from cap 2 upward each purpose is guaranteed at least 1. Optional: document it, or raise the config floor to `ge=2`.
5. **Deployment PR wording**: the `dry_run` description says "then roll back", but the cleanup dry run only counts. This is cosmetic.

### Follow-ups (out of scope)

1. Add `--timeout-graceful-shutdown 3` to the uvicorn CMD, or `stop_grace_period: 15s` in `deployment/apps/org-admin-api/compose.yml`, so the request drain plus the 5 s mail drain fits before SIGKILL.
2. Schedule the cleanup (for example a weekly `schedule:` trigger, or a reminder) instead of running it only by manual dispatch. Without it, `email_send_log` keeps growing (HMACs only, low sensitivity).
3. Pre-existing, not from this diff: forgot holds the global advisory lock while waiting for the user's `FOR SHARE` lock (`password_reset.py:70-76`). A concurrent reset of that user briefly blocks every send. The window is small because the password is hashed before the lock is taken. Consider looking up the user before taking `lock_global` in a later ticket.
