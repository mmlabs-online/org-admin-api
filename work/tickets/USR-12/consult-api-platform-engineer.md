## USR-12 consult (api-platform-engineer): allow resend for `X-MM-App: admin`

Read-only. I checked `app/modules/auth/deps.py`, the resend and forgot routes in `router.py`, `email_verification.py`, `tests/test_auth_app_context.py`, `tests/test_email_verification_api.py`, ADR-0008, `docs/consumers.md` and the ADM-002d ticket files. I did not run any tests.

### Findings

1. **Current wiring.** `get_mail_link_app_context` (deps.py:84) wraps `AppContextDep` and raises 403 `origin_not_allowed` for `admin`. `get_link_origin` (deps.py:103) depends on it. Signup, forgot and resend all use `MailLinkAppContextDep` at the route level (router.py:77, 307, 341) and `get_link_origin`. So resend cannot accept admin today without touching this shared chain.

2. **The ADM-002d reason is not written in the ADR.** The ADR bullet (ADR-0008 lines 123-125) gives only the rule. ADM-002d's identity consult (`work/tickets/ADM-002d/consult-identity-access-engineer.md`, item 5) says signup and forgot accepted admin as soon as admin origins were set, and proposed a wrapper "on signup and forgot only; keep cookie routes unchanged". The plan then also put resend on the refused list. I read the intent as follows. It is my inference, not stated anywhere, so the planner should say so in the amendment.
   - Keep the highly privileged admin origin out of the public, unauthenticated mail-link flows. These are open signup and forgot, which create accounts or send reset links to an arbitrary email, and the link then lands on the admin origin.
   - Return one uniform 403, so there is no oracle.

3. **Why it does not hold for resend (the case to record).**
   - Resend is cookie class (R3 class 2). It needs a valid `__Host-mm_device` and an `account_id` owned by that device session, else 401 `session_invalid`.
   - It creates no account and runs no Turnstile path.
   - The link goes only to the caller's own already-registered address, to the validated `Origin`.
   - Limits are unchanged: 1/min and 5/day per email (`email_send_log` is keyed by email HMAC, not by app), per-IP `verify_email_resend`, and the global daily cap.
   - Verifying an email grants no access. Admin access still needs an `admin:*`-guarded role grant, and `current_admin` re-checks `email_verified_at` and the role on every request (ADR-0008 §3).
   - So there is no new enumeration or account-creation path. The residual change is that a verification link can point at the admin origin.

4. **Real dependency, not an API risk.** The link format is `{origin}/verify-email#token=…` and every registered origin must serve `/verify-email` (consumers.md:138-139). The admin UI must therefore serve `/verify-email` at its origin. consumers.md:91-92 currently says `admin-portal` cannot host the resend screen, so that text must change.

5. **Admin has no join step.** Admin is `closed`, and join already refuses admin (consumers.md:274). Verification is therefore the only thing blocking an admin token (`email_unverified` from `/token`).

### Recommendations

**Dependency split (deps.py).** Do not add a boolean `allow_admin` flag. Use separate, explicitly named dependencies so new routes stay fail-closed.
- Extract a private helper `_validated_origin(request) -> str`. It is the body of `get_link_origin` today: read the `Origin` header, fail closed with `OriginNotAllowedError` if absent, then `normalize_origin`.
- Leave `get_mail_link_app_context`, `MailLinkAppContextDep` and `get_link_origin` as they are, now calling the helper. Signup and forgot keep using them untouched.
- Add `get_resend_link_origin(request, _app: AppContextDep) -> str`, which also calls the helper. It depends on the plain `AppContextDep`, which already guarantees `Origin` is registered for the active `X-MM-App` app, admin included.
- In `resend_verification`, change `_app: MailLinkAppContextDep` to `_app: AppContextDep` and `link_origin` to `Depends(get_resend_link_origin)`.
  - Keep `_app` before `device: DeviceDep`, as USR-04d requires. A bad app context must give 403 before any device lookup or cookie rotation.
  - Update the docstring at router.py:313-317. Remove "X-MM-App: admin gets the same 403". Say that admin is accepted for resend only (USR-12).
- Add a deps.py comment: only resend may use the plain app context for a mail link, and signup and forgot must stay on `MailLinkAppContextDep`. Rewrite the `get_mail_link_app_context` docstring, which still lists resend.
- The service, response codes and envelope do not change: 202 `no-store`, 401, 403, 409, 422, 429 with `Retry-After`. Behaviour for non-admin apps does not change.

**Tests (R8).**
- `tests/test_auth_app_context.py`:
  - `test_admin_context_refused_on_mail_routes` (line 212): reduce the parametrization to `["signup", "forgot"]`. Resend would now return 202 and the test would fail.
  - `test_admin_context_refused_even_if_link_origin_overridden` (line 268): loop over `("signup", "forgot")` only. Keep the `get_link_origin` override. Adding signup there is cheap.
  - Update the `_prepare` docstring.
