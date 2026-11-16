## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| app/modules/auth/deps.py | `_validated_link_origin` helper, new `get_resend_link_origin` on `AppContextDep`; docstring narrowed to signup/forgot |
| app/modules/auth/router.py | resend uses `AppContextDep` (before `DeviceDep`) and `get_resend_link_origin`, passes `app` to the service |
| app/modules/auth/email_verification.py | staff-only check (effective admin role) in `resend`, before the already-verified check |
| tests/test_email_verification_api.py | USR-12 admin resend tests; existing override test now targets `get_resend_link_origin` |
| tests/test_auth_app_context.py | mail-link refusal tests narrowed to signup/forgot; dependency pin test |
| docs/adr/0008-admin-api-authentication.md | ADM-002d bullet narrowed; USR-12 amendment |
| docs/identity-architecture.md | D24 row, endpoint table row |
| docs/consumers.md | unverified staff email bullet, resend notes |
| docs/adr/0007-email-verification-and-delivery.md | consequence note |

### Tests added / changed
- test_email_verification_api.py: all 19 `test_admin_resend_*` tests from the plan (unregistered-origin parametrized over 3 cases); `test_resend_app_context_checked_before_device` modified.
- test_auth_app_context.py: parametrize/override loops now signup+forgot; `test_mail_link_routes_dependency_pin` (uses `iter_route_contexts` + `dependant_calls`, since this FastAPI nests routers).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff clean, format clean, mypy "no issues found in 104 source files", pytest "1776 passed".
- alembic check: not applicable (no migration).

### Out-of-plan files
None.

### Deviations from the plan
- `test_admin_resend_non_staff_403` and the unregistered-origin test: the plan asked for a `rotated_at` unchanged assertion on the non-staff 403. The test client shares one session with the test, so the rollback-on-error cannot be observed there (value changed in-session). I kept the `set-cookie` absence assertion and dropped the `rotated_at` one for the non-staff case only; the unregistered-origin case still asserts it.
- Verified-staff test marks the user verified after `_staff` (the `_signed_in` helper fixes `verified=False`).

### Follow-ups (noticed, not done)
- Align admin origin constant (`admin.mmlabs.online`) and deploy runbook with `admin-portal.mmlabs.online`.
- Onboarding runbook: confirm with the person before granting an admin role to an existing account.
- Tell admin-pan PAN-010b can proceed once this merges (needs `/verify-email` page).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +440 / -35 lines (mostly tests and docs).
