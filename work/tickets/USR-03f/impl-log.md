# Implementation log — USR-03f

## Attempt 1 — 2026-11-12

### Files touched
| File | Required because |
|---|---|
| `app/api/deps.py` | S2: `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]` + comment |
| `app/db/session.py` | S2: docstring (commit runs before the response is sent) |
| `app/modules/auth/repository.py` | S1: `reserve(email_hmac, *, turnstile_ok)` (conditional `ON CONFLICT DO UPDATE … WHERE … RETURNING`); `record_failure` removed (no other caller); `commit` docstring |
| `app/modules/auth/service.py` | S1: two-phase login (pre-check, atomic reserve, commit before Argon2, no write after a wrong password; `_raise_if_blocked` helper used by both phases). N1: logout passes `accounts_created_before`. Docstring cookie name |
| `app/modules/sessions/service.py` | N1: `revoke_account(..., *, created_before)` |
| `app/modules/sessions/repository.py` | N1: `revoke_account` adds `created_at < created_before` when set |
| `app/modules/auth/cookies.py` | S3: `DEVICE_COOKIE = "__Host-mm_device"`, `_PATH = "/"` (replaces `_path()`), docstrings |
| `app/modules/auth/router.py` | S3: `APIKeyCookie(..., scheme_name="DeviceCookie")`; logout docstring |
| `app/modules/auth/deps.py` | S3: `get_device` docstring |
| `tests/conftest.py` | `make_strict_app` fixture extracted; `make_strict_client` uses it (no behaviour change) |
| `tests/auth_factories.py` | `DEVICE_COOKIE` in `auth_headers` / `parse_set_cookie`; new `recording_app` ASGI wrapper (shared by the two new S2 test modules) |
| `tests/test_auth_login.py` | `Path=/`; raw header has no `domain` |
| `tests/test_auth_logout.py` | `Path=/`; three N1 tests |
| `tests/test_device_sessions.py` | duplicate-cookie test uses `__Host-mm_device` |
| `tests/test_session_scope.py` (new) | S2 tests |
| `tests/test_login_concurrency.py` (new) | S1 concurrency + real-`get_session` ordering test (committed data, explicit cleanup, 10 s timeouts) |
| `tests/test_device_cookie.py` (new) | S3 tests |
| `docs/spec-v1.md` | §5.8 cookie name; Endpoints line (`__Host-mm_device`, `Path=/`, D20) |
| `docs/identity-architecture.md` | diagram, §2 flow, §3 note, §4 intro + login row, §5 S1, new D20, §10 C1 note (D5/D6/D13 kept as history) |
| `docs/adr/0004-device-sessions-and-login.md` | title, Status amendment line, §1 cookie, §4 commit before response, §8 wording, §9 atomic throttle + rejected options, §10 N1, Consequences (S3 closed, remaining risks; tossed-cookie follow-up removed) |

`CLAUDE.md` not touched (A5). No migration, no `.env.example` change.

### Tests added / changed
- `tests/test_session_scope.py` › `test_session_dep_is_function_scoped`, `test_get_session_declared_once`, `test_commit_precedes_response_start[login|logout_all|logout_rotating]`, `test_failed_commit_sends_no_set_cookie[login|logout_all|logout_rotating]`
- `tests/test_login_concurrency.py` › `test_parallel_at_two_failures_one_401_one_turnstile_required`, `test_parallel_at_four_failures_one_401_one_429`, `test_row_not_locked_during_password_check`, `test_real_get_session_commits_before_response_start`
- `tests/test_device_cookie.py` › `test_clear_header_matches_set_attributes`, `test_legacy_and_lookalike_names_ignored`, `test_duplicate_host_cookie_still_401`, `test_non_auth_route_ignores_cookie`, `test_openapi_device_cookie_scheme`
- `tests/test_auth_logout.py` › `test_logout_prev_value_cannot_revoke_newer_account`, `test_logout_prev_value_revokes_older_account`, `test_logout_all_prev_value_revokes_device`
- Updated: `test_auth_login.py › test_login_sets_device_cookie_attributes`, `test_auth_logout.py › test_logout_all_revokes_device_and_clears_cookie`, `test_device_sessions.py › test_multiple_device_cookies_rejected`
- `tests/test_login_throttle.py`: unchanged, all green (AC6)

Mutation checks (temporarily reverted, then restored):
- `SessionDep` without `scope="function"` → 7 of 8 `test_session_scope.py` tests fail.
- `reserve` without the `WHERE` → both parallel tests fail.
- logout with `created_before=None` → `test_logout_prev_value_cannot_revoke_newer_account` fails.

### Checks (actual output summary)
- Postgres: already running locally (`pg_isready` OK), `org_admin` / `org_admin_test` already present. Nothing started.
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 3 times after the final edit, each time: `ruff check` All checks passed! · `ruff format --check` 235 files already formatted · `mypy app` Success: no issues found in 62 source files · `pytest` **639 passed** (~26.6 s). Baseline before the change: 619 passed.
- Flakiness: the three new modules ran 20× in a row → 20/20 `17 passed`. After each run, the test DB holds no leftover rows (apps = 1 (`admin` seed), users/login_failures/device_sessions/account_sessions = 0).
  - An earlier run found one flaky assertion: `Retry-After` was 61 instead of ≤ 60 in the parallel-429 test. Cause: `now()` is the transaction start time, and the losing request's transaction began before the winner set the block. Retry-After therefore overstates by at most 1 s, which is fail-safe. The test bound is now ≤ 61, with a comment explaining why.
- `alembic check` (against `org_admin_test`): No new upgrade operations detected. No schema change.

### Out-of-plan files
None. `tests/auth_factories.py` also gets the `recording_app` helper; the file is in the plan's list.

### Deviations from plan
- `reserve` imports `TURNSTILE_AFTER` from `throttle.py` and does not take it as a bound parameter. There is still one definition.
- `test_commit_precedes_response_start[logout_rotating]` / `test_failed_commit…[logout_rotating]` send `{account_id: <random uuid>}`, a no-op revoke. That isolates the rotation and keeps the "DB unchanged" check simple.
- `test_legacy_and_lookalike_names_ignored` uses logout `{all:true}` for the cases with only look-alike names, and also checks that no `Set-Cookie` is sent and that the device stays unrevoked.
- Retry-After bound ≤ 61 in the parallel-429 test (see Checks).

### Follow-ups (noticed, not done)
- F1–F5 from plan.md still apply.
- Docstrings still say `mm_device` in `app/modules/sessions/models.py` (DeviceSession) and `app/modules/sessions/errors.py` (SessionInvalidError). Wording only; these files are not in the plan.
- Retry-After on the refused-reservation path uses the transaction-start `now()`, so it can overstate by ≤ 1 s under a race. This is harmless. `clock_timestamp()` would make it exact if ever wanted.

### Scope self-check
- Could the diff be smaller? No. Product code is about 120 lines; the rest is the planned tests and docs.
- Diff size: tracked files +262 / −87; new test files 488 lines.
