# consult-api-platform-engineer — USR-04

**Findings**
- Paths do not match. The ticket uses `email/verify` and `email/resend`. identity-architecture §4 uses `POST /auth/verify-email {token}` (public) and `POST /auth/verify-email/resend {account_id}` (cookie class), and CLAUDE.md R3 agrees with §4. The architecture doc wins; the ticket text should be fixed.
- `signup` has no `AppContextDep`, so the API cannot tell which app an email link should open. `App` has `origins` but no field for a verify or reset page path.
- The `SignupHook` already says "only record/enqueue work; no slow I/O" and runs in the signup transaction, which fits an outbox.
- R11 conflicts with a naive outbox. Storing the rendered email (which contains the token) would keep the token in plaintext.
- Supporting pieces already exist: CORS covers all of `/api/v1/auth/*` and exposes `Retry-After`; `RateLimitedError` sets the header; `WeakPasswordError` returns 422; `rate_limit()` takes a fixed `Scope` Literal.

**Recommendations**
1. **Endpoints**, all in the R6 envelope with `Cache-Control: no-store`:
   - `POST /auth/verify-email {token}`: public, per-IP limit. Returns 204.
   - `POST /auth/verify-email/resend {account_id}`: cookie + `X-MM-App` (`DeviceDep`). Returns 202. Returns 409 `email_already_verified`, or 429 `rate_limited` with `Retry-After`. No enumeration risk here because the caller is authenticated.
   - `POST /auth/password/forgot {email, turnstile_token}`: public, Turnstile always. Returns 202 whether or not the email exists. The 1/min and 5/day limit is keyed on `email_hash` for every email, known or not, so a 429 reveals nothing. Turnstile failure returns 403.
   - `POST /auth/password/reset {token, new_password: SecretStr}`: public. Returns 204. Revokes all of the user's account sessions. Does not log the user in and sets no cookie.
2. **Token errors.** Use one 422 `token_invalid` for unknown, expired and used tokens. R6 has no 400, so 422 keeps to it.
3. **Reset order.** Look up the token without consuming it, then check the password policy (422 `weak_password`), then consume it atomically with `UPDATE … WHERE used_at IS NULL AND expires_at > now() RETURNING`. A weak password then does not burn the link.
4. **Origin and `X-MM-App` checks.** Require `AppContextDep` on all four endpoints, the same as login, and accept it optionally on signup. It picks where the link goes and keeps CSRF handling uniform. Tokens are not bound to an app.
5. **Links.** Use `{app.origins[0]}{EMAIL_VERIFY_PATH or EMAIL_RESET_PATH}#token=…`. The token goes in a fragment so it never appears in Referer or access logs. Set the two paths and an `EMAIL_DEFAULT_LINK_BASE` (used when signup has no header) in env, document them in `docs/consumers.md`, and do not add a column to `apps`.
6. **Async sending.**
   - Use an `email_outbox` row `(user_id, purpose, app_id, attempts, next_attempt_at)` written in the request transaction.
   - Run an in-process lifespan worker that uses `SKIP LOCKED` and exponential backoff.
   - The worker creates the token, stores only its hash and sends the email. Each retry invalidates the earlier unused token.
   - The API returns 202, never a send result.
7. **OpenAPI.**
   - Each route lists its error codes in `responses=`.
   - Empty 202 and 204 responses use `response_class=Response`, which openapi-fetch handles.
   - `token` is `Field(min_length=…, max_length=128)`.
   - Add new `Scope` values: `verify_email`, `resend`, `forgot`, `reset`.
8. **Tests:**
   - Verify: happy path, then the token works afterwards (AC6).
   - Expired, used and unknown tokens all return the same 422.
   - Resend: 409 when already verified, 429 with `Retry-After` at 1/min and 5/day, 401 without a cookie, 403 for a missing header or bad Origin.
   - Forgot: same 202 for unknown and known emails, Turnstile 403, per-email 429.
   - Reset: weak password leaves the token usable, sessions are revoked, a reused token fails.
   - Worker: retries on a transient error from the fake sender, and logs contain neither the token nor the full email address.
9. **Size and split.** This is about L. Split it:
   - **04a:** sender, Gmail and fake; outbox and worker; `email_tokens` and `email_send_log` migration; signup hook; verify and resend; runbook. This alone unblocks D16 and ricotdin.
   - **04b:** forgot and reset with session revocation.

**Risks**
- An in-process worker running in several uvicorn processes double-sends without `SKIP LOCKED`, and it stops when the process stops. Outbox rows survive restarts.
- Gmail's daily limit stops all verification emails, and with them every app token (D2).
- If the email-hash limit is applied only to known emails, 429 reveals which accounts exist.
- `origins[0]` might be a staging origin.

**Open questions**
- Should a successful reset also set `email_verified_at`, since it proves ownership of the email?
- What are the token lifetimes? Suggestion: 24 h for verification, 30 min for reset.
- Does the admin app have an origin to use for staff links, or should staff use the default base?
- Is it OK to make `X-MM-App` on signup optional rather than required (no change to the v1 contract)?
