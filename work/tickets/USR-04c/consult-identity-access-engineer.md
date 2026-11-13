# consult-identity-access-engineer — USR-04c

**Findings**
- Signup gets its link origin from `get_optional_link_origin` (deps.py:84), which falls back to `settings.email_link_base_url` when there is no `X-MM-App`. `get_signup_hook` uses it, and `VerificationEmailHook.link_origin` is `str | None`, with a local-only branch that sends no email (hooks.py:43).
- Resend already uses `get_link_origin`, which depends on `AppContextDep` and normalizes the `Origin`. It needs no change.
- The `rate_limit("signup")` route dependency runs before the parameter dependencies. Turnstile and the password hash run inside `SignupService.signup`. So an app check done in a dependency returns 403 after the rate-limit hit and before Turnstile, any user insert or any email.
- No other code path needs a default origin. `app/cli.py` creates no users, and there is no admin user-creation path.
- CORS already covers `/api/v1/auth/*`, so signup needs no CORS change.

**Recommendations**
- Delete `get_optional_link_origin`. Make `get_signup_hook` take `link_origin: Annotated[str, Depends(get_link_origin)]`; that reuses `AppContextDep`, and FastAPI caches it within the request. Make `link_origin: str` in the hook and remove the `None` branch and its log line.
- Update the signup docstring to describe class 3 plus the app context.
- In config, remove the `email_link_base_url` field, its validator block and its entry in the staging/production required list. `extra="ignore"` already ignores a leftover env value; add a test that proves it.
- Remove the key from `.env.example`, and remove `TEST_EMAIL_LINK_BASE_URL` and its `os.environ` line from `tests/conftest.py`.
- Add the header to the signup calls in `test_auth_signup.py`, `test_rate_limit.py` and `test_email_verification_api.py`. Add tests that a missing header, an unknown or disabled app, and a bad or `null` Origin each return 403 with no user row and no mail queued.
- Docs to update: `consumers.md:63-66`, `deploy.md:112,119,136`, ADR-0007 §2 (amendment), the identity-architecture §4 signup row, and a v1 contract note (breaking change, no consumers yet).

**Risks**
- `test_cors.py:212` posts to signup without a header. It only checks `cache-control`, so it should still pass.
- If someone sends a junk `X-MM-App` header, it uses up a rate-limit slot before the 403. That is acceptable.

**Open questions**
- Should a disabled app return 403 `origin_not_allowed`, as login already does? I recommend yes, for consistency.
