Status: READY

# Plan — USR-12: Allow resend-verification for the admin app

**Decision: ALLOW, for staff only.** `POST /api/v1/auth/verify-email/resend` accepts `X-MM-App: admin` from a registered admin origin, but only when the signed-in user already has an effective admin-app role. That is the same predicate `/auth/token` uses for `aud=admin`. All three consults recommend allowing it. Security-architect condition C3 (staff-only) is adopted; see Assumption A1. Signup and password forgot keep refusing `admin`.

## Spec summary
- Spec §5.8, "Email flows" (`docs/spec-v1.md:68`): "email verification and password reset by one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3)."
- ADR-0008, ADM-002d amendment (`docs/adr/0008-admin-api-authentication.md:123-125`): "**No admin context on mail-link routes.** Signup, password forgot and verify-email resend refuse `X-MM-App: admin` with the same 403 `origin_not_allowed` as an unregistered app, before Turnstile, any write or any email."
- CLAUDE.md R3 class (2) **cookie**: "identity endpoints under `/api/v1/auth/*` that act for a signed-in account (… resend verification): `mm_device` cookie + `Origin` check + `X-MM-App`".
- CLAUDE.md R11: "**Verified email** is required to join an app or get any app token."

### Threat-model reason ADM-002d gave, and why it does not hold for resend
The ticket requires this to be stated. It must also go into the ADR amendment.
- **The ADR records no reason.** The bullet states only the rule. The reasoning is reconstructed from the ADM-002d work files:
  - `work/tickets/ADM-002d/consult-identity-access-engineer.md`, recommendation 5, proposed refusing **signup and forgot only**.
  - `work/tickets/ADM-002d/consult-security-architect.md` #5 added resend by analogy: "all three mail a link to the admin origin".
  - `work/tickets/ADM-002d/plan.md` adopted that wording.
  - The amendment must say that this reason is inferred.
- **The real threat applies to the unauthenticated routes.** Through signup and forgot, anyone can name any email address:
  - forgot would turn the admin origin into a place that collects password-reset tokens for every mmlabs user;
  - signup would allow open account creation and admin-branded mail to any address;
  - both need Turnstile and a `/reset-password` page on the most privileged origin.
- **Resend is different:**
  - It is cookie class. It needs `__Host-mm_device` holding an active account, so the caller has already proved the password.
  - It mails only that account's own stored address.
  - The token marks the email as verified. It is not a credential and grants no access, because admin access still needs an `admin:*` role.
  - It runs no Turnstile path and creates no account.
  - Its limits are keyed by email HMAC across all apps, plus a per-IP limit and the global cap, so admin context adds no sending capacity.
  - It exposes no new oracle.
  - **Therefore the reason does not hold for resend.**
- **One residual risk remains: F6 in consult-security-architect.**
  - An attacker could pre-register an account with a future staff member's email, log in at admin-portal, and get a genuine admin-portal-branded verification mail sent to the victim.
  - If the victim clicks it, the victim verifies the attacker's account.
  - C3 closes the admin-branding part of this path: an attacker's account holds no admin role, so admin resend refuses it.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | The decision and rationale are recorded as a dated amendment in ADR-0008 and in the decision log | `docs/adr/0008-admin-api-authentication.md` has a new section "Amendment — Admin verification resend (USR-12, 2026-11-16)". It contains: the inferred ADM-002d reason, why it does not hold for resend, the C3 staff-only rule, the residual risk, and the rejected alternative. The ADM-002d bullet (line 123) is narrowed to signup and forgot and points to it. `docs/identity-architecture.md` has a row **D24** in the decision log and an updated endpoint-table row (line 143). |
