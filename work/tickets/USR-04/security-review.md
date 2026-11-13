# Security review — USR-04

## Attempt 1

**Scope:** `git diff origin/main...HEAD` (app/, migrations/, .env.example, config). Focused tests run: `uv run pytest tests/test_email_sender.py tests/test_email_dispatcher.py -q`, 25 passed. I did not run the full suite (the reviewer runs it).

**Verdict:** no 🔴 blockers. Two 🟡 should-fix items and several 🟢 notes.

### Findings

🟡 **S1: Resend has no per-IP or per-device rate limit, and the global daily cap is easy to use up, which blocks verification for everyone.**
- Where: `app/modules/auth/router.py:212-237` (`/verify-email/resend` has no `dependencies=[rate_limit(...)]`), and `app/modules/auth/email_verification.py:61-62` (global `EMAIL_DAILY_CAP`, default 300 in `app/core/config.py`).
- Risk: one IP can sign up 20 times a minute (`signup_rate_limit=20`), and each sign-up counts one send. With paid Turnstile solves, the 300 sends per 24 h run out in about 15 minutes. Resend adds 5 sends per account per day with no per-IP control. Once the cap is reached:
  - every new user gets 201 with no email;
  - every resend returns 429 for an hour, on a rolling 24 h window;
  - after USR-04b, password-reset mail is blocked too, because both purposes share the same cap (`count_global_24h` counts every purpose).
- Fix:
  - (a) Add `dependencies=[rate_limit("verify_email_resend")]` to the resend route. It needs a new scope plus config keys, e.g. 5 per 10 min per IP.
  - (b) Count the cap per purpose, or reserve part of it for `reset`, e.g. `verify` may use at most 80 % of `EMAIL_DAILY_CAP`. Sign-up spam then cannot block account recovery. This can go to USR-04b if the owner prefers; the cap's shape is decided now, though.
  - (c) The `docs/deploy.md` runbook should say to alert on `email_not_sent reason=daily_cap`.

🟡 **S2: The global cap check is not serialized, so concurrent sends can go past it.**
- Where: `app/modules/auth/email_verification.py:56-63` and `app/modules/auth/repository.py:163-203`.
- Detail: the advisory lock is per email and purpose. `count_global_24h()` followed by `insert()` is a check-then-act across different emails. N concurrent sign-ups or resends near the cap can each read `count < cap`, so the cap can be exceeded by up to the number of concurrent requests.
- Risk: low. The cap exists to protect the Gmail quota (500/day), so a small overshoot is tolerable.
- Fix: before the global count, take a second transaction-scoped lock, e.g. `pg_advisory_xact_lock(hashtextextended('email_send_log:global', 0))`. Or accept the overshoot and document it next to the cap. If accepted, downgrade this to 🟢.

🟢 **N1: Token handling is sound.**
- `secrets.token_urlsafe(32)` gives 256 bits (`email_verification.py:65`).
- Only SHA-256 of the token is stored (`:31-33`). The column is unique, and a CHECK enforces a 32-byte hash (migration).
- Single use: `consume` is an atomic `UPDATE … WHERE used_at IS NULL AND expires_at > now() RETURNING` (`repository.py:126-140`).
- Concurrent verifies are serialized by `get_for_update` on the user row (`email_verification.py:78`). The loser either sees `email_verified_at` and returns 204, or gets 422.
- A new token supersedes older ones (`issue` → `invalidate`, `repository.py:99-124`), and all tokens are invalidated after a successful verify (`:87`).
- Unknown, expired, used and superseded tokens all get the same `token_invalid` 422.
- `__repr__` excludes the hash.

🟢 **N2: Link construction is safe.**
- The origin comes only from `normalize_origin(Origin)` after `AppContextService.check` matched it against the `X-MM-App` app's `allowed_origins`, with loopback filtered out in staging/production (`deps.py:84-102`, `service.py:80-94`), or from `EMAIL_LINK_BASE_URL`.
- `EMAIL_LINK_BASE_URL` must be a normalized mmlabs https origin, and loopback is refused in staging/production (`config.py` `_email_gate`).
- `Host` and `X-Forwarded-Host` are never used.
- The token is in the fragment (`#token=`), so it does not reach server logs or Referer, and the POST-to-verify design means mail scanners that follow links do not consume it.
- An invalid `X-MM-App` or `Origin` on sign-up returns 403 before any write.

