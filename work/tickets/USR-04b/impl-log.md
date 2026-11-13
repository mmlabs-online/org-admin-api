# Implementation log — USR-04b

## Attempt 1 — 2026-11-13

### Files touched
| File | Required because |
|---|---|
| `app/modules/auth/password_reset.py` (new) | `PasswordResetService.forgot/reset`, `RESET`, `RESET_TTL` (30 min), `RESET_PATH` (AC 1, 5–14) |
| `app/modules/auth/email_verification.py` | shared `daily_cap_allows(tokens, sends, purpose, daily_cap)` (global lock + count + rule); `prepare` uses it (AC 7) |
| `app/modules/auth/email_throttle.py` | `VERIFY_CAP_PERCENT = 80`, pure `cap_allows(...)` (AC 7) |
| `app/modules/auth/repository.py` | `EmailTokenRepository.find_active_user_id`, `count_issued_24h` (total, verify via `FILTER`); `EmailSendLogRepository.lock_global`; removed unused `count_global_24h` (AC 7, 11) |
| `app/modules/sessions/repository.py` | `revoke_all_accounts_of_user` (one UPDATE) (AC 9) |
| `app/modules/users/repository.py` | `set_password_hash` (AC 8) |
| `app/modules/auth/router.py` | `POST /password/forgot`, `POST /password/reset`; `_app: AppContextDep` on signup; `rate_limit("verify_email_resend")` on resend (AC 1–4, 8, 15, 16) |
| `app/modules/auth/schemas.py` | `ForgotPasswordRequest`, `ResetPasswordRequest` (AC 1, 8, 11) |
| `app/modules/email/messages.py` | `RESET_SUBJECT`, `reset_message` (VN then EN, 30 min, "password unchanged") (AC 5) |
| `app/core/rate_limit.py` | scopes `verify_email_resend`, `forgot`, `reset` (AC 4, 8, 16) |
| `app/core/config.py` | six new rate-limit settings (`ge=1`); `email_daily_cap` comment (AC 17) |
| `.env.example` | six keys without values; `EMAIL_DAILY_CAP` comment (AC 17) |
| `tests/test_password_reset_api.py` (new) | forgot/reset/cap API tests (AC 1–13, 15) |
| `tests/test_email_throttle.py` | `test_daily_cap_allows` (AC 7) |
| `tests/test_login_concurrency.py` | `test_login_blocked_by_reset_lock_reads_new_password` (AC 14) |
| `tests/test_auth_app_context.py` | `test_signup_403_when_signup_hook_overridden` (AC 15) |
| `tests/test_email_verification_api.py` | `test_resend_per_ip_429` (AC 16) |
| `docs/consumers.md` | "Password reset" section; `/reset-password` page requirement (AC 18) |
| `docs/deploy.md` | bootstrap: forgot then reset; corrected "revokes every device session"; ≤ 15 min token note (AC 18) |
| `docs/adr/0007-email-verification-and-delivery.md` | "Amended by USR-04b" notes on §2, §3, §4 + Consequences (AC 18) |
| `docs/identity-architecture.md` | §4 forgot/reset rows (AC 18) |

### Tests added / changed
- test_password_reset_api › test_forgot_known_user_202_sends_reset_link, test_forgot_link_uses_request_origin, test_forgot_unknown_email_202_no_mail, test_forgot_disabled_user_202_no_mail, test_forgot_per_email_limit_silent_202, test_forgot_daily_per_email_limit_silent_202, test_forgot_limit_applies_to_unknown_email, test_forgot_new_link_supersedes_previous, test_forgot_turnstile_failed_403_no_writes, test_forgot_without_app_header_403_no_side_effects (3 cases), test_forgot_unregistered_origin_403_no_side_effects (5 cases), test_forgot_403_when_link_origin_overridden, test_forgot_per_ip_429, test_forgot_validation_422 (4 cases), test_forgot_logs_free_of_token_and_address, test_unknown_email_forgot_does_not_consume_daily_cap, test_verify_share_exhausted_reset_still_sent, test_forgot_daily_cap_reached_202_logged, test_reset_happy_path_204_no_cookie_new_password_works, test_reset_revokes_all_account_sessions_every_device, test_reset_weak_password_422_token_still_usable (2 cases), test_reset_used_token_422, test_reset_expired_token_422, test_reset_superseded_token_422, test_reset_verify_token_422, test_reset_unknown_token_422, test_reset_disabled_user_422_token_not_consumed, test_reset_malformed_token_422 (5 cases, incl. `new_password` > 128), test_reset_supersedes_other_reset_and_verify_tokens, test_reset_clears_login_failures, test_reset_sets_email_verified_at_if_null, test_reset_keeps_existing_email_verified_at, test_reset_per_ip_429, test_reset_logs_only_user_id
- test_email_throttle › test_daily_cap_allows (12 cases)
- test_login_concurrency › test_login_blocked_by_reset_lock_reads_new_password (real `get_session`; a second connection holds `FOR UPDATE`, the test polls `pg_stat_activity` until the login waits on a lock, then changes the hash and commits; every wait bounded by `TIMEOUT` = 10 s; the existing `race` fixture deletes the user, app, login failure row and sessions)
- test_auth_app_context › test_signup_403_when_signup_hook_overridden
- test_email_verification_api › test_resend_per_ip_429
- The existing `test_daily_cap_resend_429_and_signup_still_201` is unchanged and green.
- Sanity check: with the route-level `_app: AppContextDep` removed from signup and forgot, both override tests fail (then restored).

### Checks (actual output summary)
- Postgres: already running on localhost:5432 (`pg_isready` OK; `org_admin` and `org_admin_test` exist). Nothing started or created.
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 1: ruff `All checks passed!`, format `317 files already formatted`, mypy `Success: no issues found in 78 source files`, pytest `959 passed in 36.04s`.
- Same, run 2: same lint/format/mypy lines, pytest `959 passed in 32.84s`.
- `alembic check` (against `org_admin_test`, at head): `No new upgrade operations detected.` (no schema change). Against the local dev `org_admin` it reports "Target database is not up to date" only because that database is not migrated locally.

### Out-of-plan files
None.

### Deviations from plan
- `PasswordResetService` takes all its collaborators (`email_hasher`, `daily_cap`, `verifier`, `policy`, `hasher`) in the constructor, so both routes resolve the Turnstile verifier, password policy and hasher dependencies even though forgot uses no policy/hasher and reset uses no verifier/cap (app-state lookups only; keeps one constructor shape).
- Plan's `test_forgot_without_app_header_403_no_side_effects` / `test_forgot_unregistered_origin_403_no_side_effects` are parametrized (missing/empty/malformed key; unknown, disabled app, missing, `null`, foreign origin).
- `test_reset_superseded_token_422` uses two direct `issue` calls; the forgot-path supersede is `test_forgot_new_link_supersedes_previous`.

### Follow-ups (noticed, not done)
- F1 (plan): CLAUDE.md R3 class 3 should say signup and forgot also require `X-MM-App` + Origin (owner edit).
- F2 (plan): duplicate sign-up 409 remains an enumeration oracle (USR-04 A7).
- USR-04d items (dispatcher `aclose` lifespan, retention cleanup, daily-cap alert runbook, route-level `AppContextDep` on resend) were not touched.

### Scope self-check
- Could the diff be smaller? No: production code ~330 lines as estimated; the rest is the planned tests and docs.
- Diff size: +1298 / -23 lines (production ~+330, tests ~+873, docs ~+85).
