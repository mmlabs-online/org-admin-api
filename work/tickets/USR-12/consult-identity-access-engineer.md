# consult-identity-access-engineer: USR-12 (resend verification for `admin`)

**Bottom line: allow it.** `POST /api/v1/auth/verify-email/resend` should accept `X-MM-App: admin`. Signup and forgot keep refusing `admin`. The reasons for refusing signup and forgot don't apply to resend. ADM-002d only included resend because the rule was framed as "every route that mails a link"; nobody wrote down a threat specific to resend.

## Findings

**1. Why ADM-002d refused `admin` on mail-link routes.**
- My ADM-002d consult proposed refusing **signup and forgot only** (`work/tickets/ADM-002d/consult-identity-access-engineer.md` rec 5).
- The security-architect consult added resend with one stated reason: "all three mail a link to the admin origin" (`consult-security-architect.md:26`).
- The planner adopted that wording (`plan.md:234`, assumption (e)). The amendment at `docs/adr/0008-admin-api-authentication.md:123-125` gives no reason specific to resend.
- The real reasons apply to signup and forgot only:
  - **Signup** is public and unauthenticated. It would make admin-portal a place where anyone can create accounts, it needs Turnstile there, and staff accounts should come from the operator path (ADR-0010).
  - **Forgot** is public and unauthenticated. It sends a reset link for *any* typed email, so admin-portal would become a password-reset front end for every mmlabs user. It also needs Turnstile and a `/reset-password` page on the most privileged origin.

**2. What resend actually does** (`app/modules/auth/router.py:294-326`, `email_verification.py:124-143`).
- It is cookie class. The device cookie must resolve to an active account (`get_active_account`), otherwise 401.
- The user row is read with `get_for_share`, so the ADM-002d security-review follow-up 3 is already fixed. A non-active user gets 401, and an already verified user gets 409 `email_already_verified`.
- The recipient is always the session user's own stored address. The caller cannot choose the target, so this is not an amplification or harassment vector.
- Rate limits:
  - Per email and purpose, 1/60 s and 5/24 h, keyed by the email HMAC under an advisory lock. This bucket is **shared across apps**, so admin resends count against the same bucket as resends from other apps.
  - Global verify share of `EMAIL_DAILY_CAP`.
  - Per IP via `rate_limit("verify_email_resend")` (5/600 s, `config.py:93-94`).
- The new token replaces older verify tokens. The link is `{validated Origin}/verify-email#token=…`.
- The email text is app-neutral ("your mmlabs account", `email/messages.py:6-21`).
- Verifying is account-wide: verifying through admin-portal unlocks every app, which is the same as verifying from any other app.

