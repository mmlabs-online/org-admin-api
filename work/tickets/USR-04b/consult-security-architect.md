# consult-security-architect — USR-04b

**Findings**
- The token primitives can be reused as they are. Tokens come from `secrets.token_urlsafe(32)` (256 bits), only the SHA-256 is stored, consume is an atomic `UPDATE … RETURNING`, the token sits in the URL fragment, and the reset is a POST. Lookup → policy check → consume is safe, provided reset also locks the user row with `get_for_update` (as verify does) so concurrent resets are serialized.
- Scope conflict: the AC says `X-MM-App` is "optional (A1)", but the owner's note of 2026-11-13 makes it **required** with Origin, and the link origin must be the validated Origin. The owner note wins, so `AppContextDep` goes on the route and its 403 must come before Turnstile, any write or any mail.
- `count_global_24h` counts every `email_send_log` row. If forgot logs a row for unknown emails ("same DB writes"), random-address spam uses up the global cap without sending any mail. It blocks reset for everyone, and verify too.
- Login race: if a login verifies the old password and then calls `upsert_account` after the reset has committed, that new session survives the reset.

**Recommendations**
1. **Per-purpose cap (must-have here).**
   - Count real sends from `email_tokens.created_at` (24 h, per purpose). Keep `email_send_log` for the per-email window only.
   - Allow verify while `verify_24h < 0.8 × EMAIL_DAILY_CAP` (240 of 300) and `total < cap`. Allow reset while `total < cap`, which guarantees reset at least 60 a day.
   - When the cap is hit, forgot still returns 202 and logs `email_not_sent reason=daily_cap purpose=reset`.
   - Take a global advisory lock before the count (S2). It is cheap.
2. **Per-IP limits (must-have).** All are new scopes with config keys:
   - `forgot`: 5 per 60 s.
   - `reset`: 10 per 60 s.
   - `verify_email_resend`: 5 per 600 s. This closes USR-04 S1(a) now, because resend spends the same cap.
3. Forgot runs the same steps for every email: per-email lock, window, insert the log row, global count. Only token issue and enqueue are conditional. No Argon2 runs on forgot. A limited request returns 202 silently.
4. Reset does all of the following in one transaction: set the password hash, revoke every account session of the user, supersede other `reset` and `verify` tokens, delete the `login_failures` row, and set `email_verified_at` if it is null. It returns 204 with no cookie.
5. Close the login race: in the login transaction, re-read the user `FOR SHARE` and compare `password_hash` before `upsert_account`.
6. Logs: `password_reset user_id=…` only, with no token, address or HMAC. Add a test that asserts this.

**Risks**
- Access JWTs that were already issued stay valid for up to 15 min (`access_token_ttl_seconds`). Document this in `consumers.md` and ADR-0007; there is no denylist in v1.
- App memberships and data created by an attacker under the pre-registered email remain after the reset. Their sessions end, but what they did does not; document this.
- Rate limiting is per process (ADR-0002), so the per-IP limits do not hold across replicas.
- The global count without the advisory lock can overshoot slightly; accepted if the lock is skipped.

**Open questions**
- Should reset also revoke the attacker's whole `device_sessions`, or only the victim's account sessions on them? I recommend account sessions only (the AC as written).
- Can retention cleanup and `aclose()` in the lifespan stay deferred? I would defer them: not needed for this ticket.
- Is 80/20 the right split, or should reset get a hard reserve of 60?
