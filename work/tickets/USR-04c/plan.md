Status: READY

# Plan — USR-04c: Require X-MM-App on signup; remove the EMAIL_LINK_BASE_URL fallback

## Spec summary
- spec-v1 §5.8: "**Email flows:** email verification and password reset by one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface".
- Ticket goal (owner, 2026-11-13): "Verification links always point to the app the user signed up in. No app-specific default page." This supersedes USR-04 plan A1 (optional `X-MM-App` on signup + `EMAIL_LINK_BASE_URL` fallback).
- identity-architecture §4 / D12: the app-context check is `X-MM-App` of an active app + an `Origin` registered for it (`AppContextService.check`). The same check now applies to signup. Signup stays R3 class 3 (public, no cookie).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Signup requires app context. A missing or malformed `X-MM-App` returns 403 `app_header_required`. An unknown or disabled app, or a missing, `null`, unregistered or loopback-in-prod `Origin`, returns 403 `origin_not_allowed`. All of these happen before Turnstile, before any user row and before any mail. | `test_email_verification_api.py › test_signup_bad_app_context_403_no_side_effects` (parametrized) checks the code, `User` count == 0, `fake_turnstile.calls == []`, `fake_email.calls == 0` and send-log count == 0 |
| 2 | The link origin is always the validated request `Origin`. | `test_signup_sends_verification_link_to_app_origin` (existing) and `test_link_ignores_host_and_forwarded_host` (adjusted) |
| 3 | `EMAIL_LINK_BASE_URL` is removed from the setting, the validator, the staging/production gate and `.env.example`. A leftover env value is ignored. SMTP keys are still required in staging/production. | `test_config.py › test_smtp_required_in_production` (parametrized over SMTP_USERNAME and SMTP_PASSWORD only); `test_stray_email_link_base_url_ignored`; `grep -rn EMAIL_LINK app tests .env.example` is empty |
| 4 | Resend behaviour is unchanged. | Existing resend tests pass without edits |
| 5 | Existing signup tests send the header; `make check` is green. | `make check` |
| 6 | Docs are updated: consumers.md, deploy.md, ADR-0007 amendment, identity-architecture §4 row, and a v1 contract note. | Reviewer reads the diffs |
| 7 | The forgot-password note on USR-04b is in place. | Already present: `work/tickets/USR-04b/ticket.md:45`. No change needed |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/auth/deps.py` | Delete `get_optional_link_origin`. `get_signup_hook` takes `link_origin: Annotated[str, Depends(get_link_origin)]`. | AC1, AC2 |
| `app/modules/auth/hooks.py` | `link_origin: str`. Remove the `None` → "no mail" branch and its log line. | AC2: mail is never skipped silently |
| `app/modules/auth/router.py` | Signup docstring: class 3 public (per-IP limit, Turnstile) plus the required app context (`X-MM-App` + `Origin`, 403). The link uses the validated Origin. | Accurate OpenAPI and docs |
| `app/core/config.py` | Remove the `email_link_base_url` field, its validation block in `_email_gate`, and its entry in the required list. Keep SMTP_USERNAME and SMTP_PASSWORD required in staging/production. Drop imports that are no longer used. Update the comment at the email block. | AC3 |
| `.env.example` | Remove `EMAIL_LINK_BASE_URL=` and its comment line(s). | AC3, R5 |
| `tests/conftest.py` | Remove `TEST_EMAIL_LINK_BASE_URL` and its `os.environ` line. | AC3 |
| `tests/test_config.py` | Drop `EMAIL_LINK_BASE_URL` from the parametrize and asserts. Delete `test_email_link_base_url_rules` and `test_email_link_base_url_loopback_only_locally`. Add `test_stray_email_link_base_url_ignored`. | AC3 |
| `tests/test_email_verification_api.py` | Remove the `TEST_EMAIL_LINK_BASE_URL` import. Replace `test_signup_without_app_header_uses_link_base` with the 403 cases (AC1). Every signup call sends `auth_headers()` and uses the `stoktok` fixture. | AC1, AC2, AC5 |
| `tests/test_auth_signup.py` | Add an autouse `stoktok` app fixture (`make_login_app`). Every `post(URL, …)` sends `auth_headers()`. This covers the rate-limit, 422, 409, Turnstile and hook-override tests. | AC5 |
| `docs/consumers.md` | Rewrite the "Sign-up" paragraph (see Approach step 6). | AC6 |
| `docs/deploy.md` | Remove the `EMAIL_LINK_BASE_URL` row (l.112). The start-up gate sentence (l.119) names SMTP only. Prerequisite step 4 (l.136): the compose line is no longer needed and a leftover value is ignored, so it can be dropped at any time. | AC6 |
| `docs/adr/0007-email-verification-and-delivery.md` | §2: append an "Amended by USR-04c (2026-11-13)" note. Signup requires app context, there is no env fallback, and every registered origin must serve the link pages. | AC6 |
| `docs/identity-architecture.md` | §4 signup row, Auth column: "public, Turnstile, per-IP limit + Origin/`X-MM-App` check". | AC6 |

`tests/test_rate_limit.py` needs no change. It only unit-tests the limiter and never calls the endpoint, so the consult was wrong on this point. `tests/test_cors.py:212-214` needs no change either. Its `client` wraps `DynamicCORSMiddleware` around a fake inner app, so the signup route never runs.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_email_verification_api.py › test_signup_bad_app_context_403_no_side_effects` | Parametrized: no headers → `app_header_required`; `Bad Key!` → `app_header_required`; unknown key → `origin_not_allowed`; disabled app (`make_login_app(..., status="disabled")`) → `origin_not_allowed`; valid key with no Origin, with `Origin: null`, or with an unregistered origin → `origin_not_allowed`. Each case asserts 0 users, no Turnstile call, no mail and an empty send log. Extends the existing `test_signup_bad_app_header_403_no_user`. |
| `test_email_verification_api.py › test_signup_sends_verification_link_to_app_origin` (keep) | The link origin is the request Origin |
| `test_email_verification_api.py › test_link_ignores_host_and_forwarded_host` (adjust) | Both calls send `auth_headers()`. The link is always `ORIGIN`, never Host or X-Forwarded-Host |
| `test_config.py › test_stray_email_link_base_url_ignored` | `EMAIL_LINK_BASE_URL=https://evil.example` in env with `app_env=production` and the full prod kwargs: `Settings` loads and has no `email_link_base_url` attribute |
| `test_config.py › test_smtp_required_in_production` (adjust) | SMTP_USERNAME and SMTP_PASSWORD are still required; the link key is no longer required |

