## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| `app/modules/entitlements/__init__.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py` | new domain module (AC1-AC3); repository holds the single `ACTIVE` expression |
| `app/api/router.py` | mount the entitlements router |
| `app/db/models.py` | register `OrgEntitlement` (R2) |
| `app/modules/audit/actions.py`, `app/modules/audit/diff.py` | `ENTITLEMENT` target, 3 actions, allowlisted diff (R4) |
| `app/core/errors.py` | `OrgHasEntitlementsError` |
| `app/modules/organizations/service.py`, `repository.py` | org delete 409 guard (`has_entitlements`) |
| `app/modules/rbac/service.py`, `repository.py` | app delete 409 guard (`app_has_entitlements`) |
| `migrations/versions/20261117_c8a1f4e72d95_adm006_org_entitlements.py` | table, index, 2 permission seeds, downgrade; `down_revision = a9d4c27e6b13` (the single head) |
| `tests/test_entitlements_api.py`, `tests/test_entitlements_migration.py` | new tests (R8) |
| `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py`, `tests/test_rbac_schema.py` | BEARER map + `entitlement_id` fill; 3 AUDITED entries (total 23); seeded permission set |
| `docs/spec-v1.md`, `docs/identity-architecture.md`, `docs/deploy.md` | dated note, API/data-model rows, expected admin-permission list |

### Tests added / changed
- `tests/test_entitlements_api.py`: all plan tests (the three 409 refusals `admin_app`/`app_disabled`/`org_mode_none` are one parametrized `test_ungrantable_app_409`).
- `tests/test_entitlements_migration.py › test_downgrade_drops_table_and_permissions`, `› test_constraints` (the 64-char plan is covered by the API 422 test: it fails the varchar(63) before the CHECK).
- Maps updated in the three existing test files.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", "299 files already formatted", mypy "Success: no issues found in 139 source files", pytest "2242 passed, 1 warning in 201.56s". The known flake did not occur. (An earlier run had one failure, `test_revoke_204_then_gone`, caused by equal `occurred_at` ordering in my test; fixed by selecting rows by action.)
- alembic: `alembic heads` = `c8a1f4e72d95` (single head); `alembic check` "No new upgrade operations detected."; `downgrade -1` then `upgrade head` both ran cleanly, `alembic check` clean again.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- `_select()` in the repository returns `Any`: the installed SQLAlchemy stubs reject every `Select[...]` annotation for the 3-column joined select.
- `EntitlementRepository.exists_for_org` from the plan was not written; `OrganizationRepository.has_entitlements` does the job (unused code otherwise).

### Scope self-check
- Could the diff be smaller? No (the plan's own minimum; `exists_for_org` already dropped).
- Diff size: not measured (no git repository in this working directory).
