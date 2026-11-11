# Review — USR-01

## Attempt 1 — 2026-11-11
Verdict: PASS

Scope judged against plan.md (slice 1). Ticket AC 6 (staff endpoints) is in USR-01b by the planner's split, recorded in ticket.md Notes.

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): green. `ruff check .` → All checks passed! · `ruff format --check .` → 100 files already formatted · `mypy app` → Success: no issues found in 36 source files · `pytest -q` → 50 passed in 0.85s. Matches impl-log, except the format file count (impl-log says 99). Tracked `.py` files: 50, and `ruff check --show-files` finds no untracked file. The pass/fail results agree, so this is environment noise and not a blocker.
- alembic check (`DATABASE_URL=…/org_admin_test uv run alembic check`): `No new upgrade operations detected.`
- Downgrade: `tests/conftest.py:32-38` runs `downgrade base` → `upgrade head` every session, so the new migration's `downgrade()` runs on every `make check` run.
- Extra sanity: the bundled list loads 38 451 entries, min length 8, and `password1234` is in it. The Dockerfile `COPY app ./app` ships `app/modules/auth/data/`. `.dockerignore` does not exclude it.

### Acceptance criteria
- [x] AC1 `users` table + migration (citext unique email, nullable `email_verified_at`, status `active|disabled`), and downgrade works. Evidence: `migrations/versions/20261111_7b847bdc5230_create_users.py:24-56` (`CREATE EXTENSION IF NOT EXISTS citext`, `ck_users_status_valid`, `uq_users_email`; downgrade drops the table, then the extension). Model in `app/modules/users/models.py:16-34`, registered in `app/db/models.py:5`. Tests: `test_users_model.py › test_status_check_rejects_unknown_value`, `› test_email_unique_is_case_insensitive`, `› test_email_roundtrips_as_str` (asserts `pg_typeof = citext`). alembic check is clean.
- [x] AC2 Argon2id with parameters from config, minimum length, common-password rejection, hash never returned or logged. Evidence: `app/modules/auth/passwords.py:51-82` (`Type.ID`, params from `Settings`, threads + `CapacityLimiter`), `:34-46` (policy: NFKC, code points, case-insensitive list). OWASP floor in `app/core/config.py:59-65`. `User.__repr__` has no hash (`models.py:33-34`). No logger or print touches a password or hash. Tests: `test_passwords.py` (6 tests, incl. `$argon2id$v=19$m=1024,t=1,p=1$` prefix and `needs_rehash`), `test_users_model.py › test_repr_has_no_password_hash`, `test_auth_signup.py › test_signup_response_has_exact_keys`.
- [x] AC3 `POST /api/v1/auth/signup`. 201 unverified, 409 for a duplicate email in any case, 422 for a weak password, a bad email, an unknown field or a too-long password, 403 for invalid Turnstile, 429 with `Retry-After`, and minimal bodies. Evidence: `app/modules/auth/router.py:22-46`, `service.py:28-43`, `errors.py`. Tests: `test_auth_signup.py` (12 tests, all pass): `test_signup_creates_unverified_active_user`, `test_duplicate_email_any_case_returns_409` (no email or id in the body), `test_weak_password_too_short_returns_422`, `test_common_password_returns_422`, `test_invalid_email_returns_422`, `test_unknown_field_and_oversized_password_return_422`, `test_invalid_turnstile_returns_403_and_creates_no_user`, `test_signup_rate_limited_returns_429_with_retry_after` (a malformed body is still counted), `test_password_never_echoed_in_error_bodies`.
- [x] AC4 Turnstile is behind an interface, with a test fake; the secret comes from env. Evidence: `app/modules/auth/turnstile.py:21-67` (Protocol, Cloudflare implementation that fails closed, logs only status, exception class or error codes, `SecretStr`). `tests/fakes.py` provides the fake, wired via `dependency_overrides` in `conftest.py:73`. `.env.example` has `TURNSTILE_SECRET_KEY=` with no value. Tests: `test_turnstile.py` (8 tests: success, failure, hostname, network error, 5xx, missing secret makes no call, test secret in production, form fields sent).
- [x] AC5 Shared per-IP limiter with configurable limits. Evidence: `app/core/rate_limit.py` (`RateLimiter` Protocol, `InMemoryRateLimiter` with eviction and key cap, `rate_limit(scope)` dependency) and `app/core/client_ip.py` (CF header only from trusted CIDRs, X-Forwarded-For never read, IPv6 /64). Limits are in `config.py:40-41`. Tests: `test_rate_limit.py` (9 tests) and `test_auth_signup.py › test_turnstile_receives_client_ip` (a spoofed CF/X-Forwarded-For header from an untrusted peer is ignored).
- [x] AC6: moved to USR-01b (the split is recorded in plan.md and ticket.md, and `work/tickets/USR-01b/ticket.md` exists).
- [x] AC7 Tests for every bullet, including the case-insensitive duplicate and Argon2id verification. Every test in the plan's "Tests to write" table exists under the planned name and passes.
- [x] AC8 422 responses never echo submitted values. Evidence: `app/core/errors.py:72-81` keeps only `type`/`loc`/`msg`. Tests: `test_errors.py › test_validation_error_details_have_no_input_or_ctx`, `test_auth_signup.py › test_password_never_echoed_in_error_bodies`.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the router only wires dependencies and maps the response. The service raises `AppError` subclasses only. The repository is SQLAlchemy only, and its savepoint keeps the outer transaction usable after a unique violation.
- R2: the migration is hand-edited and reversible, the model is registered, and `alembic check` is clean.
- R3: signup is auth class (3), public, under `/api/v1/auth` (D14), with the per-IP limit and Turnstile.
- R4: not applicable (signup is not an admin action).
- R5: every setting is in `Settings`. `.env.example` lists the keys without values. The secret is a `SecretStr` and is never logged.
- R6: the error envelope is used throughout. 429 is required by the AC, and amending R6 to list it is follow-up F1.
- R9: the path follows D13, with UUIDs and `timestamptz`.
- R10: no social login or OIDC provider.
- R11: Argon2id, Turnstile always on sign-up, and no password, hash or full email in any response, log or error.
- Out-of-plan deviations (`env_ignore_empty=True`, the `client_ip` signature, the ruff S105/S106 ignore for tests) are inside planned files and justified in impl-log.

### Nits (do not block)
- `app/core/client_ip.py:20`: the trusted CIDRs are parsed with `ip_network` on every request. They could be parsed once in the settings validator.
- `app/core/rate_limit.py:67-71`: an unknown scope raises `KeyError` at request time, not when `rate_limit("…")` is declared. Validating the scope inside `rate_limit()` would fail at import.
- Malformed JSON (a body that does not decode) is rejected by FastAPI before dependencies run, so it is not counted by the limiter. Those requests cost almost nothing (no DB, no hashing), so this is acceptable.
- impl-log reports "99 files already formatted"; the reviewer saw 100. The results are identical.
- `test_on_signup_hook_called_with_user_id` duplicates part of `make_client`. Letting `make_client` accept extra dependency overrides would remove that (already in impl-log follow-ups).

### Follow-ups (out of scope)
- Plan F1–F12 stand. F9 (`TRUSTED_PROXY_CIDRS`, `TURNSTILE_SECRET_KEY`, `TURNSTILE_ALLOWED_HOSTNAMES` in the deployment repo) and F10 (privilege for `CREATE EXTENSION citext`) are release prerequisites. F2 (409 vs 202 for a duplicate email) needs the owner's answer before the first release that includes signup.
- A future refresh of the bundled password list should use the renamed SecLists path (`xato-net-10-million-passwords-100000.txt`).
