# Security review — USR-03f

## Attempt 1

**Verdict: no 🔴 blockers, no 🟡 should-fix, 5 🟢 notes.** S1, S2, S3 and N1 (first point) are all closed, and I found no regressions.

**Checks:** `make check` passed ruff, format and mypy, then pytest failed: 5 failed and 1 error, all `relation "apps" does not exist`, scattered across unrelated modules. The cause looked like another process recreating the shared test DB at the same moment, but I did not confirm it. I re-ran only pytest twice (not the full `make check`): the new and touched modules gave 53 passed, then the full suite gave **639 passed**. Before the PASS, the orchestrator should get one fully green `make check` with nothing else using the test DB (R8).

### Original findings: are they closed?

**S2 (commit before response): closed.**
- `app/api/deps.py:11`: `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]`. It is the only `Depends(get_session` in `app/`, and `tests/test_session_scope.py` guards that.
- I checked the installed FastAPI 0.162.2, `.venv/.../fastapi/routing.py:148-153`. The `function_stack` (`async with`) exits after `response = await f(request)` and before `await response(scope, receive, send)`. So a commit failure is raised before `http.response.start`. The response that was already built, and its `Set-Cookie`, are thrown away, and Starlette returns a plain 500 with no cookie.
- Tests cover this:
  - ordering for login, logout `{all}` and rotating logout;
  - the failed final commit (500, no `set-cookie`, DB unchanged);
  - the real `get_session` with no override (`test_real_get_session_commits_before_response_start`).
- The rollback on refused or raised paths also runs before the error response, so the row lock from a refused upsert is released before the reply.

**S1 (atomic per-email decision): closed.**
- `app/modules/auth/repository.py:42-69`: `reserve()` is a single `INSERT … ON CONFLICT DO UPDATE … WHERE stale OR (not blocked AND (failures < 3 OR :turnstile_ok)) RETURNING`.
  - Postgres locks the conflicting row even when the `WHERE` is false, and re-checks against the latest committed version under READ COMMITTED, so the decision and the increment are atomic.
  - `scalar_one_or_none()` returning `None` correctly means "refused".
- `app/modules/auth/service.py:151-171`: `set_block` runs in the same transaction as the reservation, before `commit()`. A concurrent waiter therefore sees the new `blocked_until` when it gets the row lock, so the backoff race is closed too (`test_parallel_at_four_failures_one_401_one_429`).
- The Turnstile verify is outside any lock (phase 1, `service.py:155-160`).
- The row lock is released by the commit at `service.py:171`, before `get_by_email_for_share` and Argon2 (`test_row_not_locked_during_password_check`). This avoids the pool-exhaustion risk of a blocking advisory lock.
- What gets committed early: before the reservation commit, the transaction holds only the AppContext read and phase-1 reads. Device resolution and rotation run after it (`service.py:186`), so no device CAS or reuse revocation is committed early by the throttle commit. The rate limiter is in memory.
- Leaks and counter drift:
  - Refused, blocked or Turnstile-rejected attempts write nothing (refusal → raise → rollback).
  - Unknown and known emails follow the same path.
  - Success deletes the row.
  - A 500 after the reservation leaves the attempt counted, which fails closed and is documented. One small cost: a legitimate user whose successful login hits a failed final commit keeps +1 on the counter.
  - Concurrent first inserts are handled by `ON CONFLICT`.
- Deviation D-1 (the expected result is one 401 and one 403, not two 403s) is correct: the first attempt at `failures=2` is still allowed without Turnstile under D11.

**S3 (cookie tossing): closed.**
- `app/modules/auth/cookies.py:12-13, 31-44`: the cookie is `__Host-mm_device` with `Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax` and no `Domain`. `clear_device_cookie` and `clear_device_cookie_header` use the same name, path and attributes, so browsers will accept the clear, including on the 401 path in `auth/deps.py:72-80`.
- Exact, case-sensitive name matching is kept (`cookies.py:22-28`). `mm_device`, `__HOST-mm_device` and nameless parts are ignored, and more than one value still gives a 401 with no revocation.
- `read_device_cookies` is imported only in `auth/router.py` and `auth/deps.py`. `test_non_auth_route_ignores_cookie` shows that a non-auth route never rotates or sets the cookie.
- The OpenAPI scheme is `DeviceCookie`.

