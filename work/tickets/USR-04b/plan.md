Status: READY

# Plan — USR-04b: Password forgot/reset with all-session revocation

This plan covers forgot and reset, plus the must-have follow-ups that the three consults agree on:

- a route-level `AppContextDep` on signup and forgot;
- per-IP scopes `forgot`, `reset` and `verify_email_resend`;
- a per-purpose daily cap, so verification mail cannot starve reset mail;
- unknown-email forgot rows no longer use up the cap.

Three items move to the new ticket **USR-04d** (see `## Split proposal`): dispatcher shutdown, retention cleanup and the daily-cap alert runbook.

Estimated size: about 330 production lines, about 550 test lines and about 80 doc lines. No migration.

**Deviation recorded (owner note 2026-11-13 overrides an AC).** AC 1 says forgot takes an "optional `X-MM-App` (A1 rule) for the link origin". The owner note of 2026-11-13 says instead: "forgot requires `X-MM-App` + Origin like signup; the reset link origin is the request's validated Origin; there is no `EMAIL_LINK_BASE_URL` fallback". This plan follows the owner note:

- forgot without a valid app context answers 403 `app_header_required` or `origin_not_allowed`;
- the 403 comes before Turnstile, before any write and before any mail;
- the link origin is the validated `Origin`.

All three consults flagged this conflict.

## Spec summary

spec-v1.md §5.8:

> **Email flows:** email verification and password reset by one-time, expiring links, sent through
> Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3).

> **Abuse protection:** no hard lockout; … the same error for unknown email and wrong password (D11).

identity-architecture.md §4:

> `POST /api/v1/auth/password/forgot` `{email, turnstile_token}` → always 202 | public | send reset link
> `POST /api/v1/auth/password/reset` `{token, new_password}` | public | set password, revoke all sessions of the user

identity-architecture.md §5 S6: "verification/reset resend at most 1/min and 5/day per email; tokens
single-use, expiring, stored hashed."

CLAUDE.md R3 class 3 (public): "`/api/v1/auth` signup, login, verify-email, password forgot/reset
(rate-limited, Turnstile where required)". R11: "reset and verification tokens stored only as hashes
… never log passwords, tokens or full emails in errors … identical error for unknown email and wrong
password."

ADR-0007 §1 (link `{origin}/reset-password#token=…`), §2 as amended by USR-04c (link origin = validated
`Origin`, no fallback), §3 (reset TTL 30 min), §4 (send limits, global cap).

Ticket notes (owner, 2026-11-12, USR-04 security review S1/S2): "add a per-IP rate-limit scope on
`/auth/verify-email/resend`; split or reserve the global `EMAIL_DAILY_CAP` per purpose so sign-up spam
cannot block reset mail (e.g. verify ≤ 80 %); optionally serialize the global count with a global
advisory lock". Owner, 2026-11-13: "put `AppContextDep` on the route itself (signup and forgot)".

**Conflicts flagged:**

| Conflict | Decision |
|---|---|
| AC 1 "optional `X-MM-App`" vs owner note 2026-11-13 "required" | The owner note wins (deviation above). |
| identity-architecture §4 lists forgot as plain "public" | The doc row gets the same "+ Origin/`X-MM-App` check" note that signup has. |
| Ticket "→ 429 beyond" for forgot vs §4 "always 202" | Already decided in USR-04: the per-email limit is silent. |
| `docs/deploy.md` says reset "revokes every device session" | Per the AC, reset revokes account sessions only. The text is corrected. |
| CLAUDE.md R3 class 3 does not mention app context for signup/forgot | Not changed here (R7); follow-up F1 below. |

## Acceptance criteria → verification

