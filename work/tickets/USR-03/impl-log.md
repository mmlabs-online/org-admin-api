# Implementation log — USR-03

## Attempt 1 — 2026-11-12

Scope: slice 1 only per plan.md (device/account sessions, `mm_device` cookie with rotation/grace/
reuse, login with Origin/`X-MM-App` + D11 brute force + Turnstile after 3, logout). No token
endpoint, JWKS or dynamic CORS (USR-03c / USR-03e).

### Files touched
| File | Required because |
|---|---|
| `app/modules/sessions/__init__.py` | new domain module |
| `app/modules/sessions/models.py` | `DeviceSession`, `AccountSession` (AC1) |
| `app/modules/sessions/repository.py` | `SessionRepository`: find by current/prev hash + DB `now()`, add device, CAS rotate, touch (≤ hourly), revoke device (+accounts), count active accounts, account upsert, `get_active_account(created_before=)`, revoke account, `commit()` |
| `app/modules/sessions/service.py` | `new_device_token`, `device_token_hash`, `ResolvedDevice`, `DeviceSessionService` (`resolve`, `start_for_login`, `add_account`, `revoke_account`, `revoke_device`) |
| `app/modules/sessions/errors.py` | `SessionInvalidError` (401 `session_invalid`) |
| `app/modules/auth/models.py` | `LoginFailure` (AC8) |
| `app/modules/auth/repository.py` | `LoginFailureRepository` (get state with DB clock, upsert failure with 24 h reset, set block, delete, `commit()`) |
| `app/modules/auth/throttle.py` | constants, `backoff_minutes`, `EmailHasher` (HMAC-SHA256) |
| `app/modules/auth/cookies.py` | raw `Cookie` header reader (duplicates), set/clear cookie, clear header for 401s |
| `app/modules/auth/service.py` | `normalize_email` (signup now uses it), `AppContextService`, `LoginService`, `LogoutService` |
| `app/modules/auth/errors.py` | `InvalidCredentialsError`, `TurnstileRequiredError`, `AppHeaderRequiredError`, `OriginNotAllowedError` |
| `app/modules/auth/schemas.py` | `LoginRequest`, `LoginUser`, `LoginResponse`, `LogoutRequest` (exactly one of `account_id` / `all: true`) |
| `app/modules/auth/deps.py` | `get_email_hasher`, `DeviceSessionsDep`, `AppContextDep` (`X-MM-App`/`Origin` headers), `DeviceDep` (adds clearing `Set-Cookie` on 401 when a cookie was sent) |
| `app/modules/auth/router.py` | `POST /auth/login`, `POST /auth/logout`; `APIKeyCookie` scheme + `responses=` for OpenAPI |
| `app/modules/auth/passwords.py` | `PasswordHasher.verify_dummy` (lazy dummy hash, same limiter) |
| `app/core/rate_limit.py` | scope `login` |
| `app/core/config.py` | `login_rate_limit`, `login_rate_window_seconds`, `device_session_idle_days`, `device_session_max_days`, `identity_hmac_key`; validators (key required in staging/production, ≥ 32 chars; `*` refused in `cors_origins`) |
| `app/main.py` | `app.state.email_hasher` (random key only when unset, i.e. local/test) |
| `app/db/models.py` | import the three new models |
| `migrations/versions/20261112_83936724f780_create_sessions.py` | three tables, CHECKs, partial indexes; `down_revision = "b638a7c84c31"`; hand-reviewed, downgrade drops in reverse order |
| `.env.example` | new keys, no values |
| `docs/deploy.md` | `IDENTITY_HMAC_KEY` under "Required runtime config" |
| `docs/adr/0002-identity-abuse-controls-runtime.md` | amended: §5 login failure counters in Postgres + Consequences |
| `docs/adr/0004-device-sessions-and-login.md` | new ADR |
| `docs/identity-architecture.md` | §3 `login_failures` + `account_id` note; §4 login auth row |
| `tests/conftest.py` | `IDENTITY_HMAC_KEY` test value (`TEST_IDENTITY_HMAC_KEY`); `make_strict_client` fixture |
| `tests/auth_factories.py` | helpers: `make_login_user`, `make_login_app`, `make_device`, `shift`, `auth_headers`, `parse_set_cookie`, `login_body`, `sha256` |
| `tests/test_sessions_schema.py`, `tests/test_auth_login.py`, `tests/test_device_sessions.py`, `tests/test_auth_logout.py`, `tests/test_login_throttle.py`, `tests/test_auth_app_context.py`, `tests/test_config.py` | planned tests |

