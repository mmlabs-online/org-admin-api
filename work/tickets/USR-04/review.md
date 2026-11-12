# Review — USR-04

## Attempt 1 — 2026-11-12
Verdict: PASS

Scope judged against plan.md (slice USR-04). Forgot/reset is in USR-04b, so it is not missing here.

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0. `ruff check` → All checks passed; `ruff format --check` → 293 files already formatted; `mypy app` → Success, 77 source files; `pytest -q` → **861 passed in 35.63s**. This matches impl-log (861 passed). Format reports 293 files where impl-log has 292. `git status` is clean, so the extra file is git-ignored. Not material.
- Migration round trip on `org_admin_test` (`APP_ENV`/`APP_VERSION` unset): `upgrade head` → `91a78d0496f9 (head)`; `downgrade -1` → `Running downgrade 91a78d0496f9 -> 83936724f780`; `upgrade head` → `Running upgrade 83936724f780 -> 91a78d0496f9`; `alembic check` → `No new upgrade operations detected.`

### Acceptance criteria (plan.md table, 1–17)
- [x] 1 `EmailSender` + Gmail SMTP from env + fake: `app/modules/email/sender.py:34-129`, `tests/fakes.py::FakeEmailSender`. `.env.example:34-48` lists the keys with no values and marks the secret. Test: `test_gmail_sender_starttls_with_default_context_and_login`.
- [x] 2 Safe transport: STARTTLS with `ssl.create_default_context()` and no plain fallback (`sender.py:97-106`), 15 s timeout, `EmailMessage`. The error is raised outside the `except` block, so no chained reply text reaches a traceback. Tests: `test_starttls_not_offered_is_permanent`, `test_cert_error_is_permanent`, `test_classification` (asserts no `__cause__`/`__context__`).
- [x] 3 Async with retry/backoff and masked logs: `dispatcher.py:930-952`, logging through `_log` (purpose and user_id only). Tests: `test_transient_then_success`, `test_gives_up_after_max_attempts`, `test_permanent_not_retried`, `test_unexpected_error_does_not_kill_worker`, `test_logs_never_contain_token_link_or_address`, `test_signup_response_does_not_wait_for_sender`.
- [x] 4 Circuit breaker and global daily cap: `dispatcher.py:939-943`; `email_verification.py:346-347`. Tests: `test_quota_error_pauses_and_drops`, `test_auth_error_pauses`, `test_daily_cap_resend_429_and_signup_still_201`.
- [x] 5 Tokens: `secrets.token_urlsafe(32)`, stored only as SHA-256, 24 h TTL, single-use through the atomic `UPDATE … RETURNING` (`auth/repository.py:626-640`), and a new token supersedes older ones (`issue`). Tests: `test_token_stored_hashed_only` (also checks the 24 h lifetime and that the raw token appears nowhere in the table dump), `test_verify_used_token_invalid_when_not_verified`, `test_verify_expired_token_invalid`, `test_resend_invalidates_previous_token`, `test_email_schema.py`.
- [x] 6 Sign-up sends only after commit: `SessionDep` is function-scoped (`app/api/deps.py`, commit happens before the response), and the email goes through `BackgroundTasks` (`auth/deps.py:225-242`). Tests: `test_signup_sends_verification_link_to_app_origin`, `test_signup_without_app_header_uses_link_base`, `test_signup_failure_sends_nothing` (strict client), `test_signup_bad_app_header_403_no_user`.
- [x] 7 Link origin comes only from the allow-listed request `Origin` or `EMAIL_LINK_BASE_URL`, never from Host or X-Forwarded-Host (`auth/deps.py:203-222`). Test: `test_link_ignores_host_and_forwarded_host`.
- [x] 8 Verify sets `email_verified_at`, after which `/auth/token` works: `test_verify_then_token_works` (403 `email_unverified`, then 204, then 200).
- [x] 9 One uniform 422 `token_invalid`, `validation_error` on malformed input, idempotent 204: `test_verify_invalid_tokens_uniform_422` (unknown, superseded), the used/expired tests (same `TOKEN_INVALID` body), `test_verify_idempotent_when_already_verified`, `test_verify_disabled_user_token_invalid` (token stays unused), `test_verify_malformed_token_422` (5 cases; token not echoed).
- [x] 10 Resend limit of 1/min and 5/day under an advisory lock, with `Retry-After`: `email_throttle.py`, `auth/repository.py:664-704`. Tests: `test_resend_429_within_a_minute_of_signup`, `test_resend_429_after_five_a_day`, `test_email_throttle.py` (9 cases).
- [x] 11 Resend is cookie class: 401/403/409/422. Tests: `test_resend_requires_cookie`, `test_resend_foreign_account_401`, `test_resend_disabled_user_401`, `test_resend_requires_app_header_and_origin` (3), `test_resend_already_verified_409`, `test_resend_validation_422`, `test_resend_ok_202`, `test_resend_forwards_rotated_cookie`.
- [x] 12 Per-IP limit on verify: scope `verify_email` (`rate_limit.py:103-113`). Test: `test_verify_rate_limited_per_ip`.
- [x] 13 Production gate: `config.py:189-215`; messages name the key, never the value. Tests: `test_smtp_required_in_production`, `test_email_link_base_url_rules`, `test_email_link_base_url_loopback_only_locally`, `test_smtp_optional_locally`.
- [x] 14 Migration: a single revision off `83936724f780`, `downgrade()` works (round trip above), models registered (`app/db/models.py:4,15-16`), `alembic check` is clean, and CHECK texts are identical in the model and the migration.
- [x] 15 Runbook: `docs/deploy.md` "Email (USR-04)" covers the keys, deploy prerequisites and merge order, Gmail limits, what to do when they are hit, provider switch, App Password rotation, and a smoke check.
- [x] 16 Consumer contract: `docs/consumers.md` "Email verification" covers the link format, fragment handling, and the verify/resend error tables.
- [x] 17 `make check` green (above).

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: routers call `EmailVerificationService`; ORM access stays in repositories. The hook receives a plain `schedule` callable, so the service layer does not import FastAPI.
- R2: migration reviewed and reversible.
- R3: `verify-email` is public class 3 with a per-IP limit. `verify-email/resend` is cookie class 2 (`AppContextDep` + `DeviceDep`).
- R5: config comes from env, and `.env.example` has no values.
- R6: the envelope is used throughout, including 429 + `Retry-After`.
- R11: tokens are stored only as hashes; logs carry no address, link or token.
- R7: the diff is larger than the plan's estimate (~946 vs 650–750 production lines incl. migration), but every file maps to a plan row. The one out-of-plan file, `app/modules/users/repository.py` (`get_for_update`, `mark_email_verified`, +14 lines), is justified in impl-log: ORM access for the user row lock belongs in `UserRepository` under R1. The documented deviations (verify ordering, `verification_message(link)`, inline email normalization) are equivalent or safer. No unplanned features.

