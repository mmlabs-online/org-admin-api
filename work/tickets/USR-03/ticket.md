# USR-03: Device sessions (multi-account), login/logout, per-app token, JWKS, CORS/CSRF

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
A browser holds one device session with several signed-in accounts; each app gets its own short-lived JWT for the account it chose, verified locally through JWKS. Replaces the earlier refresh-token design.

## Scope
`device_sessions`, `account_sessions`; cookie `mm_device` (issue, rotate with grace, reuse detection); `POST /auth/login`, `POST /auth/logout`, `POST /auth/token`; JWT signing + `/.well-known/jwks.json`; brute-force protection; CORS from `apps.origins` and CSRF checks. Out of scope: `GET /auth/accounts`, join endpoint, client libraries (USR-03b); email flows (USR-04); orgs in tokens (USR-06 — `org_id` param rejected with 422 until then, or accepted only for `org_mode = none` apps as NULL).

## Spec reference
§5.8 (Login and sessions, Per-app access tokens, Verification, Abuse protection); identity-architecture.md §2–§5; decisions D5, D6, D7, D11, D12

## Acceptance criteria
- [ ] Tables + migration per identity-architecture.md §3 (`device_sessions` with `token_hash`, `prev_token_hash`, `prev_valid_until`, `rotated_at`, `revoked_at`, ip, user_agent; `account_sessions` unique per device + user while active); downgrade works; no `refresh_tokens` table
- [ ] Cookie `mm_device`: opaque ≥ 256-bit, `HttpOnly; Secure; SameSite=Lax; Path=/auth`, no `Domain`; only the hash stored (R11)
- [ ] `POST /auth/login` creates the device session if missing, rotates the cookie, adds/reuses the account session, returns `{account_id, user:{id,email,email_verified}}`; disabled users rejected with the same generic 401
- [ ] Rotation: on login and at most every ~24 h on cookie use; previous value accepted for 60 s; an old value after the grace → device session revoked, 401
- [ ] `POST /auth/token {audience, account_id, org_id?}` → JWT (~15 min, asymmetric, `kid`) with iss, aud, sub, email, email_verified, roles (from USR-02 `effective_roles`), iat, exp; errors: account not on this device / revoked → 401; email unverified → 403 `email_unverified`; not a member → 403 `{error:"not_member", join_available}` (`join_available` = policy ≠ closed); app disabled → 403
- [ ] `POST /auth/logout {account_id}` revokes that account session only; `{all:true}` revokes the device session and clears the cookie
- [ ] `GET /.well-known/jwks.json` publishes public keys with `kid`; private key(s) from env; key rotation procedure documented
- [ ] Brute force (D11): per-email exponential backoff after 5 failures (1, 2, 4… min, cap 15 min), per-IP ~20/min, Turnstile required after 3 failures; identical body for unknown email and wrong password; no hard lockout
- [ ] CORS (D12): allow-origin echoes only origins in `apps.origins` with credentials; every cookie-authenticated POST checks `Origin` ∈ the app's origins and requires `X-MM-App: <key>` (403 otherwise)
- [ ] Tests: two accounts on one device + switching does not affect the other; token wrong audience/expired/tampered; rotation within and after grace (reuse → revoked); backoff timings; Turnstile-after-3; CORS allowed vs unknown origin; missing `X-MM-App` → 403; logout one vs all

## Dependencies
USR-01, USR-02.

## Notes / answers from owner
- 2026-11-11: renamed from ADM-009 and redesigned (D5–D7, D11, D12). The refresh-token design is replaced by device/account sessions.
- Until USR-04 lands nobody can verify an email, so `/auth/token` would always return `email_unverified` (see report: dependency U1). The plan must include the agreed interim (e.g. staff marks a user verified via an admin endpoint) — confirm with the owner.
- Path prefix `/auth/*` vs R9: identity-architecture.md §10 C1.
