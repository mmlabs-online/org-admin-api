# Review — USR-04b

## Attempt 1 — 2026-11-13
Verdict: PASS

Scope note: `git diff origin/main...HEAD` also contains the USR-07 commits (cd3cafe…e6f0672), which are on this branch but not yet on `origin/main`. They were reviewed under USR-07. This review covers the USR-04b range `ac83285..HEAD` (commits 17fd053…d728304).

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0: ruff `All checks passed!`; format `318 files already formatted`; mypy `Success: no issues found in 78 source files`; pytest `959 passed in 30.72s`. The pass count matches impl-log (959). The format count is 318 here and 317 in impl-log. Both runs are clean, so this does not matter.
- `alembic check` (against `org_admin_test`): `No new upgrade operations detected.` No migration in the diff, as planned.

### Acceptance criteria
Plan AC numbers are used. The ticket's "optional `X-MM-App`" is overridden by the owner note of 2026-11-13. That is a recorded plan deviation, not a defect.

- [x] 1. Forgot → 202, empty body, `no-store`, for known, unknown, disabled and limited emails. Evidence: `router.py:246-284`; tests `test_forgot_known_user_202_sends_reset_link`, `test_forgot_unknown_email_202_no_mail`, `test_forgot_disabled_user_202_no_mail`, `test_forgot_per_email_limit_silent_202` (all pass).
- [x] 2. App context is required. A bad context gives 403 `app_header_required` or `origin_not_allowed` before Turnstile, before any write and before any mail. The link uses the validated Origin. Evidence: `router.py:258` (`_app: AppContextDep`) plus `get_link_origin`; tests `test_forgot_without_app_header_403_no_side_effects` (3 cases) and `test_forgot_unregistered_origin_403_no_side_effects` (5 cases) assert `fake_turnstile.calls == []`, 0 send-log rows, 0 tokens and 0 mails; `test_forgot_link_uses_request_origin`.
- [x] 3. Turnstile is always required: failure gives 403 with no writes. Evidence: `password_reset.py:62-63`; `test_forgot_turnstile_failed_403_no_writes`.
- [x] 4. Per-IP scope `forgot` → 429 with `Retry-After`. Evidence: `rate_limit.py` scope plus `router.py:250`; `test_forgot_per_ip_429`.
- [x] 5. Link `{origin}/reset-password#token=`, TTL 30 min, only the SHA-256 is stored, older links are superseded. Evidence: `password_reset.py:31-32,78-79`; `test_forgot_known_user_202_sends_reset_link` asserts lifetime == 30 min and hash == sha256(token); `test_forgot_new_link_supersedes_previous`.
- [x] 6. Silent per-email limit of 1/min and 5/24 h for every email. Both branches write the same `email_send_log` row. Unknown and disabled users get no mail. Evidence: `password_reset.py:64-74`; `test_forgot_daily_per_email_limit_silent_202`, `test_forgot_limit_applies_to_unknown_email`.
- [x] 7. Per-purpose daily cap, counted from `email_tokens` under the global advisory lock (verify ≤ 80 %, at least 1). Unknown emails do not count. A capped forgot answers 202 and logs. Evidence: `email_throttle.py:31-41`, `email_verification.py:44-54`, `repository.py:154-164,216-220`; `test_daily_cap_allows` (12 cases), `test_verify_share_exhausted_reset_still_sent`, `test_unknown_email_forgot_does_not_consume_daily_cap`, `test_forgot_daily_cap_reached_202_logged`. The existing `test_daily_cap_resend_429_and_signup_still_201` is still green.
- [x] 8. Reset → 204, no `Set-Cookie`, the new password works and the old one gets 401. There is a per-IP scope `reset`. Evidence: `router.py:287-314`; `test_reset_happy_path_204_no_cookie_new_password_works`, `test_reset_per_ip_429`.
- [x] 9. Reset revokes all account sessions on every device. Device sessions and other users' accounts are untouched. Evidence: `sessions/repository.py:95-105`; `test_reset_revokes_all_account_sessions_every_device` (both devices' `/token` → 401, other user → 200, `device_sessions.revoked_at` stays NULL).
- [x] 10. Weak password → 422 and the token stays usable. Evidence: the policy runs before consume (`password_reset.py:95-96` vs `:102`); `test_reset_weak_password_422_token_still_usable` (2 cases).
- [x] 11. Used, expired, superseded, verify-purpose, unknown token and disabled user → 422 `token_invalid`. A disabled user's token is not consumed. A malformed token → `validation_error`. Evidence: the seven `test_reset_*_422` tests, including `test_reset_disabled_user_422_token_not_consumed` and `test_reset_malformed_token_422` (5 cases).
- [x] 12. In the same transaction, reset supersedes reset and verify tokens, clears `login_failures` and sets `email_verified_at` (A8). Evidence: `password_reset.py:104-110`; `test_reset_supersedes_other_reset_and_verify_tokens`, `test_reset_clears_login_failures`, `test_reset_sets_email_verified_at_if_null`, `test_reset_keeps_existing_email_verified_at`.
- [x] 13. Logs carry no token, link or address. Evidence: `test_forgot_logs_free_of_token_and_address`, `test_reset_logs_only_user_id`.
- [x] 14. Login race: reset `get_for_update` (`password_reset.py:99`) conflicts with login's `get_by_email_for_share` (`auth/service.py:162`), which is held until commit. Evidence: `test_login_concurrency.py::test_login_blocked_by_reset_lock_reads_new_password` (401, 0 account sessions). The reverse order also works: under READ COMMITTED the revoke UPDATE runs after the lock wait and sees the login's committed session.
- [x] 15. Route-level `AppContextDep` on signup (`router.py:71`) and forgot (`router.py:258`). Evidence: `test_auth_app_context.py::test_signup_403_when_signup_hook_overridden`, `test_forgot_403_when_link_origin_overridden`.
- [x] 16. Per-IP `verify_email_resend` scope. Evidence: `router.py:221`; `test_email_verification_api.py::test_resend_per_ip_429`.
- [x] 17. All six settings come from env with `ge=1` (`config.py`). `.env.example` lists the keys without values.
- [x] 18. Docs: `consumers.md` has a "Password reset" section with status tables, the ≤ 15 min access-token note and the effects; `deploy.md` bootstrap says "forgot, then reset" and the device-session wording is corrected; ADR-0007 has amendments on §2, §3 and §4 plus Consequences; identity-architecture §4 rows are updated.
- [x] 19. `make check` is green and `alembic check` shows no drift.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the routes only build the service and handle HTTP. `PasswordResetService` raises `AppError` subclasses. All SQL is in repositories (`set_password_hash`, `revoke_all_accounts_of_user`, `find_active_user_id`, `count_issued_24h`, `lock_global`).
- R2: no schema change.
- R3: forgot and reset are class 3 public endpoints with per-IP limits. Forgot also requires app context, as the owner decided.
- R5: config comes from env only.
- R6: only existing error codes are used.
- R7: every changed file is in the plan's file list. The only extra files are the ticket's own `work/` artifacts and the USR-04d ticket/backlog row, both from the plan's split proposal.
- R8: tests cover 202/204, 403, 422 and 429 for each new endpoint. Neither endpoint addresses a resource, so 404 does not apply.
- R11: tokens are stored as SHA-256 only; `new_password` is a `SecretStr`; Argon2 runs off the event loop before the row lock; reset sets no cookie; logs contain user ids only.