- Add admin resend tests. Reuse `_admin_origins`, `make_login_user(verified=False)`, `make_device` and `auth_headers(app_key="admin", origin=…, cookie=…)`. Use the existing `_resend` and `_send_log_count` helpers from `test_email_verification_api.py`.
  - **Success:** 202, one email sent, and the body contains `{admin_origin}/verify-email#token=`. Check one `EmailToken` row, one send-log row, and `Cache-Control: no-store`.
  - **Rotation:** a rotation-due cookie is forwarded via `Set-Cookie`, as in `test_resend_forwards_rotated_cookie`.
  - **Unregistered origin:** `X-MM-App: admin` with an origin not in `apps.origins` gives 403 `origin_not_allowed`. Do the same with the admin app's origins empty (the default). Check no `Set-Cookie`, no email, and a body equal to the unknown-app 403.
  - **Cookie required:** admin resend without a cookie gives 401 `session_invalid`. A foreign `account_id` gives 401.
  - **Rate limits for admin:** 429 `rate_limited` within a minute of a prior send, with `Retry-After` between 1 and 60. 429 after 5 sends in a day, with `Retry-After == 19*3600`. Per-IP limit via `make_client(verify_email_resend_rate_limit=1)`. Existing patterns are at test_email_verification_api.py:463-501 and 610-624.
  - **Already verified:** same 409 as other apps (pattern at test_email_verification_api.py:595).
  - **Regression:** the admin signup and forgot refusal tests stay green (AC 3).
  - **Optional end-to-end:** resend, then `POST /auth/verify-email` with the token, then `/auth/token` with `audience=admin` gives 200 for a user holding an admin role.
- `tests/test_email_verification_api.py:704-722` (`test_resend_app_context_checked_before_device`): change the override from `get_link_origin` to `get_resend_link_origin`, or the test passes without testing anything.
- `tests/test_route_auth_classes.py`: the cookie-class allow-list should not change. Run it to confirm.

**OpenAPI impact.**
- The paths, bodies, statuses and error schema are unchanged.
- The only difference is the operation description from the docstring, plus any text that says admin is refused. Grep for it in router docstrings and `DeviceCookieDoc` or the `X-MM-App` parameter docs.
- This relaxes a constraint, so it is additive and not breaking (R9, no breaking changes in v1). No version bump and no deprecation are needed.

**Docs.**
- **ADR-0008:** add "Amendment — Admin verification resend (USR-12, 2026-11-16)".
  - It narrows the ADM-002d bullet at line 123 to signup and forgot.
  - It should state the threat-model reason and why it does not hold for resend (Findings 2 and 3).
  - It should name the dependency on the admin UI serving `/verify-email`.
  - It should say that signup and forgot remain refused.
  - Edit the original bullet to "(resend: see USR-12 amendment)".
- **`docs/identity-architecture.md`:** add decision D24 in the decision log. Check the last D number first, because line 271 was truncated in my search. Also update the endpoint table row at line 143. Check whether S5 or §4 mention admin refusal.
- **`docs/consumers.md`:**
  - Replace lines 91-92 with something like:
    > `admin-portal` cannot host sign-up or forgot-password screens: those calls with `X-MM-App: admin` get 403 `origin_not_allowed` (ADM-002d). Verification resend is allowed (USR-12).
  - Add a bullet to the admin UI section:
    > **Unverified staff email.** `/auth/token` with `audience: "admin"` answers 403 `email_unverified`. The UI can offer "Resend verification email": `POST /api/v1/auth/verify-email/resend`, `credentials: "include"`, `X-MM-App: admin`, body `{"account_id"}` (from `GET /auth/accounts`).
    > - 202: tell the user to check their inbox. The link is `{admin origin}/verify-email#token=…`, so the admin UI must serve `/verify-email` (read the fragment, remove it with `history.replaceState`, then `POST /auth/verify-email` with `credentials: "omit"`).
    > - 429: honour `Retry-After`. The limits are 1/min and 5/day per email.
    > - 409: already verified, so retry `/auth/token`.
    > - 401 `session_invalid`: send the user to login.
    >
    > Verifying the email does not grant admin access, which still needs a role from a super-admin.
  - In the "Email verification" section near line 165, add a note that admin is accepted for resend.

### Risks

- **Admin UI readiness.** If admin-pan does not serve `/verify-email` at `https://admin-portal.mmlabs.online`, the emailed link lands on a 404. This blocks PAN-010b even though the API change works, so coordinate with admin-pan.
- **Fail-closed drift.** If a later change reuses `get_resend_link_origin` for another route, that route would accept admin. The naming and the deps.py comment are the guard. A test that signup and forgot still refuse admin covers the main case.
- **Shared limits.** Sends are limited per email and per IP, and the daily cap is global (shared across apps). Admin-origin resend adds no new capacity for abuse, but it shares the cap with other apps. This is accepted.
- **Hash scrubbing.** The token is in the URL fragment on the admin origin, which also holds in-memory admin tokens. The admin page must remove the fragment immediately (an admin-pan concern).
- **Stale tests.** Leaving the resend case in the ADM-002d admin refusal tests, or leaving the `get_link_origin` override in the resend test, produces a red suite or a vacuous test.

### Open questions for the planner and owner

1. Does admin-pan commit to serving `/verify-email` on its registered origin? This gates the "allowed" outcome in practice.
2. Is the next decision number D24? The latest entries I could see were D21 to D23, but line 271 was truncated.
3. Should the amendment explicitly state that the ADM-002d threat-model reason is inferred from the consult and plan, since the ADR never recorded it?
4. Which origin should the tests use? Existing tests register `https://admin.mmlabs.online` (`ADMIN_ORIGIN`), while the ticket cites `https://admin-portal.mmlabs.online`. Either works for the API, but a test using the production origin would match the docs.

Relevant paths:
- /home/user/org-admin-api/app/modules/auth/deps.py
- /home/user/org-admin-api/app/modules/auth/router.py
- /home/user/org-admin-api/tests/test_auth_app_context.py
- /home/user/org-admin-api/tests/test_email_verification_api.py
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/identity-architecture.md
- /home/user/org-admin-api/docs/consumers.md
