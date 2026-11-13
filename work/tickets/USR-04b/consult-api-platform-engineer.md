# consult-api-platform-engineer — USR-04b

**Findings**
- The ticket contradicts itself. The acceptance criteria say forgot takes an "optional `X-MM-App` (A1 rule)". The owner note of 2026-11-13 says forgot requires `X-MM-App` + `Origin`, like signup, and that there is no fallback link origin. The owner note wins. `identity-architecture.md` §4 still lists forgot as just "public", so it needs the same "+ Origin/`X-MM-App` check (USR-04c)" note that signup has.
- `EmailVerificationService.prepare` is fixed to `VERIFY`, `VERIFY_TTL` and `VERIFY_PATH`. Reset needs either a version that takes the purpose as a parameter or a sibling service. It must not copy the lock/window/cap logic.
- `/verify-email/resend` has no `rate_limit(...)`. That is the S1 follow-up from the notes.
- `prepare` raises `RateLimitedError` for the per-email limit. Forgot must catch this and answer 202 silently. A 429 there would reveal that the email exists.
- The `Scope` Literal and `_LIMITS` in `app/core/rate_limit.py` need the new scopes `forgot`, `reset` and `verify_email_resend`. Each also needs its settings and `.env.example` keys (R5).
- CORS: `/password/reset` sits under the `/api/v1/auth/` prefix. Its JSON body triggers a preflight, and `ALLOW_HEADERS` already covers `content-type`. Nothing in the middleware needs to change.

**Recommendations**
- **Forgot:** `status_code=202`, `response_class=Response`, `dependencies=[rate_limit("forgot")]`, `responses={403, 422, 429}`. Put `_app: AppContextDep` on the route itself (owner nit) and build the link origin from `get_link_origin`. Request body: `ForgotPasswordRequest {email: EmailStr, turnstile_token: str(1..2048)}` with `extra="forbid"`. Order of checks: IP limit, then app context (403), then Turnstile (403 `turnstile_failed`), then the silent per-email limit. Set `Cache-Control: no-store`.
- **Reset:** public, no `X-MM-App`. This matches `/verify-email`, where the token is the credential. No cookie is set, so a forged cross-site request can't sign anyone in. Use `ResetPasswordRequest {token: Field(pattern=EMAIL_TOKEN_RE), new_password: SecretStr(max_length=128)}`, `status_code=204`, `responses={422, 429}`. Give each schema an explicit name so openapi-fetch generates clean types. Add `operation_id`s only if other routes already use them.
- **`docs/consumers.md`:** add a "Password reset" section shaped like the verify-email one: `{origin}/reset-password#token=…`, clear the hash, `Referrer-Policy: no-referrer`, a status table (202/403/429 for forgot; 204/422 `token_invalid`/422 `weak_password`/429 for reset), and the "access tokens stay valid ≤ 15 min" note. Also update the existing "(once USR-04b lands)" wording.
- **Tests** (new `tests/test_password_reset_api.py`, modelled on `test_email_verification_api.py`):
  - forgot: identical 202 for known, unknown, disabled and limited emails, with mail counted on the fake sender; Turnstile 403; app-context 403 with no side effects; per-IP 429; link built from the request's Origin
  - reset: happy path, then `/auth/token` returns 401 on two devices; a weak password leaves the token usable; used, expired, superseded and disabled-user tokens give 422; malformed token gives 422; `login_failures` cleared; `email_verified_at` set; per-IP 429; logs contain neither the token nor the address

**Risks**
- Size: the core ticket is about 450–600 lines (service, repo method, two routes, about 20 tests, docs). The follow-ups add about 200–300 more:
  - per-purpose daily cap: a config change plus a test fix (`test_daily_cap_resend_429_and_signup_still_201`)
  - resend rate-limit scope
  - lifespan `aclose`
  - retention cleanup: needs a job or CLI, which is out of scope
- I recommend splitting. Keep the per-purpose cap reservation and the resend scope in USR-04b, since reset depends on them (S1/S2). Move lifespan, retention and the advisory lock to a USR-04d hardening ticket.
- Timing: forgot must make the same DB writes on both branches. The Turnstile verify call also adds latency jitter.

**Open questions**
1. Is a 403 for a missing `X-MM-App` acceptable before the Turnstile check? It doesn't reveal whether an email exists.
2. Should reset with a disabled user still consume the token?
3. Should the reset success also revoke still-valid access tokens? The ticket only documents the ≤ 15 min window, so presumably not.
