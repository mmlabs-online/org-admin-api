# Security review — USR-40 (Attempt 1, appsec-engineer, 2026-11-26)

Saved by the orchestrator (the appsec agent is read-only at REVIEW).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit 25066d1 (diff `875130f..25066d1`). Tests: `pytest -q tests/test_auth_orgs_super_admin.py tests/test_auth_orgs.py` → 55 passed (local `org_admin_test`). Full suite left to the reviewer (R8).

## Findings (all 🟢)
- **Branch chosen server-side only:** `organizations/auth_service.py:179-182` sets `holder` from `AuthzService.app_super_admin_access(user.id, app)`; the router (`organizations/auth_router.py:74-90`) adds no query parameter. For a non-holder `include_app_orgs=False` keeps the old `Organization.id.in_(held)` WHERE for count and page (`organizations/repository.py:131-145`); only an extra `is_member` column; total and errors unchanged (`test_non_holder_sees_no_app_orgs`, `test_non_staff_with_designated_role_sees_member_list`).
- **Holder = the account behind `account_id`:** `_user()` (`auth_service.py:67-78`) resolves via `get_active_account(device.device_id, account_id, …)` and requires an active, verified user; holder check and listing use that `user.id`.
- **Live re-check every call:** `rbac/authz.py:77-90` and `rbac/repository.py:872-883` (unexpired app-scope designated role + live `admin:super-admin`, DB clock); no cache; `test_holder_loses_access_next_call`.
- **Picker and mint agree:** `super_admin` items from `org_of_app_clause` (`rbac/repository.py:44-74`); the mint uses the same clause via `is_org_of_app` (`tokens/service.py:157-159`) behind the same holder predicate. `test_every_super_admin_item_is_mintable`, `test_excluded_orgs_are_not_mintable`. Pre-existing mismatch unchanged and documented (ADR-0030 §8): suspended member orgs listed as `member` but not mintable.
- **No other members' roles leak:** role lookup filtered by `RoleAssignment.user_id == user_id` (`repository.py:148-160`); `super_admin` items `roles: []`.
- **Logs:** one info line (`auth_service.py:184-186`) with `user_id`, app key, `total` — no email, token or org names (R5/R11).
- **Audit:** read-only endpoint (R4 n/a); ADR-0030 §8 not audited; `test_holder_list_not_audited_and_logged`.
- **Schema additive:** `access: Literal["member","super_admin"]`, always present, clients tolerate unknown values (R9).

## Follow-ups (out of scope)
- 💭 Regression test: a device with a holder and a non-holder account — listing with the non-holder's `account_id` returns member orgs only (code already safe).
- 💭 Holder check and listing are two plain reads; a revocation between them may serve one stale list — negligible, the mint re-checks.