Tests live in `tests/test_password_reset_api.py` unless named otherwise.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `POST /auth/password/forgot {email, turnstile_token}` → **202**, empty body, `Cache-Control: no-store`, for known, unknown and disabled emails and for emails that hit a limit | `test_forgot_known_user_202_sends_reset_link`, `test_forgot_unknown_email_202_no_mail`, `test_forgot_disabled_user_202_no_mail`, `test_forgot_per_email_limit_silent_202` |
| 2 | Forgot **requires** app context (owner 2026-11-13). A missing or bad `X-MM-App` → 403 `app_header_required`; a bad, missing or unregistered `Origin` → 403 `origin_not_allowed`. Both come before Turnstile, any write and any mail. The link origin is the validated `Origin`. | `test_forgot_without_app_header_403_no_side_effects`, `test_forgot_unregistered_origin_403_no_side_effects` (no Turnstile call, 0 `email_send_log` rows, no mail), `test_forgot_link_uses_request_origin` |
| 3 | Turnstile is always required. Failure → 403 `turnstile_failed`, with no writes. | `test_forgot_turnstile_failed_403_no_writes` |
| 4 | Per-IP scope `forgot` (default 5/60 s) → 429 with `Retry-After` | `test_forgot_per_ip_429` (`make_client(forgot_rate_limit=1)`) |
| 5 | The link is `{origin}/reset-password#token=<token>`. The TTL is 30 min. Only the SHA-256 is stored, with purpose `reset`. A new link supersedes the user's older unused reset links. | `test_forgot_known_user_202_sends_reset_link` (regex, the `email_tokens` row has `expires_at - created_at = 30 min`), `test_forgot_new_link_supersedes_previous` |
| 6 | Per-email limit of 1/min and 5/24 h on purpose `reset`, applied silently to **every** email (known or unknown). Unknown and disabled users get no mail. Both branches make the same `email_send_log` write. | `test_forgot_per_email_limit_silent_202`, `test_forgot_daily_per_email_limit_silent_202`, `test_forgot_limit_applies_to_unknown_email` (one `email_send_log` row per accepted attempt, known or unknown) |
| 7 | **Per-purpose daily cap.** The cap counts real sends, i.e. `email_tokens` issued in the last 24 h. Verify is allowed while `verify_24h < max(1, cap·80/100)` and `total_24h < cap`. Reset is allowed while `total_24h < cap`. Unknown-email forgot rows never count. The count runs under one global advisory lock. A capped forgot still answers 202 and logs `email_not_sent reason=daily_cap purpose=reset`. | `test_email_throttle.py::test_daily_cap_allows` (table), `test_verify_share_exhausted_reset_still_sent` (cap 5: 4 sign-ups mailed, the 5th not, then forgot mailed), `test_unknown_email_forgot_does_not_consume_daily_cap` (cap 1), `test_forgot_daily_cap_reached_202_logged`; the existing `test_email_verification_api.py::test_daily_cap_resend_429_and_signup_still_201` stays green unchanged |
| 8 | `POST /auth/password/reset {token, new_password}` is public with per-IP scope `reset` (default 10/60 s). On success: 204, no `Set-Cookie`, no login. The new password works and the old one gets 401. | `test_reset_happy_path_204_no_cookie_new_password_works`, `test_reset_per_ip_429` |
| 9 | Reset revokes **all** non-revoked `account_sessions` of the user on every device. Device sessions and other users' account sessions on the same device stay alive. `/auth/token` → 401 for every device of the user. | `test_reset_revokes_all_account_sessions_every_device` |
| 10 | A weak password → 422 `weak_password`, and the token stays usable | `test_reset_weak_password_422_token_still_usable` |
| 11 | Unknown, expired, used or superseded tokens, a verify-purpose token, or a disabled user → 422 `token_invalid` (one body). A disabled user's token is not consumed. A malformed token → 422 `validation_error`. | `test_reset_used_token_422`, `test_reset_expired_token_422`, `test_reset_superseded_token_422`, `test_reset_verify_token_422`, `test_reset_unknown_token_422`, `test_reset_disabled_user_422_token_not_consumed`, `test_reset_malformed_token_422` |
| 12 | In the same transaction, reset also: supersedes the user's other unused **reset and verify** tokens; deletes the `login_failures` row for the email HMAC; sets `email_verified_at` if it is null (A8) and leaves an existing value unchanged | `test_reset_supersedes_other_reset_and_verify_tokens`, `test_reset_clears_login_failures`, `test_reset_sets_email_verified_at_if_null`, `test_reset_keeps_existing_email_verified_at` |
| 13 | Logs carry neither the token, the link nor the address. Reset logs only `password_reset user_id=…`. | `test_forgot_logs_free_of_token_and_address`, `test_reset_logs_only_user_id` |
| 14 | Login race is closed. Reset holds `FOR UPDATE` on the user row while it changes the hash and revokes. Login holds `FOR SHARE` from reading the hash until `upsert_account` commits. A login therefore either finishes before the reset (and its session is revoked) or reads the new hash. | `test_login_concurrency.py::test_login_blocked_by_reset_lock_reads_new_password` (real `get_session`, a separate connection holds `FOR UPDATE`, changes the hash and commits; the login with the old password → 401, no account session created) |
| 15 | Route-level `AppContextDep` on **signup** and **forgot** (owner nit 2026-11-13): the 403 holds even when the link-origin/hook dependency is overridden | `test_auth_app_context.py::test_signup_403_when_signup_hook_overridden`, `test_password_reset_api.py::test_forgot_403_when_link_origin_overridden` |
| 16 | Per-IP scope `verify_email_resend` (default 5/600 s) on `/auth/verify-email/resend` (S1) | `test_email_verification_api.py::test_resend_per_ip_429` (`make_client(verify_email_resend_rate_limit=1)`) |
| 17 | The new settings are config only (R5). `.env.example` lists `FORGOT_RATE_LIMIT`, `FORGOT_RATE_WINDOW_SECONDS`, `RESET_RATE_LIMIT`, `RESET_RATE_WINDOW_SECONDS`, `VERIFY_EMAIL_RESEND_RATE_LIMIT` and `VERIFY_EMAIL_RESEND_RATE_WINDOW_SECONDS` without values. | Read the diff of `app/core/config.py` and `.env.example` |
| 18 | Docs are updated: a reset page section in `docs/consumers.md`; `docs/deploy.md` bootstrap says "then reset the password"; the note that access tokens already issued stay valid ≤ 15 min after a reset; an ADR-0007 amendment; identity-architecture §4 rows | Read the docs diff |
| 19 | `make check` is green; `alembic check` shows no drift (no schema change) | CI / `make check` |

