Status: READY

# Plan: USR-06c, org-scoped roles in tokens and default-role hygiene for org apps

> On 2026-11-17 the owner answered both blocking consult questions (ticket Notes):
> (1) an additive `org_roles` claim is approved, and `roles` stays the D10 union;
> (2) the data marker `permissions.scope` (`app` | `org`) is approved, and the spec §5.3 default-role wording may be amended.
> This plan does not re-ask them. Every other choice the consults left open is recorded under `## Assumptions` (A1–A16), with a default and no blocking question.

## Spec summary
- spec §5.8 line 67 today: "Claims: `sub`, `email`, `email_verified`, `aud`, `roles`, `org` (if scoped), `iat`, `exp`." This ticket amends it with the line the owner approved (see Approach step 9).
- spec §5.3 line 48 today: "a default role `<app>:member` holding all its permissions except those flagged `is_admin` … Policy and role contents are data." This ticket amends it as approved: for `org_mode = required` apps, the built default role also excludes `scope = 'org'` permissions.
- identity-architecture.md D7: "JWT (~15 min): iss, aud, sub, email, email_verified, roles, org?, iat, exp". D9: "Default role `<app>:member` = all app permissions except `is_admin` ones … Policy and role content are data." D10: "token `roles` = app-scope + selected-org roles."
- ADR-0013 §3: "`effective_*` with an org (the union) is never used to authorize anything on its own." ADR-0013 §6: "Token claim `org` is the org UUID as a string, present only when requested."
- ADR-0014 §11 (the S1 guard, with the owner-superset precondition "fixed for good by USR-06c") and the Consequences paragraph naming the USR-06c work.
- CLAUDE.md rules that apply: R1, R2 (reversible migration, CHECK text matches the model), R4 (scope change audited), R6, R8, R9 (additive only), R11.
- Ticket AC1–AC6 and the owner answers (Notes, 2026-11-17).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | (AC1) An org token carries `org_roles`: the sorted role keys held in exactly the `org` org (unexpired, org `active`, prefix-filtered). The value is the `org_role_keys` result the 403 gate already computes, so there is no second query. `roles` is unchanged (the union). A token without `org` has neither `org` nor `org_roles`. | `tests/test_auth_token.py::test_token_with_org_has_org_claim_and_union_roles` (extended: `org_roles == ["calechip:org-owner"]`, `roles` unchanged), `::test_org_roles_excludes_app_scope_only_role`, `::test_org_roles_role_in_both_scopes_in_both_claims`, `::test_org_roles_excludes_other_org_role`, `::test_token_without_org_app_scope_only` (extended: `"org_roles" not in claims`), `::test_token_happy_path` (exact claim set unchanged for non-org tokens) |
| 2 | (AC1, AC5) The design is recorded in the new ADR-0015, plus amendments to ADR-0013, ADR-0014, ADR-0005 (claims list), ADR-0003 §7, identity-architecture.md (data model, notes, D7/D9/D10) and spec §5.8/§5.3. `docs/consumers.md` gives the org-authorization rule. | Doc diff review: every file in the Docs rows of the file table is changed as described in Approach step 9 |
| 3 | (AC2) For `org_mode = required` apps, `_build_for_app` targets `is_admin = false AND scope = 'app'`. A rebuild removes `scope = 'org'` permissions the member role already holds. This covers every caller: `build_default_role`, `AppService._create`, `AppService.register` (CLI) and future USR-05. For `org_mode = none` apps the target is unchanged (all non-admin permissions, `scope` ignored). | `tests/test_rbac_default_role.py::test_org_app_build_excludes_org_scope`, `::test_org_app_rebuild_removes_org_scope_from_member`, `::test_none_app_build_unchanged_with_org_scope_present`, `::test_org_app_build_refused_when_member_is_owner`; `tests/test_cli_register_app.py::test_register_org_app_does_not_widen_member`, `::test_register_org_permission_flag_creates_org_scope` |
| 4 | (AC2) `permissions.scope` is classification data. If `scope` is omitted on create, the reserved key decides first (`<app>:org:create` → `app`, `<app>:org:invite` → `org`). Otherwise `org` on `org_mode = required` apps (fail closed) and `app` on other apps. An explicit reserved-key violation is 422 `reserved_scope`. On an org app, flipping app→org while the default role holds the permission is 409. Flipping org→app on an org app needs `admin:*`. A scope change is audited. | `tests/test_rbac_scopes.py` (pure: `default_scope`, `reserved_scope`); `tests/test_rbac_permissions_api.py::test_create_scope_default_org_on_org_app`, `::test_create_scope_default_app_on_none_app`, `::test_create_reserved_keys_scope_derived`, `::test_create_reserved_scope_violation_422`, `::test_patch_scope_reserved_violation_422`, `::test_patch_scope_to_org_held_by_default_409`, `::test_patch_scope_to_app_on_org_app_requires_super`, `::test_patch_scope_null_422`, `::test_read_includes_scope`, `::test_patch_scope_audited` |
| 5 | (AC2) Other write paths cannot re-widen an org app's default role. `PATCH /apps/{key}` setting `default_role_id` (or switching `org_mode` to `required`) so that the default role holds a `scope = 'org'` permission → 422 `role_has_org_permissions`. `PUT …/roles/{id}/permissions` giving the default role of an org app a `scope = 'org'` permission → 409. | `tests/test_rbac_apps_api.py::test_patch_default_role_with_org_permission_422`, `::test_patch_org_mode_required_default_holds_org_permission_422`; `tests/test_rbac_roles_api.py::test_put_org_permission_on_org_app_default_role_409`, `::test_put_org_permission_on_none_app_default_role_ok` |
| 6 | (AC3) `default_role_id` can never equal `org_owner_role_id`. Layer 1: `set_default_role_if_null` adds `org_owner_role_id IS DISTINCT FROM :role_id` and returns whether it wrote. Layer 2: the DB CHECK `ck_apps_default_role_not_org_owner` (NULL-safe). Layer 3: the existing 422 `same_as_default_role` on PATCH. The migration aborts on violating rows and `downgrade()` drops the CHECK. | `tests/test_rbac_default_role.py::test_set_default_role_if_null_skips_org_owner`; `tests/test_rbac_schema.py::test_apps_default_not_owner_check_rejects_equal`, `::test_apps_default_not_owner_check_allows_nulls`, `::test_check_texts`; existing `tests/test_rbac_apps_api.py` `same_as_default_role` tests stay green; `tests/test_permission_scope_migration.py::test_preflight_aborts_on_default_equals_owner` |
| 7 | (AC4) The USR-06b guard stays as defense in depth, restated. Invitations need `org_mode = required`, `join_policy = open`, default and owner roles set and present, an admin-free default role, and a default role that holds **no `scope = 'org'` permission**. The owner-superset precondition and `roles_overlap` are removed. Same 403 `org_invite_unavailable` on create and accept (accept not consumed). ADR-0014 §11 is updated. | `tests/test_invitations_create.py::test_create_unavailable_when_default_holds_org_permission` (renamed from `…_overlaps_owner`), `::test_create_ok_when_default_shares_app_scope_permission_with_owner`, `::test_create_ok_after_build_default_role`; `tests/test_invitations_accept.py::test_accept_unavailable_when_default_widened_not_consumed` (still green, now via scope) |
| 8 | (AC5) `docs/consumers.md` tells org apps to authorize org data **only** from `org_roles` (mapped through the role catalog) and to scope by `org`. `roles` is for app-level features. A token with `org` but no `org_roles` is rejected. The "re-widening" warnings and the superset precondition are removed. The `mmlabs-auth` impact is listed as a follow-up. | Doc diff review of `docs/consumers.md` (Organizations, Invitations, token checklist) and the impl-log follow-ups list |
| 9 | (AC6) Invitations still work end to end for a correctly configured org app whose member role was built by `build_default_role`. | `tests/test_invitations_accept.py::test_invite_accept_token_after_build_default_role` (build → invite → accept → org token with `org_roles == [invited role]`, and the invitee's app-scope `calechip:member` absent from `org_roles`) |
| 10 | (R2) Migration: `permissions.scope` with a deterministic backfill (A5), the scope CHECK, the owner≠default CHECK with preflight, and a working `downgrade()`. `alembic check` is clean. Model and migration CHECK texts match. | `tests/test_permission_scope_migration.py::test_backfill_classifies_org_app_permissions`, `::test_none_app_permissions_stay_app`, `::test_downgrade_drops_column_and_checks_then_upgrade_recomputes`, `::test_preflight_aborts_on_default_equals_owner`; CI `alembic check` |
| 11 | (R8) `make check` is green. | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261117_<rev>_usr06c_permission_scope.py` (new) | New revision with `down_revision = "c5e91d3a7b24"` (verify with `alembic heads`). Order: preflight → add column → backfill → scope CHECK → owner≠default CHECK. `downgrade()` reverses it. See Data model. | AC3, AC6, AC10 (R2) |
| `app/modules/rbac/models.py` | `PermissionScope(StrEnum)` with `APP = "app"` and `ORG = "org"`. `Permission.scope: Mapped[str] = mapped_column(String(10), nullable=False, default="app", server_default="app")`. `CheckConstraint("scope IN ('app', 'org')", name="scope_valid")` on `permissions`. `CheckConstraint("default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id", name="default_role_not_org_owner")` on `apps`. | AC3, AC6 |
| `app/modules/rbac/scopes.py` (new) | Pure functions, no ORM: `reserved_scope(app_key, key) -> PermissionScope \| None` and `default_scope(app_key, org_mode, key) -> PermissionScope` (see A2/A3). | AC4 (unit-testable rule shared by API and CLI) |
| `app/modules/rbac/schemas.py` | `PermissionCreate.scope: PermissionScope \| None = None`. `PermissionUpdate.scope: PermissionScope \| None = None`, with explicit null rejected (extend `_non_null`). `PermissionRead.scope: PermissionScope`. `AppRegister.org_permissions: list[PermissionKey] = Field(default_factory=list, max_length=50)`. Docstrings updated. | AC4, AC3 (CLI path) |
| `app/modules/rbac/repository.py` | `set_default_role_if_null` adds `App.org_owner_role_id.is_distinct_from(role_id)` to the WHERE clause and returns `bool` (rowcount == 1). `list_permissions(..., scope: str \| None = None)`. New `role_has_org_permission(role_id) -> bool`, shaped like `role_has_admin_permission`. | AC3, AC5, AC6, AC7 |
| `app/modules/rbac/service.py` | `_build_for_app`: scope-filtered target for org apps, plus the member-is-owner refusal (A8). `AppService._update`: `role_has_org_permissions` 422 (A9). `AppService.register`: `org_permissions` handling (A11). `PermissionService._create` / `update`: scope default, reserved rules, flip guards, `require_super` for org→app (A4). `RoleService.replace_permissions`: the 409 for org-scope permissions on the default role of an org app (A10). Update the `build_default_role` docstring. | AC3, AC4, AC5, AC6 (R1: rules live in the service) |
| `app/modules/audit/diff.py` | `PERMISSION = ("key", "description", "is_admin", "scope")` | AC4 (R4: scope changes appear in `changes`) |
| `app/modules/tokens/service.py` | Keep the `org_role_keys` result, emit `claims["org_roles"]` next to `claims["org"]`, and keep `roles` as today. | AC1 |
| `app/modules/invitations/service.py` | `_unavailable_reason`: replace the owner-overlap block with `role_has_org_permission(default.id)` → reason `default_role_org_permission`, and keep the other reasons. Update the `_guard` docstring (defense in depth, ADR-0015). Drop the `roles_overlap` import. | AC7 |
| `app/modules/invitations/rules.py` | Remove `roles_overlap` (no remaining caller). | AC7 |
| `app/cli.py` | `--org-permission` (append). `REGISTER_USAGE` updated. New fixed messages and `_REASONS` entries: `reserved_scope`, `scope_conflict` (exit 64), `org_mode_required` (exit 64), `scope_mismatch` (exit 1), `member_is_org_owner` (exit 1). `_FIELD_MESSAGES["org_permissions"] = MSG_BAD_PERMISSION`. `_print_result` prints `+ org permission <k>`. | AC3 (CLI is a member-role write path, identity F4) |
| `tests/rbac_factories.py` | `make_permission(..., scope: str = "app")` | test setup |
| `tests/invitation_factories.py` | `setup_calechip`: mark `org:invite`, `org:edit` and `data:read` as `scope="org"` (`org:create` stays `app`). This keeps the existing guard tests meaningful. | AC7, AC9 |
| `tests/test_auth_token.py` | Extend 2 tests and add 3 (AC row 1). | AC1 |
| `tests/test_rbac_scopes.py` (new) | Pure tests for `scopes.py`. | AC4 |
| `tests/test_rbac_default_role.py` | Add the build and `set_default_role_if_null` tests (AC rows 3 and 6). | AC3, AC6 |
| `tests/test_rbac_permissions_api.py` | `PERMISSION_READ_KEYS` gains `scope`; add the scope tests (AC row 4). | AC4 |
| `tests/test_rbac_apps_api.py` | Add the `role_has_org_permissions` tests (AC row 5). | AC5 |
| `tests/test_rbac_roles_api.py` | Add the PUT tests (AC row 5). | AC5 |
| `tests/test_rbac_schema.py` | Tests for both CHECKs (reject, allow NULLs, `pg_get_constraintdef` text). | AC6, AC10 |
| `tests/test_permission_scope_migration.py` (new) | Backfill, downgrade/upgrade roundtrip and preflight tests, in the pattern of `tests/test_orgs_migration.py` (plain `def`, end at head, cleanup in `finally`). | AC10 |
| `tests/test_invitations_create.py`, `tests/test_invitations_accept.py` | Rename and add the guard tests (AC rows 7 and 9). | AC7, AC9 |
| `tests/test_invitations_rules.py` | Delete the five `test_roles_overlap_*` tests (the function is gone). Keep the `is_subset` and `safe_org_name` tests. | AC7 |
| `tests/test_cli_register_app.py` | Add the `--org-permission` and refusal tests (A11). | AC3 |
| `tests/test_admin_guards.py`, `tests/test_audit_*.py` | Only where they break: a permission snapshot now includes `scope`, and the org→app flip is a new `admin:*` case (add one `Case` to the guard matrix). | R8 |
| `docs/adr/0015-org-roles-claim-and-permission-scope.md` (new) | The decision record (Approach step 9). | AC1, AC2, AC4 |
| `docs/adr/0013-org-scoped-identity.md` | §6 adds `org_roles`. Consequences: replace the "re-widens" paragraph with a pointer to ADR-0015. | AC1 |
| `docs/adr/0014-org-invitations.md` | §11 restated as scope-based. Drop the precondition and add the removal criterion. Rewrite Consequences (the residual risk moves to ADR-0015). | AC4 |
| `docs/adr/0005-access-token-signing.md` | The claims list (line ~39) adds `org_roles` (only with `org`). | AC1 (claims documented where they are defined) |
| `docs/adr/0003-rbac-keys-and-matching.md` | §7 gets a one-line amendment: for `org_mode = required` apps the build excludes `scope = 'org'` (ADR-0015). | AC2 |
| `docs/identity-architecture.md` | Data model `permissions` line adds `scope app\|org default 'app'`. The `apps` block notes the owner≠default CHECK. Notes line 126 amended. D7 adds `org_roles?`. D9 adds the org-app exclusion. D10 adds "`org_roles` = selected-org roles only (ADR-0015)". | AC1, AC2 |
| `docs/spec-v1.md` | §5.8 claims line and §5.3 default-role clause, worded as approved by the owner (2026-11-17). | owner answers 1 and 2 (the spec wins) |
| `docs/consumers.md` | Organizations section (lines 139-145), Invitations staff requirement (lines 183-191), token claims and `org_mode = required` paragraph (lines 373-379). | AC5 |
| `docs/deploy.md` | "Register an app": `--org-permission` and the new exits. New subsection "Permission scope (USR-06c)" under Migration rules: preflight abort meaning, a read-only review query, and a note that the next member-role build narrows org apps. | R2 runbook, operational risk from the consults |
| `work/tickets/USR-05/ticket.md` | Add one Notes line: the manifest must declare `scope` per permission (on org apps a missing scope → 422 is recommended) and goes through `_build_for_app`. The test: pushing twice never adds an `org` permission to `<app>:member`. | caller instruction and security R-E (planning note only; no scope change to USR-05) |

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_auth_token.py › test_token_with_org_has_org_claim_and_union_roles` (extend) | `org_roles == ["calechip:org-owner"]`; `roles` still the union |
| `test_auth_token.py › test_org_roles_excludes_app_scope_only_role` | An app-scope-only role (`calechip:member` plus a staff-granted `calechip:editor`, both `org_id` NULL) is in `roles` and absent from `org_roles` |
| `test_auth_token.py › test_org_roles_role_in_both_scopes_in_both_claims` | The same role at app scope and in the org is in both claims |
| `test_auth_token.py › test_org_roles_excludes_other_org_role` | A role held in org B is not in the org-A `org_roles` |
| `test_auth_token.py › test_token_without_org_app_scope_only` (extend) | Neither `org` nor `org_roles` |
| `test_rbac_scopes.py › test_reserved_scope_*`, `› test_default_scope_*` | Reserved keys, org/none defaults, another app's `org:create` not reserved |
| `test_rbac_default_role.py › test_org_app_build_excludes_org_scope` | Org app with `org:create` (app), `org:invite` (org), `projects:read` (app), `projects:delete` (org) → member = `{org:create, projects:read}` |
| `test_rbac_default_role.py › test_org_app_rebuild_removes_org_scope_from_member` | A hand-added org permission is in `removed` |
| `test_rbac_default_role.py › test_none_app_build_unchanged_with_org_scope_present` | `none` app regression: an `org`-scoped permission is still included |
| `test_rbac_default_role.py › test_org_app_build_refused_when_member_is_owner` | 409 `member_is_org_owner`; owner role permissions and `default_role_id` unchanged |
| `test_rbac_default_role.py › test_set_default_role_if_null_skips_org_owner` | The repository returns False and the default stays NULL when `role_id == org_owner_role_id` (direct repository call) |
| `test_rbac_default_role.py › test_app_create_org_mode_required_builds_scoped_member` | `AppService._create` path (an empty member is fine; then add permissions and rebuild) |
| `test_rbac_permissions_api.py › test_create_scope_default_org_on_org_app` / `› test_create_scope_default_app_on_none_app` / `› test_create_reserved_keys_scope_derived` / `› test_create_reserved_scope_violation_422` | Create defaults and reserved keys |
| `test_rbac_permissions_api.py › test_patch_scope_reserved_violation_422` / `› test_patch_scope_to_org_held_by_default_409` / `› test_patch_scope_to_org_none_app_held_by_default_ok` / `› test_patch_scope_to_app_on_org_app_requires_super` (403 without `admin:*`, denied audit row; 200 with it) / `› test_patch_scope_null_422` / `› test_patch_scope_audited` / `› test_read_includes_scope` | PATCH guards, audit, read shape |
| `test_rbac_apps_api.py › test_patch_default_role_with_org_permission_422` / `› test_patch_org_mode_required_default_holds_org_permission_422` | Default-role write paths |
| `test_rbac_roles_api.py › test_put_org_permission_on_org_app_default_role_409` / `› test_put_org_permission_on_none_app_default_role_ok` / `› test_put_org_permission_on_owner_role_ok` | PUT guard is limited to the default role of org apps |
| `test_rbac_schema.py › test_permissions_scope_check_rejects_unknown` / `› test_apps_default_not_owner_check_rejects_equal` / `› test_apps_default_not_owner_check_allows_nulls` (both NULL, either NULL) / `› test_check_texts` | DB layer |
| `test_permission_scope_migration.py › test_backfill_classifies_org_app_permissions` | The A5 table: `org:create`→app; `org:invite`→org even when held by default only; default-only→app; owner-only→org; both→org; neither→org; an org app with no owner role set |
| `test_permission_scope_migration.py › test_none_app_permissions_stay_app` / `› test_downgrade_drops_column_and_checks_then_upgrade_recomputes` / `› test_preflight_aborts_on_default_equals_owner` / `› test_migration_never_touches_role_permissions` | Migration |
| `test_invitations_create.py › test_create_unavailable_when_default_holds_org_permission` (rename) / `› test_create_ok_when_default_shares_app_scope_permission_with_owner` / `› test_create_ok_after_build_default_role` | The restated guard |
| `test_invitations_accept.py › test_invite_accept_token_after_build_default_role` | AC6 end to end |
| `test_cli_register_app.py › test_register_org_app_does_not_widen_member` / `› test_register_org_permission_flag_creates_org_scope` / `› test_org_permission_on_none_app_refused` / `› test_same_key_in_both_flags_refused` / `› test_reserved_scope_refused` (`--org-permission x:org:create`, `--permission x:org:invite`) / `› test_existing_scope_mismatch_refused` / `› test_member_is_owner_refused` | CLI path; every refusal happens before the first write |

## Approach
1. **Migration and models (R2).** Write the revision exactly as in Data model. Mirror both CHECKs in `models.py` with the same text. Run `alembic upgrade head`, `alembic check` and `alembic downgrade -1 && alembic upgrade head` locally.
2. **Pure rules.** Add `app/modules/rbac/scopes.py`:
   - `reserved_scope(app_key, key)`: `f"{app_key}:org:create"` → APP, `f"{app_key}:org:invite"` → ORG, else None.
   - `default_scope(app_key, org_mode, key)`: `reserved_scope(...)` if set, else ORG when `org_mode == "required"`, else APP.
3. **Repository (R1).**
   - `set_default_role_if_null` gets `.where(App.org_owner_role_id.is_distinct_from(role_id))` and `-> bool`.
   - `list_permissions(..., scope=None)`.
   - `role_has_org_permission(role_id)`.
4. **Default-role build (AC2), in `_build_for_app`.**
   - After the member role is fetched or created, when `app.org_mode == required` and `app.org_owner_role_id == role.id`: raise `ConflictError("The member role is the app's org owner role", details=[{"field": "org_owner_role_id", "reason": "member_is_org_owner"}])` before any permission write (A8).
   - Target: `list_permissions(app.id, is_admin=False, scope="app")` for org apps, and the unchanged call otherwise.
   - `set_default_role_if_null`'s result is not surfaced in `DefaultRoleResult`. With step 4's refusal it cannot be False because of the owner (A7).
5. **Permission service (AC4).**
   - `_create`: `scope = data.scope or default_scope(app.key, app.org_mode, data.key)`. If `data.scope` is explicit and contradicts `reserved_scope`, raise 422 `details=[{"field": "scope", "reason": "reserved_scope"}]`. Pass `scope=` to `Permission(...)`.
   - `update`:
     - Next to the existing early 422s: a reserved-scope violation → the same 422.
     - Widen the `require_super` condition with `or (changes.get("scope") == "app" and app.org_mode == "required")` (it sits inside the existing try/except, so the denied audit row is free).
     - After the `is_admin` 409s: if `changes.get("scope") == "org"`, `app.org_mode == "required"`, the default role is set and `role_holds_permission(default, perm)`, raise `ConflictError("The app's default role holds this permission")`.
     - The audit diff now carries `scope` via `diff.PERMISSION`.
6. **App PATCH and role PUT (AC2 invariant on every path).**
   - `AppService._update`, after the owner≠default check: compute `org_mode_after = changes.get("org_mode", app.org_mode)`. If `org_mode_after == "required"`, `default_role_id is not None`, `("default_role_id" in changes or "org_mode" in changes)` and `await repo.role_has_org_permission(default_role_id)`, raise 422 `details=[{"field": "default_role_id", "reason": "role_has_org_permissions"}]`.
   - `RoleService.replace_permissions`, next to the admin checks at line 1065: if `role.id == app.default_role_id`, `app.org_mode == "required"` and any permission in `found` is `scope == "org"`, raise `ConflictError("A default role of an org app must not hold org-level permissions")`.
7. **Token (AC1).** In `TokenService.issue`:
   ```python
   org_roles = await self.authz.org_role_keys(user.id, app.key, data.org_id)
   if not org_roles:
       raise OrgAccessDeniedError(...)
   roles = await self.authz.effective_roles(user.id, app.key, data.org_id)
   claims["org"] = str(data.org_id)
   claims["org_roles"] = org_roles
   ```
   The list is never empty (the gate guarantees it). Do not log it (the log line stays unchanged).
8. **Guard (AC4) and CLI.**
   - Guard: `_unavailable_reason` keeps `org_mode`, `join_policy`, `roles_unset`, `roles_missing` and `default_role_admin`, then `if await self.rbac.role_has_org_permission(default.id): return "default_role_org_permission"`. The owner-permission read and `roles_overlap` go away.
   - CLI and `register` (A11):
     - Pre-validation, before the first write: prefix and wildcard checks over both lists; a key in both lists → 422 `scope_conflict`; `org_permissions` while the resulting `org_mode` (`app.org_mode` if the app exists, else `data.org_mode or none`) is not `required` → 422 `org_mode_required`; a reserved-scope contradiction → 422 `reserved_scope`; an existing key whose stored scope differs from its flag → 409 `scope_mismatch` (next to the `is_admin` check).
     - Writes: create with explicit `scope=`.
     - Result: `RegisterResult` gains `org_permissions_added`, and the audit `changes` gains `org_permissions_added`.
     - Member rebuild: `if added or org_added` (unchanged call, `set_default=False`).
9. **Docs.**
   - ADR-0015 (Status: Accepted, owner 2026-11-17). Context: S1 (USR-06 security review), N-2 (USR-06b). Decision:
     1. the `org_roles` claim (content, presence iff `org`, `roles` unchanged, R9);
     2. `permissions.scope`, with the default rules A2/A3, the reserved keys and the flip guards A4;
     3. the build rule for org apps, with `none` apps unchanged;
     4. owner≠default in three layers;
     5. the guard kept as defense in depth, with its removal criterion (every org-mode consumer pins an `mmlabs-auth` release that authorizes org data from `org_roles` only);
     6. backfill A5.
     Rejected alternatives: redefining `roles` (breaks R9/D10), owner-derived classification (N-2), a role-level marker (roles mix scopes), the boolean `is_org_level` (A1). Residual risk: consumers still reading `roles`, a staff-granted app-scope role that holds `org` permissions, and an org_mode none→required flip (A13). Consequences: the next build narrows org apps' member roles; calechip has to be told.
   - Spec §5.8 claims: "Claims: `sub`, `email`, `email_verified`, `aud`, `roles` (app-scope plus selected-org roles), `org` (if scoped), `org_roles` (if scoped: role keys held in that org only; org apps authorize org data from it, ADR-0015), `iat`, `exp`."
   - Spec §5.3: "a default role `<app>:member` holding all its permissions except those flagged `is_admin` (for `org_mode = required` apps also except org-level permissions, `permissions.scope = 'org'`; ADR-0015)". Both carry "(owner, 2026-11-17)".
   - consumers.md:
     - Claims line adds `org_roles`.
     - Replace lines 377-379 with the rule: authorize org-scoped data and actions **only** from `org_roles` mapped through the role catalog, and scope by `org`; use `roles` of a token **without** `org_id` for app-level actions (create or pick an org); never authorize org data from `roles` of an org token; if `org` is present and `org_roles` is absent, reject the token.
     - Organizations: replace the warning at 142-145 with "mark org-level permissions `scope = org` (the default on org apps); the member build skips them".
     - Invitations staff requirement: restate as scope-based, drop the superset and "until USR-06c" sentences.
     - Add a follow-up note for `mmlabs-auth`.
   - ADR-0013, ADR-0014, ADR-0005, ADR-0003 and identity-architecture as in the file table.
   - deploy.md as in the file table, with this review query:
     ```sql
     SELECT a.key, p.key, p.scope FROM permissions p JOIN apps a ON a.id = p.app_id WHERE a.org_mode = 'required' ORDER BY 1, 2;
     ```
10. Run `make check`. Record follow-ups in the impl-log (see Assumptions A14).

Rules: R1 (SQL only in the repository, rules in the service and `scopes.py`, routers untouched apart from the schemas they already use), R2 (step 1), R3 (no new endpoint; auth classes unchanged), R4 (the scope change is in the permission diff; the CLI audit gains `org_permissions_added`; the migration writes data without audit, the same as earlier backfills, and never touches `role_permissions`), R5 (no config), R6 (existing error classes), R7 (out-of-scope findings go to follow-ups), R9 (additive: one claim, one response field, two optional request fields, one CLI flag), R11 (no token or email in logs; `org_roles` is not logged).

## Specialist input used
- **All five:** the `org_roles` claim reusing the gate's `org_role_keys`, present only with `org`, `roles` unchanged: adopted (the owner approved it).
- **software-architect R-B** (no column; build = `{org:create}` only): rejected. The owner chose the `scope` marker. The architect's own fail-closed condition for a marker ("must default to org-level") is adopted as A2.
- **software-architect R-C, security F6, DB optimizer §3** (NULL-safe CHECK, `IS DISTINCT FROM` only in the repository WHERE, preflight that aborts, no auto-fix): adopted.
- **software-architect R-D** (keep the guard code unchanged): rejected in favour of identity R5 and security R-D (restate exactly by scope). That removes N-2, as the caller instructed.
- **identity R2** (scope column, reserved keys, flip guards, `admin:*` for org→app, audit allowlist): adopted. Its default `'app'` for new permissions on org apps is rejected in favour of security R-B `'org'` (A2). Its owner-derived backfill is rejected in favour of security R-F (A5).
- **identity R3** (CLI `--org-permission`, the scope-mismatch refusal, the PATCH `default_role_id` 422, the PUT refusal): adopted (A9–A11).
- **identity Q5 / security Risk 1** (refuse app-scope grants of roles that hold `org` permissions): deferred to a follow-up (A14).
- **security R-E** (USR-05 manifest carries `scope`, 422 when missing): written into the USR-05 ticket note.
- **security R-G** (reject a token with `org` but no `org_roles`): adopted in consumers.md.
- **api-platform B** (boolean `is_org_level`): rejected. The owner approved `scope` (`app`|`org`) (A1). Its "allow scope on `none` apps, ignored" is adopted (A3). Its "refuse the PUT that puts org-level permissions on the default role" is adopted (A10). Its "fail closed when no permission is marked" is rejected: with the default `org` and backfill A5, an org app cannot end up with everything unmarked by accident (A13 covers the none→required flip).
- **DB optimizer** (`VARCHAR(10) NOT NULL DEFAULT 'app'` is metadata-only on PG 17; the member-is-owner rebuild hazard): adopted (A8). Its key-pattern backfill is rejected (A5).
- **All:** the catalog stays unchanged (no ETag churn): adopted.

## Assumptions
- **A1. Marker form.** `permissions.scope varchar(10) NOT NULL DEFAULT 'app'`, `CHECK (scope IN ('app', 'org'))`, independent of `is_admin` (no combined CHECK). These are the owner's words in answer 2.
- **A2. Default on create when `scope` is omitted (admin API).** The reserved key decides first. Otherwise `org` for `org_mode = required` apps (fail closed: a forgotten classification never reaches the member role) and `app` otherwise. This is security R-B, and the architect's fail-closed note. Clients can always send `scope` explicitly.
- **A3. Reserved keys, all apps.** `<app>:org:create` is always `app` (ADR-0013 §1 checks it at app scope). `<app>:org:invite` is always `org` (ADR-0014 §1 checks it org-exact). An explicit contradiction is 422 `reserved_scope`. `scope` may be set on `org_mode = none` apps; it is ignored by their build.
- **A4. Flip guards, `org_mode = required` apps only.**
  - app→org is refused with 409 while the default role holds the permission. This mirrors the `is_admin` guard; refuse rather than auto-strip (D9).
  - org→app needs `admin:*`. It makes the permission eligible for the member role, mirroring `is_admin` true→false.
  - On `none` apps scope flips are free (the scope has no effect there).
- **A5. Migration backfill (security R-F, deterministic).** Only `org_mode = 'required'` apps, all of their permissions, in this priority:
  1. key `<app>:org:create` → `app`;
  2. key `<app>:org:invite` → `org`;
  3. held by the default role and **not** by the owner role → `app`;
  4. everything else → `org`.
  It is chosen over identity R2 because R2 marks permissions held by neither role as `app` (fail open). Permissions held by both roles become `org`, so the guard refuses invitations until staff rebuild the member role (audited). That is the same refusal the current overlap guard gives. The migration **never** writes `role_permissions` (R4). A downgrade loses `scope`. A re-upgrade recomputes it from the current role contents (documented in the docstring).
- **A6. CHECK name and text.** Model `name="default_role_not_org_owner"`, which becomes `ck_apps_default_role_not_org_owner` (migration `op.f(...)`). Text: `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id`. Scope CHECK: `scope_valid`, which becomes `ck_permissions_scope_valid`. The preflight `SELECT count(*) FROM apps WHERE default_role_id = org_owner_role_id` raises `RuntimeError("USR-06c: … apps have default_role_id = org_owner_role_id; fix them before upgrading")` and never auto-fixes. It runs first in `upgrade()`.
- **A7. `set_default_role_if_null`** returns `bool`. Callers ignore it: the step-4 refusal already makes "role == owner" unreachable from `_build_for_app`, so the WHERE clause is the second layer and the CHECK the third. A write that would violate the CHECK elsewhere surfaces through the existing `IntegrityError` → 409 mapping in `_update`.
- **A8. Member-is-owner refusal.** For org apps, a rebuild must not rewrite the owner role's permissions (it would now strip the `org` permissions from the owner). It is refused with 409 `member_is_org_owner` on every build path (CLI exit 1 with a fixed message). It is not needed for `none` apps (an owner role needs `org_mode = required`, per the CHECK).
- **A9. PATCH app.** 422 `role_has_org_permissions` when the resulting default role of a resulting `required` app holds a `scope = 'org'` permission. It is checked only when `default_role_id` or `org_mode` actually changes, so unrelated PATCHes on legacy-contaminated apps are not blocked (the guard covers them).
- **A10. Role PUT.** 409 when the role is the default role of an org app and the target set contains a `scope = 'org'` permission. This applies to the whole target, not only the added permissions, so a contaminated default role can be narrowed but never re-widened. A PUT that keeps an existing contaminating permission is therefore refused too. That is intended: staff must remove it (or rebuild). The PUT on the owner role and on other roles is unaffected.
- **A11. CLI.**
  - `--permission` means explicitly `scope = app` (its documented meaning: "goes into the member role"); `--org-permission` means `scope = org` and is allowed only when the resulting `org_mode` is `required`.
  - A key passed under the other scope than the one stored → 409 `scope_mismatch` (mirrors `is_admin_permission`).
  - The `ops-org-admin-api` workflow input for `--org-permission` lives in `mmlabs-online/deployment` (follow-up).
- **A12. The guard stays.** Its error code, its placement (create and accept), its other conditions and `join_policy = open` are all unchanged. The owner role must still be set and present (`roles_unset` / `roles_missing`) even though the scope check no longer reads it, to keep the existing behaviour (R7).
- **A13. `org_mode` none→required flip.** Existing permissions keep their stored scope (`app` unless set). The PATCH refuses only when the default role already holds `org` permissions (A9). Staff must classify org-level permissions as `org` before or after flipping. This is documented in ADR-0015 and consumers.md; no automatic reclassification (R7).
- **A14. Follow-ups (not in this diff):**
  - `mmlabs-auth`: an `org_roles` accessor; `require_org_permission(k)` mapping only `org_roles` through the catalog; reject a token with `org` and no `org_roles`; a release before calechip relies on it; a TS `org_roles` type.
  - calechip: a migration note, and the narrowing at the next build.
  - Refuse app-scope grants of roles holding `org` permissions on org apps.
  - Retire the guard once every org-mode consumer is on `org_roles`.
  - Optionally expose `scope` in the role catalog.
  - The deployment-repo workflow input for `--org-permission`.
  - Pre-existing: a user holding only org roles gets `not_member` at `/auth/token`.
- **A15. Seed.** The ticket's "seed" path: no seed migration builds a non-admin member role (the admin app is refused by `_build_for_app`), so the seed is covered by the `_build_for_app` change with nothing extra.
- **A16. Production data.** Whether calechip exists in the production `org_admin` DB is unknown here. A5 and A6 are safe either way (deterministic, abort instead of rewrite). deploy.md tells the operator to run the review query after deploy.

## Blocking questions
None. The owner answered both on 2026-11-17 (ticket Notes).

## Split proposal
None. Estimated diff: about 330 lines of production code (migration about 90, services about 120, CLI about 40, the rest small), about 450 lines of tests and about 150 of docs. This is above the rough 400-line guide, but it is one spec feature: the S1 fix. The parts depend on each other. The guard rewrite and the build rule need `scope`. The token claim alone would leave AC2–AC4 unmet. A split would ship a column nothing uses, or a guard that still relies on the owner-superset precondition. So it stays one loop. If the reviewer finds the CLI part (A11) too large, it is the cleanest part to move to a follow-up, because the build filter already stops the CLI from re-widening.

## Data model / migration
Revision `20261117_<rev>_usr06c_permission_scope.py`, `down_revision = "c5e91d3a7b24"`.

`upgrade()`:
1. Preflight (A6): count `apps` rows with `default_role_id = org_owner_role_id`. If non-zero, raise `RuntimeError` (no auto-fix).
2. `op.add_column("permissions", sa.Column("scope", sa.String(10), nullable=False, server_default="app"))`. On PG 17 this is metadata-only.
3. Backfill (A5), one statement:
   ```sql
   UPDATE permissions p SET scope = 'org'
   FROM apps a
   WHERE p.app_id = a.id AND a.org_mode = 'required'
     AND p.key <> a.key || ':org:create'
     AND (
       p.key = a.key || ':org:invite'
       OR NOT (
         a.default_role_id IS NOT NULL
         AND EXISTS (SELECT 1 FROM role_permissions rp
                     WHERE rp.role_id = a.default_role_id AND rp.permission_id = p.id)
         AND NOT EXISTS (SELECT 1 FROM role_permissions rp
                         WHERE rp.role_id = a.org_owner_role_id AND rp.permission_id = p.id)
       )
     );
   ```
   A NULL `org_owner_role_id` makes the `NOT EXISTS` true. A NULL `default_role_id` makes everything except `org:create` `org`.
4. `op.create_check_constraint(op.f("ck_permissions_scope_valid"), "permissions", "scope IN ('app', 'org')")`.
5. `op.create_check_constraint(op.f("ck_apps_default_role_not_org_owner"), "apps", "default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id")`.

`downgrade()`: drop `ck_apps_default_role_not_org_owner`, drop `ck_permissions_scope_valid`, drop column `permissions.scope`. It is lossy for `scope` only (documented in the docstring).

Model: `Permission.scope` with `default="app"` and `server_default="app"` (matching the migration for `alembic check`), plus both `CheckConstraint`s with the exact texts above. No new index: scope filters run inside one app's small permission set (`uq_permissions_app_id_key` leads with `app_id`). No change to `role_permissions`, `roles` or `role_assignments`.

## API contract
No new endpoint, no change to auth classes. Every change is additive (R9).

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/token` | unchanged | JWT gains `org_roles: string[]` **only when** `org` is present (sorted org-exact role keys); `roles` unchanged | unchanged | cookie class (R3 class 2) |
| POST | `/api/v1/apps/{key}/permissions` | adds optional `scope: "app" \| "org"` (absent or null → A2/A3 default) | `PermissionRead` + `scope` | 201; 422 `reserved_scope` (field `scope`); existing codes | `admin:roles:manage` (unchanged; `admin:*` rules unchanged) |
| PATCH | `/api/v1/apps/{key}/permissions/{id}` | adds optional `scope` (explicit null → 422) | `PermissionRead` + `scope` | 200; 422 `reserved_scope`; 409 default role holds it (app→org on an org app); 403 org→app on an org app without `admin:*` (denied audit row) | `admin:roles:manage` (+ `admin:*` for org→app on org apps) |
| GET | `/api/v1/apps/{key}/permissions` | unchanged | items gain `scope` | unchanged | unchanged |
| PATCH | `/api/v1/apps/{key}` | unchanged | unchanged | new 422 `role_has_org_permissions` (field `default_role_id`); an equal owner/default is still 422 `same_as_default_role` (DB CHECK backstop → 409) | unchanged |
| PUT | `/api/v1/apps/{key}/roles/{id}/permissions` | unchanged | unchanged | new 409 (default role of an org app with a `scope = org` permission) | unchanged |
| GET | `/api/v1/apps/{key}/roles` (public catalog) | unchanged | **unchanged** (no ETag change) | unchanged | public |
| POST | `/api/v1/auth/orgs/{org_id}/invitations`, `/api/v1/auth/invitations/accept` | unchanged | unchanged | 403 `org_invite_unavailable` now means "the default role holds an org-level permission" (or the unchanged other conditions) | cookie class |
| CLI | `register-app` | adds `--org-permission <key>` (repeatable) | prints `+ org permission <k>` | exit 64: `reserved_scope`, `scope_conflict`, `org_mode_required`; exit 1: `scope_mismatch`, `member_is_org_owner` | operator |