🟢 **N3: No header injection.**
- `EmailMessage()` uses `policy.default`, which refuses CR/LF in header values (`sender.py:98-102`).
- `To` is a validated `EmailStr` and `Subject` is a constant.
- `EMAIL_FROM` comes from env and goes through `formataddr`.

🟢 **N4: TLS checks are correct.**
- `starttls(context=ssl.create_default_context())` enforces `CERT_REQUIRED` and `check_hostname`, and smtplib passes `server_hostname=self._host` (`sender.py:110`).
- If STARTTLS is not offered, `SMTPNotSupportedError` is classified as permanent. There is no plaintext fallback, and login happens only after STARTTLS.
- The `python:3.12-slim` base image ships `ca-certificates`.

🟢 **N5: Exceptions and logs carry no secrets.**
- The SMTP error is mapped inside `except`, then raised outside it, so there is no `__context__` and no reply text, which would echo the recipient (`sender.py:114-117`).
- `EmailDeliveryError` carries only the kind and the numeric code.
- The dispatcher logs event, purpose, user_id, attempt and code only. Unexpected errors are logged by type name only (`dispatcher.py:70-72, 101-102`).
- `OutgoingEmail` fields `to`, `subject` and `body` are `repr=False`.
- `DisabledEmailSender` logs no link.
- `SecretStr` is used for the password.

🟢 **N6: Responses do not enable account enumeration.**
- `verify-email` returns 204 or 422, keyed only on a 256-bit token, and is limited per IP (20/min).
- `resend` is cookie class. The `account_id` must belong to the presented device (`get_active_account(device_id, …)`, `email_verification.py:93-97`), otherwise 401. Its 409 `email_already_verified` is visible only to the account owner.
- Sign-up already returns 409 for a taken email (existing behaviour). The hook only runs after the insert succeeds, so sign-up cannot be used to send mail to existing accounts.

🟢 **N7: Disabled users are handled.** `verify` returns 422 when the user is not ACTIVE (`:79-80`), and `resend` returns 401 (`:99-100`).

🟢 **N8: Mail is sent only after commit.**
- The session dependency is function-scoped, so the commit happens before the response.
- If the commit fails, the error handler builds a new response without the background tasks, so no mail goes out for rolled-back tokens or sign-ups.
- The send log and token are written in the same transaction as the sign-up or resend.

🟢 **N9: Dispatcher memory growth is bounded.**
- The queue has `maxsize=1000` and drops with a log line when full.
- Only one sequential worker runs, and the per-message work is bounded (3 attempts × 15 s socket timeout + backoff).
- Enqueues happen only after the DB-counted cap, so at most ~`EMAIL_DAILY_CAP` messages per day reach memory.
- Two minor gaps:
  - `aclose()` is never wired into app shutdown (`app/main.py`), so in-flight mail is lost on restart. This is accepted in ADR-0007, but wire it in a lifespan for a clean shutdown.
  - The 15 s timeout applies per socket operation, so a slow server that keeps trickling data can hold the single worker longer.

🟢 **N10: Config refusal works.**
- In staging/production, `SMTP_USERNAME`, `SMTP_PASSWORD` and `EMAIL_LINK_BASE_URL` are required.
- `env_ignore_empty=True` means the empty keys in `.env.example` do not break local runs.
- `build_email_sender` falls back to `DisabledEmailSender` only when the keys are missing, which production refuses.

🟢 **N11: The migration is safe.**
- It only creates new tables, and `downgrade()` drops them in the right order.
- The FK uses `ON DELETE CASCADE`.
- Minor: the only index whose first column is `user_id` is partial (`WHERE used_at IS NULL`). A user delete therefore scans used tokens on cascade. This is a performance issue, not a security one.

### Follow-ups (out of scope)
1. **USR-04b (reset):** a password reset must revoke all device sessions and accounts of the user. The reason: an attacker can sign up with a victim's address, and the victim may click "verify" by mistake. The attacker then holds a verified account under the victim's email, and the victim's recovery path is reset, so reset has to evict the attacker. The reset share of the daily cap is covered under S1(b).
2. Retention job for `email_send_log` (rows older than 24 h are useless) and `email_tokens` (used or expired). Both tables currently grow without limit.
3. Wire `EmailDispatcher.aclose()` into the app lifespan.
4. Lower or extend the per-IP sign-up limit: 20/min is generous given that sign-up sends mail. Consider adding an hourly or daily per-IP window.
5. Monitoring and alerting on `email_quota_exceeded`, `email_auth_failed` and `email_not_sent reason=daily_cap`.