## Approach
1. deps: delete `get_optional_link_origin` and wire `get_signup_hook` to `get_link_origin`, which pulls `AppContextDep`. `rate_limit("signup")` still runs first. The 403 is raised while dependencies resolve, before `SignupService.signup`, which runs Turnstile → hash → insert → hook. That gives AC1's ordering, as both consults confirm. Because FastAPI resolves sub-dependencies before it validates the body, a request with no header and a bad body now gets 403, not 422. That is intended.
2. hooks: make `link_origin` a non-optional `str` and remove the local-only branch. When local has no SMTP, `DisabledEmailSender` (USR-04 A10) still logs that nothing was sent.
3. config: remove the field, validator and gate entry. `extra="ignore"` (config.py:23) makes a leftover env value harmless.
4. Tests as listed above.
5. Do not touch `AppContextService`. Login, resend and signup share one rule (R11 CSRF). `rate_limit` still counts 403 probes per IP.
6. Docs, `consumers.md` "Sign-up" paragraph, to say:
   - Signup **requires** `X-MM-App` and a browser `Origin` that is registered for that app; otherwise it returns 403 `app_header_required` or `origin_not_allowed` before any account is created. Calls from a browser use `credentials: "include"` like the other `/auth/*` calls, which lets CORS answer the preflight that the custom header triggers.
   - The link is `{request Origin}/verify-email#token=…`, so **every registered origin of an app must serve `/verify-email`**, and `/reset-password` later (USR-04b).
   - Add a "v1 contract change (USR-04c, 2026-11-13)" note: signup without `X-MM-App` used to succeed and now returns 403. This change is allowed inside v1 (R9) because no consumers exist yet.

Rules: R1 (no ORM in the router; the check stays in the service), R3 (class 3 plus the app-context check), R5 (key removed from config and `.env.example`), R6 (existing error codes), R7 (nothing beyond the ticket), R8 (tests above), R11 (Origin plus `X-MM-App` on a POST that starts an email flow).

## Specialist input used
- identity-access-engineer: adopted all recommendations (reuse `get_link_origin`, non-optional hook origin, config/.env/conftest cleanup, docs list). Rejected "update `test_rate_limit.py`" because that file never calls the endpoint.
- security-architect: adopted the order and side-effect assertions (Turnstile call count, no user, no mail), keeping SMTP required in production, the stray-env test, and the "every origin serves `/verify-email`" doc rule. On the `admin` question, did not adopt refusal; see A2.

## Assumptions
- **A1 Disabled app → 403 `origin_not_allowed`**, the same as login. `AppContextService.check` already does this, so no new code is needed.
- **A2 `X-MM-App: admin` on signup is allowed.** There is no special case. Reasons:
  - Staff accounts are ordinary mmlabs Identity users (one account for every app), and the admin UI is a registered app whose origins go through the same D12/D19 check.
  - Login and resend already accept `admin`, so refusing it only on signup would split the rule and add a special case to the auth path.
  - Signup grants nothing: an `admin` token is still refused (`tokens/service.py:58`), and admin access needs role assignment.
  - The only cost is that the admin UI origin must serve `/verify-email` like every app, which the step 6 doc rule covers.
  - If the owner prefers to refuse, that is a one-line check in a follow-up.
- **A3** The deployment repo's compose line is removed by the owner at any time. It is harmless meanwhile, so it is not a deploy blocker.
- **A4** The `@mmlabs/auth-client` signup helper (future) must send the header. This is covered by the consumers.md contract; no code exists yet.

## Blocking questions
None

## Split proposal
None. Estimated ~150 changed lines.

## Data model / migration
None

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` | Body unchanged `{email, password, turnstile_token}`. **Headers now required:** `X-MM-App: <key>` and `Origin` registered for that active app. | `SignupResponse` (unchanged) | 201; 403 `app_header_required` / `origin_not_allowed` / `turnstile_failed`; 409; 422; 429 | Public (R3 class 3) plus the app-context check |
