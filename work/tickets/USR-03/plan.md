Status: READY

# Plan — USR-03: Device sessions (multi-account), login/logout, brute force (slice 1 of 3)

This plan covers **only the first slice** of the original ticket (see `## Split proposal`): device and
account sessions, the `mm_device` cookie with rotation, grace and reuse detection, login (Origin and
`X-MM-App` check, D11 brute force, Turnstile after 3 failures) and logout. `/auth/token` + JWKS move
to **USR-03c**; dynamic CORS from `apps.origins` moves to **USR-03e**.

## Spec summary
spec-v1.md §5.8:
> **Login and sessions:** … A browser holds one device session (cookie `mm_device`, rotated with a 60 s
> grace) with any number of signed-in accounts; each app remembers its own chosen account, so signing
> into another account in one app does not change another app (D5, D6). Logout per account or for the
> whole device.

> **Abuse protection:** no hard lockout; per-email exponential backoff after 5 failures (cap 15 min),
> per-IP limit, Turnstile after 3 failures; the same error for unknown email and wrong password (D11).
> CORS only for registered app origins; cookie-authenticated POSTs check Origin and `X-MM-App` (D12).

identity-architecture.md §5:
> **S1 Cookie (D5):** `mm_device` = opaque random (≥ 256 bit), `HttpOnly; Secure; SameSite=Lax;
> Path=/api/v1/auth`, no `Domain` (host-only on `api.mmlabs.online`); only its hash is stored.
> **S2 Rotation (D6):** rotate on login and at most every ~24 h; the previous value stays valid for
> 60 s. Presenting an old value after the grace window = reuse → revoke the whole device session.
> **S3 Brute force (D11):** … Per-email exponential backoff after 5 failures (1, 2, 4 … min, cap 15
> min); per-IP ~20 req/min on login …; Turnstile required after 3 failures …

identity-architecture.md §2 step 1–2: "App B posts email + password to `POST /api/v1/auth/login`
(header `X-MM-App: B`, Origin checked). API ensures a `device_session` for the `mm_device` cookie
(creates one if missing), rotates the cookie (D6), creates an `account_session` for that user on the
device, returns `{account_id, …}`." §2 Log out: "`{account_id}` revokes one account session …
`{all:true}` revokes the device session."

