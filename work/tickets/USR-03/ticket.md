# USR-03: Device sessions (multi-account), cookie rotation, login/logout with Origin check

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | architecture, auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/17 |

## Goal
A browser holds one device session with several signed-in accounts; each app gets its own short-lived JWT for the account it chose, verified locally through JWKS. Replaces the earlier refresh-token design.

## Scope
`device_sessions`, `account_sessions`; cookie `mm_device` (issue, rotate with grace, reuse detection); `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `POST /api/v1/auth/token`; JWT signing + `/.well-known/jwks.json`; brute-force protection; CORS from `apps.origins` and CSRF checks. Out of scope: `GET /api/v1/auth/accounts`, join endpoint, client libraries (USR-03b); email flows (USR-04); orgs in tokens (USR-06 — `org_id` param rejected with 422 until then, or accepted only for `org_mode = none` apps as NULL).

## Spec reference
§5.8 (Login and sessions, Per-app access tokens, Verification, Abuse protection); identity-architecture.md §2–§5; decisions D5, D6, D7, D11, D12

## Acceptance criteria
- [ ] Tables + migration per identity-architecture.md §3 (`device_sessions` with `token_hash`, `prev_token_hash`, `prev_valid_until`, `rotated_at`, `revoked_at`, ip, user_agent; `account_sessions` unique per device + user while active); downgrade works; no `refresh_tokens` table
- [ ] Cookie `mm_device`: opaque ≥ 256-bit, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`, no `Domain`; only the hash stored (R11)
- [ ] `POST /api/v1/auth/login` creates the device session if missing, rotates the cookie, adds/reuses the account session, returns `{account_id, user:{id,email,email_verified}}`; disabled users rejected with the same generic 401
- [ ] Rotation: on login and at most every ~24 h on cookie use; previous value accepted for 60 s; an old value after the grace → device session revoked, 401
- [ ] `POST /api/v1/auth/token {audience, account_id, org_id?}` → JWT (~15 min, asymmetric, `kid`) with iss, aud, sub, email, email_verified, roles (from USR-02 `effective_roles`), iat, exp; errors: account not on this device / revoked → 401; email unverified → 403 `email_unverified`; not a member → 403 `{error:"not_member", join_available}` (`join_available` = policy ≠ closed); app disabled → 403
- [ ] `POST /api/v1/auth/logout {account_id}` revokes that account session only; `{all:true}` revokes the device session and clears the cookie
- [ ] `GET /.well-known/jwks.json` publishes public keys with `kid`; private key(s) from env; key rotation procedure documented
- [ ] Brute force (D11): per-email exponential backoff after 5 failures (1, 2, 4… min, cap 15 min), per-IP ~20/min, Turnstile required after 3 failures; identical body for unknown email and wrong password; no hard lockout
- [ ] CORS (D12): allow-origin echoes only origins in `apps.origins` with credentials; every cookie-authenticated POST checks `Origin` ∈ the app's origins and requires `X-MM-App: <key>` (403 otherwise)
- [ ] Tests: two accounts on one device + switching does not affect the other; token wrong audience/expired/tampered; rotation within and after grace (reuse → revoked); backoff timings; Turnstile-after-3; CORS allowed vs unknown origin; missing `X-MM-App` → 403; logout one vs all

## Dependencies
USR-01, USR-02.

## Notes / answers from owner
- 2026-11-11: renamed from ADM-009 and redesigned (D5–D7, D11, D12). The refresh-token design is replaced by device/account sessions.
- 2026-11-11 (D16): no interim "mark verified" path. Until USR-04 ships, `/api/v1/auth/token` returns `email_unverified` for real users; tests create verified users directly in fixtures.
- 2026-11-11 (D13): endpoints under `/api/v1/auth/*`, cookie `Path=/api/v1/auth`.
- 2026-11-12: planner split this ticket. USR-03 keeps sessions, cookie, rotation, login (Origin/X-MM-App, D11 brute force, Turnstile after 3) and logout. Token + JWKS → USR-03c; dynamic CORS → USR-03e. The token/JWKS/CORS acceptance criteria above moved to those tickets (see plan.md → "Split proposal").
