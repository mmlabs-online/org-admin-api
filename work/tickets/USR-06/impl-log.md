## Attempt 1 — 2026-11-17 (slice USR-06a)

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261117_b4d82f6a1c93_usr06a_org_foundation.py` | AC1-AC3 schema: `organizations.created_by`, FK `role_assignments.org_id` RESTRICT with orphan preflight, `apps.org_owner_role_id` + same-app FK + CHECK; reversible (R2) |
| `app/modules/organizations/models.py` | `created_by` + partial index (AC1) |
| `app/modules/organizations/schemas.py` | `OrganizationRead.created_by` (optional), `AuthOrgCreate/Read`, `MyOrgRead` |
| `app/modules/organizations/repository.py` | `has_role_assignments`, `count_created_by`, `list_for_user_app` (org-exact, unexpired) |
| `app/modules/organizations/service.py` | admin delete -> 409 `org_has_assignments` (pre-check + IntegrityError) |
| `app/modules/organizations/auth_service.py` (new) | `OrgSelfService.create/list_mine` |
| `app/modules/organizations/auth_router.py` (new) | cookie routes `POST/GET /api/v1/auth/orgs` (R3) |
| `app/modules/organizations/errors.py` (new) | `org_create_forbidden`, `org_create_unavailable`, `org_limit_reached` |
| `app/core/errors.py` | `OrgHasAssignmentsError` |
| `app/api/router.py` | mount the new router |
| `app/modules/rbac/models.py` | `role_assignments.org_id` FK; `apps.org_owner_role_id`, FK, CHECK |
| `app/modules/rbac/schemas.py` | `AppUpdate`/`AppRead.org_owner_role_id` |
| `app/modules/rbac/service.py` | `org_owner_role_id` validation in `AppService._update`; role delete, `is_admin` guards (mirror default role) |
| `app/modules/rbac/repository.py`, `authz.py` | org-exact `org_role_keys` |
| `app/modules/rbac/actor.py` | `SYSTEM_ORG_CREATE` |
| `app/modules/audit/diff.py` | `created_by` in ORG, `org_owner_role_id` in APP snapshots |
| `app/modules/tokens/service.py`, `errors.py` | org branch, `org` claim, `OrgAccessDeniedError` |
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | `org_create`, `orgs_list` scopes, `ORG_CREATE_MAX_PER_USER` |
| `docs/identity-architecture.md`, `docs/spec-v1.md`, `docs/consumers.md`, `docs/adr/0013-org-scoped-identity.md` | docs follow code, new ADR (plan) |
| tests (see below) | R8 |

### Tests added / changed
- `tests/test_auth_orgs.py` (new, 30 tests): create (owner grant in org, forbidden, org-scoped permission does not count, not member, owner role unset / with admin perm, slug 409, validation 422, org-less app 422, unverified, bad account 401, per-user cap, 429, audit rows, refusal writes nothing) and list (calling app only, other-app roles, expired, suspended with status, empty page, pagination, 422/403/401/429).
- `tests/test_orgs_schema.py` (new): FK RESTRICT, unknown org refused, NULL = app scope, `created_by` SET NULL, owner CHECK, same-app FK.
- `tests/test_orgs_migration.py` (new): data survives upgrade, downgrade reverses, orphan `org_id` aborts without touching rows.
- `tests/test_auth_token.py`: old "any org_id -> 422" test replaced by the AC6 matrix (union roles + `org` claim, no org, app member without org role 403, unknown = non-member answer, suspended, expired, other app's role, org-less app 422, admin audience 422).
- `tests/test_organizations.py`: `created_by` optional, delete with assignments (also expired only) 409 and no audit, delete without 204.
- `tests/test_rbac_apps_api.py`: `org_owner_role_id` happy/unknown/foreign/admin-perm/org_mode none/admin app/org_mode switch/audited; read keys include the new field.
- `tests/test_rbac_roles_api.py`: delete org owner role 409, add admin permission 409, flip `is_admin` 409.
- `tests/test_route_auth_classes.py`: two routes added to `COOKIE` (sweeps pass unchanged).
- `tests/test_audit_coverage.py`: `POST /auth/orgs` classified in `IDENTITY_NOT_AUDITED` with a comment: it writes two rows, which the one-row harness cannot express; `test_auth_orgs.py` covers its audit.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", format clean, mypy "Success: no issues found in 107 source files", pytest "1838 passed, 1 warning in 152.64s".
- `uv run alembic upgrade head && uv run alembic check` on the local DB: "No new upgrade operations detected."

### Out-of-plan files
- `tests/rbac_factories.py` (new `make_org` helper), `tests/test_rbac_authz.py`, `tests/test_rbac_schema.py`: existing tests used random `org_id`s that the new FK refuses; they now create real orgs.

### Follow-ups (noticed, not done)
- CLAUDE.md R11 says cookie `mm_device`, `Path=/api/v1/auth`; spec §5.8/D20 and the code use `__Host-mm_device`, `Path=/` (plan A9). Owner to fix CLAUDE.md.
- `test_audit_coverage` harness could support multi-row audited identity routes.
- Token tests decode the JWT without verifying the signature for the new org cases (signing is covered by the happy-path test).
- Promote `AssignmentService._grant_locked` to a public method (join and org create both call it).
- USR-06b (invitations) and member management remain as planned.

### Scope self-check
- Could the diff be smaller? No; each file is in the plan or is a test fixture fix forced by the new FK.
- Diff size: +1761 / -31 lines over 38 files (about 40% tests).

## Should-fix (after PASS review, USR-06a)

- S1: `AppService._update` (`app/modules/rbac/service.py`) refuses `org_owner_role_id == default_role_id` (422, field `org_owner_role_id`, reason `same_as_default_role`), using effective values so both PATCH directions and a combined PATCH are covered. Tests in `tests/test_rbac_apps_api.py` (three). Warning about `build_default_role` re-widening `<app>:member` added to `docs/consumers.md` and ADR-0013 Consequences.
- S2: `tests/test_audit_coverage.py`: `POST /auth/orgs` moved to new `IDENTITY_MULTI_ROW_AUDITED`, asserted disjoint from the other two sets and included in the classification sweep.
- Nit: `app/modules/rbac/repository.py` uses `OrganizationStatus.ACTIVE.value`.
- Checks: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` exit 0, "1841 passed, 1 warning".
