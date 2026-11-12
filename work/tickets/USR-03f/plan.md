Status: READY

# Plan — USR-03f: Session hardening: commit before response, atomic login throttle, cookie-tossing defence

## Spec summary
- spec-v1.md §5.8, "Login and sessions": "A browser holds one device session (cookie `mm_device`, rotated with a 60 s grace) with any number of signed-in accounts … (D5, D6). Logout per account or for the whole device."
- spec-v1.md §5.8, "Abuse protection": "no hard lockout; per-email exponential backoff after 5 failures (cap 15 min), per-IP limit, Turnstile after 3 failures; the same error for unknown email and wrong password (D11)."
- spec-v1.md §5.8, "Endpoints": "all under `/api/v1/auth/*` (cookie `Path=/api/v1/auth`); JWKS at `/.well-known/jwks.json` (D13)." **The owner changed this on 2026-11-12 (ticket notes):** "S3 → use `__Host-mm_device` with `Path=/` (spec D13/R11 cookie path changes from `/api/v1/auth` to `/`; update spec, identity-architecture.md and ADR-0004 accordingly; CLAUDE.md R11 wording is for the owner to update)."
- CLAUDE.md R11: "rotation on login and at most every ~24 h, 60 s grace … Brute force: no hard lockout — per-email exponential backoff after 5 failures (cap 15 min), per-IP ~20/min, Turnstile after 3 failures."
- ADR-0004 §4 (commit before raise) and §5: "A request authenticated by the previous value may only use account sessions created strictly before `rotated_at` (`ResolvedDevice.accounts_created_before`…)."
- Source findings: `work/tickets/USR-03/security-review.md`, Attempt 1: S1 (check-then-act race on the per-email counter), S2 (the commit runs after the response and its `Set-Cookie` are sent), S3 (cookie tossing from sibling `*.mmlabs.online` hosts), N1 (logout `{account_id}` ignores `accounts_created_before`).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | **S2:** `SessionDep` is `Depends(get_session, scope="function")` and is the only `Depends(get_session…)` in `app/`. Every endpoint therefore commits before `http.response.start` | `tests/test_session_scope.py › test_session_dep_is_function_scoped`, `› test_get_session_declared_once`; `grep -rn "Depends(get_session" app` returns one line |
| 2 | **S2:** for login, logout `{all}` and a 24 h-rotating logout `{account_id}`, the last commit happens before `http.response.start` | `tests/test_session_scope.py › test_commit_precedes_response_start[login|logout_all|logout_rotating]` (strict app + recording ASGI wrapper); `tests/test_login_concurrency.py › test_real_get_session_commits_before_response_start` (real `get_session`, no override) |
| 3 | **S2:** if the final commit fails, the response is a 500 with no `Set-Cookie`, and the DB is unchanged | `tests/test_session_scope.py › test_failed_commit_sends_no_set_cookie[login|logout_all|logout_rotating]` |
| 4 | **S1:** the backoff/Turnstile decision and the failure increment are one atomic step (a conditional upsert that takes the row lock). The reservation is committed **before** Argon2, and the row lock is not held during Argon2 or the Turnstile call | `tests/test_login_concurrency.py › test_parallel_at_two_failures_one_401_one_turnstile_required`, `› test_parallel_at_four_failures_one_401_one_429`, `› test_row_not_locked_during_password_check` |
| 5 | **S1 (corrected, see Deviation D-1):** two parallel attempts without a token at `failures=2` give exactly one 401 `invalid_credentials` and one 403 `turnstile_required`; the counter ends at 3 | `test_parallel_at_two_failures_one_401_one_turnstile_required` |
| 6 | **S1:** the existing D11 behaviour is unchanged. No Turnstile call below 3 failures, 429 before any Argon2, blocked or Turnstile-rejected attempts are not counted, unknown and known emails behave identically, success deletes the row, and the counter resets after 24 h | every test in `tests/test_login_throttle.py` stays green unchanged |
| 7 | **S3:** the cookie is `__Host-mm_device` with `Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Max-Age`, and no `Domain`; clearing uses the same name, path and attributes | `tests/test_auth_login.py › test_login_sets_device_cookie_attributes` (updated, asserts the raw header), `tests/test_auth_logout.py › test_logout_all_revokes_device_and_clears_cookie` (updated), new `tests/test_device_cookie.py › test_clear_header_matches_set_attributes` |
| 8 | **S3:** the name match is exact and case-sensitive. A legacy or tossed `mm_device`, a `__HOST-mm_device`, or a nameless `=__Host-mm_device=x` is ignored and does not count toward the duplicate-401 rule. Two `__Host-mm_device` values still give 401 | `tests/test_device_cookie.py › test_legacy_and_lookalike_names_ignored`, `› test_duplicate_host_cookie_still_401`; `tests/test_device_sessions.py › test_multiple_device_cookies_rejected` (updated) |
| 9 | **S3:** a non-auth route that receives the cookie neither reads, rotates nor sets it | `tests/test_device_cookie.py › test_non_auth_route_ignores_cookie` |
| 10 | **S3:** the OpenAPI security scheme is `DeviceCookie`: `apiKey` in `cookie`, name `__Host-mm_device` | `tests/test_device_cookie.py › test_openapi_device_cookie_scheme` |
| 11 | **N1:** logout `{account_id}` using the previous cookie value (within grace) revokes only account sessions with `created_at < accounts_created_before` (strict). Newer ones stay active and the response is still 204 (no oracle). `{all:true}` with the previous value still revokes the whole device | `tests/test_auth_logout.py › test_logout_prev_value_cannot_revoke_newer_account`, `› test_logout_prev_value_revokes_older_account`, `› test_logout_all_prev_value_revokes_device` |
| 12 | Docs: spec §5.8, identity-architecture.md (diagram, §4 intro, §4 table, §5 S1, new D20, §10 C1 note) and ADR-0004 are updated. CLAUDE.md is **not** edited | review the diff; `git diff --stat -- CLAUDE.md` is empty |
| 13 | `make check` is green | the reviewer runs `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/api/deps.py` | `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]`, plus a 2-line comment explaining why (commit before the response is sent; S2, ADR-0004) | S2 |
| `app/db/session.py` | Docstring only: "commits before the response is sent (function scope via `SessionDep`)" | S2 (documentation of the contract) |
| `app/modules/auth/repository.py` | Add `reserve(email_hmac, *, turnstile_ok) -> int \| None` (conditional upsert, see Approach S1). Remove `record_failure` (no longer used). Keep `get`, `set_block`, `delete`, `commit` | S1 |
| `app/modules/auth/service.py` | `LoginService.login`: two phases (pre-check, then atomic reserve), commit the reservation before Argon2, no failure write after Argon2. `LogoutService.logout`: pass `device.accounts_created_before` to `revoke_account` | S1, N1 |
| `app/modules/sessions/service.py` | `revoke_account(device_id, account_id, *, created_before: datetime \| None)` passes the cutoff through | N1 |
| `app/modules/sessions/repository.py` | `revoke_account(..., *, created_before)` adds `AccountSession.created_at < created_before` when it is not None | N1 |
| `app/modules/auth/cookies.py` | `DEVICE_COOKIE = "__Host-mm_device"`, `_path()` returns `"/"` (or is replaced by a `_PATH = "/"` constant; the `settings` argument of `set_device_cookie`/`clear_device_cookie` stays for `Max-Age`). Update the docstrings; the exact-name match stays | S3 |
| `app/modules/auth/router.py` | `APIKeyCookie(name=DEVICE_COOKIE, scheme_name="DeviceCookie", auto_error=False)`; logout docstring names `__Host-mm_device` | S3 (OpenAPI) |
| `app/modules/auth/deps.py` | Docstring of `get_device` only (`__Host-mm_device`) | S3 (wording) |
| `tests/conftest.py` | Extract `make_strict_app` (returns the strict-override `FastAPI` app) from `make_strict_client`; `make_strict_client` uses it, with no behaviour change. This lets tests wrap the ASGI app and set `raise_app_exceptions=False` | S2 tests |
| `tests/auth_factories.py` | `auth_headers` sends `__Host-mm_device=…`; `parse_set_cookie` looks for `__Host-mm_device`; add the `DEVICE_COOKIE` import instead of hard-coding the name | S3 |
| `tests/test_auth_login.py` | Path assert → `"/"`; assert the raw `set-cookie` header has no `domain` (case-insensitive) | S3 |
| `tests/test_auth_logout.py` | Path assert → `"/"`; add the three N1 tests | S3, N1 |
| `tests/test_device_sessions.py` | Line 231: duplicate header uses `__Host-mm_device` | S3 |
| `tests/test_session_scope.py` (new) | S2 tests (scope, single declaration, ordering, failed commit) | S2 |
| `tests/test_login_concurrency.py` (new) | S1 concurrency tests and the real-`get_session` ordering test, on committed data with explicit cleanup | S1, S2 |
| `tests/test_device_cookie.py` (new) | S3 tests (attributes/clear, look-alike names, duplicates, non-auth route, OpenAPI) | S3 |
| `docs/spec-v1.md` | §5.8 "Login and sessions": cookie `__Host-mm_device`. "Endpoints": "(cookie `__Host-mm_device`, `Path=/`, host-only; D13 as amended by D20)" | owner decision S3 |
| `docs/identity-architecture.md` | Diagram line 18 (`cookie __Host-mm_device (HttpOnly, Secure, Lax, Path=/, host-only)`). §2 lines 55/112 (cookie name). §4 intro line 124 and table row line 130. §5 S1 lines 152-153. New **D20** in §8. A note under §10 C1 ("cookie path superseded by D20"). D5/D6/D13 rows stay as written (history); D20 amends them | owner decision S3 |
| `docs/adr/0004-device-sessions-and-login.md` | §1 cookie (name, `Path=/`, why `__Host-`, exact-name match, nameless-cookie note, legacy cookie ignored). §4 (commit before the response via function scope; failure counter now committed at reservation). §9 (two-phase atomic throttle). §10 (N1 cutoff on `{account_id}`; `{all}` stays whole-device). Consequences: replace the "clearing a tossed `Domain=mmlabs.online` cookie" follow-up with a line saying S3 is closed by the prefix; add "Amended by USR-03f (2026-11-12)" under Status | ADR maintenance (ticket AC "ADR-0004 updated") |