## Files to create / modify

| File | Change | Required because |
|---|---|---|
| `app/modules/auth/password_reset.py` (new) | `PasswordResetService` with `forgot(email, link_origin) -> OutgoingEmail \| None` and `reset(token, new_password) -> None`; constants `RESET = "reset"`, `RESET_TTL = timedelta(minutes=30)`, `RESET_PATH = "/reset-password"` | AC 1, 5–14 (R1 service layer) |
| `app/modules/auth/email_verification.py` | `prepare` calls the shared cap check `daily_cap_allows(...)` in place of `sends.count_global_24h()`; adds module function `async daily_cap_allows(tokens, sends, purpose, daily_cap) -> bool` (global lock + count + pure rule); `RESET` is not defined here | AC 7 (one gate for both purposes; no copied lock/window/cap logic, per the api consult) |
| `app/modules/auth/email_throttle.py` | `VERIFY_CAP_PERCENT = 80`; pure `cap_allows(purpose, *, total_24h, verify_24h, daily_cap) -> bool` | AC 7 (unit-testable rule) |
| `app/modules/auth/repository.py` | `EmailTokenRepository.find_active_user_id(token_hash, purpose)` (unused + unexpired, no consume); `EmailTokenRepository.count_issued_24h() -> tuple[int, int]` (total, verify) in one query with `count(*) FILTER (WHERE purpose='verify')`; `EmailSendLogRepository.lock_global()` (`pg_advisory_xact_lock(hashtextextended('email_daily_cap', 0))`); remove the now-unused `EmailSendLogRepository.count_global_24h` | AC 7, 11 (R1 repository layer) |
| `app/modules/sessions/repository.py` | `revoke_all_accounts_of_user(user_id) -> None`: one `UPDATE account_sessions SET revoked_at = now() WHERE user_id = … AND revoked_at IS NULL` | AC 9 |
| `app/modules/users/repository.py` | `set_password_hash(user, password_hash) -> None` (assign + flush) | AC 8 (keeps the write in the repository) |
| `app/modules/auth/router.py` | New routes `POST /password/forgot` and `POST /password/reset`; `_app: AppContextDep` on `signup`; `dependencies=[rate_limit("verify_email_resend")]` on `resend_verification` | AC 1–4, 8, 15, 16 |
| `app/modules/auth/schemas.py` | `ForgotPasswordRequest`, `ResetPasswordRequest` | AC 1, 8, 11 |
| `app/modules/email/messages.py` | `RESET_SUBJECT`, `reset_message(link)` (VN then EN, "expires in 30 minutes", "if you did not ask, ignore; your password is unchanged") | AC 5 |
| `app/core/rate_limit.py` | `Scope` + `_LIMITS` gain `forgot`, `reset`, `verify_email_resend` | AC 4, 8, 16 |
| `app/core/config.py` | `forgot_rate_limit=5`, `forgot_rate_window_seconds=60`, `reset_rate_limit=10`, `reset_rate_window_seconds=60`, `verify_email_resend_rate_limit=5`, `verify_email_resend_rate_window_seconds=600` (all `ge=1`); comment on `email_daily_cap` updated ("verify ≤ 80 %, reset uses the rest") | AC 17 (R5) |
| `.env.example` | The six keys, no values, with comments; the `EMAIL_DAILY_CAP` comment updated | AC 17 (R5) |
| `tests/test_password_reset_api.py` (new) | Forgot/reset/cap API tests | AC 1–13, 15 |
| `tests/test_email_throttle.py` | `test_daily_cap_allows` | AC 7 |
| `tests/test_login_concurrency.py` | `test_login_blocked_by_reset_lock_reads_new_password` | AC 14 |
| `tests/test_auth_app_context.py` | `test_signup_403_when_signup_hook_overridden` | AC 15 |
| `tests/test_email_verification_api.py` | `test_resend_per_ip_429` | AC 16 |
| `docs/consumers.md` | "Password reset" section; replace "(and `/reset-password` once USR-04b lands)" | AC 18 |
| `docs/deploy.md` | Bootstrap: "If you don't know the password, use forgot password, then reset it (ends every signed-in session of the account)"; fix "revokes every device session" | AC 18 |
| `docs/adr/0007-email-verification-and-delivery.md` | "Amended by USR-04b" notes on §1, §3 and §4, plus Consequences (see Approach step 9) | AC 18 (decision outlives the ticket) |
| `docs/identity-architecture.md` | §4 forgot row: "public, Turnstile, per-IP limit + Origin/`X-MM-App` check (USR-04b)"; forgot/reset ticket column → USR-04b; reset row: "revoke all account sessions of the user" | AC 18 |

