# Review — USR-03

## Attempt 1 — 2026-11-12
Verdict: PASS

Scope reviewed: plan.md slice 1 (device/account sessions, `mm_device` cookie with rotation, grace
and reuse detection, login with Origin/`X-MM-App`, D11 brute force, Turnstile after 3, logout).
The token endpoint, JWKS and dynamic CORS moved to USR-03c / USR-03e; they are not counted as missing.
Diff: `git diff origin/main...HEAD` (commit 1a934f8 for code; the earlier commits are consults and the plan).

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` → exit 0:
  `ruff check` All checks passed; `ruff format --check` 225 files already formatted; `mypy app`
  Success: no issues found in 62 source files; `pytest -q` **619 passed** in 29.37s. This matches
  impl-log (619 passed). The impl-log says 224 formatted files and I got 225; both runs pass, so
  this does not matter.
- Migration against `org_admin_test` (`alembic -x url=…`): `upgrade head` → `current` =
  `83936724f780 (head)`; `downgrade -1` → "Running downgrade 83936724f780 -> b638a7c84c31";
  `upgrade head` → "Running upgrade b638a7c84c31 -> 83936724f780"; `alembic check` → "No new
  upgrade operations detected." I compared `\d` output for `device_sessions`, `account_sessions`
  and `login_failures` with the plan's data model (CHECKs, partial unique and partial indexes, FKs
  with `ON DELETE CASCADE`, `timestamptz`, `inet`, `varchar(512)`). They match.
- The test suite's session fixture also runs `downgrade base` and then `upgrade head` on every run,
  so the downgrade path is tested too.

### Acceptance criteria
- [x] AC1 Tables and migration (reversible, models registered, no `refresh_tokens`, `alembic check` clean). Evidence: `migrations/versions/20261112_83936724f780_create_sessions.py`; `app/db/models.py:5,8`; `tests/test_sessions_schema.py` (9 tests, including `test_no_refresh_tokens_table`, `test_one_active_account_session_per_device_user` and the cascade tests); the commands above.
- [x] AC2 Cookie attributes and hash-only storage. Evidence: `app/modules/auth/cookies.py:27-37` (no `domain`), `app/modules/sessions/service.py:23-30` (`token_urlsafe(32)`, sha256); `test_auth_login.py › test_login_sets_device_cookie_attributes` (HttpOnly, Secure, Lax, Path, no Domain, Max-Age 30 d, ≥ 43 chars) and `› test_only_cookie_hash_is_stored`.
- [x] AC3 Login creates or rotates the device session, upserts the account session, returns the exact body with `no-store`, and gives one identical 401 for unknown email, wrong password and disabled user. Evidence: `app/modules/auth/service.py:764-808`, `router.py:521-560`; `test_login_creates_device_and_account_session` (exact keys), `test_login_again_reuses_account_session`, `test_two_accounts_on_one_device`, `test_invalid_credentials_identical_bodies[unknown|wrong_password|disabled]`, `test_unknown_email_runs_dummy_verify`.
- [x] AC4 Rotation happens on login and after 24 h. The previous value is accepted within 60 s without re-rotation. Reuse after the grace revokes the device and its accounts, and that revocation is committed before the 401. Evidence: `sessions/service.py:190-229`, `sessions/repository.py:1023-1042` (CAS); `test_device_sessions.py › test_rotates_after_24h`, `› test_no_rotation_before_24h`, `› test_prev_value_within_grace_accepted_without_rotation` (logout and login), `› test_reuse_after_grace_revokes_device` (strict client), `› test_reuse_revocation_needs_commit` (control test: removing the commit makes the revocation disappear), `› test_concurrent_rotation_cas_loser_accepted`.
- [x] AC5 Fixation: login on an empty device mints a new session, the previous value is limited to accounts created before `rotated_at`, and multiple cookies get a 401 with no revocation. Evidence: `sessions/service.py:1231-1255`, `repository.py:1098-1113`; `test_login_on_empty_device_mints_new_session`, `test_prev_value_cannot_use_accounts_added_after_rotation`, `test_multiple_device_cookies_rejected`.
- [x] AC6 Idle and absolute expiry. Evidence: `sessions/service.py:1183-1188`; `test_idle_expired_device_rejected`, `test_absolute_expired_device_rejected`, `test_revoked_device_rejected` (each also checks that login then mints a new device).
- [x] AC7 Logout one account vs all; a foreign id is a no-op; body must hold exactly one of the two fields. Evidence: `auth/service.py:818-828`, `schemas.py:625-637`, `router.py:563-585`; `test_auth_logout.py` (5 tests, 6 validation cases, `test_logout_without_cookie_401`).
- [x] AC8 Brute force: per-IP 20/min, per-email backoff 1/2/4/8/15 min before any Argon2 work, Turnstile at ≥ 3 failures, unknown emails counted like known ones, reset on success and after 24 h, failure committed before the 401. Evidence: `auth/throttle.py`, `auth/repository.py`, `auth/service.py:772-800`; `test_login_throttle.py` (all 12 planned tests, including `test_unknown_email_counted_like_known`, which compares identical status/body/`Retry-After` sequences, and `test_failure_counter_persists_despite_401` with the strict client).
- [x] AC9 CSRF via `X-MM-App` and Origin on login and logout, with loopback origins ignored in production. Evidence: `auth/service.py:711-735`, `deps.py:257-268`. On logout, `AppContextDep` is declared before `DeviceDep` (`router.py:569-577`), so a cross-origin request cannot trigger rotation or reuse revocation. Tests: `test_auth_app_context.py` (7 tests, parametrized).
- [x] AC10 No secrets in responses or logs, and `*` is refused in `CORS_ORIGINS`. Evidence: `test_responses_never_echo_secrets` (bodies and `caplog`; password, cookie, Turnstile token, email, cookie hash) and `test_config.py › test_cors_origins_wildcard_refused`. Log lines carry only event names and session ids (`auth/service.py:799,807`, `sessions/service.py:1200,1227`).
- [x] AC11 `IDENTITY_HMAC_KEY` is required in staging/production and must be ≥ 32 chars. Evidence: `config.py:126-136`; `test_identity_hmac_key_required_in_production`, `test_identity_hmac_key_too_short`, `test_identity_hmac_key_optional_locally`.
- [x] AC12 `make check` is green (see above).

### Scope and size (R7)
The production diff is about 1,090 lines (including the 118-line migration). The plan estimated about 600. Tests are about 1,220 lines against an estimate of about 650.
- Every changed production, test and doc file is in the plan's "Files to create / modify" list. There are no out-of-plan files.
- The `work/` changes outside USR-03 (backlog, ADM-002, USR-03b/c/e, USR-04, USR-06 tickets) come from the orchestrator's plan/split commit 45e1670, not from the implementer.
- The extra size comes from three things:
  - verbose SQLAlchemy CAS, upsert and revoke builders, with every statement using DB `now()` and `synchronize_session=False`;
  - the docstrings that ADR-0004 points to;
  - six small extra tests that strengthen planned criteria (`test_login_body_validation`, `test_device_defaults`, `test_login_failures_failures_positive`, `test_origin_is_normalized`, `test_identity_hmac_key_optional_locally`, `test_account_session_unique_while_active`).
- I found no extra endpoints, settings or behaviour beyond the plan.
- The impl-log lists three deviations, all minor and in the plan's spirit:
  - `resolve` takes a list of cookie values and has a `commit_on_reuse` flag. The flag implements Approach 5.7: at login, reuse is revoked inside the successful login transaction.
  - `IDENTITY_HMAC_KEY` must be ≥ 32 chars in every environment when it is set. This is stricter than the plan.
  - Logout does not apply `accounts_created_before`. This follows plan Approach 6, and revoking an account only reduces access.

Verdict on size: **justified, no scope creep.**

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: routers only read headers, body and cookies and set cookies and `Cache-Control`. Services raise `AppError` subclasses. All SQL is in `sessions/repository.py` and `auth/repository.py`. The commit-before-raise goes through `repo.commit()`, as the plan decided.
- R2: one hand-written migration with a working downgrade, and the models are registered.
- R3: login is public (rate-limited, Turnstile after 3) plus the Origin/`X-MM-App` check (plan assumption A1, waiting for the owner to reword R3). Logout is cookie class.
- R4: login/logout audit events wait for ADM-004. ADR-0004 records this.
- R5: the new settings live in `config.py` and are listed in `.env.example` without values. The HMAC key is a `SecretStr`.
- R6: every error goes through the envelope, and 429 carries `Retry-After`.
- R9: the paths are under `/api/v1/auth`, ids are UUIDs and timestamps are `timestamptz`.
- R11: Argon2 with a dummy verify, the hash-only cookie, rotation with a 60 s grace and reuse revocation, D11 backoff, the identical 401, and the Origin/`X-MM-App` check.

### Nits (do not block)
- `auth/service.py:680,704-708`: `_LOOPBACK_HOSTS` and `_is_loopback` copy the private set in `rbac/origins.py:17`. A public helper in `origins.py` would avoid this; USR-03e will need it too. The impl-log already lists this as a follow-up.
- `docs/identity-architecture.md` §4: the `POST /api/v1/auth/token` row still names ticket `USR-03`. It should say `USR-03c` after the split.
- `router.py:510` `_ERR` describes every error response only as "Error envelope". The `responses=` map could name the codes (`invalid_credentials`, `origin_not_allowed`, …) for OpenAPI readers.

### Follow-ups (out of scope)
- **Concurrent bypass of the per-email gates.** `LoginService.login` reads `login_failures` (`auth/service.py:774`) before the Argon2 check and records the failure afterwards, without a lock. A burst of parallel attempts for one email that all read `failures < 3` (or "not blocked") all skip Turnstile and backoff. The counter stays exact because the upsert is atomic, so only the gates race. Per-IP limits (20/min) bound this from one IP, but not across many IPs. A possible hardening is an atomic "attempt" increment before verification, or a `SELECT … FOR UPDATE` on an upserted row. This needs a security decision, so it is not a rework item.
- These come from the plan and are still open: a cleanup job for stale `login_failures` and old sessions; password rehash on login; a global failure-rate Turnstile trigger; a `Sec-Fetch-Site` check; origin uniqueness across apps; clearing a tossed `Domain=` cookie; the owner rewording R3 for login (A1).
