
# Attempt 1

## Files touched
- app/modules/rbac/repository.py: added `app_has_org_assignments` (AC1 blocker; `app_has_assignments` unchanged).
- app/modules/rbac/service.py: `AppService._update` guard after the 422 checks, before the origins block; 409 `org_mode_in_use`.
- app/modules/rbac/router.py: PATCH 409 description text (OpenAPI).
- app/modules/entitlements/service.py: `grant` refuses `admin` before locking, then uses `rbac.lock_app` (AC4 race fix); docstring line.
- tests/test_rbac_apps_api.py: 9 tests from the plan plus small local helpers (`_app_updated_rows`, `_add_entitlement`, `_assert_org_mode_in_use`).
- tests/test_entitlements_api.py: `test_grant_locks_app_row`.
- docs/spec-v1.md, docs/adr/0013-org-scoped-identity.md, docs/adr/0015-org-roles-claim-and-permission-scope.md, docs/identity-architecture.md, docs/consumers.md: doc notes per plan.

## Tests added
10 (as listed in plan).

## Checks
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree: ruff, format check, mypy pass; `2420 passed, 1 warning in 245.80s`, exit 0. (One earlier run stopped at ruff E501 in router.py; fixed, then re-run in full.) The BUG-001 flake did not occur.

## Out-of-plan files
None.

## Follow-ups
As in plan.md (pending invitations as blocker, bulk clear of org data, report of pre-existing orphaned data).

## Scope self-check
Only plan files touched; no migration; no commit made.
