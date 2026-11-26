## Attempt 1 — 2026-11-26

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261126_e7b2c4f9a316_usr38_apps_super_admin_role.py` (new) | column, same-app FK, 3 CHECKs; `downgrade()` reverses (R2) |
| `app/modules/rbac/models.py` | `App.super_admin_role_id`, FK + 3 CHECKs matching the migration |
| `app/modules/rbac/authz.py` | `is_designated_super_admin_role`, `AuthzService.app_super_admin_access` (shared contract for USR-39 / USR-40) |
| `app/modules/rbac/repository.py` | G5 predicate includes the designated role; `is_active_staff_super_admin`, `app_super_admin_access` |
| `app/modules/rbac/service.py` | designation rules in `AppService._update` / `_check_super_admin_role`; `role_is_privileged`, `_guard_privileged`, `_grant_locked` grant rules; role delete, role PUT and permission scope-flip guards; `is_super_admin` in definitions |
| `app/modules/rbac/schemas.py` | `AppUpdate` / `AppFields.super_admin_role_id`, `RoleDefinitionRead.is_super_admin` (additive) |
| `app/modules/rbac/manifest.py` | designated role is not manifest-managed |
| `app/modules/rbac/router.py` | PATCH `/apps/{key}` OpenAPI text (409/422, super_when) |
| `app/modules/invitations/service.py` | refuse the designated role at invite create and accept re-check (plan Q5) |
| `app/modules/audit/diff.py` | `super_admin_role_id` in the `app.updated` diff (R4) |
| `docs/adr/0030-app-super-admin-role.md` (new), `docs/identity-architecture.md` (D42), `docs/adr/0008`, `0013`, `0015`, `0029`, `docs/spec-v1.md` | ADR-0030, D42, amendment lines, OQ80-83 / OQ90 ticked, spec §5.16 deviation recorded |

### Tests added / changed
- `tests/test_rbac_super_admin_role.py` (new, 66 tests): designation (happy, clear, with `org_mode`, 401/403/404, admin app, org_mode none, other app, unknown, system, default, org owner, same-as-super-admin both ways, org permission, wildcard, assignments x3, audit, no-op, change), grant rules (staff, expiry, non-staff x5, renewal, `org_id`, self-grant, no wildcard), revoke (3), system actor, invitations create / accept, delete, `org_mode`, role PUT (4) and scope flip, predicate agreement, `is_super_admin` flag, G5 user disable + staff session service, `app_super_admin_access` true / false (9) / admin app, pure predicate unit.
- `tests/test_app_manifest_api.py`: 4 manifest tests.
- `tests/test_rbac_schema.py`: 4 constraint tests (distinct check parametrized over default / owner).
- `tests/test_super_admin_role_migration.py` (new): upgrade / downgrade / upgrade.
- Updated for the additive fields: `tests/test_openapi_contract.py`, `tests/test_rbac_role_definitions_api.py`, `tests/test_rbac_apps_api.py`.

### Checks (actual output summary)
- lint: `ruff check .` reports exactly the 13 known errors, all in kit-managed `.github/scripts/risk_tier.py` (not fixed). `ruff check app tests migrations`: All checks passed.
- format:check: `ruff format --check tests app migrations`: 441 files already formatted (only `risk_tier.py` would be reformatted, known).
- typecheck: `mypy app`: Success, no issues found in 216 source files.
- test: `pytest -q` full run: 1 failed, 4319 passed (`test_app_read_has_exact_keys`, expected key set missing the new field); fixed; re-run of `test_rbac_apps_api.py`, `test_openapi_contract.py`, `test_rbac_role_definitions_api.py`: 179 passed. Net: all tests green (4320 passed after the fix).
- alembic (local dev DB only): `upgrade head`, `downgrade -1`, `upgrade head` OK; `alembic check`: No new upgrade operations detected.

### Out-of-plan files
- `tests/test_rbac_apps_api.py` (APP_READ_KEYS) and `tests/test_rbac_role_definitions_api.py` (KEYS): exact-key assertions needed the additive fields. `tests/test_openapi_contract.py` was in the plan's conditional list.

### Deviations from the plan
- 409 for the designated-role delete and `org_mode` use `ConflictError` with `details[{field, reason}]` (as planned); `role_has_assignments` at designation is a plain `ConflictError` with details instead of `RoleHasAssignmentsError` (its message is about permissions).
- `test_revoke_holder_sessions_needs_wildcard_403` calls `StaffSessionService.list` directly: the session routes already require `admin:*` at the route, so an HTTP test cannot reach the G5 branch.
- `test_privilege_predicates_agree` calls `_guard_privileged` directly (a denied HTTP request rolls the shared test transaction back).
- Role PUT requires `admin:roles:manage` (not `roles:write`) in tests; the plan text named `admin:roles:write`.
- `test_invitation_accept_after_designation_token_invalid` asserts a 4xx and that the invitation stays pending (not the exact code).
- USR-36 bulk route is not merged: no bulk code; the ADR-0029 amendment line (422 `super_admin_role`) is in the diff and `test_batch_designated_role_422` is not written.

### Follow-ups (noticed, not done)
- USR-36 must implement and test the request-level `super_admin_role` 422 (ADR-0029 amended).
- `_grant_locked` / `_guard_privileged` hotspots shared with USR-25, USR-36, USR-37, USR-39, USR-40: expect rebases.
- 13 known ruff errors in `.github/scripts/risk_tier.py` (kit-managed).

### Scope self-check
- Could the diff be smaller? No: every file maps to the plan.
- Diff size: see `git show --stat` of the commit.
