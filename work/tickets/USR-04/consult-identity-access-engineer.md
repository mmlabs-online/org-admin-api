# consult-identity-access-engineer — USR-04

**Findings**
- Signup (`router.py` `/signup`) has no app context. It does not read `X-MM-App` or `Origin`, so today the API cannot tell which app a verification link should point to. Login already uses `AppContextDep`.
- The `on_signup` hook runs inside the signup transaction and may "only record/enqueue". There is also no "revoke all account sessions of a user" in `sessions/` (`revoke_account` and `revoke_device` are per device).
- §4 already lists `verify-email` (public), `verify-email/resend {account_id}` (cookie), and `forgot` / `reset` (public).
- Conflict to flag: CLAUDE.md R11 still says cookie `mm_device`, `Path=/api/v1/auth`. D20 superseded this with `__Host-mm_device`, `Path=/`.
- Conflict to flag: the AC says resend over the limit returns 429 "for reset", and forgot must "always 202". A 429 that only known emails can trigger would reveal which emails exist.
- "Async with retry" clashes with R11 (tokens stored only as hashes). A durable DB outbox would have to store the raw token or the rendered link.

**Recommendations**
1. **Links: option (b).** Each link points to the requesting app's own page, with the token in the URL fragment:
   - `{origin}/verify-email#token=…` and `{origin}/reset-password#token=…`
   - `origin` is the request `Origin`, checked against that app's `apps.origins` (D19; loopback is ignored in production).
   - Email scanners that prefetch the link only do a GET of the app's page. Without running JS they never POST, so the single-use token is not consumed.
   - The fragment never reaches app server logs or `Referer`. The SPA should strip it with `history.replaceState` and set `Referrer-Policy: no-referrer`.
   - Reject (a): a GET that changes state gets consumed by scanners, and it needs a redirect to an app origin, which risks becoming an open redirect.
   - Reject (c): it is a hosted UI (spec §3 non-goal).
   - Fix the paths by convention in `docs/consumers.md`; no new column.
2. **App context on the public endpoints.** Add `AppContextDep` (X-MM-App plus Origin check) to signup and forgot. They stay public (class 3) and only need it to pick the link origin.
   - Verify and reset: public, no cookie. The token is the credential. CORS must allow them (they are already under `/api/v1/auth/*`).
   - Resend: cookie class (`{account_id}`).
3. **Token model.** One `email_tokens` table:
   - 32 random bytes (URL-safe base64), stored as SHA-256 with a unique index (the raw value has enough entropy, so HMAC is not needed). Bound to `user_id`.
   - Consume atomically: `UPDATE … SET used_at=now() WHERE token_hash=:h AND used_at IS NULL AND expires_at>now() RETURNING user_id`.
   - Issuing a new token marks older unused tokens of the same purpose as used.
   - TTL: verify 24 h, reset 30 min.
4. **Verify behaviour.** Sets `email_verified_at` only if it is null. If the user is already verified, return 200 even when the token was already used, so double clicks are harmless. Unknown, expired or used tokens all get one uniform 422 `token_invalid`.
5. **Reset behaviour.**
   - Apply the USR-01 password policy and hash with Argon2id off the event loop.
   - Revoke **all** non-revoked `account_sessions` of the user, on every device.
   - Delete the `login_failures` row for the email's HMAC, and mark all reset tokens used.
   - Set `email_verified_at` if it is null (the reset proves the user controls the inbox).
   - Return 204 and do not log the user in. Disabled users get `token_invalid`.
6. **Resend limits.** `email_send_log.email_hash` = HMAC-SHA256 with `IDENTITY_HMAC_KEY` (reuse `EmailHasher`), counted per purpose.
   - For forgot, count *requests* per email HMAC whether or not the account exists, so the 429 is the same for unknown emails.
   - Forgot still needs Turnstile and the per-IP limit (scope `forgot`).
   - Timing: do the same DB writes on both branches.
7. **Sending.** After commit, queue the message in-process (asyncio worker, 3–5 tries, exponential backoff with jitter). The raw token lives only in memory.
   - If the process crashes, that email is lost and the user resends.
   - Logs carry the user id and purpose, never the token or the full address.

**Risks**
- Access tokens already issued stay valid for up to about 15 minutes after a reset (JWTs cannot be revoked).
- In-memory sends are lost on restart.
- Gmail limits (about 500/day) leave the forgot flow open to email-bombing. The per-email 1/min and 5/day limits cap it, but an attacker can fill a victim's daily quota.
- Making `X-MM-App` required on signup is a v1 contract change. It is acceptable only because there are no consumers yet.

**Open questions**
1. Forgot over the limit: return a uniform 429 for every email (recommended), or always 202 and silently drop?
2. Accept lost sends on restart, or store an encrypted outbox (encryption key from env)?
3. Should the link paths be a fixed convention or per-app configuration?
4. Should reset also mark the email verified (recommended: yes)?
5. Update CLAUDE.md R11 to match D20.

**ricotdin SPA integration**
- Add routes `/verify-email` and `/reset-password`. Each reads the token from the fragment, then POSTs JSON with `X-MM-App: ricotdin` to verify-email or reset.
- After verifying, it retries `getToken`.
- `@mmlabs/auth-client` gains `verifyEmail`, `resendVerification`, `forgotPassword` and `resetPassword`.