| 2 | Admin resend from `https://admin-portal.mmlabs.online` sends the link to that validated Origin, still requires the device cookie, and keeps the 1/min and 5/day limits unchanged | Tests `test_admin_resend_staff_202_link_to_admin_origin`, `test_admin_resend_requires_cookie`, `test_admin_resend_foreign_account_401`, `test_admin_resend_429_within_a_minute`, `test_admin_resend_429_after_five_a_day`, `test_admin_resend_shares_limit_with_other_apps`, `test_admin_resend_per_ip_429`. `git diff` shows no change to `email_throttle.py`, `config.py`, rate-limit keys or `prepare()`. |
| 3 | Signup and forgot with `X-MM-App: admin` still return 403 `origin_not_allowed` before Turnstile, any write or any email | `test_admin_context_refused_on_mail_routes[signup\|forgot]`, `test_admin_context_refusal_matches_unknown_app` and `test_admin_context_refused_even_if_link_origin_overridden` (now over signup and forgot) stay green. New `test_mail_link_routes_dependency_pin`. |
| 4 | Tests cover: admin resend success; an unregistered origin with `X-MM-App: admin` → 403; rate limits for admin; an already-verified account behaves as for other apps | See "Tests to write". Already verified: `test_admin_resend_already_verified_staff_409`. Plus the staff-only tests (`test_admin_resend_non_staff_403`, `test_admin_resend_expired_admin_role_403`, `test_admin_resend_other_app_role_only_403`). |
| 5 | `docs/consumers.md` (admin UI section) states what the admin UI can do for an unverified staff email | Line 91-92 is replaced, and an "Unverified staff email" bullet is added to the "Calling the admin API from the admin UI" section. It covers the flow, the codes, the `/verify-email` page contract, and the `/reset-password` exemption for admin-portal. |
| 6 | If refused: no code change | N/A: the decision is ALLOW. |
| — | `make check` green (R8) | Reviewer runs `make check`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/auth/deps.py` | Extract private `_validated_link_origin(request) -> str` from the body of `get_link_origin`; `get_link_origin` keeps `_app: MailLinkAppContextDep` and calls the helper. Add `get_resend_link_origin(request, _app: AppContextDep) -> str` with a docstring saying admin is accepted only here (USR-12) and that signup and forgot must stay on `MailLinkAppContextDep`. Narrow the `get_mail_link_app_context` docstring to "(signup, forgot)". | AC2/AC3: split the dependency so only resend accepts admin (consult C1 / R-A). |
| `app/modules/auth/router.py` | `resend_verification`: `_app: MailLinkAppContextDep` → `app: AppContextDep`, still declared **before** `device: DeviceDep`. `link_origin` → `Depends(get_resend_link_origin)`. Pass `app` to `service.resend(...)`. Rewrite the docstring: drop "admin gets the same 403", add "admin accepted for staff only (USR-12)". Update imports. | AC2. |
| `app/modules/auth/email_verification.py` | `resend(self, app: App, device, account_id, link_origin)`. After the account and user (`get_for_share`) checks and **before** the already-verified check: if `app.key == ADMIN_APP_KEY` and `not await AuthzService(session).effective_roles(user.id, ADMIN_APP_KEY)`, raise `OriginNotAllowedError("Origin is not allowed")`. Hold `self.authz = AuthzService(session)` in `__init__`. | C3 staff-only (A1). This is a business rule, so it lives in the service (R1). |
| `tests/test_email_verification_api.py` | Add the "admin context (USR-12)" test section (below). Fix `test_resend_app_context_checked_before_device` to override `get_resend_link_origin` instead of `get_link_origin`, and update its docstring. | AC2/AC4; the existing test would otherwise become vacuous. |
| `tests/test_auth_app_context.py` | `test_admin_context_refused_on_mail_routes` parametrize → `["signup", "forgot"]`. The override test loops over `("signup", "forgot")`. Remove the resend branch from `_prepare` and update its docstring; drop imports that become unused. Add `test_mail_link_routes_dependency_pin`. | AC3: the resend cases now return 202 and would fail. Pin against later "unifying" the dependencies. |
| `docs/adr/0008-admin-api-authentication.md` | Narrow the ADM-002d bullet (line 123): "Signup and password forgot refuse … (resend: see the USR-12 amendment)". Add a new section "Amendment — Admin verification resend (USR-12, 2026-11-16)" after the USR-11 amendment. | AC1. |
| `docs/identity-architecture.md` | Add decision-log row **D24** after D23. Endpoint table row (line 143): note "resend accepts `X-MM-App: admin` for staff only (USR-12); signup and forgot refuse admin (ADM-002d)". | AC1. |
| `docs/consumers.md` | Replace lines 91-92. Add an "Unverified staff email" bullet to the admin UI section. Line 138-139: note that admin-portal must serve `/verify-email` but is exempt from `/reset-password`. "Resend" paragraph (line 165): note that `admin` is accepted for staff only. | AC5. |
| `docs/adr/0007-email-verification-and-delivery.md` | Line 85: add "(includes admin-portal since USR-12)". | Keeps the "must serve `/verify-email`" consequence accurate. |

No other files. In particular, these do not change: `test_route_auth_classes.py` (the cookie-class allow-list is unchanged), `test_password_reset_api.py`, `email_throttle.py`, `config.py` and `.env.example`.

## Tests to write
New tests go in `tests/test_email_verification_api.py`, in a new section `# --- admin context (USR-12) ---`. The `stoktok` fixture there is autouse.
- Constant `ADMIN_PORTAL = "https://admin-portal.mmlabs.online"`.
- Helper `_admin_portal_origins(db_session)` sets the seeded `admin` app's `origins = [ADMIN_PORTAL]` and flushes.
- Helper `_staff(db_session, role=None, **kw)` returns `(user, raw, account_id)`:
  - `_signed_in(...)`, then `assign(user, role or await super_admin_role(db_session))`.
