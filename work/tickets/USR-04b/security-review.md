# Security review — USR-04b

## Attempt 1

**Verdict: PASS with should-fixes.** I found no blockers. The diff reviewed is `d728304` (USR-04b). `git diff origin/main...HEAD` also contains the already-merged USR-07 commits, because the local `origin/main` is stale; I did not review those again. `uv run pytest tests/test_password_reset_api.py -q` → 48 passed.

### Findings

🟡 **S1 — Deadlock between reset and a concurrent verification resend (also already present in `verify()`)**
- **Where:** `app/modules/users/repository.py:21-27` (`get_for_update` uses plain `FOR UPDATE`), used by `app/modules/auth/password_reset.py:99`.
- **How it happens:**
  - Reset takes `FOR UPDATE` on the user row. Later, at `password_reset.py:106`, it calls `tokens.invalidate(user.id, VERIFY)`, which needs the user's unused verify-token rows.
  - Resend (`EmailVerificationService.prepare` → `tokens.issue`) takes those same verify-token rows first, with no user lock (`email_verification.py:111`, `users.get`). It then inserts a new `email_tokens` row. That insert's foreign-key check needs `FOR KEY SHARE` on the user row, and `FOR UPDATE` blocks `FOR KEY SHARE`.
  - So: resend holds the token rows and waits for the user row; reset holds the user row and waits for the token rows. Postgres aborts one of them after `deadlock_timeout`, and the client gets a 500.
- **When it can happen:** an unverified user with a live verify token runs reset while another tab or app calls resend. It is rare and the user causes it themselves, but it is real. `EmailVerificationService.verify()` (`email_verification.py:95-102`) has the same pattern against resend and predates this ticket.
- **Fix:** lock with `FOR NO KEY UPDATE`, i.e. `.with_for_update(key_share=True)` in `get_for_update`, the same as `RbacRepository.get_app_for_update`.
  - `password_hash` and `email_verified_at` are not key columns, so this is enough.
  - The lock still conflicts with login's `FOR SHARE`, so the login-race guarantee below is kept.
  - Foreign-key inserts no longer block, so the cycle disappears.

🟡 **S2 — The cap split protects reset from verify, but nothing protects verify from reset**
- **Where:** `app/modules/auth/email_throttle.py:31-41`.
- **Problem:** reset may use the whole `EMAIL_DAILY_CAP`. The per-email limit is 5/day for any registered address, so about 60 known addresses are enough to use up the default 300/day through forgot. That blocks every verification email: new users cannot verify, and so cannot join apps. Victims' addresses work for this; the attacker does not need to own them, and the victims also each get 5 unwanted emails a day.
- **Context:** the ticket only asked that verification not starve reset, so this is not a regression; before, the cap was simply shared.
- **Fix:** give each purpose a ceiling, e.g. verify ≤ 80 % and reset ≤ 50 %, so each always has a floor. Or move this to USR-04d along with the daily-cap alert runbook.

💭 **N1 — A timing difference between known and unknown emails remains on forgot** (`password_reset.py:75-86`)
- Responses are identical (always 202), and so are the limit and send-log writes. The known-user branch adds one UPDATE and one INSERT on `email_tokens` and builds the token, roughly 1–2 ms.
- The email is sent after the response (BackgroundTasks run after the commit and after `response.start`), so it adds no delay.
- This is in line with the ticket's "limit timing differences". No action needed. Optional: pad the unknown branch with a dummy `email_tokens` lookup.

💭 **N2 — A theoretical way to tell whether an email is registered, through resend's 429 at the cap**
- Forgot to a known email issues a token, so it counts toward the cap; forgot to an unknown email does not.
- An attacker who pushes the global count to cap−1 could probe one victim email, then see whether their own resend returns `429 daily_cap`.
- This needs about 300 emails/day of their own traffic and dozens of accounts that pass Turnstile, and it reveals one answer per day. It is not practical. Noted for the threat model only.

💭 **N3 — The global cap lock and count run for every accepted forgot, including unknown emails** (`password_reset.py:69-73`)
- The `email_daily_cap` advisory lock serializes all verify and reset sends. While forgot holds it, it also waits on `FOR SHARE` for the user row; reset holds that row only after hashing, so the wait is in milliseconds.
- `count_issued_24h` has no index on `email_tokens.created_at` and does a sequential scan. That is fine today: the table grows only with real sends, bounded by the cap.
- Once the cap is reached, `email_not_sent reason=daily_cap purpose=reset` is logged for unknown emails as well. That is slightly noisy but contains no personal data, and the per-IP limit plus Turnstile bound it.