Lock ordering: forgot takes the per-email advisory lock, then the global lock, then the user row `FOR SHARE`. Verify/resend take the per-email lock, then the global lock, and no user lock. Reset and verify-email take the user `FOR UPDATE` and no advisory locks. There is no cycle.

### Nits (do not block)
- `router.py:246-314`: both routes build `PasswordResetService` with every collaborator. Forgot resolves the policy and hasher it never uses; reset resolves the Turnstile verifier and daily cap it never uses. These are app-state lookups only, and impl-log records this as a deviation. Splitting the constructor, or passing collaborators per method, would read more clearly.
- `password_reset.py:76-77`: a forgot for an active user holds the global cap advisory lock while it waits for `FOR SHARE` on the user row. If a reset or verify-email holds `FOR UPDATE` on that user at the same moment, every mail-sending request waits behind it for a few milliseconds. This is harmless at current volume.
- The ruff format file count differs by one from impl-log (317 vs 318). Both runs are clean.

### Follow-ups (out of scope)
- F1 (plan): CLAUDE.md R3 class 3 should say that signup and forgot also require `X-MM-App` plus Origin. This is an owner edit.
- F2 (plan): the duplicate sign-up 409 is still an enumeration oracle, which weakens forgot's always-202.
- USR-04d: dispatcher `aclose` lifespan, retention cleanup (with a possible `email_tokens(created_at)` index for the cap count), the daily-cap alert runbook, and route-level `AppContextDep` on resend.
