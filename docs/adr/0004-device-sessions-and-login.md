# ADR-0004: Device sessions, the `mm_device` cookie and login

Status: Accepted (USR-03 plan, slice 1, 2026-11-12)
Date: 2026-11-12

## Context
mmlabs Identity replaces refresh tokens with one device session per browser holding any number of
signed-in accounts (D5, D6; identity-architecture.md §2, §3, §5 S1–S2). Login and logout run in the
browser against `/api/v1/auth/*`, so the cookie needs rotation, reuse detection and CSRF protection.
Per-app tokens (USR-03c) and dynamic CORS (USR-03e) build on this.

## Decision
1. **Cookie.** `mm_device` = `secrets.token_urlsafe(32)` (256 bit), `HttpOnly; Secure;
   SameSite=Lax; Path=/api/v1/auth`, no `Domain` (host-only), `Max-Age` = idle timeout, refreshed
   on every rotation. Only `sha256(value)` is stored (`bytea`, 32 bytes, CHECKed); a fast hash is
   enough for a 256-bit random secret. The cookie is read from the raw `Cookie` header so that
   more than one `mm_device` value is detected: cookie endpoints answer 401 `session_invalid`
   (no revocation), login starts a new device session.
2. **Rotation (D6), all times from the database clock, no row lock.** On login (forced) and when
   `rotated_at` is ≥ 24 h old: compare-and-swap `UPDATE … SET prev_token_hash = token_hash,
   prev_valid_until = now() + 60 s, token_hash = :new, rotated_at = now() WHERE id = :id AND
   token_hash = :old AND revoked_at IS NULL`. A lost CAS (a concurrent request rotated first)
   re-reads once and is then handled as a previous-value hit within the grace window — never as
   reuse. `last_seen_at` is otherwise refreshed at most hourly.
3. **Grace and reuse.** The previous value is accepted until `prev_valid_until`, without
   re-rotation and without `Set-Cookie` (also on login). After the grace window it is reuse: the
   device session and all its account sessions are revoked, 401 `session_invalid`, the cookie is
   cleared. `prev_token_hash` is kept after the grace so later reuse is still detected (one step
   back only).
4. **Commit before raise.** The reuse revocation (and the login failure counter, ADR-0002 §5) must
   survive the error response, while `get_session` rolls back on any exception. The service calls
   `repo.commit()` immediately before raising; nothing else is pending in those transactions.
   Rejected: a "commit-on-raise" flag on `AppError` honoured by `get_session` (changes core
   transaction semantics for every endpoint). Tests use a strict client fixture that mirrors
   `get_session`, plus a control test proving the commit is load-bearing. At login, reuse is
   revoked in the (successful) login transaction itself and a new device session is created —
   login never 401s on a bad cookie.
5. **Fixation mitigations.** Login on a device session with no active account session revokes it
   and mints a new one. A request authenticated by the previous value may only use account sessions
   created strictly before `rotated_at` (`ResolvedDevice.accounts_created_before`; USR-03b/03c
   must apply it).
6. **Identifiers.** `account_id` in the API = `account_sessions.id` (per device, opaque, reused
   while active: upsert on the partial unique index `(device_session_id, user_id) WHERE revoked_at
   IS NULL`). The user id is returned separately as `user.id`.
7. **Expiry.** A device session is unusable when revoked, idle for more than
   `DEVICE_SESSION_IDLE_DAYS` (default 30, `last_seen_at`) or older than `DEVICE_SESSION_MAX_DAYS`
   (default 365, `created_at`): 401 on cookie endpoints, a new device session on login.
8. **Login auth class.** `POST /api/v1/auth/login` is public (R3 class 3: per-IP limit, Turnstile
   after 3 failures) **and** requires `X-MM-App` of an active app and an `Origin` that equals, after
   `normalize_origin`, one of that app's `origins` (login CSRF: it reads and sets `mm_device`).
   Missing/malformed header → 403 `app_header_required`; unknown or disabled app, missing/`null`/
   unregistered Origin → 403 `origin_not_allowed`. Loopback origins are ignored in
   staging/production. Logout is cookie class (R3 class 2) with the same check. The static
   `CORSMiddleware` stays until USR-03e; `CORS_ORIGINS` may not contain `*`.
9. **Login.** Order: per-IP limit → app/Origin check → body → per-email backoff (429 before any
   Argon2 work) → Turnstile when ≥ 3 failures → password check (a dummy Argon2 verify for unknown
   emails; disabled users' passwords are checked too) → one 401 `invalid_credentials` for unknown
   email, wrong password and disabled user. Success deletes the failure row, rotates or creates the
   device session and upserts the account session; 200 `{account_id, user:{id, email,
   email_verified}}` with `Cache-Control: no-store`.
10. **Logout.** `{account_id}` revokes that account session on this device only (another device's id
    → 204, no change, no oracle); `{all: true}` revokes the device session and its account sessions
    and clears the cookie. Tokens already issued (USR-03c) stay valid until they expire.

## Consequences
- No `refresh_tokens` table; revoking an account session replaces "revoke refresh token".
- Logs carry only event names and session ids — never emails, cookies, hashes or passwords.
- Audit events for login/logout wait for ADM-004 (R4).
- Follow-ups: cleanup of revoked/expired sessions; password rehash on login; clearing a tossed
  `Domain=mmlabs.online` cookie.
