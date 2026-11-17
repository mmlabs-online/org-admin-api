## Attempt 1 — 2026-11-17 (slice USR-06a)
Verdict: PASS

Scope reviewed: slice USR-06a per the owner split (ticket.md Notes, 2026-11-17) and the plan's AC mapping. AC4 and the invitation parts of AC6/AC7 belong to USR-06b and were not required.

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", format "238 files already formatted", mypy "Success: no issues found in 107 source files", pytest "1838 passed, 1 warning in 143.96s". The warning is the existing `forkpty()` DeprecationWarning in `tests/test_cli_create_account.py` (not part of this diff). Exit 0. Matches impl-log.md.
- `uv run alembic heads`: single head `b4d82f6a1c93`.
- `uv run alembic upgrade head && uv run alembic check`: "No new upgrade operations detected."
- Round trip: `alembic downgrade -1` ran `b4d82f6a1c93 -> a7c3e5f19b42` (current `a7c3e5f19b42`); `alembic upgrade head` back to `b4d82f6a1c93`; `alembic check` again "No new upgrade operations detected."

### Acceptance criteria (USR-06a)
- [x] AC1: `organizations` owned by Identity, `created_by` nullable (`ON DELETE SET NULL`), `description`/`status` kept, existing data kept, reversible, admin CRUD unchanged except the additive `created_by`. Evidence: `migrations/versions/20261117_b4d82f6a1c93_usr06a_org_foundation.py:35-50,97-106`; `app/modules/organizations/models.py:41-43`; `app/modules/organizations/schemas.py:33` (optional, default None); `tests/test_orgs_migration.py::test_upgrade_keeps_existing_org_and_downgrade_reverses`, `tests/test_orgs_schema.py::test_created_by_set_null_on_user_delete`, `tests/test_organizations.py::test_admin_org_read_has_optional_created_by` plus the existing admin CRUD tests, all passing; my own downgrade/upgrade round trip.
- [x] AC2: `role_assignments.org_id` FK RESTRICT, NULL = no org, orphan preflight aborts without touching rows, admin delete with assignments returns 409 `org_has_assignments` (no audit row). Evidence: migration `:52-72`; `app/modules/rbac/models.py:197-204`; `app/modules/organizations/service.py:57-71`; `tests/test_orgs_schema.py::test_role_assignments_org_fk_restrict`, `::test_role_assignment_unknown_org_refused`, `::test_org_id_null_means_app_scope`; `tests/test_orgs_migration.py::test_upgrade_fails_on_orphan_org_id`; `tests/test_organizations.py::test_delete_org_with_assignments_409`, `::test_delete_org_with_only_expired_assignment_409`, `::test_delete_org_without_assignments_204`.
- [x] AC3 (org owner role, owner Q2): `apps.org_owner_role_id` with same-app composite FK and CHECK `org_mode = 'required'`; PATCH validation for unknown, foreign, `is_admin`-holding role, `org_mode none`, admin app; role delete and `is_admin` add/flip guards. Evidence: `app/modules/rbac/service.py:405-426,798-805,984-985,1058-1059`; `tests/test_rbac_apps_api.py::test_patch_org_owner_role_*`, `::test_patch_org_mode_none_while_owner_set_422`; `tests/test_rbac_roles_api.py` (delete 409, add admin permission 409, flip `is_admin` 409); `tests/test_orgs_schema.py::test_org_owner_requires_org_mode_check`, `::test_org_owner_role_same_app_fk`.
- [x] AC3 (`POST /api/v1/auth/orgs`): app-scope `<app>:org:create` only (`effective_permissions(user, app)` with `org_id=None` resolves to `org_id IS NULL`, `app/modules/rbac/repository.py:281-285`); creator gets the owner role in the new org through `_grant_locked` with the non-privileged `SYSTEM_ORG_CREATE`; 201. Evidence: `app/modules/organizations/auth_service.py:284-367`; `tests/test_auth_orgs.py::test_create_org_grants_owner_role_in_org`, `::test_create_org_requires_org_create_permission_403`, `::test_create_org_permission_held_only_in_an_org_does_not_count`, `::test_create_org_not_member_403`, `::test_create_org_no_owner_role_configured_403`, `::test_create_org_owner_role_with_admin_perm_403`, `::test_create_org_slug_taken_409`, `::test_create_org_validation_422`, `::test_create_org_on_org_less_app_422`, `::test_create_org_unverified_403`, `::test_create_org_bad_account_401`, `::test_create_org_per_user_cap_403`, `::test_create_org_rate_limited_429`.
- [x] AC3 (list mine per app): `GET /api/v1/auth/orgs?account_id=` returns `Page[MyOrgRead]`, org-exact and unexpired roles of the calling app only. Evidence: `app/modules/organizations/repository.py:494-543`; `tests/test_auth_orgs.py::test_list_my_orgs_only_calling_app`, `::test_list_excludes_orgs_with_only_other_app_roles`, `::test_list_excludes_expired`, `::test_list_includes_suspended_with_status`, `::test_list_app_scope_only_user_gets_empty_page`, `::test_list_paginates`, `::test_list_on_org_less_app_422`, `::test_list_requires_account_id_422`, `::test_list_unverified_403`, `::test_list_bad_account_401`, `::test_list_rate_limited_429`.
- [x] AC5: token with `org_id` requires an org-exact unexpired role of this app in an `active` org. The answer for unknown, suspended and non-member orgs is the same 403 `org_access_denied`. `org` claim = UUID string, `roles` = app-scope plus that org. Without `org_id` the token has app-scope roles only and no `org`. An `org_mode = none` app (including `admin`) with `org_id` gets 422 `org_not_supported`. Evidence: `app/modules/tokens/service.py:58-61,84-91`; `app/modules/rbac/repository.py:309-329`; `tests/test_auth_token.py::test_token_with_org_has_org_claim_and_union_roles`, `::test_token_without_org_app_scope_only`, `::test_token_app_member_without_org_role_403`, `::test_token_unknown_org_403_same_as_non_member`, `::test_token_suspended_org_403`, `::test_token_expired_org_role_403`, `::test_token_other_app_role_in_org_does_not_count`, `::test_token_org_less_app_ignores_org_id_422`, `::test_token_admin_audience_org_id_422`.
- [x] AC6 (org-create part): `organization.created` and `role_assignment.granted` are written in the same transaction with actor `EndUser`, `app_key` and `org_id`, and contain no email. A refusal writes nothing. Evidence: `auth_service.py:334-356`; `tests/test_auth_orgs.py::test_create_org_audit_rows`, `::test_create_org_refusal_writes_no_audit`.
- [x] AC7 (06a part): create org → owner role, token roles with and without org, no role in org → 403, org-less app → 422. All covered by the tests listed above, all passing.
- [x] Plan row 8 (R3 cookie class): both routes are in `COOKIE` in `tests/test_route_auth_classes.py:48-49` and the sweeps pass.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: routers are HTTP only (`auth_router.py`); services raise `AppError` subclasses; SQL stays in the repositories.
- R2: hand-written migration with a working `downgrade()`, verified by the round trip and `alembic check`. No new model class, so `app/db/models.py` is unchanged.
- R3: cookie class (`AppContextDep`, `DeviceDep`, no `require_permission`). `/auth/orgs` is already listed in CLAUDE.md R3 class 2.
- R4: org creation is audited in the same transaction. The admin app PATCH diff now includes `org_owner_role_id` (`audit/diff.py`).
- R5: the new limits come from settings and are listed in `.env.example` without values.
- R6: only 401/403/409/422 are new (plus the existing 400/429 from the shared deps), all through the error envelope.
- R7: the out-of-plan test files (`tests/rbac_factories.py`, `tests/test_rbac_authz.py`, `tests/test_rbac_schema.py`) are justified because random `org_id`s now violate the new FK. The work/ files are orchestrator bookkeeping.
- R9: additive except the 409 on admin delete, which the owner agreed to (Q3).
- R11: logs carry ids only, and the audit `changes` hold no email or token.