## Acceptance criteria → verification
Criteria 1–4, 6, 8 and the CSRF half of 9 of the original ticket, restated for this slice. Token
(AC5), JWKS (AC7) and the CORS echo half of AC9 are in USR-03c / USR-03e.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Tables `device_sessions`, `account_sessions` (and `login_failures`, see Data model) created by one migration with a working `downgrade()`; models imported in `app/db/models.py`; no `refresh_tokens` table; `alembic check` clean | `tests/test_sessions_schema.py` (all); `uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check` recorded in impl-log; CI `alembic check` |
| 2 | Cookie `mm_device`: ≥ 256-bit opaque value; `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`; no `Domain`; persistent `Max-Age` = idle timeout; only the SHA-256 is stored | `test_auth_login.py › test_login_sets_device_cookie_attributes`, `› test_only_cookie_hash_is_stored` |
| 3 | `POST /api/v1/auth/login` creates the device session if missing, rotates it if present, adds or reuses the account session, returns 200 `{account_id, user:{id,email,email_verified}}` with `Cache-Control: no-store`; unknown email, wrong password and disabled user all get the same 401 `invalid_credentials` body | `test_auth_login.py › test_login_creates_device_and_account_session`, `› test_login_again_reuses_account_session`, `› test_two_accounts_on_one_device`, `› test_invalid_credentials_identical_bodies` (parametrized: unknown, wrong password, disabled) |
| 4 | Rotation on login and at most every 24 h on cookie use; previous value accepted for 60 s without re-rotation and without `Set-Cookie`; previous value after the grace → device session and all its account sessions revoked (persisted despite the 401), 401 `session_invalid`, cookie cleared | `test_device_sessions.py › test_rotates_after_24h`, `› test_no_rotation_before_24h`, `› test_prev_value_within_grace_accepted_without_rotation`, `› test_reuse_after_grace_revokes_device` (strict client), `› test_concurrent_rotation_cas_loser_accepted` |
| 5 | Fixation mitigations: login on a device session with no active account session mints a new device session; a request authenticated by the previous value may only use account sessions created before `rotated_at`; a request carrying more than one `mm_device` cookie → 401 `session_invalid`, no revocation | `test_device_sessions.py › test_login_on_empty_device_mints_new_session`, `› test_prev_value_cannot_use_accounts_added_after_rotation`, `› test_multiple_device_cookies_rejected` |
| 6 | Device session expiry: idle > 30 days (`last_seen_at`) or age > 365 days (`created_at`) → treated as invalid (401 on cookie endpoints, new device session on login) | `test_device_sessions.py › test_idle_expired_device_rejected`, `› test_absolute_expired_device_rejected` |
| 7 | `POST /api/v1/auth/logout {account_id}` revokes only that account session on this device (another device's id → 204, no change); `{all:true}` revokes the device session and its account sessions and clears the cookie; `{}` or both fields → 422 | `test_auth_logout.py › test_logout_one_keeps_other_account`, `› test_logout_foreign_account_is_noop`, `› test_logout_all_revokes_device_and_clears_cookie`, `› test_logout_body_validation` (parametrized), `› test_logout_without_cookie_401` |
| 8 | Brute force (D11): per-IP scope `login` (20/min) → 429 `rate_limited` + `Retry-After`; per-email backoff after 5 failures (1, 2, 4, 8, 15 min cap) → 429 `rate_limited` + `Retry-After` before any Argon2 work; Turnstile required once an email has ≥ 3 failures (missing → 403 `turnstile_required`, invalid → 403 `turnstile_failed`); unknown emails counted exactly like known ones; counter reset on success; no hard lockout | `test_login_throttle.py › test_backoff_minutes` (pure, parametrized), `› test_fifth_failure_blocks_one_minute`, `› test_backoff_doubles_and_caps`, `› test_blocked_email_skips_password_check`, `› test_turnstile_required_after_three_failures`, `› test_turnstile_invalid_rejected`, `› test_unknown_email_counted_like_known`, `› test_success_resets_counter`, `› test_failure_counter_persists_despite_401` (strict client), `› test_login_per_ip_limit` |
| 9 | CSRF (D12, this slice): login and logout require `X-MM-App: <key>` of an **active** app (missing or malformed → 403 `app_header_required`) and an `Origin` that equals, after `normalize_origin`, one of that app's `origins` (else 403 `origin_not_allowed`; same code for unknown/disabled app); loopback origins ignored in staging/production | `test_auth_app_context.py › test_missing_app_header_403`, `› test_unknown_or_disabled_app_403`, `› test_missing_or_null_origin_403`, `› test_origin_of_other_app_403`, `› test_loopback_origin_ignored_in_production`, `› test_logout_checks_origin` |
| 10 | No passwords, cookie values, hashes, Turnstile tokens or full emails in responses, logs or error details (R5/R11); `CORS_ORIGINS` may not contain `*` | `test_auth_login.py › test_responses_never_echo_secrets` (also checks `caplog`); `test_config.py › test_cors_origins_wildcard_refused` |
| 11 | Staging/production refuse to start without `IDENTITY_HMAC_KEY` (≥ 32 chars) | `test_config.py › test_identity_hmac_key_required_in_production`, `› test_identity_hmac_key_too_short` |
| 12 | `make check` green | reviewer runs `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/sessions/__init__.py` | create (empty) | new domain module |
| `app/modules/sessions/models.py` | create `DeviceSession`, `AccountSession` | AC1 |
| `app/modules/sessions/repository.py` | create `SessionRepository`: lookup by hash, insert device, CAS rotate, touch, revoke device (+accounts), upsert/revoke account, count active accounts, `commit()` | AC1, AC3–AC7; R1 (SQLAlchemy only here) |
| `app/modules/sessions/service.py` | create `DeviceSessionService` (`resolve`, `start_for_login`, `add_account`, `revoke_account`, `revoke_device`), token helpers `new_device_token()` / `device_token_hash()` | AC2–AC7 |
| `app/modules/sessions/errors.py` | create `SessionInvalidError` (401 `session_invalid`) | R6 |
| `app/modules/auth/models.py` | create `LoginFailure` | AC8 (backoff storage) |
| `app/modules/auth/repository.py` | create `LoginFailureRepository` (read state, upsert failure, set block, delete, `commit()`) | AC8; R1 |
| `app/modules/auth/throttle.py` | create `EmailHasher` (HMAC-SHA256), constants (`TURNSTILE_AFTER=3`, `BACKOFF_AFTER=5`, `BACKOFF_CAP_MIN=15`, `COUNTER_RESET=24h`), pure `backoff_minutes(failures)` | AC8 |
| `app/modules/auth/cookies.py` | create: read `mm_device` from the raw `Cookie` header (count occurrences), `set_device_cookie(response, value, settings)`, `clear_device_cookie_header(settings)` | AC2, AC5 (multiple cookies) |
| `app/modules/auth/service.py` | add `normalize_email()` (used by signup too), `AppContextService.check(app_key, origin)`, `LoginService.login(...)`, `LogoutService.logout(...)` | AC3, AC7–AC9 |
| `app/modules/auth/errors.py` | add `InvalidCredentialsError` (401), `TurnstileRequiredError` (403), `AppHeaderRequiredError` (403), `OriginNotAllowedError` (403) | R6 |
| `app/modules/auth/schemas.py` | add `LoginRequest`, `LoginResponse`, `LoginUser`, `LogoutRequest` | AC3, AC7 |
| `app/modules/auth/deps.py` | add `get_email_hasher`, `AppContextDep` (headers → `AppContextService`), `DeviceDep` (cookie → `resolve`, adds cookie-clear header on `SessionInvalidError`) | AC4, AC9; R1 (HTTP only) |
| `app/modules/auth/router.py` | add `POST /auth/login`, `POST /auth/logout` | AC3, AC7 |
| `app/modules/auth/passwords.py` | add `PasswordHasher.verify_dummy(password)` (lazy dummy hash with current params, same `CapacityLimiter`) | AC3/AC8: equal timing for unknown email |
| `app/core/rate_limit.py` | add scope `"login"` to `Scope` and `_LIMITS` | AC8 |
| `app/core/config.py` | add `login_rate_limit=20`, `login_rate_window_seconds=60`, `device_session_idle_days=30`, `device_session_max_days=365`, `identity_hmac_key: SecretStr \| None`; validators: key required (≥ 32 chars) in staging/production; `*` refused in `cors_origins` | AC6, AC8, AC10, AC11; R5 |
| `app/main.py` | `app.state.email_hasher = EmailHasher(key or secrets.token_bytes(32))` (random only in local/test) | AC8 |
| `app/db/models.py` | import `DeviceSession`, `AccountSession`, `LoginFailure` | R2 |
| `migrations/versions/20261112_<rev>_create_sessions.py` | create; `down_revision = "b638a7c84c31"` | AC1; R2 |
| `.env.example` | add `IDENTITY_HMAC_KEY=`, `LOGIN_RATE_LIMIT=`, `LOGIN_RATE_WINDOW_SECONDS=`, `DEVICE_SESSION_IDLE_DAYS=`, `DEVICE_SESSION_MAX_DAYS=` (no values) | R5 |
| `docs/deploy.md` | "Required runtime config": `IDENTITY_HMAC_KEY` (secret, ≥ 32 random chars, env file; app and `migrate` refuse to start without it in staging/production; never rotate casually — rotating resets backoff counters) | AC11; deploy will otherwise fail |
| `docs/adr/0002-identity-abuse-controls-runtime.md` | amend: new §5 "Login failure counters in Postgres" + Consequences line | decision outlives the ticket (planner rule 7) |
| `docs/adr/0004-device-sessions-and-login.md` | create: cookie, hashing, rotation algorithm (CAS, grace, reuse), fixation rules, commit-before-raise, `account_id` = `account_sessions.id`, expiry defaults, login auth class | planner rule 7 |
| `docs/identity-architecture.md` | §3: add `login_failures`, note `account_id` = `account_sessions.id`; §4 login row: "public + Origin/`X-MM-App`" | keep plan doc in sync (no CLAUDE.md edit) |
| `tests/conftest.py` | `os.environ["IDENTITY_HMAC_KEY"]` test value; new fixture `make_strict_client` (session override mirrors `get_session`: commit on success, rollback on exception) | commit-before-raise tests |
| `tests/auth_factories.py` | create: `make_login_user(session, password, *, status, verified)` with a real Argon2 hash, `make_login_app(session, key, origins)`, `parse_set_cookie(resp)`, `auth_headers(app_key, origin)` | test helpers |
| `tests/test_sessions_schema.py` | create | AC1 |
| `tests/test_auth_login.py` | create | AC2, AC3, AC10 |
| `tests/test_device_sessions.py` | create | AC4–AC6 |
| `tests/test_auth_logout.py` | create | AC7 |
| `tests/test_login_throttle.py` | create | AC8 |
| `tests/test_auth_app_context.py` | create | AC9 |
| `tests/test_config.py` | add cases | AC10, AC11 |

Estimated size: ~480 production lines (excluding the migration, ~120) and ~650 test lines. Over the
~400 guide, accepted by the orchestrator for this slice; the remaining work is split out.

## Tests to write
All API tests send `X-MM-App` and `Origin` of a test app (`make_login_app(origins=["https://stoktok.mmlabs.online"])`)
unless the test is about those headers. httpx will not send a `Secure` cookie to `http://test`, so
tests pass the cookie explicitly (`headers={"cookie": "mm_device=<v>"}`) and parse `Set-Cookie` with
`http.cookies.SimpleCookie`. Time-based cases move stored timestamps back with SQL (`UPDATE … SET
rotated_at = now() - interval '25 hours'`); inside one test transaction `now()` is constant.

| Test (file › name) | Covers |
|---|---|
| `test_sessions_schema.py › test_token_hash_length_check` | CHECK `octet_length(token_hash) = 32` |
| `› test_prev_pair_check` | CHECK prev hash ⇔ prev_valid_until |
| `› test_one_active_account_session_per_device_user` | partial unique; a revoked row does not block a new one |
| `› test_device_delete_cascades_accounts`, `› test_user_delete_cascades_accounts` | FKs |
| `› test_login_failures_hmac_length_check`, `› test_no_refresh_tokens_table` | AC1 |
| `test_auth_login.py › test_login_sets_device_cookie_attributes` | HttpOnly, Secure, SameSite=Lax, Path=/api/v1/auth, no Domain, Max-Age = 30 d, value ≥ 43 url-safe chars |
| `› test_only_cookie_hash_is_stored` | `token_hash == sha256(cookie)`; raw value nowhere in the row |
| `› test_login_creates_device_and_account_session` | 200 body keys exactly `{account_id, user}`, `user` keys `{id,email,email_verified}`; `Cache-Control: no-store` |
| `› test_login_again_reuses_account_session` | same `account_id`, cookie rotated, one active row |
| `› test_two_accounts_on_one_device` | second login with the cookie → same device, two account sessions, different `account_id`s; first account session untouched |
| `› test_invalid_credentials_identical_bodies` | unknown email / wrong password / disabled user (correct password): same status and body |
| `› test_unknown_email_runs_dummy_verify` | `verify_dummy` called (spy) |
| `› test_login_with_unknown_cookie_creates_new_device` | garbage cookie on login → new device session, no 401 |
| `› test_responses_never_echo_secrets` | password, email (except `user.email` on success), cookie and token not in bodies or `caplog` |
| `test_device_sessions.py › test_rotates_after_24h` | logout with cookie whose `rotated_at` is 25 h old → `Set-Cookie` with new value; prev hash set, `prev_valid_until = now()+60s` |
| `› test_no_rotation_before_24h` | no `Set-Cookie` |
| `› test_prev_value_within_grace_accepted_without_rotation` | old value within grace: 204/200, no `Set-Cookie`, `token_hash` unchanged; also on login (no re-rotation) |
| `› test_reuse_after_grace_revokes_device` | strict client, setup committed: old value after grace → 401 `session_invalid`, cookie cleared, device and account sessions `revoked_at` set **after** the request |
| `› test_reuse_revocation_needs_commit` | control: monkeypatch `SessionRepository.commit` to a no-op → revocation gone (proves the strict fixture rolls back and the commit is load-bearing) |
| `› test_concurrent_rotation_cas_loser_accepted` | service level: two `resolve()` calls on the same current value, second sees CAS miss → re-read → accepted as prev within grace, no revocation |
| `› test_login_on_empty_device_mints_new_session` | device with all account sessions revoked → login creates a new device row; old one revoked |
| `› test_prev_value_cannot_use_accounts_added_after_rotation` | service level: `resolve()` via prev returns `accounts_created_before = rotated_at`; `repo.get_active_account(…, created_before=…)` excludes the login-time account session |
| `› test_multiple_device_cookies_rejected` | `Cookie: mm_device=a; mm_device=b` → 401, no revocation |
| `› test_revoked_device_rejected`, `› test_idle_expired_device_rejected`, `› test_absolute_expired_device_rejected` | AC6 |
| `test_auth_logout.py › test_logout_one_keeps_other_account` | 204; other account session still active |
| `› test_logout_foreign_account_is_noop` | account of another device → 204, unchanged |
| `› test_logout_all_revokes_device_and_clears_cookie` | 204, `Max-Age=0`, same Path, no Domain; next request with the cookie → 401 |
| `› test_logout_body_validation` | `{}`, both fields, `all:false`, extra field → 422 |
| `› test_logout_without_cookie_401` | 401 `session_invalid` |
| `test_login_throttle.py › test_backoff_minutes` | f<5 → None; 5→1, 6→2, 7→4, 8→8, 9→15, 50→15 |
| `› test_fifth_failure_blocks_one_minute` | 5 wrong passwords (with Turnstile after 3) → 6th → 429, `Retry-After` 1–60 |
| `› test_backoff_doubles_and_caps` | set `failures` via SQL, one more failure → `blocked_until` matches |
| `› test_blocked_email_skips_password_check` | blocked → 429 and hasher not called, counter unchanged |
| `› test_turnstile_required_after_three_failures` | 4th attempt without token → 403 `turnstile_required`; with `"pass"` → normal flow |
| `› test_turnstile_invalid_rejected` | 403 `turnstile_failed`; counter unchanged |
| `› test_turnstile_not_called_below_threshold` | fake verifier has no calls for the first 3 attempts |
| `› test_unknown_email_counted_like_known` | identical status/body/headers sequence for both over 6 attempts |
| `› test_success_resets_counter` | row deleted |
| `› test_counter_resets_after_24h` | `last_failed_at` 25 h ago → next failure stores `failures = 1` |
| `› test_failure_counter_persists_despite_401` | strict client: failure row present after the 401 |
| `› test_login_per_ip_limit` | `make_client(login_rate_limit=2)` → 3rd request 429 `rate_limited` |
| `test_auth_app_context.py` (6 tests listed in AC9) | AC9 |
| `test_config.py › test_identity_hmac_key_required_in_production`, `› test_identity_hmac_key_too_short`, `› test_cors_origins_wildcard_refused` | AC10, AC11 |

## Approach
1. **Migration + models** (R2). One revision after `b638a7c84c31` with the three tables below.
   Models declare every index (incl. partial ones with `postgresql_where=text("…")`) so `alembic
   check` sees no drift. `device_sessions` uses `created_at` only (no `TimestampMixin`, consult-db 1).
2. **Commit-before-raise — decision: explicit commit in the service, via `repo.commit()`, immediately
   before raising** (consult-software-architect finding 1 / rec. "pick one and test it",
   consult-identity finding 1). Used in exactly two places: (a) reuse after grace in
   `DeviceSessionService.resolve` (revoke device + accounts → commit → raise `SessionInvalidError`);
   (b) `LoginService` after recording a failure (upsert → set block → commit → raise
   `InvalidCredentialsError`). Nothing else is pending in those transactions, so the commit only
   persists the intended write. Rejected alternative: a "commit-on-raise" flag on `AppError` honoured
   by `get_session` — it changes core transaction semantics for every endpoint, and FastAPI 0.16x runs
   yield-dependency exit code late, which makes it harder to reason about. Testing: the existing
   test override never rolls back, so it cannot catch a missing commit; the new `make_strict_client`
   fixture mirrors `get_session` (commit on success, rollback on exception) — tests using it call
   `await db_session.commit()` after setup (releases the savepoint; the outer test transaction still
   rolls back), and a control test proves the commit is load-bearing.
3. **Device token.** `secrets.token_urlsafe(32)` (256 bit); stored as `sha256(value)` (`bytea`, 32
   bytes). A fast hash is enough for a 256-bit random secret (consults db/identity/security agree).
   Cookie read from the raw `Cookie` header so duplicates are detectable (Starlette keeps only one).
4. **Resolve / rotation algorithm** (`DeviceSessionService.resolve(raw, *, force_rotate=False)`),
   all times from the database clock (`now()`), no row lock:
   - no cookie → `SessionInvalidError`; more than one `mm_device` → `SessionInvalidError` (no
     revocation; logged `mm_device_multiple`).
   - `SELECT …, now() FROM device_sessions WHERE token_hash = :h OR prev_token_hash = :h`.
   - not found, revoked, idle-expired (`last_seen_at < now() - idle`) or absolute-expired
     (`created_at < now() - max`) → `SessionInvalidError`.
   - **current hit** (`token_hash = :h`): if `force_rotate` or `rotated_at <= now() - 24h` →
     CAS: `UPDATE device_sessions SET prev_token_hash = token_hash, prev_valid_until = now() +
     interval '60 seconds', token_hash = :new, rotated_at = now(), last_seen_at = now() WHERE id = :id
     AND token_hash = :h AND revoked_at IS NULL RETURNING id`. 1 row → return the new raw value for
     `Set-Cookie`. 0 rows → a concurrent request rotated first: re-read once and handle as below
     (a prev hit within grace is accepted, never treated as reuse). Otherwise `UPDATE … SET
     last_seen_at = now() WHERE id = :id AND last_seen_at < now() - interval '1 hour'`.
   - **prev hit within grace** (`now() <= prev_valid_until`): accept, **no re-rotation and no
     `Set-Cookie`** (also on login, consult-identity), and return `accounts_created_before =
     rotated_at` so callers only use account sessions created strictly before the rotation (fixation
     mitigation, consult-identity/security). Account sessions created in the rotating transaction
     have `created_at = rotated_at` (same `now()`) and are excluded.
   - **prev hit after grace** → reuse: revoke device + its active account sessions, commit, log
     warning `device_session_reuse` with the device session id only, raise `SessionInvalidError`.
     `prev_token_hash` is kept after the grace so later reuse is still detected (one step back only —
     documented risk).
   - Result: `ResolvedDevice(device_id, new_cookie: str | None, accounts_created_before: datetime | None)`.
5. **Login** (`POST /api/v1/auth/login`), checks in this order:
   1. `rate_limit("login")` per IP (dependency) → 429.
   2. `AppContextDep` (step 7) → 403.
   3. Body validation → 422.
   4. `email = normalize_email(body.email)`; `h = EmailHasher.hmac(email)`; read `login_failures`
      (counter treated as 0 if `last_failed_at < now() - 24h`). `blocked_until > now()` → 429
      `rate_limited` with `Retry-After = ceil(blocked_until - now())` — no Argon2, no counting.
   5. `failures >= 3`: token missing → 403 `turnstile_required`; `verifier.verify(token, ip)` false →
      403 `turnstile_failed` (not counted). Below 3, any token is ignored and Cloudflare is not called.
   6. User lookup (`get_by_email_for_share`); unknown → `verify_dummy(password)`; known →
      `hasher.verify`. Failure, or correct password on a `disabled` user → upsert failure
      (`failures + 1`, reset to 1 after 24 h idle), if `backoff_minutes(f)` → set `blocked_until =
      now() + m min`, commit, raise 401 `invalid_credentials` (one message for every case).
   7. Success: delete the `login_failures` row; device: if the cookie resolves to a usable device
      **with at least one active account session**, rotate (`force_rotate=True`, or no rotation on a
      prev hit within grace); if it resolves to a device with no active account session, revoke it and
      create a new one (fixation, consult-identity); if absent/invalid/expired/duplicated → create a
      new device session (`ip` from `ClientIpDep`, `user_agent` truncated to 512). Reuse detected at
      login revokes the old device (in the same, successful transaction) and creates a new one — login
      never 401s on a bad cookie.
   8. Account session: `INSERT … ON CONFLICT (device_session_id, user_id) WHERE revoked_at IS NULL DO
      UPDATE SET last_used_at = now() RETURNING id` (consult-db 2).
   9. 200 `LoginResponse`, `Set-Cookie` when a new value exists, `Cache-Control: no-store`.
   Password rehash on login is **not** in this slice (follow-up). Login/logout audit events wait for
   ADM-004 (R4 "once ADM-004 lands"); structured log lines are emitted now (consult-security
   "Logging"), never with emails, cookies, hashes or passwords.
6. **Logout** (`POST /api/v1/auth/logout`, cookie class): `AppContextDep` → `DeviceDep` (resolve; may
   rotate and set a cookie) → `{account_id}`: `UPDATE account_sessions SET revoked_at = now() WHERE id
   = :id AND device_session_id = :device AND revoked_at IS NULL` (0 rows → still 204, no oracle);
   `{all:true}`: revoke device + accounts and send the clearing cookie (`Max-Age=0`, same Path, no
   Domain). 204 with `Cache-Control: no-store`. Already issued JWTs (USR-03c) stay valid until expiry
   — documented in ADR-0004.
7. **Origin / `X-MM-App` check before dynamic CORS** (`AppContextService.check`, used by login and
   logout; consult-software-architect "require_app_context can initially check against a direct DB
   lookup", consult-security "CSRF / Origin"):
   - `X-MM-App` missing or not matching `APP_KEY_RE` → 403 `app_header_required`.
   - `RbacRepository.get_app_by_key(key)`; unknown or `status != active` → 403 `origin_not_allowed`.
   - `Origin` missing, `null`, or failing `normalize_origin` → 403 `origin_not_allowed`.
   - In staging/production, loopback entries of `app.origins` are dropped and a loopback `Origin` is
     rejected (consult-security "Origin rules").
   - Normalized `Origin` must equal one of the remaining `app.origins` → else 403
     `origin_not_allowed`. Returns the `App` (USR-03c needs it for `audience == X-MM-App`).
   - The existing static `CORSMiddleware` stays in this slice (browsers on listed `CORS_ORIGINS` can
     read responses); a new validator refuses `*` in `CORS_ORIGINS` because Starlette would then echo
     any origin with credentials (consult-security finding 2). Dynamic CORS is USR-03e.
8. **Backoff storage — decision: Postgres table `login_failures`** (consult-identity "Backoff
   storage"; consult-db rec. 4 offered it as the durable option; consult-architect/api preferred
   memory). Justification against ADR-0002: the in-memory limiter is accepted for per-IP limits, but
   for per-email backoff it (a) resets on every deploy/restart, (b) fails open when 10 000 keys per
   scope are flooded (cheap with rotating IPv6 /64s, each new email is one request), which would also
   reset the Turnstile-after-3 requirement — the main defence against distributed guessing, and
   (c) cannot be shared once a second worker exists. One indexed upsert per **failed** login is cheap.
   Keyed by `HMAC-SHA256(IDENTITY_HMAC_KEY, normalized email)` so no email (or unsalted, brute-forceable
   email hash) is stored (R11); USR-04 can reuse the key for `email_send_log`. ADR-0002 gets §5.
   Stale-row cleanup is a follow-up (rows are deleted on success; volume is low).
9. **R-rules:** R1 routers only parse headers/body/cookies and set cookies; services raise `AppError`
   subclasses; repositories hold all SQL. R2 one migration with downgrade. R3 see Assumption A1. R5
   new settings in `config.py` and `.env.example`. R6 all errors via the envelope (codes below). R9
   paths under `/api/v1/auth`. R11 cookie, hashing, rotation, backoff as specified.

**Error codes (R6 envelope `{"error":{"code","message","details"}}`):**

| Status | Code | When | Details / headers |
|---|---|---|---|
| 401 | `invalid_credentials` | unknown email, wrong password, disabled user | `null`, identical message |
| 401 | `session_invalid` | no / unknown / revoked / expired / duplicated cookie, reuse after grace | `null`; `Set-Cookie` clear when a cookie was sent |
| 403 | `app_header_required` | `X-MM-App` missing or malformed | `null` |
| 403 | `origin_not_allowed` | unknown or disabled app, Origin missing / null / not in the app's origins | `null` |
| 403 | `turnstile_required` | ≥ 3 failures and no token | `null` |
| 403 | `turnstile_failed` | token rejected (existing class) | `null` |
| 429 | `rate_limited` | per-IP `login` scope or per-email backoff (existing class) | `{"retry_after": n}`, `Retry-After` |
| 422 | `validation_error` | body validation | existing handler |

## Specialist input used
- **software-architect:** adopted finding 1 (commit-before-raise; mechanism chosen = explicit commit),
  sessions module boundary, direct DB lookup for the app context before dynamic CORS, split 03a/03b/03c
  (renamed to USR-03 / USR-03c / USR-03e). Rejected: in-memory backoff (see Approach 8); renaming
  USR-03b (orchestrator keeps USR-03b, new IDs 03c/03e). Ed25519 recommendation passed to USR-03c.
- **identity-access-engineer:** adopted cookie/hash, rotation cases incl. "no re-rotation on prev hit,
  also on login", reuse commit, fixation (new device when empty; prev value limited to pre-rotation
  accounts), multiple cookies → 401, check order, dummy verify, disabled user counted and generic, DB
  backoff with HMAC key and ADR-0002 amendment, logout semantics, Origin/`X-MM-App` on login, 30 d idle /
  1 y absolute expiry. Deferred: rehash on login (follow-up), token/JWT items (USR-03c), CORS (USR-03e).
  Changed: per-email 429 uses existing `rate_limited` instead of `login_backoff` (one client code path,
  `Retry-After` carries the timing).
- **security-architect:** adopted login CSRF via Origin + `X-MM-App`, loopback ignored in
  staging/production, prev hash kept after grace, unknown emails counted, `no-store`, logging rules,
  `*` refused in `CORS_ORIGINS`. Deferred to follow-ups: global failure-rate Turnstile trigger,
  `Sec-Fetch-Site` check, origin uniqueness across apps (admin exclusive), clearing a tossed
  `Domain=mmlabs.online` cookie. JWT items → USR-03c; CORS items → USR-03e.
- **database-optimizer:** adopted table shapes, CHECKs, partial indexes, non-unique prev-hash index,
  account upsert, CAS with DB clock, one migration. Rejected: `SELECT … FOR UPDATE` in addition to CAS
  (CAS alone is race-safe; no lock held). Backoff table adopted from its "if in DB" option, keyed by
  HMAC (column `email_hmac`). Retention/cleanup → follow-up.
- **api-platform-engineer:** adopted 200 login / 204 logout, `no-store`, error codes, `extra="forbid"`
  schemas, `LogoutRequest` exactly-one rule, `APIKeyCookie`/`X-MM-App` in OpenAPI and `responses=` map,
  exact-key tests. Token/JWKS/CORS recommendations → USR-03c/USR-03e.

## Assumptions
Owner confirms these in the PR.
- **A1 — Login auth class (owner decision note).** Login stays **public** (R3 class 3: rate-limited,
  Turnstile where required) **and** additionally requires `Origin` + `X-MM-App` like the cookie class,
  because it reads and sets `mm_device` (login CSRF). identity-architecture.md §2 already says "Origin
  checked"; R3's wording lists login only as public. CLAUDE.md is **not** edited; the PR asks the
  owner to reword R3 (e.g. "public, plus the R11 Origin/`X-MM-App` check for endpoints that set
  `mm_device`").
- **A2 — `account_id` = `account_sessions.id`** (per device, opaque; reused while active). The user id is
  returned separately in `user.id`.
- **A3 — Expiry defaults:** idle 30 days (`last_seen_at`, refreshed at most hourly), absolute 365 days;
  cookie `Max-Age` = idle timeout, refreshed on each rotation. Settings `DEVICE_SESSION_IDLE_DAYS`,
  `DEVICE_SESSION_MAX_DAYS`.
- **A4 — Constants, not settings:** rotation 24 h, grace 60 s (D6), Turnstile after 3, backoff after 5,
  cap 15 min, counter reset after 24 h without failures (D11). Backoff schedule: failure 5 → 1 min, 6 →
  2, 7 → 4, 8 → 8, ≥ 9 → 15.
- **A5 — New secret `IDENTITY_HMAC_KEY`** required in staging/production (deploy must set it before this
  release; documented in `docs/deploy.md`). In local/test, if unset, a random per-process key is used.
- **A6 — Disabled users:** the password is still checked; the outcome is the generic 401 and counts as a
  failure, so responses cannot reveal a correct password for a disabled account.
- **A7 — Multiple `mm_device` cookies** → 401 on cookie endpoints and a new device session on login; the
  cookie this API set is cleared, a tossed `Domain=` cookie is not (follow-up).
- **A8 — Release:** this slice alone gives apps no token; it must not be presented to app clients as
  usable until USR-03c and USR-03e ship. It can still be deployed (endpoints are protected by the
  checks above).
- **A9 —** No new dependency in this slice (only stdlib `secrets`, `hashlib`, `hmac`).

## Blocking questions
None.

## Split proposal
The original USR-03 (≈ 2 tables + 1, 3 endpoints, JWT crypto, JWKS, middleware, brute force, ~40
tests) is XL; all five consults recommend a split.

**Slice 1 — USR-03 (this plan):** device/account sessions + migration + cookie + rotation with grace and
reuse detection + login (Origin/`X-MM-App`, D11 brute force, Turnstile after 3) + logout.

**USR-03c — Signing keys, per-app token endpoint, JWKS and key rotation runbook**
- Goal: an app gets its own short-lived JWT for the account it chose on this device, verifiable locally
  through `/.well-known/jwks.json` (D7, §5.8 "Per-app access tokens", "Verification by apps").
- Acceptance criteria:
  - `POST /api/v1/auth/token {audience, account_id, org_id?}` (cookie class: `AppContextDep` +
    `DeviceDep`) → 200 `{access_token, token_type:"Bearer", expires_in, audience}`, `Cache-Control:
    no-store`; JWT ~15 min, asymmetric (algorithm decided in its plan; consults recommend EdDSA/Ed25519
    with `PyJWT[crypto]`, pinned algorithms), header `kid`, `typ: at+jwt`; claims `iss`, `aud`, `sub`,
    `email`, `email_verified`, `roles` (`AuthzService.effective_roles`), `iat`, `exp`, `jti`.
  - `audience` must equal `X-MM-App` (else 403 `origin_not_allowed` or a dedicated code), Origin bound
    to that app (USR-03 `AppContextService`).
  - Account session must belong to this device, be active and, when the cookie was the previous value,
    be created before `rotated_at`; user `disabled` → 401 `session_invalid`.
  - Errors in the R6 envelope: app unknown/disabled → 403 `app_disabled`; unverified → 403
    `email_unverified`; non-member → 403 `not_member` with `details:{"join_available": join_policy !=
    "closed"}`; `org_id` not null → 422 until USR-06.
  - Signing keys from env (`SecretStr`, PEM), `kid` (e.g. RFC 7638 thumbprint), extra public keys for
    rotation; staging/production refuse to start without a valid key.
  - `GET /.well-known/jwks.json` (public, root mount, per-IP limit): public keys only (test: no `d`),
    `ETag` / 304, `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *` without
    credentials.
  - Key rotation runbook in `docs/deploy.md`; ADR for algorithm and key format.
  - Tests: two accounts on one device, switching does not affect the other; wrong audience / expired /
    tampered token rejected by a reference verifier; `kid` matches JWKS; every error code above; 401/403
    for missing cookie/headers.
- Areas: architecture, auth, security, api. Priority P1.
- Dependencies: USR-03, USR-02.

**USR-03e — Dynamic CORS from `apps.origins`; remove or restrict `CORS_ORIGINS`**
- Goal: browsers can call the identity endpoints only from registered app origins (D12 / S4).
- Acceptance criteria:
  - Pure-ASGI middleware (e.g. `app/core/cors.py` behind an `OriginRegistry` protocol, implemented in
    rbac as a cache of active apps' origins with a 30–60 s TTL invalidated by app writes) replaces the
    static `CORSMiddleware` for `/api/v1/auth/*`: echoes `Origin` only when it is in some active app's
    origins, with `Access-Control-Allow-Credentials: true`; always `Vary: Origin`; unknown origin → no
    ACAO headers.
  - Preflight: methods `GET, POST`, headers `Content-Type, X-MM-App`, `Max-Age` ~600.
  - Loopback origins ignored in staging/production.
  - Error responses (401/403/429) also carry the CORS headers so clients can read the envelope.
  - `CORS_ORIGINS` removed, or kept only for non-credentialed admin Bearer calls (owner decides; admin
    UI origins could come from `apps[admin].origins` without credentials).
  - `.env.example` / `docs/deploy.md` updated.
  - Tests: preflight allowed vs unknown origin, credentialed echo, `Vary`, 401 body readable
    cross-origin, disabled app's origin not echoed, cache invalidation on app update.
- Areas: api, security, auth. Priority P1.
- Dependencies: USR-03 (USR-02 for `apps.origins`). Can run in parallel with USR-03c.

**Dependency changes for existing tickets**
- **USR-03b** (accounts list, join, client libs): USR-03, **USR-03c**, **USR-03e** — join retries
  `/token`, `mmlabs-auth` needs JWKS, the TS client needs browser CORS. Its `GET /accounts` must apply
  the prev-value restriction (`accounts_created_before`).
- **ADM-002**: replace "USR-03 (staff get an `aud=admin` token via `/api/v1/auth/token`)" with
  **USR-03c**; add **USR-03e** (admin UI browser calls need CORS). Keep the other dependencies.
- **USR-04**: keep USR-03 (sessions to revoke on reset); its AC "afterwards `/api/v1/auth/token` … work"
  needs **USR-03c** (add it). It can reuse `IDENTITY_HMAC_KEY` for `email_send_log`.
- **USR-06**: add **USR-03c** (org-aware tokens extend the token endpoint).
- Order of work: USR-03 → USR-03c ∥ USR-03e → USR-03b → ADM-002 → …

**Follow-ups (not in any slice yet; for the orchestrator to ticket or log)**
- Cleanup job for stale `login_failures` (> 24 h) and revoked/expired sessions (> 30 days).
- Password rehash on login when `needs_rehash`.
- Global failure-rate trigger making Turnstile mandatory for everyone (credential stuffing).
- `Sec-Fetch-Site: cross-site` rejection; origin uniqueness across apps (at least `admin` exclusive).
- Clearing a tossed `Domain=mmlabs.online` `mm_device` cookie.
- Owner: reword R3 for login (A1).

## Data model / migration
One revision `create_sessions`, `down_revision = "b638a7c84c31"`. Names via `op.f()` / explicit names
matching the naming convention.

```
device_sessions
  id                uuid         NOT NULL  PK pk_device_sessions
  token_hash        bytea        NOT NULL  UNIQUE uq_device_sessions_token_hash
  prev_token_hash   bytea        NULL
  prev_valid_until  timestamptz  NULL
  created_at        timestamptz  NOT NULL  DEFAULT now()
  last_seen_at      timestamptz  NOT NULL  DEFAULT now()
  rotated_at        timestamptz  NOT NULL  DEFAULT now()
  revoked_at        timestamptz  NULL
  ip                inet         NULL
  user_agent        varchar(512) NULL      (truncated in the service)
  CHECK ck_device_sessions_token_hash_length: octet_length(token_hash) = 32
  CHECK ck_device_sessions_prev_pair: (prev_token_hash IS NULL) = (prev_valid_until IS NULL)
  INDEX ix_device_sessions_prev_token_hash ON (prev_token_hash) WHERE prev_token_hash IS NOT NULL

account_sessions
  id                 uuid         NOT NULL  PK pk_account_sessions
  device_session_id  uuid         NOT NULL  FK fk_account_sessions_device_session_id_device_sessions
                                            → device_sessions.id ON DELETE CASCADE
  user_id            uuid         NOT NULL  FK fk_account_sessions_user_id_users → users.id ON DELETE CASCADE
  created_at         timestamptz  NOT NULL  DEFAULT now()
  last_used_at       timestamptz  NOT NULL  DEFAULT now()
  revoked_at         timestamptz  NULL
  UNIQUE INDEX uq_account_sessions_active ON (device_session_id, user_id) WHERE revoked_at IS NULL
  INDEX ix_account_sessions_user_id ON (user_id)

login_failures
  email_hmac      bytea        NOT NULL  PK pk_login_failures
  failures        integer      NOT NULL
  last_failed_at  timestamptz  NOT NULL  DEFAULT now()
  blocked_until   timestamptz  NULL
  CHECK ck_login_failures_email_hmac_length: octet_length(email_hmac) = 32
  CHECK ck_login_failures_failures_positive: failures >= 1
```

- `account_sessions.device_session_id` is the leading column of the partial unique index, so no
  separate FK index is needed.
- No FK from `login_failures` to `users`: unknown emails are counted too (D11 identical responses).
- `downgrade()`: drop `login_failures`, then `account_sessions` (indexes first), then
  `device_sessions`. `citext` and existing tables untouched. No data migration; no `refresh_tokens`.
- CHECK texts in the models must match the migration exactly (Alembic does not compare CHECKs).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | headers `X-MM-App`, `Origin`, optional cookie `mm_device`; body `LoginRequest{email: EmailStr, password: SecretStr (≤ 128), turnstile_token: str (1–2048) \| null}`, `extra="forbid"` | 200 `LoginResponse{account_id: uuid, user:{id: uuid, email: str, email_verified: bool}}`; `Set-Cookie: mm_device=…; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=2592000` (omitted on a prev hit within grace); `Cache-Control: no-store` | 200, 401 `invalid_credentials`, 403 `app_header_required` / `origin_not_allowed` / `turnstile_required` / `turnstile_failed`, 422, 429 `rate_limited` | public (R3 class 3, rate-limited, Turnstile after 3) + Origin/`X-MM-App` check (A1) |
| POST | `/api/v1/auth/logout` | headers `X-MM-App`, `Origin`, cookie `mm_device`; body `LogoutRequest{account_id: uuid}` **or** `{all: true}` (exactly one), `extra="forbid"` | 204; `Set-Cookie` clear (`Max-Age=0`, same Path, no Domain) on `{all:true}`, or a rotated value when > 24 h; `Cache-Control: no-store` | 204, 401 `session_invalid`, 403 `app_header_required` / `origin_not_allowed`, 422 | cookie (R3 class 2) |

OpenAPI: `APIKeyCookie(name="mm_device", auto_error=False)` security scheme (the dependency reads the
raw header itself), `X-MM-App` declared as a header parameter, `responses=` map listing the codes
above.
