# consult-identity-access-engineer — USR-04b

**Findings**
- The building blocks are already there: `EmailTokenRepository.find_user_id/consume/invalidate/issue`, `EmailSendLogRepository.lock/window/insert/count_global_24h`, `resend_retry_after`, `LoginFailureRepository.delete`, `UserRepository.get_for_update`, `PasswordHasher.hash` (runs off the event loop through anyio), and `PasswordPolicy.check`. Still missing: `reset_message` in `email/messages.py`, the `forgot`/`reset` rate-limit scopes and settings, and `SessionRepository.revoke_all_accounts_of_user`.
- `TokenService` already rejects a revoked account session through `get_active_account`, so an `/auth/token` → 401 for every device follows once the revocation is in place.
- **Conflict:** the acceptance criterion says "optional `X-MM-App`" for forgot. The owner's note (2026-11-13) says it is **required** (`X-MM-App` + `Origin`, link origin taken from the validated `Origin`). The owner note wins. Update the acceptance criterion and write 403 tests.
- `find_user_id` returns the token in any state (used or expired). For reset you need to read a token that is still valid without consuming it.

**Recommendations**
1. Reset order:
   - (a) Find the valid token: unused, not expired, purpose `reset`. If none → 422 `token_invalid`. Check this before the password policy so a dead link never asks for a new password.
   - (b) Run `policy.check`. On 422 `weak_password` the token stays unused.
   - (c) Hash the new password with Argon2id **before** taking any lock.
   - (d) In one transaction: `get_for_update(user)` (disabled → 422 `token_invalid`), atomic `consume`, set `password_hash`, `revoke_all_accounts_of_user`, `invalidate(user, "reset")`, delete `login_failures` for `hmac(email.strip().lower())`, and `mark_email_verified` if it is null.
   - Hashing before consuming keeps the row lock off the slow Argon2 step and is still atomic. Taking the user lock before revoking puts a concurrent login (which holds `FOR SHARE`) strictly before or after the reset. Log only `password_reset user_id=…`.
2. `revoke_all_accounts_of_user`: one `UPDATE account_sessions SET revoked_at=now() WHERE user_id=… AND revoked_at IS NULL`. Leave device sessions alone, because other users' accounts may live on them.
3. Forgot:
   - Normalize the email, then Turnstile, then lock and window on (`reset`, hmac) for every email.
   - If limited or over the cap → silent 202.
   - Otherwise insert into the send log, then look up the user. Only an active user gets `tokens.issue` plus a scheduled mail. Unknown and disabled users get the same send-log write.
   - Use a separate service method; do not reuse `prepare`, which raises.
4. Scope for this ticket:
   - **Include:** route-level `AppContextDep` on signup and forgot (a few lines); a per-purpose cap with verify ≤ 80 % of `EMAIL_DAILY_CAP` (about 20 lines, and it directly protects delivery of reset mail); the per-IP scope on resend (about 5 lines, since the rate-limit scope wiring is being touched anyway).
   - **New ticket (ADM or USR):** `EmailDispatcher.aclose()` in a lifespan, retention cleanup, the global advisory lock, and the `daily_cap` alert runbook.
   - With this split the ticket stays around 400 production lines.

**Risks**
- Timing differences remain: only known users get the token insert and the mail enqueue. These are small but measurable. Accepted, and should be documented.
- Access tokens already issued stay valid for up to 15 minutes after a reset (doc acceptance criterion).
- A per-IP limit kept in process memory resets when the process restarts.

**Open questions**
- Should a reset also revoke the device session cookie on the device where the reset was done? (Recommendation: no. Reset is public and sees no cookie.)
- Should the verify share be fixed at 80 %, or be a setting (`EMAIL_VERIFY_CAP_SHARE`)?
- Should reset sends count against the global cap at all, or only against their own reserved share?