## Tests to write

| Test (file › name) | Covers |
|---|---|
| test_password_reset_api › test_forgot_known_user_202_sends_reset_link | 202, empty body, no-store, one mail to the user, link regex `(https://\S+)/reset-password#token=(…)`, token row purpose `reset`, TTL 30 min, hash = SHA-256 of the link token |
| › test_forgot_link_uses_request_origin | second registered origin → link uses it |
| › test_forgot_unknown_email_202_no_mail | 202, no mail, one `email_send_log` row (purpose reset), no `email_tokens` row |
| › test_forgot_disabled_user_202_no_mail | as above for `status=disabled` |
| › test_forgot_per_email_limit_silent_202 | second call within 60 s → 202, still one mail, still one log row |
| › test_forgot_daily_per_email_limit_silent_202 | five log rows shifted back > 60 s → sixth → 202, no mail |
| › test_forgot_limit_applies_to_unknown_email | unknown email: second call → no new log row |
| › test_forgot_new_link_supersedes_previous | two forgot calls (first log row shifted back > 60 s) → first token → reset 422 `token_invalid` |
| › test_forgot_turnstile_failed_403_no_writes | token "fail" → 403 `turnstile_failed`, 0 log rows, no mail |
| › test_forgot_without_app_header_403_no_side_effects | 403 `app_header_required`, Turnstile not called, 0 rows, no mail |
| › test_forgot_unregistered_origin_403_no_side_effects | 403 `origin_not_allowed`, same checks |
| › test_forgot_403_when_link_origin_overridden | `dependency_overrides[get_link_origin]` → constant; no header → still 403 |
| › test_forgot_per_ip_429 | second call → 429 + `Retry-After` |
| › test_forgot_validation_422 | invalid email; extra field; missing turnstile_token |
| › test_forgot_logs_free_of_token_and_address | caplog contains neither the address nor the token |
| › test_unknown_email_forgot_does_not_consume_daily_cap | cap 1: 3 unknown forgot calls, then a known one → mail sent |
| › test_verify_share_exhausted_reset_still_sent | cap 5: 5 sign-ups → 4 mails + `email_not_sent reason=daily_cap purpose=verify`; then forgot for a known user → mail sent |
| › test_forgot_daily_cap_reached_202_logged | cap 1 used by a sign-up → forgot → 202, no mail, log `reason=daily_cap purpose=reset` without the address |
| › test_reset_happy_path_204_no_cookie_new_password_works | 204, no `Set-Cookie`, login with new password 200, old one 401 |
| › test_reset_revokes_all_account_sessions_every_device | user on 2 devices + another user on device 1 → `/auth/token` 401 for the user on both, 200 for the other user; `device_sessions.revoked_at` stays NULL |
| › test_reset_weak_password_422_token_still_usable | 422 `weak_password`, then a strong password → 204 |
| › test_reset_used_token_422 | second use → 422 `token_invalid` |
| › test_reset_expired_token_422 | SQL shift `created_at`/`expires_at` into the past → 422 |
| › test_reset_superseded_token_422 | covered via the forgot supersede test, plus a direct `issue` call |
| › test_reset_verify_token_422 | a verify-purpose token → 422 |
| › test_reset_unknown_token_422 | random 43-char token → 422 |
| › test_reset_disabled_user_422_token_not_consumed | 422; `used_at` stays NULL |
| › test_reset_malformed_token_422 | short token → 422 `validation_error`; `new_password` over 128 chars → 422 |
| › test_reset_supersedes_other_reset_and_verify_tokens | other unused reset + verify tokens get `used_at` set |
| › test_reset_clears_login_failures | `login_failures` row for HMAC(email) gone |
| › test_reset_sets_email_verified_at_if_null / test_reset_keeps_existing_email_verified_at | A8 |
| › test_reset_per_ip_429 | `reset_rate_limit=1` |
| › test_reset_logs_only_user_id | `password_reset user_id=` present; no token, no address |
| test_email_throttle › test_daily_cap_allows | parametrized: cap 300 → verify allowed at 239, refused at 240; reset allowed at total 299, refused at 300; cap 1 → verify allowed at 0 |
| test_login_concurrency › test_login_blocked_by_reset_lock_reads_new_password | AC 14 |
| test_auth_app_context › test_signup_403_when_signup_hook_overridden | AC 15 |
| test_email_verification_api › test_resend_per_ip_429 | AC 16 |