- Admin calls use `_resend(..., app_key="admin", origin=ADMIN_PORTAL)`.

| Test (file › name) | Covers |
|---|---|
| `test_email_verification_api.py › test_admin_resend_staff_202_link_to_admin_origin` | 202, empty body, `Cache-Control: no-store`. One email to `user.email`; `_link(email)[0] == ADMIN_PORTAL`. One `EmailToken` row, one send-log row (AC2, AC4 success). |
| `› test_admin_resend_non_admin_role_staff_202` | A user with only a custom admin role (`make_role(admin_app, "admin:support")`, no `admin:*`) → 202. The predicate is "any effective admin role", not super-admin only. |
| `› test_admin_resend_forwards_rotated_cookie` | Staff with a rotation-due cookie (`shift … rotated_at -25 hours`) → 202 with `Set-Cookie`, value ≠ raw. |
| `› test_admin_resend_verify_then_admin_token` | End to end: admin resend → `POST /auth/verify-email` with the token → 204 → `POST /auth/token` `{audience: "admin"}` with `X-MM-App: admin`, Origin `ADMIN_PORTAL` → 200 (ticket goal). |
| `› test_admin_resend_unregistered_origin_403` (parametrized) | `X-MM-App: admin` with: (a) admin origins empty (default, no `_admin_portal_origins`) and Origin `ADMIN_PORTAL`; (b) origins set, Origin = stoktok's `ORIGIN`; (c) Origin `https://evil.mmlabs.online`. Each gives 403 `origin_not_allowed`, no `set-cookie` (use a rotation-due cookie, and assert `rotated_at` is unchanged), no email, `_send_log_count == 0`, and a body equal to the unknown-app 403 (`app_key="nosuchapp"`). |
| `› test_admin_resend_non_staff_403` | Unverified user with no role, admin context, registered origin → 403 `origin_not_allowed` with a body identical to the unregistered-origin 403. No `EmailToken` or send-log rows, no email. With a rotation-due cookie: no `set-cookie` and `rotated_at` unchanged, because the session rolls back on error. |
| `› test_admin_resend_non_staff_verified_403` | A verified non-staff user in admin context → 403 `origin_not_allowed`, not 409. The staff check runs before the verified check, so non-staff behaviour is the same as before USR-12. |
| `› test_admin_resend_expired_admin_role_403` | `assign(..., expires_at=now - 1 day)` → 403 (`effective_roles` ignores expired roles). |
| `› test_admin_resend_other_app_role_only_403` | User holding only a stoktok role → 403 in admin context. |
| `› test_admin_resend_requires_cookie` | No cookie, admin context → 401 `session_invalid`. |
| `› test_admin_resend_foreign_account_401` | Staff cookie plus another device's `account_id` → 401 `session_invalid`, no email. |
| `› test_admin_resend_disabled_user_401` | `status="disabled"` staff → 401. |
| `› test_admin_resend_already_verified_staff_409` | Verified staff → 409 `email_already_verified`, no email, no send-log row (same as other apps; AC4). |
| `› test_admin_resend_429_within_a_minute` | Two admin resends back to back → second is 429 `rate_limited` with `1 <= Retry-After <= 60`. One email sent. |
| `› test_admin_resend_429_after_five_a_day` | Five send-log rows inserted as in `test_resend_429_after_five_a_day` → 429, `Retry-After == 19*3600`, no email. |
| `› test_admin_resend_shares_limit_with_other_apps` | Signup from stoktok (`_signup`) creates the user; assign a super-admin role and `make_device`; immediate admin resend → 429. Admin context gets no separate bucket. |
| `› test_admin_resend_per_ip_429` | `make_client(verify_email_resend_rate_limit=1)`, two staff users from one IP → second is 429 `rate_limited`. |
| `› test_resend_app_context_checked_before_device` (modified) | Override `get_resend_link_origin` instead of `get_link_origin`. Same assertions. |
| `test_auth_app_context.py › test_admin_context_refused_on_mail_routes[signup\|forgot]` (modified) | AC3 regression. |
| `› test_admin_context_refused_even_if_link_origin_overridden` (modified: signup, forgot) | AC3: the route-level `MailLinkAppContextDep` still refuses admin. |
| `› test_mail_link_routes_dependency_pin` (new) | Walk `route.dependant` recursively (`.dependencies[*].call`). `POST /api/v1/auth/signup` and `/password/forgot` include `get_mail_link_app_context`; `/verify-email/resend` does not, and includes `get_app_context` and `get_resend_link_origin`. |

