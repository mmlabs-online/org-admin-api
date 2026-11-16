## Attempt 1

## Security review: USR-12, attempt 1 (branch claude/blissful-wozniak-fx4lm9)

I reviewed `git diff origin/main...HEAD` without changing anything. As asked, I did not run `make check` or pytest, so nothing below was confirmed by a test run.

### Findings

**🟡 should-fix: `docs/consumers.md:95` says something untrue about the admin UI's cookie use**
- The new admin UI bullet says resend with `X-MM-App: admin` is "the only admin-context call that uses the cookie".
- That is wrong. `/auth/token` with `X-MM-App: admin` also uses `credentials: "include"` and the device cookie (the same file says so at line 70), and so does logout.
- **Risk:** an admin-pan developer could take this literally and get cookie handling wrong on `/auth/token` or logout. That would break sign-in, or lead them to treat resend as a special case in their CSRF and credentials handling.
- **Fix:** replace the parenthetical with something like "(cookie class, like `/auth/token`: the only admin-context call that sends mail)", or simply delete it.

**💭 note: `docs/consumers.md:147-148` still has the general rule without the admin exception**
- It says "Every registered origin of an app must serve `/verify-email` and `/reset-password`", but line 91–92 now exempts admin-portal from `/reset-password`.
- **Fix:** append "(admin-portal: `/verify-email` only; see the admin UI section)".

**💭 note: the pin test could also pin the link-origin dependency (`tests/test_auth_app_context.py:281-291`)**
- It already pins that `get_mail_link_app_context` is on signup and forgot and not on resend, and that resend has `get_app_context` and `get_resend_link_origin`. `dependant_calls` walks dependencies recursively, so this is sound.
- Optional hardening: add `assert get_resend_link_origin not in routes[SIGNUP] | routes[FORGOT]`. If someone wires the wrong origin dependency on those routes later, that would then fail loudly.
- It is not exploitable today: the route-level `_app: MailLinkAppContextDep` (`router.py:78` and `:342`) refuses admin on its own.

**💭 note: a non-staff 403 comes after the device lookup, by design**
- An unregistered origin gets its 403 before `DeviceDep` runs. The non-staff 403 is raised in the service, after `DeviceDep` has resolved and possibly rotated the cookie.
- The rotation is rolled back by `get_session` (`app/db/session.py:31-35`, rollback on exception), and the 403 response carries no `Set-Cookie`.
- The net effect is no rotation, no token, no send-log row and no email. Optionally, `test_admin_resend_non_staff_403` could also assert `_rotated_at` is unchanged, as the unregistered-origin test does.

No 🔴 blockers.

### Verified OK
- **Signup and forgot still refuse admin before anything else runs.** Both keep the route-level `MailLinkAppContextDep` (`router.py:78`, `:342`), and `get_link_origin` / `get_signup_hook` still depend on it (`deps.py:110-113`, `:122-127`). Admin is refused with 403 before Turnstile, any write or any email. The existing parametrized test still covers signup and forgot, and the override test now covers both as well.
- **Resend with admin needs a registered admin Origin.** `AppContextDep` is a route-level parameter declared before `DeviceDep` (`router.py:308-312`), so an unknown app or unregistered origin gets 403 before any device lookup or rotation.
  - `test_admin_resend_unregistered_origin_403` covers no origins, another app's origin and a foreign origin.
  - It asserts no `Set-Cookie`, `rotated_at` unchanged, no email, no send-log row, and a body identical to an unknown app's.
- **Resend still requires a valid device cookie and an account on that device.** Covered by tests: no cookie gives 401 `session_invalid`, another device's account gives 401, a disabled user gives 401.
- **Resend with admin requires a staff role.** `email_verification.py:143-146` uses `AuthzService.effective_roles(user.id, "admin")`.
  - This is the same check `/auth/token` uses (`tokens/service.py:73`) and `admin_auth/service.py:44`: roles valid for the whole app, not expired, with the prefix checked twice.
  - It runs after the session and user-status checks and before the already-verified 409 and `prepare()`, so a non-staff caller gets no token, no send-log row and no email.
  - Tests cover: no role, verified with no role, expired admin role, and a role only in another app (all 403). A non-super admin role (`admin:support`) gets 202.
- **No new enumeration oracle.** The 403 for non-staff only happens after the caller has proved a session for that account. It has the same body as an unregistered origin. It tells the caller only their own staff status, which `/auth/token` `not_member` already reveals. The email address is not an input.
- **Rate limits are unchanged and shared across apps.** The route-level `rate_limit("verify_email_resend")` per IP is untouched, as is the throttle in `prepare()` (keyed on the email HMAC, independent of app). Tests cover 1/min, 5/day (`Retry-After` 19h), the limit shared with the signup email from another app, and the per-IP 429.
- **The link and verify flow are correct.**
  - The link goes to the validated, normalized `Origin` (`_validated_link_origin`, which fails closed if `Origin` is missing).
  - The end-to-end test runs `/auth/token` → `email_unverified` → resend → verify → `/auth/token` 200.
- **Layering (R1) holds.** The service raises `OriginNotAllowedError` (an `AppError`), with no `HTTPException`. There is no schema change, no new config and no logging of email or token.
- **Docs are accurate apart from the 🟡 above.**
  - ADR-0008: the original bullet is narrowed with a pointer, and the dated amendment states the ADM-002d reason (inferred), why it does not apply to resend, the staff-only rule (F6), the residual risks and the rejected alternative.
  - D24 is in `identity-architecture.md` §8, and the §API table is updated.
  - ADR-0007 consequence updated.
  - `consumers.md` sets out what `/verify-email` needs for admin-portal: public page, hash read and then removed, `no-referrer`, `credentials: "omit"`, no `X-MM-App`.

### Follow-ups (out of scope)
- Before granting an admin role to an existing account, confirm with the person, because of the pre-hijack risk through other apps' origins. The ADR already lists this as a residual risk; it needs an ops runbook entry or a ticket.
- Whether to keep the staff-only rule is the owner's call. It is a planner default, and the ADR says it may be reverted at PR review.

### Files reviewed
- /home/user/org-admin-api/app/modules/auth/deps.py
- /home/user/org-admin-api/app/modules/auth/router.py
- /home/user/org-admin-api/app/modules/auth/email_verification.py
- /home/user/org-admin-api/tests/test_auth_app_context.py
- /home/user/org-admin-api/tests/test_email_verification_api.py
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/adr/0007-email-verification-and-delivery.md
- /home/user/org-admin-api/docs/identity-architecture.md
- /home/user/org-admin-api/docs/consumers.md

**Verdict: PASS (security), with one 🟡 docs fix recommended (`docs/consumers.md:95`). No blockers.**