## Approach

1. **Config and scopes (R5).**
   - Add the six settings and `.env.example` keys.
   - Add `forgot`, `reset` and `verify_email_resend` to `Scope` and `_LIMITS`.
   - Put `rate_limit("verify_email_resend")` on `/verify-email/resend`.

2. **Per-purpose cap (AC 7, S2).**
   - In `email_throttle.py`, add `cap_allows`:

     ```
     cap_allows(purpose, *, total_24h, verify_24h, daily_cap):
         if total_24h >= daily_cap: return False
         if purpose == "verify": return verify_24h < max(1, daily_cap * VERIFY_CAP_PERCENT // 100)
         return True
     ```

   - In `email_verification.py`, add `daily_cap_allows`. It calls `sends.lock_global()`, then `tokens.count_issued_24h()`, then `cap_allows`.
   - In `prepare`, replace the cap check with `if not await daily_cap_allows(...): raise DailyCapReachedError(DAILY_CAP_RETRY_SECONDS)`. Everything else stays the same.
   - Counting `email_tokens.created_at` counts real sends only: a token is issued exactly when a mail is queued. Unknown-email forgot rows (which only write `email_send_log`) never count.
   - Lock order is the same on every path: per-(purpose, email) lock, then the global lock, then (forgot only) the user row. Reset and login take no advisory locks, so there is no cycle.
   - The `max(1, …)` keeps the existing cap=1 test valid. Below a cap of 5 the reset reserve can be under 20 %. That is documented; production uses 300 (verify ≤ 240, reset ≥ 60).
   - No index on `email_tokens.created_at` (no migration in this ticket). Rows are bounded by the cap itself (≤ 300/day), so the scan stays small. USR-04d adds retention.