## Approach
1. **deps.py.**
   - Extract `_validated_link_origin`.
   - Add `get_resend_link_origin` on plain `AppContextDep`.
   - Fix the docstrings.
   - No boolean flag. Named dependencies keep new routes fail-closed: they must choose `MailLinkAppContextDep` deliberately.
2. **email_verification.py.** Add an `app` parameter and the C3 staff check to `resend`. Order inside `resend`:
   1. account (401)
   2. user `get_for_share`, active (401)
   3. **admin-context staff check (403)**
   4. already verified (409)
   5. `prepare()`: per-email lock and limits, global cap, token
   - Reuse `AuthzService.effective_roles(user.id, ADMIN_APP_KEY)`, the predicate `TokenService.issue` uses (`app/modules/tokens/service.py:73`), so there is no second query.
   - The error is `OriginNotAllowedError("Origin is not allowed")`, byte-identical to the app-context 403.
3. **router.py.** Rewire `resend_verification`. The route-level `app: AppContextDep` stays before `DeviceDep` (USR-04d). As a result:
   - An unregistered origin gives 403 before any device lookup or rotation.
   - The non-staff 403 is raised after device resolution, but `get_session` rolls back on error and the router only sets the cookie on success. So it carries no `Set-Cookie` and the rotation is not persisted. This is tested, and it corrects consult risk R-d.
4. **Tests.** Adjust the existing tests first (they would go red), then add the new ones.
5. **Docs.**
   - ADR-0008 amendment content:
     - the date;
     - that it supersedes the resend part of the ADM-002d bullet;
     - the inferred ADM-002d reason (labelled as inferred);
     - why the reason does not hold for resend (cookie class, own address, no Turnstile or account creation, shared limits);
     - the C3 rule and the 403 behaviour;
     - residual risks: links to admin-portal now exist (R-a), and pre-hijack through other apps' origins remains (R-b; onboarding follow-up);
     - the rejected alternative: open to any signed-in account;
     - the dependency on admin-portal serving `/verify-email`.
   - D24 row in `docs/identity-architecture.md` summarising the same.
   - consumers.md and ADR-0007 updates as listed in "Files to create / modify".
6. Run `make check`.

Rules:
- **R1:** the business rule is in the service, and the router only wires.
- **R2:** no schema change.
- **R3:** resend stays cookie class; the auth-class allow-list is unchanged.
- **R4:** resend is not an admin mutation and is not audited (ADR-0009). Unchanged.
- **R5:** no config.
- **R6:** existing error codes only.
- **R7:** the admin-origin constant mismatch and the onboarding runbook go to follow-ups.
- **R8:** tests listed above.
- **R9:** additive relaxation, not breaking; the OpenAPI change is the description only.
- **R11:** the Origin and `X-MM-App` checks are unchanged. Verified email is still required for tokens. Logs carry `user_id` only.

### Merge-coordination note (not a blocker)
The emailed link is `https://admin-portal.mmlabs.online/verify-email#token=…`. **admin-pan must serve `/verify-email` (PAN-010b) before admin resend is useful.** Otherwise the link lands on a 404.

The page contract goes in consumers.md:
- a public route outside the auth guard;
- read `location.hash`, then `history.replaceState(null, "", "/verify-email")` at once;
- `Referrer-Policy: no-referrer`, no third-party scripts;
- `POST /api/v1/auth/verify-email` with `credentials: "omit"` and no `X-MM-App`;
- on 204, re-call `/auth/token`; otherwise send the user to login.

The owner should merge this PR together with, or after, admin-pan's `/verify-email`. The API change is safe on its own: without the page, staff simply get a dead link.

## Specialist input used
- **api-platform-engineer:**
  - Adopted: dependency split with no boolean flag; changes to the existing tests (parametrize and the override in `test_resend_app_context_checked_before_device`); OpenAPI impact is additive (description only); consumers.md text; D24 numbering (D23 is the last entry, confirmed at `docs/identity-architecture.md:279`).
  - Adjusted: the amendment states explicitly that the ADM-002d reason is inferred (its Q3, answered yes).
