# consult-api-platform-engineer — USR-03f (S2 and S3, API view)

**Findings**
- The installed versions are FastAPI 0.162.2 and Starlette 1.10.0. `Depends(..., scope="function")` exists in this version (`params.Depends.scope: Literal["function","request"]`).
- How FastAPI runs a request (`fastapi/routing.py` ~146-155):
  - it opens `request_stack`, then `function_stack`, then calls `f(request)`, which runs the endpoint and serialises the response;
  - `function_stack` closes, then `await response(...)` sends the response, then `request_stack` closes.
  - So a function-scoped `get_session` commits and closes after the `Response` object is built (including the `Set-Cookie` headers merged from the `response: Response` parameter) but before `http.response.start` is sent.
- If the commit raises, the exception goes out through `wrap_app_handling_exceptions` and the built response is thrown away. No `Set-Cookie` leaks.
- Errors still map correctly:
  - An `AppError` raised by the endpoint goes into the generator, which rolls back and re-raises. The registered handlers (`app/core/errors.py:131-133`) still produce the envelope, and the 401's `exc.headers` cookie-clear still works.
  - A failed commit (`SQLAlchemyError`) has no handler. Starlette's `ServerErrorMiddleware` returns a plain-text 500, not the R6 envelope. This was already true for any unhandled error.
- What the repo-wide change touches:
  - There is only one declaration, `app/api/deps.py:9`. It is used by `health`, `organizations`, `rbac` (×2), `users`, `auth/router.py` and the `auth/deps.py` providers (`get_device_sessions`, `get_app_context`).
  - None of those providers is a generator. FastAPI's `DependencyScopeError` (a request-scoped generator cannot depend on a function-scoped one) therefore cannot trigger.
  - There is no `BackgroundTasks` or `StreamingResponse` that uses the session after the response.
- Test overrides still work. An override keeps the scope of the `Depends` it replaces (`utils.py:694`), so the `dependency_overrides[get_session]` overrides in `tests/conftest.py` and `tests/test_auth_signup.py:153` become function-scoped with no edits.
- The explicit mid-request commits stay valid:
  - `auth/repository.py:74` (the failure counter is committed before the 401);
  - `sessions/repository.py:151` (revoke on reuse).
- The dependency cache key includes the computed scope (`dependencies/models.py:92-96`). A future bare `Depends(get_session)` would open a second session in the same request.
- The cookie name appears in `cookies.py` (`DEVICE_COOKIE`), the OpenAPI `APIKeyCookie(name=DEVICE_COOKIE)` in `router.py`, and the tests:
  - `tests/auth_factories.py:91,100`;
  - `test_device_sessions.py:231`;
  - the path asserts in `test_auth_login.py:62` and `test_auth_logout.py:77`.

**Recommendations**
- **S2:** change one line, to `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]`. This keeps R1/R6 and avoids commits inside services.
  - Add a comment explaining why the scope is needed.
  - Add a guard test that the app has exactly one `get_session` declaration (or grep in review).
- **S2 tests** (use the strict client, with `ASGITransport(app, raise_app_exceptions=False)` for the failure case):
  - (a) Ordering: wrap the ASGI `send` to record `http.response.start`, and wrap `db_session.commit` to record `"commit"`. Assert commit comes first, for login, rotating logout and a 24 h-rotating `resolve`.
  - (b) Failure: patch `commit` to raise `OperationalError`. Assert a 500 and no `set-cookie` header, for login and logout (`all=true`).
- **S3:**
  - Set `DEVICE_COOKIE = "__Host-mm_device"` and make `_path()` return `"/"`. Keep `Secure` and no `Domain`, both of which the prefix requires.
  - The `delete_cookie` call must use the same `path="/"`.
  - `read_device_cookies` already matches on the exact name. Update the test helpers and the two path asserts.
- **OpenAPI:** use `APIKeyCookie(name=DEVICE_COOKIE, scheme_name="DeviceCookie", auto_error=False)`. A stable scheme id stops the renamed cookie from changing the component name again later.
- **Size:** about 0.5 day for the API part. S2 is 1 line plus about 4 tests. S3 is about 3 lines plus about 6 test edits, plus the spec, identity-architecture §1/§5/D5 and ADR-0004 wording.

**Risks**
- If a commit fails, the 500 is not in the R6 envelope. List a generic `Exception` handler as a follow-up (R7); it is outside this ticket.
- Connections now go back to the pool earlier. This is a benefit. Any future code that touches the session after the response would break, for example background tasks.
- Browsers that already hold the old `mm_device` (Path=/api/v1/auth) keep sending it until it expires. It is harmless because it is ignored, but those users are signed out once.
- With `Path=/` the cookie is also sent to admin and `/docs` routes. Admin auth must never read it (R3 class 1).

**Open questions**
1. Should login clear the old `mm_device` cookie for a one-time migration, or just let it expire? (Has USR-03 been deployed to `api.mmlabs.online` with real sessions?)
2. Should the 500 envelope handler be its own ADM follow-up?