### Checked and fine
- **Enumeration:** forgot always returns 202, after the app-context and Turnstile checks. The silent per-email limit is applied before the user lookup, keyed by `email_hmac` for every email. The send-log row is written on every accepted request. Disabled and unknown users get no email.
  - A 403 (missing app/origin, or Turnstile failure) and a per-IP 429 do not depend on the email.
  - Reset returns the same 422 `token_invalid` for an unknown, expired, used or superseded token and for a disabled user.
- **Tokens:**
  - `secrets.token_urlsafe(32)` (256 bits); only SHA-256 is stored, the TTL is 30 min, and the token is in the URL `#` fragment, so it is not sent to servers or in Referer.
  - The link origin is the validated, normalized `Origin` registered for the `X-MM-App` app (`deps.py:84`), so the Host header cannot change it.
  - The email goes to the stored `user.email`, not the address in the request.
  - The atomic consume uses `UPDATE … WHERE used_at IS NULL AND expires_at > now() RETURNING`. Two concurrent resets with one token: one wins and the other gets 422. A weak password is checked before consume, so the token stays usable.
- **Reset atomicity and lock order:**
  - Order: lookup → policy check → Argon2 hash (no locks held) → user lock → consume → set password → revoke sessions → supersede reset and verify tokens → delete the login-failures row → set `email_verified_at`, all in the request transaction. Any error rolls the whole thing back.
  - Against login, forgot, verify, logout and device revocation I found no lock cycle, apart from S1. Logout and device revocation overlap with reset on at most one account-session row, so they cannot form a two-way cycle.
- **Session revocation vs. login race:**
  - Login reads the user `FOR SHARE` after its separate reservation commit and keeps that lock through the Argon2 check and `upsert_account` until the commit.
  - If reset locks first, login waits and then reads the new hash (READ COMMITTED returns the updated row version), so the old password gets 401.
  - If login locks first, reset waits until login commits; its revoke UPDATE then sees the committed new account session and revokes it.
  - No other code path inserts account sessions. Access tokens already issued stay valid for at most 15 min, as documented.
  - `test_login_blocked_by_reset_lock_reads_new_password` covers the first order.
- **Cap bypass:**
  - The cap now counts tokens actually issued, under a global advisory lock, so concurrent requests cannot all pass the check.
  - Unknown emails cannot use up the cap.
  - Lock order is per-email advisory lock → global advisory lock in both purposes, with different per-purpose keys, so they cannot deadlock each other.
  - Sign-up holds only its own new user row and nothing forgot waits on.
- **Logging:** only `user_id` and event names are logged (`password_reset_requested`, `password_reset`, the dispatcher's `purpose`/`user_id`). No token, address or password.
  - The 422 handler drops `input`/`ctx`. `new_password` is a `SecretStr`, and the max-length error does not echo the value.
- **Error format and CORS:**
  - Errors use the standard error envelope (R6) and `Cache-Control: no-store` is set.
  - Both routes are under `/api/v1/auth/*`, so `DynamicCORSMiddleware` covers them, with the allow-origin limited to `apps.origins`.
  - Forgot needs a preflight (because of `X-MM-App`), which is already allowed.
  - Reset is public (R3 class 3); the token is the credential, so CSRF does not apply.
- **Route-level `AppContextDep`:** it is now on signup and forgot directly. The override tests prove the 403 still happens when the email hook or link-origin dependency is replaced.
- **New per-IP scopes** `verify_email_resend`, `forgot` and `reset` are wired up with `ge=1` settings, and `.env.example` lists them without values (R5).

### Follow-ups (out of scope)
1. Apply the S1 `FOR NO KEY UPDATE` fix to `verify()` as well. It shares `get_for_update`, so the one-line fix covers both; add a concurrency test for resend vs. reset/verify.
2. S2: a per-purpose ceiling for reset, or a verify floor (USR-04d).
3. USR-04d retention cleanup must keep `email_tokens` rows for at least 24 h, because the cap now counts tokens. Deleting a user also deletes their tokens (cascade), which lowers the count; acceptable while v1 has no self-service account deletion.
4. Send a "your password was changed" notice after a successful reset, to warn the real owner if the reset was malicious (spec decision).
5. Add an index on `email_tokens(created_at)` if the table grows past the 24 h window (with USR-04d retention).
6. The per-IP limiter is in-memory and per-process, so limits multiply with the number of workers (existing behaviour, not from this ticket).