Not touched: `CLAUDE.md` (owner action, see Assumptions A5), migrations (no schema change), `.env.example` (no new config).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_session_scope.py › test_session_dep_is_function_scoped` | `typing.get_args(SessionDep)[1]` is a `Depends` whose `dependency is get_session` and `scope == "function"` (AC1) |
| `tests/test_session_scope.py › test_get_session_declared_once` | Walk `app/**/*.py` text: exactly one `Depends(get_session` occurrence, in `app/api/deps.py` (AC1; guards against a second, request-scoped declaration, which the dependency cache would treat as a separate session) |
| `tests/test_session_scope.py › test_commit_precedes_response_start` (parametrized: `login`, `logout_all`, `logout_rotating`) | Wrap `db_session.commit` to append `"commit"` to `events`. Wrap the app in a small ASGI wrapper whose `send` appends `"response.start"` on `http.response.start`. Assert that the last `"commit"` comes before `"response.start"` and that `Set-Cookie` is present. `logout_rotating` uses a device with `rotated_at` shifted by `-25 hours` (AC2) |
| `tests/test_session_scope.py › test_failed_commit_sends_no_set_cookie` (same 3 cases) | `ASGITransport(app=make_strict_app(), raise_app_exceptions=False)`. `db_session.commit` is wrapped so that the **final** commit raises `sqlalchemy.exc.OperationalError("commit", {}, Exception("boom"))`; earlier commits (the login reservation) go through. Use a counter `fail_on_call = n`: login n=2, logouts n=1. Assert status 500, no `set-cookie` header, and DB unchanged: login → no `device_sessions` row for the user; logout_all → device and accounts not revoked; logout_rotating → `token_hash` unchanged (AC3) |
| `tests/test_login_concurrency.py › test_parallel_at_two_failures_one_401_one_turnstile_required` | Committed user with `failures=2`. `LoginFailureRepository.get` is wrapped to wait on an `asyncio.Barrier(2)`, so both requests finish phase 1 before either reserves. Two requests with a wrong password and no token, through `asyncio.gather` under `asyncio.wait_for(…, 10)`. Assert sorted statuses `[401, 403]`, codes `invalid_credentials` and `turnstile_required`, and final `failures == 3` (AC4, AC5) |
| `tests/test_login_concurrency.py › test_parallel_at_four_failures_one_401_one_429` | Same barrier, `failures=4`, both with `turnstile_token="pass"`. Exactly one reserves (count 5, block 1 min), the other gets 429 with `Retry-After`. Final `failures == 5` and `blocked_until` is set (backoff decision is atomic, AC4) |
| `tests/test_login_concurrency.py › test_row_not_locked_during_password_check` | Patch `PasswordHasher.verify_dummy`/`verify` to signal `entered` and await `release`. While request A is paused there, a third connection runs `SELECT … FROM login_failures WHERE email_hmac = :h FOR UPDATE NOWAIT` and it succeeds (no `LockNotAvailable`). Then release A and expect 401 (AC4: no lock is held across Argon2, so the pool is safe) |
| `tests/test_login_concurrency.py › test_real_get_session_commits_before_response_start` | No `get_session` override. Monkeypatch `AsyncSession.commit` at class level to record events (calling the original), with the same recording ASGI wrapper. A successful login: the last commit comes before `response.start` (AC2 with the real dependency) |
| `tests/test_device_cookie.py › test_clear_header_matches_set_attributes` | `clear_device_cookie_header(settings)` contains `__Host-mm_device=""`, `Path=/`, `Secure`, `HttpOnly`, `SameSite=lax`, `Max-Age=0` and no `Domain` (AC7) |
| `tests/test_device_cookie.py › test_legacy_and_lookalike_names_ignored` | With `Cookie: mm_device=junk; __HOST-mm_device=junk; =__Host-mm_device=junk; __Host-mm_device=<valid>`, logout `{account_id}` → 204. With only the legacy or look-alike names, logout → 401 `session_invalid` (no device cookie) and login creates a new device (AC8) |
| `tests/test_device_cookie.py › test_duplicate_host_cookie_still_401` | Two `__Host-mm_device` values → 401, and the device is not revoked (AC8) |
| `tests/test_device_cookie.py › test_non_auth_route_ignores_cookie` | A device with `rotated_at` at -25 h. `GET /healthz` and `GET /api/v1/organizations` with the cookie: no `set-cookie` header, and the device's `token_hash`/`last_seen_at` are unchanged (AC9) |
| `tests/test_device_cookie.py › test_openapi_device_cookie_scheme` | `app.openapi()["components"]["securitySchemes"]["DeviceCookie"] == {"type": "apiKey", "in": "cookie", "name": "__Host-mm_device"}` (AC10) |
| `tests/test_auth_logout.py › test_logout_prev_value_cannot_revoke_newer_account` | Device with alice (`created_at` -1 h). Rotate through the service, `add_account(bob)` (same `now()` → `bob.created_at == rotated_at`). Logout `{account_id: bob}` with the **old** raw value → 204, and bob is still active (AC11, strict `<`) |
| `tests/test_auth_logout.py › test_logout_prev_value_revokes_older_account` | Same setup; `{account_id: alice}` with the old value → 204 and alice is revoked (AC11) |
| `tests/test_auth_logout.py › test_logout_all_prev_value_revokes_device` | Same setup; `{all: true}` with the old value → 204, the device and both accounts are revoked, and the cookie is cleared (AC11; documents the D20/ADR choice) |
| Updated: `test_auth_login.py › test_login_sets_device_cookie_attributes`, `test_auth_logout.py › test_logout_all_revokes_device_and_clears_cookie`, `test_device_sessions.py › test_multiple_device_cookies_rejected` | New name and `Path=/` (AC7, AC8) |

**Concurrency fixture (`tests/test_login_concurrency.py`):** these tests cannot use `db_session`. It is one connection inside a rolled-back transaction, and an `AsyncSession` refuses concurrent operations. Write a module-local fixture instead:
- It creates its data with `SessionLocal()` from `app.db.session` (the real engine, already pointed at the test DB by `conftest.py`) and **commits** it: one user per test (unique email) and one app with a unique key matching `APP_KEY_RE` (e.g. `"race-" + uuid4().hex[:8]`) and origin `ORIGIN`. Seed `login_failures` with the HMAC from `EmailHasher(HMAC_KEY)`.
- It yields a client from `create_app()` with only `get_turnstile_verifier` overridden. `get_session` is **not** overridden, so each request gets its own pooled connection.
- In `finally` it deletes, in a fresh `SessionLocal()` and with a commit: the `login_failures` row, `account_sessions` and `device_sessions` of the user, the user, and the app.
- Every `gather` is wrapped in `asyncio.wait_for(..., timeout=10)`, so a regression fails instead of hanging.
- Keep one module docstring line: "committed data, explicit cleanup; not rolled back per test".

## Approach

### 1. S2: commit before the response (R1, R6)
1. In `app/api/deps.py`, set `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]` with a comment.
   - FastAPI 0.162.2 closes the function-scoped exit stack after the endpoint has built its `Response` (including the `Set-Cookie` merged from `response: Response`), and before `await response(...)` sends `http.response.start` (consult-api-platform-engineer, consult-identity-access-engineer).
   - A failed commit raises out of the endpoint call, so the built response is thrown away: a 500, with no `Set-Cookie`.
2. No per-service commits are added, and R1 is unchanged. The two existing commit-before-raise paths stay as they are:
   - the reuse revocation in `sessions/service.py`;
   - the failure counter, which in S1 becomes the reservation commit.
3. Test overrides of `get_session` keep the scope of the `Depends` they replace, so `make_client`, `make_strict_client` and `test_auth_signup.py:153` need no change for scope. In `conftest.py`, extract `make_strict_app` only so tests can wrap the app.
4. Nothing in the repo uses the session after the response (no `BackgroundTasks` or `StreamingResponse`), and no generator dependency depends on `SessionDep`, so `DependencyScopeError` cannot occur (consult-api-platform-engineer).

### 2. S1: atomic per-email decision. Decision: conditional-upsert reserve-then-decide, committed before Argon2
**Chosen:** the security architect's reserve-then-decide, done as **one conditional upsert**.

**Rejected: the identity engineer's blocking `pg_advisory_xact_lock`.**
- It is held across Argon2 and the Turnstile HTTP call (timeout 5 s). Each waiting request for the same email holds a pooled connection (`db_pool_size` 10 + overflow) while it waits.
- A burst against one email can therefore exhaust the pool and take down every endpoint. The per-IP limit does not bound a multi-IP burst.

**Also rejected: `pg_try_advisory_xact_lock` → 429.**
- It adds a new 429 cause that is not in D11.
- A legitimate user racing an attacker on their own email would get spurious 429s.
- It still holds the lock (and a connection) across Argon2 for the winner.

**Why the conditional upsert:**
- The row lock lives only for the upsert-to-commit window: microseconds, with no Argon2 and no network in it.
- It needs no rollback and no compensation.
- It works with the savepoint-based test fixtures.

**Flow in `LoginService.login`, in D11 order:**
1. **Phase 1, pre-check (no lock):**
   - `state = failures.get(email_hmac)`.
   - If blocked → 429 (as today, with `Retry-After` from the DB clock).
   - If `state.failures >= TURNSTILE_AFTER`, the token is required: none → 403 `turnstile_required`; otherwise call `verifier.verify` → on false 403 `turnstile_failed`; on true set `turnstile_ok = True`.
   - This keeps the Turnstile network call outside any lock, and Turnstile is never called below the threshold (the existing test).
2. **Phase 2, atomic reserve:** call `failures.reserve(email_hmac, turnstile_ok=turnstile_ok)`, which runs one statement:
   ```
   INSERT INTO login_failures (email_hmac, failures) VALUES (:h, 1)
   ON CONFLICT (email_hmac) DO UPDATE
     SET failures = CASE WHEN <stale> THEN 1 ELSE login_failures.failures + 1 END,
         last_failed_at = now()
     WHERE <stale>
        OR ((login_failures.blocked_until IS NULL OR login_failures.blocked_until <= now())
            AND (login_failures.failures < :turnstile_after OR :turnstile_ok))
   RETURNING failures
   ```
   - `<stale>` is `login_failures.last_failed_at < now() - COUNTER_RESET`, built with SQLAlchemy as in today's `record_failure`. `:turnstile_after` is `TURNSTILE_AFTER`, passed in (one definition in `throttle.py`).
   - `ON CONFLICT DO UPDATE` locks the conflicting row even when the `WHERE` is false, and it re-evaluates against the latest committed version under READ COMMITTED. Concurrent attempts for one email therefore queue on the row lock only for the length of this statement plus the commit below.
   - Returns the new count, or `None` when the row was not updated (refused).
3. **Refused (`None`):** re-read with `failures.get` (the row is locked by this transaction, so the read is consistent), then raise:
   - 429 with `Retry-After` if blocked;
   - otherwise 403 `turnstile_required`. This includes a request that carried a token which phase 1 did not need to verify; the client retries with a token (Assumption A2).
   - Nothing is written. The raise rolls back through `get_session` and releases the lock before the response (S2).
4. **Reserved (`n`):** if `backoff_minutes(n)` is set, call `failures.set_block(email_hmac, minutes)` (as today, but now at reservation). Then `await failures.commit()`: the reservation and any block are durable, and the row lock is released **before** Argon2.
   - This replaces today's "commit before the 401": a failed password needs no further write.
   - A crash during Argon2 leaves the attempt counted, which fails closed.
5. **Password check:** unchanged (dummy verify for an unknown email; a disabled user gets the same 401). On failure, log `login_failed` and raise 401. Do **not** increment again.
6. **Success:** `failures.delete(email_hmac)` (clears the reservation and any block), then device and account work as today. The final commit happens in `get_session`, before the response (S2).

**Equivalence with today's tests (AC6):**
- Blocked and Turnstile-rejected attempts are never counted. They are refused by the `WHERE` or rejected in phase 1, and nothing is committed.
- The 5th counted failure sets a 1-min block. Known and unknown emails are identical.
- `test_failure_counter_persists_despite_401` still holds: the reservation is committed.
- `record_failure` is removed from the repository; no other caller exists (verify with grep).
- Rules: R1 (SQL only in the repository; the service decides), R11 (identical error for unknown email and wrong password; HMAC key only).

### 3. S3: `__Host-mm_device`, `Path=/` (owner decision 2026-11-12)
1. In `cookies.py`, set `DEVICE_COOKIE = "__Host-mm_device"` and the path to `"/"`. `set_cookie` keeps `secure=True, httponly=True, samesite="lax"` and no `domain`. `delete_cookie` uses the same name, `path="/"` and the same attributes, or the browser ignores the clear.
   - `read_device_cookies` keeps exact, case-sensitive `name == DEVICE_COOKIE` matching, so `mm_device`, `__HOST-mm_device` and nameless `=…` parts are ignored. The more-than-one-value → 401 rule stays (consult-security-architect, consult-identity-access-engineer).
2. Legacy `mm_device` (`Path=/api/v1/auth`) is ignored and never migrated. Old browsers sign in once more. **No legacy-clear `Set-Cookie`** (Assumption A1).
3. In `router.py`, use `APIKeyCookie(name=DEVICE_COOKIE, scheme_name="DeviceCookie", auto_error=False)`, so the component name is stable (consult-api-platform-engineer).
4. R3 class 1 is unaffected. The cookie now reaches admin and health routes, but only `app/modules/auth` reads it (`read_device_cookies` is imported only in `auth/router.py` and `auth/deps.py`); `test_non_auth_route_ignores_cookie` pins this.
5. Docs:
   - **D20** in identity-architecture.md §8: "**Cookie `__Host-mm_device`, `Path=/` (USR-03f, owner 2026-11-12):** the device cookie is `__Host-mm_device` — `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Domain` — so sibling `*.mmlabs.online` hosts cannot set or shadow it (cookie tossing). Amends D5 and D13 for the cookie only; identity endpoints stay under `/api/v1/auth/*`. A legacy `mm_device` is ignored. Logout `{account_id}` with the previous value only reaches account sessions created before the rotation; `{all:true}` with the previous value revokes the whole device (denial of service within 60 s only, never escalation)."
   - spec §5.8 and ADR-0004 as listed in the file table.
   - ADR-0004 also records the remaining risks: XSS on the same host (public `/docs`, accepted 2026-11-11) can still set a non-HttpOnly `__Host-` cookie when none exists (login CSRF / fixation), and older browsers may accept nameless-cookie smuggling, which the duplicate-401 rule limits to denial of service.

### 4. N1: logout `{account_id}` with the previous value
- `LogoutService.logout` calls `devices.revoke_account(device.device_id, data.account_id, created_before=device.accounts_created_before)`.
- The repository adds `AccountSession.created_at < created_before` (strict) when it is not None, the same predicate as `get_active_account`.
- The response is 204 either way (no oracle).
- `{all:true}` is unchanged. Revoking more is never an escalation, so it is recorded in D20/ADR-0004 §10.

### 5. Docs and checks
- Update the docs.
- Run `make check`.
- Record any extra file in `impl-log.md` (R7).

**Rules:**
- R1 (layering kept);
- R2 (no schema change; `alembic check` unchanged);
- R3 (no endpoint changes class);
- R5 (no new config);
- R6 (unchanged except the pre-existing plain-text 500, see Follow-ups);
- R7 (Follow-ups below);
- R8 (tests per item);
- R11: the cookie attributes change by owner decision, recorded in D20. The CLAUDE.md wording is the owner's action.

## Specialist input used
- **consult-api-platform-engineer:** adopted the one-line `scope="function"` change, the ordering and failure test design (recording `send`, `raise_app_exceptions=False`), `scheme_name="DeviceCookie"`, and the exact-name/path test edits. Adopted the risk that a failed commit gives a plain-text 500 → Follow-up F1. The single-declaration guard is adopted as a test, not as a review grep only.
- **consult-identity-access-engineer:**
  - Adopted the S2 analysis, the S1 test-mismatch correction (Deviation D-1), and the requirement for separate connections with committed data and cleanup.
  - Adopted N1 with strict `<` and `{all}` kept whole-device. Adopted the legacy cookie being ignored and never migrated.
  - **Rejected** the blocking `pg_advisory_xact_lock`: it holds a pool connection per waiter across Argon2 and Turnstile (pool-exhaustion denial of service, as the security architect says).
  - The optional legacy-clear header is skipped (A1). Login with the previous value creating an invisible account → Follow-up F4.
- **consult-security-architect:**
  - Adopted reserve-then-decide committed before Argon2, built as a conditional `ON CONFLICT DO UPDATE … WHERE`. The upsert itself takes the row lock, so no separate `SELECT … FOR UPDATE` is needed.
  - Adopted: `__Host-` with `Path=/`, `Secure`, no `Domain`; clearing with identical attributes; exact case-sensitive matching; the tests for legacy/look-alike names and non-auth routes; the docs list including D20; and not editing CLAUDE.md.
  - Rejected `pg_try_advisory_xact_lock` → 429 (a new 429 cause outside D11).
  - CSP or removing `/docs`, and `SameSite=Strict` → Follow-ups F2, F3.
- **USR-03 security-review.md:** S1, S2, S3 and N1 (first point) are fixed here. The second N1 point → F4.

## Assumptions
- **A1. No legacy-clear header.** USR-03 has not been released with real user sessions. No app (USR-03b/03c) reads the cookie yet, so old `mm_device` cookies exist only in test or staging browsers and expire on their own (idle 30 days). This is cheap to add later if the owner wants it.
- **A2.** A request refused in phase 2 because the count reached 3 between phase 1 and phase 2 gets 403 `turnstile_required`, even if it carried a token that phase 1 did not need to verify. This is rare (only under a race), safe, and the client simply retries with a token.
- **A3.** Moving the block (`set_block`) from "after the failed password" to "at the reservation that reaches the threshold" is behaviourally equivalent for sequential requests: success deletes the row, which also clears the block. Under concurrency it closes the backoff race, as the ticket requires.
- **A4.** No new pytest marker. The concurrency tests live in their own module with explicit cleanup, so `pyproject.toml` is untouched.
- **A5. Owner action (not in this diff):** reword CLAUDE.md R11 "Cookie `mm_device`: opaque, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`, host-only (no `Domain`)" to "Cookie `__Host-mm_device`: opaque, `HttpOnly; Secure; SameSite=Lax; Path=/`, host-only (no `Domain`) (D20)". The R3 class 2 text "`mm_device` cookie" should also become `__Host-mm_device`.
- **A6.** The log event name `mm_device_multiple` stays as is. It is an event name, not a cookie name, and renaming it would break log queries for no gain.

### Deviation from the ticket text
- **D-1 (S1 test expectation).** The ticket AC says "two parallel attempts at `failures=2` both get `turnstile_required`". That is wrong. `TURNSTILE_AFTER = 3` with `>=` means that at `failures=2` exactly **one** attempt may still proceed without Turnstile: the one whose reservation takes the count from 2 to 3.
  - The correct outcome is exactly one 401 `invalid_credentials` and one 403 `turnstile_required`, with a final count of 3.
  - This plan tests that (AC5), per consult-identity-access-engineer.
  - The orchestrator should record the corrected criterion in `ticket.md`.

## Blocking questions
None

## Split proposal
None.
- Product code is about 120 changed lines: deps 1, cookies ~5, repository ~35, service ~40, sessions ~10, router ~2.
- The rest is tests (~350) and doc wording.
- There is one spec area (§5.8 sessions/abuse protection) and no migration. The four items share the same files and test fixtures, so splitting them would mean reworking the same code twice.

## Data model / migration
None. `login_failures` is used as is: the `failures >= 1` CHECK is respected, because a reservation always writes ≥ 1 and no 0-row is ever created. `account_sessions` is unchanged (the filter is a query predicate only).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | unchanged | unchanged. `Set-Cookie: __Host-mm_device=…; HttpOnly; Max-Age=…; Path=/; SameSite=lax; Secure` | 200, 401 `invalid_credentials`, 403 `app_header_required` / `origin_not_allowed` / `turnstile_required` / `turnstile_failed`, 422, 429. A failed commit gives 500 with no `Set-Cookie` | public (R3 class 3) + Origin/`X-MM-App` |
| POST | `/api/v1/auth/logout` | unchanged (`{account_id}` \| `{all:true}`), cookie `__Host-mm_device` | 204. Rotation or clearing `Set-Cookie` uses `Path=/`. `{account_id}` with the previous value only affects account sessions created before the rotation | 204, 401 `session_invalid`, 403, 422. A failed commit gives 500 with no `Set-Cookie` | cookie (R3 class 2) |
| — | OpenAPI | security scheme renamed `DeviceCookie` (apiKey, cookie, `__Host-mm_device`) | — | — | — |

Breaking-change note (R9): the cookie name and path change before any client ships (USR-03b/03c not done; A1). The JSON contracts are unchanged.

## Follow-ups (R7, not in this diff)
- **F1.** A generic `Exception` handler that returns the R6 envelope for 500s. A failed commit currently gives Starlette's plain-text 500; this predates the ticket. Proposed as an ADM ticket.
- **F2.** CSP on `/docs`, or disable `/docs` in production. The cookie now reaches `/` and XSS on the same host is the remaining way to toss it (consult-security-architect).
- **F3.** `SameSite=Strict` as defence in depth. This is a spec change and an owner decision.
- **F4.** Login using the previous cookie value within the grace window adds an account session dated after `rotated_at`, which that client cannot see (security-review N1, second point). Fix in USR-03b by forcing a fresh rotation, or by returning a new cookie.
- **F5.** The owner rewords CLAUDE.md R11 and R3 (A5).
