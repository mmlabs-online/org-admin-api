# USR-12 consult from the security architect: admin context on `POST /auth/verify-email/resend`

**Recommendation: allow it, for staff only, under conditions C1–C8 below.** The reason ADM-002d gave for refusing signup and forgot does not apply to resend. Allowing any account (not just staff) would open one small but real phishing path, and C3 closes it.

## Findings

**F1. What ADM-002d actually protected against**
- The original ticket scope (`work/tickets/ADM-002d/ticket.md` item 5) covered only signup and forgot.
- Resend was added by analogy in my ADM-002d consult (`consult-security-architect.md` #5: "all three mail a link to the admin origin") and in `plan.md:234`. No separate threat was named for resend.
- The real threat was about **unauthenticated** routes, where anyone can name any email:
  - **forgot:** anyone could make the API send a **password-reset token** (an account-takeover credential) for any address, including staff, to the admin origin. That would turn the admin UI into a place that collects reset tokens, and would make "admin portal password reset" emails something an attacker can trigger for phishing.
  - **signup:** anyone could create accounts, and get admin-branded mail sent to any address they choose.

**F2. Why that does not hold for resend**
- Resend is cookie class: it needs `__Host-mm_device` holding an active account (`email_verification.py:127-134`), so the caller has already proved the password.
- The recipient is always that account's own `user.email`. The caller cannot choose who gets the mail.
- The token only marks the email as verified, it is not a credential. Using it still needs access to the mailbox, so R11 ("verified email before any app token") is not weakened.
- The link origin is the `Origin` header, checked against `apps.origins` for `admin`. That list has at most 2 entries, is set only by the CLI, and can never be shared with another app (ADR-0008, ADM-002c).
- The email template has no text the caller controls.

**F3. Email bombing**
- The 1/min and 5/day limits are keyed per email HMAC and purpose, across all apps (`sends.lock(email_hmac, VERIFY)`), and signup counts as the first send. The global daily cap and its 80% share for verify mails are shared too. There is also a per-IP limit (`verify_email_resend`, 5 per 600 s by default).
- So the admin context adds **no** extra sending capacity: the worst case is still 5 mails a day to an address the caller controls the account for.

**F4. Enumeration**
- No new oracle. The outcomes are 401 `session_invalid`, 409 `email_already_verified` and 429, all about the caller's own account. The unregistered-origin case answers 403 before any device lookup, as today.

**F5. Any mmlabs account can currently use the admin context**
- `login` with `X-MM-App: admin` checks no membership.
- `/token` raises `email_unverified` **before** `not_member` (`tokens/service.py:71-79`). So a non-staff user who signs in on admin-portal also sees `email_unverified`, and the admin UI would show them "resend" too.

**F6. A real gap if resend is open to any account (pre-hijack phishing)**
1. An attacker signs up in any app with an address that has no account yet (for example a future staff member's email). The attacker knows the password; the account is unverified.
2. The attacker logs in on admin-portal and calls resend. The victim receives a genuine mmlabs email linking to `https://admin-portal.mmlabs.online/verify-email`.
3. A future staff member expecting admin onboarding is much more likely to click an admin-portal link than a stoktok one.
4. The click verifies **the attacker's** account. If ops then grants an admin role to "the user with that email", the attacker gets admin.

The same attack already works through other apps' origins. Admin branding raises the click rate exactly for the target that matters. Limiting admin resend to accounts that already hold an admin-app role (C3) removes the branding advantage, because the attacker's account has no role.

**F7. Lock ordering is already fixed.** ADM-002e added `get_for_share` in resend, so the earlier concurrent-disable race is closed and allowing admin does not reopen it.

## Recommendations (conditions for "allow")

- **C1. Narrow refusal (AC2, AC3).**
  - Split the dependency. Signup and forgot keep `MailLinkAppContextDep`.
  - Resend uses `AppContextDep` plus a resend-specific link-origin dependency that does **not** refuse `admin`.
  - Do **not** loosen `get_link_origin` itself: the signup hook and forgot depend on it, and `test_admin_context_refused_even_if_link_origin_overridden` must stay meaningful for forgot.
- **C2. Order unchanged (R3, R11).** The route-level app context still resolves before `DeviceDep`. An unregistered origin with `X-MM-App: admin` gets 403 `origin_not_allowed` with no device lookup, no `Set-Cookie` and no email.
- **C3. Staff-only.**
  - After the device, account and user checks (and before `prepare()`), when `app.key == "admin"` the user must have the same admin-app membership predicate that `/token` uses for `aud=admin`. Reuse it; do not write a second query.
  - Otherwise answer 403 `origin_not_allowed`, exactly as non-staff get today, with no token and no send-log row. This changes nothing for non-staff.
  - It does not leak anything to third parties: only the account owner can ask, and `/token` would tell them `not_member` once verified.
  - If the planner decides to drop C3, record F6 as an accepted risk in the ADR amendment.
- **C4. Limits unchanged (AC2).** No new limit keys and no admin bypass. The per-email limits, the global and per-purpose caps, and the per-IP limit apply as they are.
- **C5. Link format unchanged:** `{validated admin origin}/verify-email#token=…`.
  - `docs/consumers.md` (AC5) must say admin-pan serves `/verify-email` with the full contract: clear the fragment, `Referrer-Policy: no-referrer`, `credentials: "omit"`.
  - It must also say admin-pan is **exempt** from serving `/reset-password`, since forgot stays refused there. Today the doc says every registered origin must serve both pages.
  - Replace the line at `consumers.md:91-92` with: "admin-portal may offer resend-verification to a signed-in staff member; it cannot host sign-up or forgot-password."
- **C6. Tests (AC4, R8).**
  - Change the `resend` case of `test_admin_context_refused_on_mail_routes` and of the override test into "allowed" tests.
  - Add:
    - staff resend → 202, with the email link starting `https://admin-portal.mmlabs.online/verify-email#token=`
    - an unregistered origin with `X-MM-App: admin` → 403, no `Set-Cookie`, no dispatcher call
    - a non-staff account in admin context → 403 `origin_not_allowed`, no `email_tokens` or `email_send_log` rows
    - a second call within 60 s → 429; 5 per day → 429, shared with a signup sent from another app for the same email
    - an already-verified staff account → 409 `email_already_verified`
  - Signup and forgot with admin → still 403 before Turnstile, any write or any email.
- **C7. Record the decision (AC1).**
  - Add a dated amendment to ADR-0008 stating:
    - it supersedes the resend part of "No admin context on mail-link routes";
    - the reason, from F1 and F2;
    - the C3 rule;
    - the residual risk.
  - Add a matching decision-log entry in `docs/identity-architecture.md`.
- **C8. Audit and logs.** No change. Resend writes no audit row today, and admin context adds no privileged action, so R4 is not affected. Logs keep `user_id` only, never the email.

## Risks
- **R-a. Mail that now links to admin-portal.** Staff can no longer be taught that "admin-portal never emails you links". Phishers can copy the link pattern, but cannot make mmlabs send it to people who are not staff (C3).
- **R-b. Pre-hijack (F6) is wider than this ticket.** It also works through other apps. Onboarding should not grant an admin role to an account found by email unless that person signed it up themselves. Suggest a follow-up runbook note: before granting, confirm with the person, or make them run a password reset first.
- **R-c. A future admin-pan bug.** XSS on `/verify-email` could leak verify tokens, but only for staff who asked for them; the token is low value and the page has a small surface.
- **R-d. Cookie rotation before the C3 403.** The C3 check runs after device resolution, so the 403 may carry a rotated cookie. That is harmless and consistent with other cookie-class 403s.

## Open questions
1. Does the planner accept C3 (staff-only), or does it prefer allowing every account and recording F6 as an accepted risk? I recommend C3.
2. For C3, which predicate counts as "staff"? Any unexpired admin-app role assignment (so the CLI `grant-super-admin` case on an unverified user is covered)? Please confirm which helper `/token` uses so the planner can reuse it.
3. Should non-staff get a dedicated code instead of `origin_not_allowed`, for example `not_member` as on `/token`? `origin_not_allowed` keeps today's behaviour; `not_member` is easier for the UI to explain. Either is safe.
4. Does password reset revoke the other accounts and device sessions of a user? That decides whether "reset before grant" is a sufficient onboarding mitigation for R-b.

## Files
- `/home/user/org-admin-api/app/modules/auth/deps.py` (lines 84-109)
- `/home/user/org-admin-api/app/modules/auth/router.py` (lines 294-326)
- `/home/user/org-admin-api/app/modules/auth/email_verification.py` (lines 76-143)
- `/home/user/org-admin-api/app/modules/auth/email_throttle.py`
- `/home/user/org-admin-api/app/modules/tokens/service.py` (lines 68-79)
- `/home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md` (line 123)
- `/home/user/org-admin-api/docs/consumers.md` (lines 91-92 and 138-174)
- `/home/user/org-admin-api/work/tickets/ADM-002d/consult-security-architect.md`
- `/home/user/org-admin-api/work/tickets/ADM-002d/plan.md`