### Tests added / changed
- `test_sessions_schema.py` › `test_token_hash_length_check`, `test_prev_pair_check`, `test_device_defaults`, `test_one_active_account_session_per_device_user`, `test_device_delete_cascades_accounts`, `test_user_delete_cascades_accounts`, `test_login_failures_hmac_length_check`, `test_login_failures_failures_positive`, `test_no_refresh_tokens_table`
- `test_auth_login.py` › `test_login_sets_device_cookie_attributes`, `test_only_cookie_hash_is_stored`, `test_login_creates_device_and_account_session`, `test_login_again_reuses_account_session`, `test_two_accounts_on_one_device`, `test_invalid_credentials_identical_bodies[unknown|wrong_password|disabled]`, `test_unknown_email_runs_dummy_verify`, `test_login_with_unknown_cookie_creates_new_device`, `test_login_body_validation`, `test_responses_never_echo_secrets` (bodies + `caplog`)
- `test_device_sessions.py` › `test_rotates_after_24h`, `test_no_rotation_before_24h`, `test_prev_value_within_grace_accepted_without_rotation` (logout and login), `test_reuse_after_grace_revokes_device` (strict client), `test_reuse_revocation_needs_commit` (control), `test_concurrent_rotation_cas_loser_accepted`, `test_login_on_empty_device_mints_new_session`, `test_prev_value_cannot_use_accounts_added_after_rotation`, `test_multiple_device_cookies_rejected`, `test_revoked_device_rejected`, `test_idle_expired_device_rejected`, `test_absolute_expired_device_rejected` (each also checks login then mints a new device), `test_account_session_unique_while_active`
- `test_auth_logout.py` › `test_logout_one_keeps_other_account`, `test_logout_foreign_account_is_noop`, `test_logout_all_revokes_device_and_clears_cookie`, `test_logout_body_validation` (6 cases), `test_logout_without_cookie_401`
- `test_login_throttle.py` › `test_backoff_minutes` (8 cases), `test_fifth_failure_blocks_one_minute`, `test_backoff_doubles_and_caps` (5 cases), `test_blocked_email_skips_password_check`, `test_turnstile_required_after_three_failures`, `test_turnstile_invalid_rejected`, `test_turnstile_not_called_below_threshold`, `test_unknown_email_counted_like_known`, `test_success_resets_counter`, `test_counter_resets_after_24h`, `test_failure_counter_persists_despite_401` (strict client), `test_login_per_ip_limit`
- `test_auth_app_context.py` › `test_missing_app_header_403` (missing/empty/malformed), `test_unknown_or_disabled_app_403`, `test_missing_or_null_origin_403` (missing/`null`/invalid/foreign), `test_origin_of_other_app_403`, `test_origin_is_normalized`, `test_loopback_origin_ignored_in_production`, `test_logout_checks_origin`
- `test_config.py` › `test_identity_hmac_key_required_in_production`, `test_identity_hmac_key_too_short`, `test_identity_hmac_key_optional_locally`, `test_cors_origins_wildcard_refused`

### Checks (actual output summary)
- Postgres: already running locally (`pg_isready` → accepting connections); no setup needed.
- Baseline before changes: `make check` → 531 passed.
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`:
  - `ruff check .` → All checks passed!
  - `ruff format --check .` → 224 files already formatted
  - `mypy app` → Success: no issues found in 62 source files
  - `pytest -q` → **619 passed** in 23.07s
- Migration against the test DB (`alembic -x url=…/org_admin_test`):
  - `upgrade head` → Running upgrade b638a7c84c31 -> 83936724f780, create sessions
  - `downgrade -1` → Running downgrade 83936724f780 -> b638a7c84c31, create sessions
  - `upgrade head` → Running upgrade b638a7c84c31 -> 83936724f780, create sessions
  - `alembic check` → No new upgrade operations detected.
- OpenAPI: login/logout list `X-MM-App` + `origin` header params, `APIKeyCookie` security, and the planned response codes.

### Deviations from plan (minor)
- `DeviceSessionService.resolve` takes the list of `mm_device` values (`Sequence[str]`) instead of a single raw value, so the multiple-cookie rule lives in the service; it also has `commit_on_reuse` (login passes `False` so reuse at login is revoked in the successful login transaction, per Approach 5.7).
- `IDENTITY_HMAC_KEY` shorter than 32 chars is refused in every environment when set (plan: required ≥ 32 in staging/production); unset stays allowed in local/test.
- Extra tests beyond the plan list: `test_login_body_validation`, `test_device_defaults`, `test_login_failures_failures_positive`, `test_origin_is_normalized`, `test_identity_hmac_key_optional_locally`, `test_account_session_unique_while_active`.
- Logout `{account_id}` does not apply `accounts_created_before` (plan Approach 6 specifies a plain device-scoped revoke).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Cleanup job for stale `login_failures` (> 24 h) and revoked/expired sessions (plan follow-up).
- Password rehash on login when `needs_rehash` (plan follow-up).
- `rbac/origins.py` keeps its loopback host set private; `auth/service.py` has a small duplicate `_is_loopback` — could become a public helper in `origins.py` (USR-03e will need it too).
- Owner: reword R3 for login (A1).

### Scope self-check
- Could the diff be smaller? No — every file is in the plan's list; app + migration diff is ~1 090 lines (incl. the 118-line migration; plan estimated ~480 + ~120 — the SQLAlchemy builders and docstrings are more verbose than estimated), tests ~1 220.
- Diff size: +2 415 / −12 lines (34 files; app + migrations +1 087 / −11, tests +1 222, docs/env the rest).