3. **Forgot service** (`PasswordResetService.forgot`, the same steps for every email):
   1. `email = normalize_email(...)`, `hmac = email_hasher.hmac(email)`.
   2. `sends.lock(hmac, RESET)`, then `window`, then `resend_retry_after`. If limited, return `None` (silent).
   3. `daily_cap_allows(..., RESET)` is false: log `email_not_sent reason=daily_cap purpose=reset` (no user id, no address) and return `None`. No `email_send_log` row is written in this case, the same as `prepare`.
   4. `sends.insert(hmac, RESET)` for every email.
   5. `user = users.get_by_email_for_share(email)`. If it is `None` or not active, return `None`.
   6. Otherwise `tokens.issue(user.id, RESET, email_token_hash(token), RESET_TTL)`, build `reset_message(f"{link_origin}{RESET_PATH}#token={token}")`, log `password_reset_requested user_id=…`, and return the `OutgoingEmail` (purpose `reset`).

   Remaining timing differences (the token insert and the enqueue for known users only) are accepted (identity consult, Risks) and recorded in the ADR. Turnstile runs in the service before step 2 (`TurnstileFailedError`). No Argon2 runs on forgot.

4. **Forgot route.**

   ```
   @router.post("/password/forgot", status_code=202, response_class=Response,
                dependencies=[rate_limit("forgot")], responses={403,422,429})
   async def forgot_password(request, response, session, background_tasks, _app: AppContextDep,
                             body: ForgotPasswordRequest, remote_ip: ClientIpDep,
                             link_origin = Depends(get_link_origin), verifier, dispatcher)
   ```

   - Check order: IP limit, then app context (403), then the service (Turnstile 403, then the silent per-email limit, then the cap).
   - When the service returns an email, add `background_tasks.add_task(dispatcher.enqueue, email)`. Background tasks run after the commit.
   - Set `Cache-Control: no-store`.
   - Docstring: "Public (R3 class 3) + required app context (owner 2026-11-13)".

5. **Reset service** (`PasswordResetService.reset`). This follows the identity consult §1 and the security consult §4.
   1. `user_id = tokens.find_active_user_id(hash, RESET)`. If `None`, raise `TokenInvalidError`.
   2. `policy.check(new_password)`. `WeakPasswordError` leaves the token untouched.
   3. `password_hash = await hasher.hash(new_password)`. This runs off the event loop, before any row lock.
   4. `user = users.get_for_update(user_id)`. If it is `None` or not active, raise `TokenInvalidError`. The request transaction rolls back, so the token is not consumed.
   5. `tokens.consume(hash, RESET)`. If `None` (a concurrent reset), raise `TokenInvalidError`.
   6. `users.set_password_hash(user, password_hash)`.
   7. `sessions.revoke_all_accounts_of_user(user.id)`.
   8. `tokens.invalidate(user.id, RESET)` and `tokens.invalidate(user.id, VERIFY)`.
   9. `failures.delete(email_hasher.hmac(normalize_email(user.email)))`.
   10. If `email_verified_at` is null, `users.mark_email_verified(user)`.
   11. Log `password_reset user_id=…`.

   Everything runs in the one request transaction (`SessionDep` commits). Revocation covers account sessions only. Device sessions may hold other users' accounts, and reset sees no cookie.

6. **Reset route.**

   ```
   @router.post("/password/reset", status_code=204, response_class=Response,
                dependencies=[rate_limit("reset")], responses={422,429})
   ```

   - Public, with no app context and no cookie: the token is the credential, the same as `/verify-email`.
   - Set `Cache-Control: no-store`. No `Set-Cookie`.

7. **Login race (decided: no code change).**
   - Login already re-reads the user `FOR SHARE` (`get_by_email_for_share`) after the failure-counter commit and holds it until the request commits, through Argon2 verify and `upsert_account`. `start_for_login` runs with `commit_on_reuse=False`.
   - Reset's `get_for_update` conflicts with that lock. Either the reset waits for the login to commit and then revokes the new account session, or the login waits and Postgres hands it the updated row (new hash), so it fails.
   - The security consult's re-check proposal is therefore already met by the existing lock. `test_login_blocked_by_reset_lock_reads_new_password` pins this behaviour.

8. **Signup.** Add `_app: AppContextDep` to the `signup` signature. FastAPI caches it with the one inside `get_link_origin`, so there is no extra query.