### Nits (do not block)
- `app/core/config.py:10` imports `app.modules.rbac.origins` (core → module). It is a pure function and works today, but moving `normalize_origin` / `is_loopback_origin` to `app/core` later would keep the dependency direction clean.
- `router.py:786-788` builds `EmailVerificationService` with `email_hasher` / `daily_cap` for `verify`, which uses neither.
- When resend fails (409/429) after a device-cookie rotation, the rotated cookie is not forwarded. This is the same existing pattern as `/token`, not introduced here.
- `count_global_24h` is not serialized across emails, so concurrent sends can overshoot `EMAIL_DAILY_CAP` slightly. That is fine for a soft cap.

### Follow-ups (out of scope)
- Plan F1–F6 as written (CLAUDE.md R11 cookie wording vs D20; sign-up 409 enumeration; retention for `email_send_log` / used tokens; bind tokens to the email once email change exists; ticket Scope path wording; auth-client methods).
- Owner action: the deployment-repo PR (GitHub secrets for SMTP, envfile validation, `EMAIL_LINK_BASE_URL` in compose) must merge **before** the release containing USR-04. Without it, `migrate` and the API refuse to start in production.
- `EmailDispatcher.aclose()` is not wired to app shutdown (there is no lifespan). Queued mail is lost on restart by design (A3).