### Nits (do not block)
- `tests/test_audit_coverage.py:511-514` puts `POST /auth/orgs` in `IDENTITY_NOT_AUDITED`, even though the route is audited. The comment explains this, but the set name now reads wrong. A small multi-row case in the harness would be cleaner (already listed in the impl-log follow-ups).
- `app/modules/rbac/authz.py:636-640`: the `app_key:` prefix filter is redundant because the repository query already filters `App.key == app_key`. It is harmless and consistent with `effective_roles`.
- `app/modules/rbac/repository.py:323` compares with the literal `"active"`. `OrganizationStatus.ACTIVE.value` would match how the rest of the code reads.
- The per-user cap (`count_created_by`) is serialized only by the per-app lock, so concurrent creates in two different org-mode apps could go one over the cap. Today only calechip has orgs, so this is negligible.

### Follow-ups (out of scope)
- CLAUDE.md R11 cookie name and path (`mm_device`, `Path=/api/v1/auth`) disagree with spec §5.8/D20 and the code (`__Host-mm_device`, `Path=/`). The owner needs to update CLAUDE.md (plan A9).
- Tell admin-pan about the new 409 `org_has_assignments` on `DELETE /api/v1/organizations/{id}`.
- Promote `AssignmentService._grant_locked` to a public method (it is now used by join and org create).
- USR-06b (invitations), then org member management (needs `/idea` and a spec line first).