9. **Docs.**
   - `consumers.md`: a "Password reset" section shaped like verify-email.
     - Forgot needs `X-MM-App` and `credentials: "include"` (preflight).
     - The page `/reset-password` reads the hash, clears it with `history.replaceState`, uses `Referrer-Policy: no-referrer`, and POSTs `{token, new_password}`.
     - Status tables: forgot 202/403 `app_header_required`/403 `origin_not_allowed`/403 `turnstile_failed`/422/429; reset 204/422 `token_invalid`/422 `weak_password`/422 `validation_error`/429.
     - Notes: "every signed-in session of the account ends; access tokens already issued stay valid ≤ 15 min (`ACCESS_TOKEN_TTL_SECONDS`); after reset, show 'sign in with your new password'".
   - `deploy.md`: the bootstrap text.
   - ADR-0007 amendment:
     - reset link and TTL;
     - forgot requires app context;
     - the silent per-email limit;
     - cap counted from `email_tokens` per purpose (verify ≤ 80 %, reset uses the rest) under a global advisory lock;
     - reset revokes account sessions (not device sessions) and supersedes reset and verify tokens;
     - accepted risks: the forgot timing difference, the access token ≤ 15 min, and that data an attacker created before the reset stays.
   - identity-architecture §4: update the forgot and reset rows.

Rules applied:

| Rule | How it is met |
|---|---|
| R1 | Routes do HTTP only; services raise `AppError` subclasses; SQL stays in repositories. |
| R3 | Forgot and reset are class 3. Forgot also requires app context, like signup. |
| R5 | New settings come from env. |
| R6 | Existing error codes only. |
| R7 | Follow-ups are listed below. |
| R8 | Tests per endpoint: happy, 404 n/a (no resource path), 422, 403, 429. |
| R9 | `/api/v1/auth/...` paths from §4. |
| R11 | Hashed tokens, no secrets in logs, no cookie on reset, no new enumeration. |

## Specialist input used

Adopted:

| Consult | Item | Reason or note |
|---|---|---|
| api-platform-engineer | The ticket contradiction: the owner note wins | Taken as the deviation above. |
| api-platform-engineer | Separate service method that does not reuse the raising `prepare`, and no copied cap logic | Shared `daily_cap_allows`. |
| api-platform-engineer | New scopes and settings | Taken as specified. |
| api-platform-engineer | Route shapes and status codes, `Cache-Control: no-store` | Taken as specified. |
| api-platform-engineer | `consumers.md` section shape and test list | Taken as specified. |
| api-platform-engineer | No `operation_id` | Other routes do not use them. |
| identity-access-engineer | Reset order: valid-token lookup → policy → hash before locks → `get_for_update` → consume → writes | Taken as the reset steps. |
| identity-access-engineer | `revoke_all_accounts_of_user` as one UPDATE | Taken as specified. |
| identity-access-engineer | Forgot sequence | Taken as specified. |
| identity-access-engineer | Scope split (include route-level app context, the 80 % cap and the resend scope; defer the rest) | Except that the global advisory lock is included: about 8 lines, and the user asked for it. |
| identity-access-engineer | The 80 % share | A fixed constant, not a setting: one fewer knob, and the owner gave the number. |
| identity-access-engineer | Reset sends count against the global cap | Yes, they do. |
| security-architect §1 | Count real sends from `email_tokens` per purpose; verify < 80 %; reset < cap; global advisory lock; a capped forgot gives 202 plus a log line | Taken as specified. |
| security-architect §2 | Scope defaults: forgot 5/60 s, reset 10/60 s, resend 5/600 s | Taken as specified. |
| security-architect §3 | Same steps for every email | Taken as specified. |
| security-architect §4 | Supersede reset **and** verify tokens | Taken as specified. |
| security-architect §6 | Log only the user id | Taken as specified. |
| security-architect | Account sessions only; defer retention and `aclose` | Taken as specified. |

Rejected or changed:

| Consult | Item | Reason |
|---|---|---|
| security-architect §5 | A new re-check of the login hash | Already met by the existing `FOR SHARE` lock held through `upsert_account` (Approach step 7). A concurrency test pins it instead. |
| api-platform-engineer | Defer the advisory lock to USR-04d | It is included here because it is tiny and protects the cap model. |
| identity-access-engineer | Run the cap check after inserting the send log | The cap check comes **before** the `email_send_log` insert, as in `prepare`. A capped request therefore does not use up the per-email window, and known and unknown emails still make the same writes. |

## Assumptions