**3. Does the ADM-002d threat still hold for resend? No.**
- Anything an attacker can do through admin resend they can already do through resend from any other app: they need the account's password and a cookie, and the mail goes to the owner's address.
- The only real difference is that the link points to `admin-portal.mmlabs.online`. That origin is already public: it is in the CORS echo, and login and token already accept `X-MM-App: admin`.
- Pre-registration (an attacker signs up with a victim's email, then the victim clicks a link) already works through signup on any app. Allowing admin adds nothing.

**4. Today's dead end.**
- The ADR-0010 CLI cannot verify an existing account ("never on an existing account", D16 amendment).
- Unverified staff get 403 `email_unverified` from `/auth/token` for `aud=admin`, and their only way out is to visit another app. That conflicts with R11's intent ("verified email is required", not "verified through some other app").

**5. Splitting resend from signup and forgot is clean.**
- `get_mail_link_app_context` (`deps.py:84-92`) is used only through the route-level `_app` on the three routes and through `get_link_origin` (`deps.py:103`).
- Resend is the only caller that would need a different origin dependency. Signup's link origin comes in through `get_signup_hook` → `get_link_origin`, and forgot uses `get_link_origin` directly.

**6. Existing tests that must change.** These are intentional; acceptance criterion 3 only protects the signup and forgot ones.
- `tests/test_auth_app_context.py:212`: the `resend` param of `test_admin_context_refused_on_mail_routes`.
- `tests/test_auth_app_context.py:268`: `resend` in the loop of `test_admin_context_refused_even_if_link_origin_overridden`.
- These tests use `ADMIN_ORIGIN = "https://admin.mmlabs.online"` (`:179`), but the ticket and spec §5.9 say `https://admin-portal.mmlabs.online`.

## Recommendations

**R-A. Code** (acceptance criterion 2; R1, R3, R11).
- In `auth/deps.py`, pull the body of `get_link_origin` out into a private `_validated_link_origin(request) -> str`.
- Keep `get_link_origin(request, _app: MailLinkAppContextDep)` for signup and forgot, unchanged in behaviour.
- Add `get_resend_link_origin(request, _app: AppContextDep) -> str`. Its docstring should say that `admin` is allowed only here because resend is cookie class and mails only the session user's own address (USR-12).
- Narrow the docstring of `get_mail_link_app_context` to "(signup, forgot)".
- In `router.py` `resend_verification`:
  - `_app: AppContextDep` and `link_origin: Annotated[str, Depends(get_resend_link_origin)]`.
  - Keep `_app` declared **before** `device: DeviceDep`, so a bad app context still answers 403 before any device lookup or cookie rotation.
  - Update the docstring and drop the "admin gets the same 403" line.
- No service, schema, migration or rate-limit change.

**R-B. Tests** (acceptance criteria 3 and 4; R8). Use `https://admin-portal.mmlabs.online` as the admin origin in the new tests.
- **Admin resend succeeds:** 202; the fake dispatcher gets one mail to the user's address; the link starts with `https://admin-portal.mmlabs.online/verify-email#token=`; one `email_send_log` row; cookie rotation works as for other apps.
- **End to end:** redeem the token via `POST /auth/verify-email`, then `/auth/token` with `audience: admin` no longer returns `email_unverified` (use a staff fixture with an admin role). This proves the ticket goal.
- **Unregistered origin:** `X-MM-App: admin` with a non-admin origin (another app's registered origin, and an unknown origin) → 403 `origin_not_allowed`, no `Set-Cookie`, no mail, no send-log row.
- **Rate limits:**
  - A second admin resend within 60 s → 429 with `Retry-After`.
  - **Cross-app sharing:** a resend from another app followed by an admin resend within 60 s → 429. This pins that admin gets no separate bucket.
  - The 6th request in 24 h → 429.
  - Per-IP `verify_email_resend` limit → 429.
- **Already verified:** → 409 `email_already_verified` with no send-log row, as for other apps.
- **401 cases:** no cookie, and an `account_id` that is not on the device.
- **Disabled user:** → 401.
- **Signup and forgot:** keep `test_admin_context_refused_on_mail_routes[signup|forgot]`, `test_admin_context_refusal_matches_unknown_app`, and the override test for forgot. Remove the resend cases (point 6 above).
- Optionally, a structural pin test asserting that `signup` and `forgot_password` depend on `get_mail_link_app_context` and `resend_verification` does not. This catches someone later "unifying" the dependencies back.

**R-C. Decision record** (acceptance criterion 1).
- Add a dated amendment to ADR-0008: "Resend allowed for admin (USR-12, 2026-11-16)". It should state:
  - the ADM-002d reason ("mails a link to the admin origin");
  - why that is not a threat for resend: authenticated, own address only, shared limits, origin already public;
  - that signup and forgot stay refused, with their own reasons: unauthenticated, any email, Turnstile, account creation.
- Edit the ADM-002d bullet to read "Signup and password forgot … (resend: see USR-12 amendment)".
- Add D24 to `docs/identity-architecture.md`, since D22 and D23 are taken.
- ADR-0007 Consequences already says "every app that triggers … resend must serve `/verify-email`". Add a note that this now includes admin-portal.

**R-D. `docs/consumers.md`** (acceptance criterion 5).
- Replace lines 91-92 with: admin-portal must not host sign-up or forgot-password; it **may** offer resend for a signed-in, unverified staff member, and then **must** serve `/verify-email`.
- Describe the flow: `/auth/token` returns 403 `email_unverified` → show a "send verification email" button → `POST /auth/verify-email/resend` with `credentials: "include"`, `X-MM-App: admin` and `{account_id}` → handle 202, 401, 409 and 429 using the existing table.

**R-E. What the admin-portal `/verify-email` page must do** (hand to admin-pan, PAN-010b):
- Be a **public** route, outside the auth guard. The link may open in another browser that has no session.
- Read `location.hash`, then call `history.replaceState(null, "", "/verify-email")` immediately.
- Serve the page with `Referrer-Policy: no-referrer` and load no third-party scripts or analytics on it. The fragment token is readable by any JS on the most privileged origin.
- `POST /api/v1/auth/verify-email` with `{token}`, `credentials: "omit"` and **no** `X-MM-App`. Never put the token in a query string.
- On 204: show "verified". If an admin session exists, call `/auth/token` again (`audience: admin`) and then `GET /api/v1/me/permissions`; otherwise send the user to login.
- On 422 `token_invalid`: offer resend if signed in, else login. On 429: honour `Retry-After`.
- Do not implement `/reset-password` (forgot is still refused for admin).

## Risks
- **New public page on the most privileged origin:** admin-portal gains a public, token-bearing page (`/verify-email`). An XSS there is already game over for admin tokens, so the verify token adds little. Still, the page's script surface needs to stay minimal (R-E).
- **Non-staff mail to admin-portal:** any mmlabs user can log in at admin-portal (login accepts `admin`) and now trigger a verification mail with a link to admin-portal. This is harmless (own address, verification is account-wide), but it is a visible behaviour change. Note it in the amendment.
- **Dependency-order regression:** moving `_app` after `DeviceDep` would rotate the cookie before the 403. The existing no-`Set-Cookie` assertion should carry over to the new unregistered-origin test.
- **Admin origins not registered or cleared:** if admin origins are not registered, or were cleared with `set-admin-origins --clear`, admin resend fails closed with 403. That is the correct behaviour, but admin-pan should expect it.
- **Inconsistent admin origin:** the admin origin constant differs between the tests and docs (`admin.mmlabs.online` vs `admin-portal.mmlabs.online`), which could confuse the new tests and the runbook.

## Open questions
1. Which admin origin is actually registered in production? Spec §5.9 says `https://admin-portal.mmlabs.online`, while the ADM-002d runbook and test constant use `https://admin.mmlabs.online`. Should the docs and runbook be aligned in this ticket or in a follow-up? Under R7 I'd make it a follow-up and use admin-portal only in the new tests.
2. Should admin-pan confirm it will ship `/verify-email` before or together with this PR? If the PR merges first, admin resend links will 404 on admin-portal. Suggest the amendment notes the dependency, and the owner merges when admin-pan is ready.
3. Does the owner want an audit or log line for resend? ADR-0009:93 currently lists resend as "not audited". I recommend no change, to stay in scope.