- **identity-access-engineer:**
  - Adopted: R-A code shape; R-B test list, including the cross-app shared limit and the end-to-end token test; R-C ADR wording plus the ADR-0007 note; R-D and R-E consumers.md and `/verify-email` page contract; the dependency pin test.
  - Rejected: "non-staff use is harmless, allow all" (see A1).
  - Adopted its Q1 suggestion: the admin-origin constant mismatch goes to follow-ups.
  - Adopted its Q3 suggestion: no audit change.
- **security-architect:**
  - Adopted: C1, C2, C3 (staff-only, reusing the `/token` predicate, 403 `origin_not_allowed`), C4, C5 (including the `/reset-password` exemption note), C6, C7, C8.
  - Corrected risk R-d: the C3 403 carries no rotated cookie, because the session rolls back on error. Pinned by a test.
  - Its Q2 (which predicate) is answered: `AuthzService.effective_roles(user_id, "admin")`, any unexpired app-scope admin role.
  - Its Q3: kept `origin_not_allowed` (see A2).
  - Its R-b onboarding mitigation goes to follow-ups.

## Assumptions
- **A1. C3 staff-only (planner default, per the orchestrator's instruction to take the more conservative option).**
  - In admin context, resend requires at least one effective (unexpired, app-scope) `admin` role. This is the predicate `/token` uses for `aud=admin`. Otherwise it answers 403 `origin_not_allowed`, so non-staff see exactly what they saw before USR-12.
  - The predicate is reused, not reimplemented. Feasibility: `AuthzService.effective_roles` is a single read and is already used by `TokenService`.
  - *Rejected alternative:* allow any signed-in account (identity-access-engineer). It is simpler and reaches the same verification outcome for staff, but it re-opens the admin-branded pre-hijack phishing path (security-architect F6) for no staff benefit.
  - If the owner prefers the open variant: remove the check in `resend` and the three non-staff tests, and record F6 as an accepted risk in the amendment.
- **A2.** Non-staff in admin context get `origin_not_allowed` (the same as today and the same as an unregistered origin), not `not_member`. This avoids a contract change for the admin UI and keeps one uniform 403. The UI already treats non-staff via `/token` → `not_member` after verification.
- **A3.** The staff check runs before the already-verified check. A verified non-staff user therefore gets 403, not 409. AC4's "already verified behaves as for other apps" applies to staff (409).
- **A4.** New tests use `https://admin-portal.mmlabs.online`, as the ticket and orchestrator require. The existing `ADMIN_ORIGIN = "https://admin.mmlabs.online"` in `test_auth_app_context.py` and the ADM-002d runbook text in `docs/deploy.md` are left unchanged (R7) and listed as a follow-up.
- **A5.** No new rate-limit key, no admin bucket and no audit row. The limits are the existing per-email HMAC (shared across apps), per-IP `verify_email_resend` and the global cap.
- **A6.** `make_login_user(email=…)`, `assign(..., expires_at=…)`, `super_admin_role`, `make_role` and `auth_headers(app_key=, origin=, cookie=)` exist in `tests/auth_factories.py` and `tests/rbac_factories.py` and are reused. No new factory files are needed.
- **A7.** The decision is recorded as "decided by org-admin-api" (ticket note: "Final decision is org-admin-api's"). D24 is marked as decided (USR-12), with C3 as a planner default the owner may revert at PR review.

Follow-ups (not in this diff):
- (a) Align the admin origin constant and the runbook to `admin-portal.mmlabs.online`.
- (b) Onboarding runbook (security R-b): confirm with the person, or require a password reset, before granting an admin role to an existing account found by email.
- (c) Tell admin-pan that PAN-010b can proceed once this merges.

## Blocking questions
None.

## Split proposal
None. About 40 lines of app code, about 250 lines of tests and about 60 lines of docs, all for one feature.

## Data model / migration
None.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/verify-email/resend` | Cookie `__Host-mm_device`, headers `Origin` + `X-MM-App` (now also `admin`), body `{account_id: uuid}` | 202 empty, `Cache-Control: no-store`, optional rotated `Set-Cookie` | 202; 401 `session_invalid`; 403 `app_header_required` / `origin_not_allowed` (unregistered origin, **or `admin` context for a user without an admin role**); 409 `email_already_verified`; 422 `validation_error`; 429 `rate_limited` + `Retry-After` | None (cookie class, R3 class 2). For `X-MM-App: admin`: any effective admin-app role. |

Unchanged:
- `POST /api/v1/auth/signup` and `POST /api/v1/auth/password/forgot` still answer 403 `origin_not_allowed` to `X-MM-App: admin`.
- Paths, bodies, status codes and schemas are unchanged. Only the resend operation's description changes, which is additive under R9.