**N1 (logout cutoff): closed.**
- `service.py:214-217` passes `accounts_created_before`.
- `sessions/repository.py:137-150` adds the strict `created_at < created_before` filter, the same predicate as `get_active_account`. The response is still 204 either way, so it reveals nothing.
- `{all:true}` with the previous value still revokes the whole device. That is a denial of service within the 60 s grace and is recorded in D20.

### Findings

🟢 **N-a: the ADR wording on nameless-cookie smuggling is too optimistic.**
- **Where:** `docs/adr/0004-device-sessions-and-login.md` §1: "Older browsers that accept nameless-cookie smuggling can at most cause such a 401 (denial of service)."
- **Gap:** that holds only when the victim already has a real `__Host-mm_device`. If the victim has none, a nameless cookie whose value is `__Host-mm_device=<attacker device>`, set by a sibling host with `Domain=mmlabs.online`, reaches the server as the only value. On a pre-RFC6265bis browser that is login CSRF or fixation, the original S3 case.
- **Fix:** reword it as "a 401 when a real cookie exists; otherwise the same login-CSRF risk as XSS on the same host, limited to outdated browsers".
- **Test gap:** `tests/test_device_cookie.py` tests the header form `=__Host-mm_device=…`, but a browser sends a nameless cookie without the leading `=`. So that test covers parser hygiene, not the smuggling case.

🟢 **N-b: `now()` is the transaction start time.**
- **Where:** `repository.py:49-55` (`stale`, `blocked_until <= now()`) and `set_block` (`repository.py:71-77`).
- **Effect:** both use `func.now()`, which in Postgres is fixed when the request transaction starts (at the AppContext query, before the Turnstile call, which can take up to its 5 s timeout). Blocks can be set a few seconds shorter than intended, and refusals and `Retry-After` are slightly stricter. This is not exploitable.
- **Fix if wanted:** use `statement_timestamp()` in `reserve`/`set_block`, or accept it.

🟢 **N-c: legacy `mm_device` sessions stay active on the server.**
- They are ignored rather than revoked, so they stay active until idle expiry (30 days).
- Anyone holding an old raw value can still present it as `__Host-mm_device=<old>` from a non-browser client, because lookup is by hash only.
- This is acceptable under A1 (no production users), but the server side is not invalidated.

🟢 **N-d: wider cookie scope with `Path=/`.**
- The cookie now reaches `/docs`, health, and the unauthenticated admin routes. With `CORSMiddleware(allow_credentials=True)` (`app/main.py:29-36`) it is also sent on credentialed cross-origin calls from `cors_origins`.
- Today nothing outside `app/modules/auth` reads it, so it grants no authority. Keep that invariant when ADM-002 adds Bearer auth: never fall back to the cookie on admin routes.
- Cosmetic: the log key `mm_device_multiple` (`sessions/service.py:66`) uses the old name.

🟢 **N-e: CLAUDE.md now disagrees with the spec.**
- CLAUDE.md R11 and R3 still say `mm_device` with `Path=/api/v1/auth`, while the spec, identity-architecture.md (D20) and ADR-0004 now say `__Host-mm_device` with `Path=/`.
- The spec wins, and this is already F5 (owner edit). It is listed here so the reviewer doesn't flag it as an R11 violation.

### Follow-ups (out of scope, R7)
- Correct the ADR-0004 §1 smuggling sentence (N-a). Consider dropping or renaming the `=__Host-mm_device=` test case, or adding the browser-realistic form.
- Optionally use `statement_timestamp()` in the throttle SQL (N-b).
- F1: a generic 500 handler that returns the R6 envelope (a failed commit currently gives Starlette's plain-text 500, and its CORS headers are missing).
- F2: CSP on `/docs` or turn `/docs` off in production. XSS on the same host is now the remaining way to toss the cookie.
- F3: `SameSite=Strict` (owner decision).
- F4: login with the previous value inside the grace window adds an account session the client cannot see (USR-03b).
- F5: the owner updates CLAUDE.md R11/R3 (N-e).
- ADM-002: admin Bearer auth must never accept `__Host-mm_device` (N-d).
- If legacy cookies ever reach production, revoke them on the server or send a legacy clear (N-c).
- From USR-03: cleanup of `login_failures` and old sessions, rehash on login, and per-IP limits by IPv6 /64.

### Orchestrator note (not part of the appsec reply)
The appsec run collided with the reviewer's run on the shared test DB. Afterwards the orchestrator ran `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` alone: ruff, format and mypy clean, **639 passed in 26.24s**.
