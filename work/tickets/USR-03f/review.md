# Review — USR-03f

## Attempt 1 — 2026-11-12
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): `ruff check` All checks passed! · `ruff format --check` 236 files already formatted · `mypy app` Success: no issues found in 62 source files · `pytest` **639 passed** in 27.58 s. Matches impl-log (639 passed); the only difference is the format count (236 vs 235), which is a nit.
- alembic check (no migration in the diff; run against `org_admin_test`): `No new upgrade operations detected.`
- Flakiness: `test_login_concurrency.py` + `test_session_scope.py` + `test_device_cookie.py` + `test_auth_logout.py` 15× in a row: 15/15 `30 passed`. `test_login_concurrency.py` + `test_login_throttle.py` + `test_auth_login.py` 5× more: 5/5 `39 passed`.
- Leftover rows in `org_admin_test` after all runs: apps = 1 (`admin` seed only), users = 0, login_failures = 0, device_sessions = 0, account_sessions = 0.
- `grep -rn "Depends(get_session" app` → one line (`app/api/deps.py:11`). `record_failure` has no references left. `git diff origin/main...HEAD -- CLAUDE.md` is empty.

### Acceptance criteria
- [x] AC1 S2 `SessionDep` function-scoped and declared once — `app/api/deps.py:11`; `test_session_scope.py › test_session_dep_is_function_scoped`, `› test_get_session_declared_once` pass.
- [x] AC2 S2 commit before `http.response.start` for login, logout `{all}` and a rotating logout — `test_commit_precedes_response_start[login|logout_all|logout_rotating]` (strict app plus recording ASGI wrapper) and `test_login_concurrency.py › test_real_get_session_commits_before_response_start` (real `get_session`, class-level commit patch) pass.
- [x] AC3 S2 failed final commit → 500, no `Set-Cookie`, DB unchanged — `test_failed_commit_sends_no_set_cookie[3 cases]` pass. The test checks `calls == fail_on_call`, so it really fails the final commit, and login's reservation commit goes through first.
- [x] AC4 S1 atomic decision + increment, reservation committed before Argon2, no lock during Argon2 or Turnstile — `app/modules/auth/repository.py:42-69` (`ON CONFLICT DO UPDATE … WHERE allowed … RETURNING`); `app/modules/auth/service.py:151-171` (phase 1 with Turnstile outside any lock, then reserve, `set_block`, commit before the user lookup and verify). `test_parallel_at_two_failures…`, `test_parallel_at_four_failures_one_401_one_429` and `test_row_not_locked_during_password_check` (`FOR UPDATE NOWAIT` succeeds while verify is paused, and the count is already 2) pass. The impl-log mutation check (drop the `WHERE` → both parallel tests fail) shows the tests can detect the race.
- [x] AC5 S1 corrected criterion (D-1, recorded in ticket.md): at `failures=2`, one 401 `invalid_credentials` and one 403 `turnstile_required`, counter ends at 3 — `test_parallel_at_two_failures_one_401_one_turnstile_required` passes. An `asyncio.Barrier` makes both requests finish phase 1 before either reserves, so the race is real.
- [x] AC6 D11 behaviour unchanged — `tests/test_login_throttle.py` is untouched in the diff and all its tests pass.
- [x] AC7 S3 cookie `__Host-mm_device`, `Path=/`, Secure, HttpOnly, Lax, Max-Age, no Domain; clearing uses identical attributes — `app/modules/auth/cookies.py:12-44`; `test_auth_login.py › test_login_sets_device_cookie_attributes` (asserts the raw header), `test_auth_logout.py › test_logout_all_revokes_device_and_clears_cookie`, `test_device_cookie.py › test_clear_header_matches_set_attributes` pass.
- [x] AC8 S3 exact, case-sensitive name match; look-alikes are ignored; duplicate `__Host-` values → 401 — `test_legacy_and_lookalike_names_ignored`, `test_duplicate_host_cookie_still_401`, `test_device_sessions.py › test_multiple_device_cookies_rejected` pass.
- [x] AC9 S3 non-auth routes ignore the cookie — `test_non_auth_route_ignores_cookie` passes.
- [x] AC10 OpenAPI `DeviceCookie` scheme — `app/modules/auth/router.py:42-44`; `test_openapi_device_cookie_scheme` passes.
- [x] AC11 N1: logout `{account_id}` with the previous value is limited to sessions with `created_at < accounts_created_before` (strict); `{all}` revokes the whole device — `app/modules/auth/service.py:214-217`, `app/modules/sessions/repository.py:137-150`; the three `test_logout_prev_value_*` / `test_logout_all_prev_value_revokes_device` tests pass.
- [x] AC12 Docs: spec §5.8 (lines 64, 70), identity-architecture.md (D20 at line 222, §4 intro, §5 S1, C1 note at line 233; D5/D6/D13 kept as history) and ADR-0004 (§1 cookie, legacy name ignored, §4, §9, §10, Consequences) are updated. CLAUDE.md is unchanged.
- [x] AC13 `make check` green (see above).

### Issues
None.

Correctness notes (checked, no action needed):
- Phase 1 and phase 2 interleaving: a request that phase 1 lets through without Turnstile is refused by the upsert `WHERE` once a concurrent reservation reaches 3. It then gets 403 `turnstile_required` (A2), or 429 if a block was set. The refused upsert still locks the row (Postgres `ON CONFLICT DO UPDATE` semantics), so the re-read is consistent. Nothing is written on refusal, so blocked or rejected attempts are not counted.
- A stale counter restarts at 1 regardless of `blocked_until`. This is safe: the longest block is 15 min, far shorter than the 24 h reset.
- Success at ≥ 3 failures increments the counter and then deletes the row in the same request. A crash between the reservation and the verify leaves the attempt counted (fail-closed).
- Function scope with the strict-client override: the override inherits the `Depends` scope. The ordering tests show this, and the impl-log mutation check (drop `scope="function"` → 7/8 fail) confirms they would catch a regression.
- Concurrency fixture: committed data, unique app key and user per test, cleanup in `finally`, every await bounded by 10 s. No leftover rows after 20 extra module runs.

### Rules R1–R11
No violations found.
- R1: the SQL stays in the repositories, the service decides, and only `AppError` subclasses are raised.
- R2: no schema change, and `alembic check` is clean.
- R3: no endpoint changes auth class.
- R5: no new config.
- R6: unchanged. The plain-text 500 on a failed commit predates this ticket (F1).
- R7: every changed file is in the plan.
- R8: tests exist for each item.
- R11: the cookie attributes change by the owner's decision, recorded as D20. CLAUDE.md R11/R3 wording is the owner's action (F5).

### Nits (do not block)
- `ruff format --check` reports 236 files here, and impl-log says 235. The test count matches, so this is probably counting noise, not a mismatch in substance.
- Docstrings still say `mm_device` in `app/modules/sessions/models.py:26` and `app/modules/sessions/errors.py:7` (impl-log follow-up; these files are outside the plan).
- The `Retry-After` test bound is ≤ 61 because `now()` is the transaction start time. This is fail-safe and documented in the test.

### Follow-ups (out of scope)
- F1–F5 from plan.md: R6 envelope for 500s; CSP on `/docs` or disabling it in production; `SameSite=Strict`; login with the previous cookie value creates an account session that client cannot see (USR-03b); owner rewords CLAUDE.md R11/R3 for `__Host-mm_device`, `Path=/`.
- Update the stale `mm_device` docstrings in `sessions/models.py` and `sessions/errors.py`.
