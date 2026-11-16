## Attempt 1 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 1: ruff "All checks passed!", format "230 files already formatted", mypy "Success: no issues found in 104 source files", pytest "1 failed, 1775 passed". The failure was `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` (`[2, 3] == [1, 2, 2]`). That test and the purge code are not in this diff (last touched by ADM-004c/d). The test passed 5 times out of 5 on its own (`25 passed` each time). It is a pre-existing flake; see Follow-ups.
- `make check`, run 2 (full): ruff, format and mypy clean, pytest "1776 passed, 1 warning in 129.28s". This matches the impl-log ("1776 passed").
- alembic check: not applicable. The diff has no model or migration change.

### Acceptance criteria
- [x] The decision and rationale are recorded as a dated amendment in ADR-0008 and in the decision log. Evidence:
  - `docs/adr/0008-admin-api-authentication.md`: new section "Amendment — Admin verification resend (USR-12, 2026-11-16)". It gives the inferred ADM-002d reason (labelled as inferred), why that reason does not hold for resend, the staff-only rule, residual risks and the rejected alternative.
  - The ADM-002d bullet is narrowed to signup and forgot and points to the amendment.
  - `docs/identity-architecture.md`: D24 row, and the endpoint-table row is updated.
- [x] Admin resend from `https://admin-portal.mmlabs.online` sends the link to that validated Origin, still requires the device cookie, and keeps the limits unchanged. Evidence:
  - `test_admin_resend_staff_202_link_to_admin_origin`: link origin == ADMIN_PORTAL, one token and one send-log row.
  - `test_admin_resend_requires_cookie`: 401 without the cookie.
  - `test_admin_resend_foreign_account_401` and `test_admin_resend_disabled_user_401`.
  - `test_admin_resend_429_within_a_minute`, `test_admin_resend_429_after_five_a_day` and `test_admin_resend_per_ip_429`.
  - `test_admin_resend_shares_limit_with_other_apps`: there is no separate admin bucket.
  - `git diff --stat` shows no change to `email_throttle.py`, `config.py` or `prepare()`.
  - `get_resend_link_origin` (`app/modules/auth/deps.py`) only normalizes the Origin that `AppContextDep` already validated.
- [x] Signup and forgot with `X-MM-App: admin` still return 403 `origin_not_allowed` before Turnstile, any write or any email. Evidence: these tests pass (now over signup and forgot):
  - `test_admin_context_refused_on_mail_routes[signup|forgot]`
  - `test_admin_context_refusal_matches_unknown_app`
  - `test_admin_context_refused_even_if_link_origin_overridden`

  New: `test_mail_link_routes_dependency_pin` pins `get_mail_link_app_context` on signup and forgot, and `get_app_context` plus `get_resend_link_origin` on resend.
- [x] Tests cover admin resend success, an unregistered origin with admin → 403, the rate limits for admin, and an already-verified account. Evidence:
  - Success: `test_admin_resend_staff_202_link_to_admin_origin`.
  - Unregistered origin: `test_admin_resend_unregistered_origin_403[no_origins|other_app_origin|evil_origin]`. The body is identical to the unknown-app 403, with no Set-Cookie, `rotated_at` unchanged, no email and no send-log row.
  - Rate limits: the three 429 tests plus the shared-bucket test.
  - Already verified: `test_admin_resend_already_verified_staff_409` gives 409 `email_already_verified`, the same as other apps.
  - Staff-only rule: `test_admin_resend_non_staff_403`, `test_admin_resend_non_staff_verified_403`, `test_admin_resend_expired_admin_role_403`, `test_admin_resend_other_app_role_only_403` and `test_admin_resend_non_admin_role_staff_202`.
  - End to end: `test_admin_resend_verify_then_admin_token` (email_unverified, then resend, verify 204, then admin token 200).
- [x] `docs/consumers.md` (admin UI section) says what the admin UI can do for an unverified staff email. Evidence: the "Unverified staff email (USR-12)" bullet covers the flow, the 202/429/409/403 handling and the `/verify-email` page contract. The exemption line and the Resend paragraph are updated.
- [x] "If refused" criterion: N/A, the decision is ALLOW.

### Issues
None.

Things I verified beyond the tests:
- The staff-only 403 is raised after `DeviceDep` may have rotated the cookie. In production `get_session` (`app/db/session.py:24-35`) rolls back on any exception, and the router only sets the cookie on success. So a non-staff 403 neither persists the rotation nor leaks a Set-Cookie. The implementer's deviation (no `rotated_at` assertion for the non-staff case, because the test shares one session) is justified. The Set-Cookie absence is asserted.
- The predicate `AuthzService.effective_roles(user.id, "admin")` is the same one `TokenService.issue` uses (`app/modules/tokens/service.py:73`). It ignores expired roles and filters keys by the app prefix.
- The order inside the service is: account (401), user active (401), staff check (403), verified (409), throttle. A non-staff user therefore sees exactly the pre-USR-12 behaviour.
- `AppContextDep` stays before `DeviceDep` in the route signature, so a bad origin or app answers 403 before any device lookup. `test_resend_app_context_checked_before_device` is updated to override `get_resend_link_origin`, so it is not vacuous.
- Credentialed CORS for `/api/v1/auth/*` already echoes the admin app's origins (`app/main.py:58-63`; `/auth/token` from admin-portal relies on it). No CORS change is needed.

### Rules R1–R11
No violations found.
- R1: the staff check is in the service, and the router only rewires dependencies.
- R2: no schema change.
- R3: resend stays cookie class, and `test_route_auth_classes` is unchanged and green.
- R6: only existing error codes are used.
- R7: every changed file is in the plan's file list. The `work/backlog.md` and `ticket.md` changes are orchestrator state changes.
- R8: `make check` is green.
- R9: the change is an additive relaxation, and only the OpenAPI description changes.
- R11: the Origin and `X-MM-App` checks are unchanged, verified email is still required for tokens, and logs carry `user_id` only.

### Nits (do not block)
- `docs/consumers.md` "Unverified staff email" bullet says `X-MM-App: admin` is "(the only admin-context call that uses the cookie)". That is wrong: `/auth/token` with `X-MM-App: admin` also uses the cookie (`credentials: "include"`, stated a few lines above in the same section). Suggested wording: "(like `/auth/token`, a cookie call; never send the cookie to Bearer endpoints)".
- ADR-0008 amendment, "admin access still needs an `admin:*` role". `admin:*` is the super-admin wildcard permission. The rule (and the code) is "any effective `admin` app role", so "an `admin` app role" would be more precise.
- ADR-0008 ADM-002d bullet: the rewrapped lines are uneven (one line is over 100 characters). This is cosmetic only.

### Follow-ups (out of scope)
- `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` failed once in a full run (`[2, 3]` vs `[1, 2, 2]` per-batch deleted counts) and passed in isolation and in the next full run. It is a flaky pre-existing test (ADM-004c/d). Open a bug to make the batch-count assertion deterministic.
- From the plan:
  - Align the admin origin constant (`admin.mmlabs.online` in `tests/test_auth_app_context.py`) and the deploy runbook with `admin-portal.mmlabs.online`.
  - Onboarding runbook (security R-b): confirm with the person before granting an admin role to an existing account.
  - Tell admin-pan that PAN-010b can proceed. The admin-portal `/verify-email` page must ship with or before this PR, or the link is dead.