- **B1** Answers to the consults' open questions:
  - The app-context 403 comes before Turnstile. It reveals nothing about the email.
  - A disabled user's reset gives 422 and the token is not consumed.
  - Reset does not revoke access tokens already issued (≤ 15 min, documented).
  - Reset does not revoke the device session.
- **B2** The verify share is fixed at 80 % (`VERIFY_CAP_PERCENT`), with a floor of 1. When the cap is reached, a reset still counts toward the 300 total.
- **B3** A capped forgot or a limited forgot writes no `email_send_log` row. An accepted forgot writes one row for every email. Unknown and disabled users get no token and no mail.
- **B4** Forgot logs `password_reset_requested user_id=…` only for active users. This is an internal log, not a response, so there is no enumeration.
- **B5** Reset message copy: plain text, Vietnamese then English, subject `Đặt lại mật khẩu mmlabs / Reset your mmlabs password`. The owner may change the wording.
- **B6** Attacker-created data under a pre-registered email (memberships) remains after a reset. This is documented in the ADR, and no change is made.
- **B7** Per-IP limits are per process (ADR-0002). This is unchanged.

## Blocking questions

None.

## Split proposal

This plan covers **USR-04b** as scoped above. New follow-on ticket:

**USR-04d — Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert**

- Areas: auth, db, ops.
- Priority: P2.
- Dependencies: USR-04b.
- Acceptance criteria:
  - `EmailDispatcher.aclose()` is wired into the FastAPI lifespan in `app/main.py`. On shutdown the worker stops within a bounded time and queued mail is logged as dropped (count only, no addresses or links). Test: the app lifespan calls `aclose` once.
  - Retention cleanup:
    - deletes `email_send_log` rows older than 48 h;
    - deletes `email_tokens` that are used or expired and older than 7 days;
    - runs as a CLI command (`python -m app.cli purge-email-records`), callable from the ops workflow on a schedule;
    - is idempotent and batched, with tests;
    - if the planner finds the `email_tokens.created_at` count (USR-04b cap) needs it, adds an index on `email_tokens (created_at)` with a hand-reviewed migration and downgrade.
  - Runbook in `docs/deploy.md`: alert on log lines `email_not_sent reason=daily_cap` (purpose verify or reset). Covers what it means, how to check Gmail quota, and when to raise `EMAIL_DAILY_CAP`.
  - Route-level `AppContextDep` on `/auth/verify-email/resend` too, for consistency with the 2026-11-13 owner nit. Today it comes only through `get_link_origin` and `DeviceDep`.

**Follow-ups (not in this diff):**
- **F1** CLAUDE.md R3 class 3 should say signup and forgot also require `X-MM-App` + Origin. The R11 cookie text is still stale (USR-04 F1). This is for the owner to edit.
- **F2** The duplicate sign-up 409 is still an enumeration oracle (USR-04 A7). It limits what forgot's always-202 achieves.

## Data model / migration

None. `email_tokens.purpose` and `email_send_log.purpose` CHECKs already allow `reset`. No index is added (see Approach step 2; possible in USR-04d). `alembic check` must stay clean.

## API contract

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/password/forgot` | Headers `X-MM-App`, `Origin` (required). Body `ForgotPasswordRequest {email: EmailStr, turnstile_token: str(1..2048)}`, `extra="forbid"` | empty, `Cache-Control: no-store` | 202; 403 `app_header_required` / `origin_not_allowed` / `turnstile_failed`; 422 `validation_error`; 429 `rate_limited` (per IP only) | public (R3 class 3) + app context; per-IP scope `forgot` |
| POST | `/api/v1/auth/password/reset` | `ResetPasswordRequest {token: str(pattern=EMAIL_TOKEN_RE), new_password: SecretStr(max_length=128)}`, `extra="forbid"` | empty, `Cache-Control: no-store`, no `Set-Cookie` | 204; 422 `token_invalid` / `weak_password` / `validation_error`; 429 `rate_limited` | public (R3 class 3); per-IP scope `reset` |
| POST | `/api/v1/auth/signup` | unchanged | unchanged | unchanged (403 now also from the route-level dependency) | unchanged |
| POST | `/api/v1/auth/verify-email/resend` | unchanged | unchanged | + 429 from the new per-IP scope `verify_email_resend` | unchanged |
