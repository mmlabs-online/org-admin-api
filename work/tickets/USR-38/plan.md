Status: READY

# Plan — USR-38: Designated per-app super-admin role for org apps (simap SIM-043)

> **High** (migration, `app/modules/rbac/**`): stops at PLAN for Ming's approval (Gate `plan-approval`), never merged
> by an agent. Batch 2026-11-26-1; USR-39 and USR-40 build on this plan (shared contract in "Approach" step 9).
> Written for the **default answers** of "Blocking questions for Ming" (Q1–Q13). Q1 is a **spec conflict** (spec
> §5.16 proposal vs every consultant); Q1, Q3, Q5 and Q7 change code, the others only confirm behaviour.

## Scope summary
- Ticket Goal: "Staff can designate one app-scope role of an `org_mode = required` app (first: `simap:super-admin`)
  as that app's super-admin role and grant it only to mmlabs staff super-admins, audited — the foundation for staff
  access to every org of the app (USR-39, USR-40)."
- Ticket Scope: "Designation (proposed, OQ80): nullable `apps.super_admin_role_id` (role of the same app; only
  `org_mode = required`; never the `admin` app; distinct from default and org-owner roles); set by staff with
  `admin:*`; migration with working `downgrade()`." / "Manifest can neither declare, change, delete the designated
  role nor change its permissions or the designation; deleting the designated role is refused; changing `org_mode`
  away from `required` while designated is refused." / "The designated role is privileged because of the
  designation (not because of its permission content): granting needs `admin:*`." / "Grant rules (proposed, OQ81):
  app scope only (`org_id` → 422); recipient must hold an active app-scope `admin:super-admin` at grant time (422,
  reason set at PLAN); self-assignment forbidden; refused by the USR-36 bulk route at request level; audited as
  `role_assignment.granted`." / "Lifecycle (OQ82, proposed: mint-time re-check in USR-39, no cascade revoke) and
  expiry (OQ83, proposed optional) settled at PLAN."
- Ticket Out of scope: "Token minting (USR-39), org picker (USR-40), `admin:super-admin` in any app token,
  impersonation."
- Ticket Scope reference: "Needs ADR-0030 + decision-log D42 (amends ADR-0008 privileged-role rules and ADR-0013 /
  ADR-0015 org-token rule); written in this plan and covering USR-39 / USR-40."
- Spec §5.16 "Decided by the owner (2026-11-26)": "The power is a **per-app role of the target app,
  `simap:super-admin`** … **not** `admin:super-admin` in a simap token" / "It is a **generic per-app mechanism for
  `org_mode = required` apps**, not a simap special case."
- Spec §5.16 Proposed (line 415, OQ80): "An app marks one of its roles as its super-admin role by data … a nullable
  `apps.super_admin_role_id` (like `apps.org_owner_role_id`), only for `org_mode = required` apps, never for the
  `admin` app. **The role is app-scope and holds all of the app's permissions (org-scope ones included).** Set by
  staff (`admin:*`); the manifest can neither declare, change nor delete it, nor change its permission set."
- Spec §5.16 Proposed (line 416, OQ81–83): "App scope only (`org_id` refused); needs `admin:*`; the recipient must hold
  `admin:super-admin` at grant time ("staff only"); self-assignment forbidden (consistent with USR-25); audited as
  `role_assignment.granted` today; never assignable through the §5.15 bulk route. Holding it is app membership (no
  separate join, no `not_member`)."
- Spec §8 OQ80 (line 558) also proposes the role "is `is_system` so the manifest can neither declare, edit nor delete
  it" and "`org_mode` changes to `none` … refused while designated, like ADM-019's `org_mode_in_use`". OQ82 (line
  560): "(b) keep them but have the mint also require `admin:super-admin` at mint time … (proposed: (b) at least)".
  OQ83 (line 561): "Proposed: optional." OQ90 (line 568): "designation per app, simap first".
- ⚠️ **Spec conflict (Q1):** spec line 415 proposes "holds all of the app's permissions (org-scope ones included)";
  all five consultants recommend refusing `scope = org` permissions (and `<app>:*`) on the designated role because
  `roles` is the D10 union and reaches every token of the app (ADR-0015 §Consequences already names this residual
  risk). The spec text is a *proposal* (OQ80 open), so the spec does not decide it; this plan implements the
  consultants' default and asks Ming. If Ming keeps the spec text, three checks drop (Q1) and Q3's default flips.
- ⚠️ **Spec conflict (Q2):** OQ80 proposes `is_system`; the architect recommends `is_system = false` with protection
  from the designation. Default: `is_system = false`.
- `work/decisions.md`: nothing on super-admin / SIM-043. Numbering checked: ADR-0027 intentionally unused (ADM-042
  plan Q13), ADR-0029 / D41 taken by USR-36; **ADR-0030 and D42 are free** and reserved for this ticket by spec
  line 428 and the USR-39 / USR-40 tickets.
- CLAUDE.md R1 (layering), R2 (migration + `downgrade()`, `alembic check`), R3 class 1 (Bearer; no new route), R4
  (audit in the same transaction), R6 (error envelope), R7, R8, R9 (additive fields only), R11.

## Acceptance criteria → verification
All tests in `tests/test_rbac_super_admin_role.py` unless another file is named.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | Staff with `admin:*` set and clear the designation via `PATCH /api/v1/apps/{key}` | `test_set_designation_happy`, `test_clear_designation_happy`, `test_set_designation_with_org_mode_in_same_request`; `GET /apps/{key}` shows `super_admin_role_id` |
| 1b | Refused for the `admin` app, `org_mode != required`, a role of another app, and without `admin:*` (403) | `test_set_designation_admin_app_422`, `test_set_designation_org_mode_none_422`, `test_set_designation_other_app_role_422`, `test_set_designation_unknown_role_422`, `test_set_designation_without_wildcard_403` (+ denied audit row), `test_clear_designation_without_wildcard_403`, `test_set_designation_without_apps_write_403`, `test_set_designation_no_token_401`, `test_set_designation_unknown_app_404` |
| 1c | Further designation rules (Q1, Q2, Q4) | `test_set_designation_system_role_422` (`<key>:member`), `test_set_designation_default_role_422`, `test_set_designation_org_owner_role_422`, `test_set_default_role_to_designated_422`, `test_set_org_owner_role_to_designated_422`, `test_set_designation_role_with_org_permission_422`, `test_set_designation_role_with_wildcard_422`, `test_set_designation_role_with_assignments_409` (app scope, org scope and expired rows) |
| 1d | The change is audited | `test_designation_change_is_audited` (`app.updated`, `changes.super_admin_role_id` = `{from, to}`; set and clear); `test_noop_designation_needs_no_wildcard` (same value → 200, no audit row) |
| 2a | Granting to an account without active `admin:super-admin` is refused | `test_grant_recipient_not_staff_422` parametrized: no admin role, only a lesser admin-app role, `admin:super-admin` expired, recipient disabled, recipient unverified → 422 `recipient_not_staff_super_admin`, nothing written |
| 2b | Granting to a staff super-admin succeeds and writes `role_assignment.granted` | `test_grant_to_staff_super_admin_201` (one audit row, `changes.role_key` = `simap:super-admin`); `test_grant_with_expiry_201` (OQ83 optional); `test_renew_expired_designated_assignment_rechecks_recipient` (expired duplicate + recipient no longer staff → 422) |
| 2c | Org-scoped grant and self-assignment are refused | `test_grant_with_org_id_422` (`org_id_not_allowed`, checked before the org lookup: unknown org id still 422 `org_id_not_allowed`); `test_self_grant_403` (`self_assignment_forbidden`, denied audit row); `test_grant_without_wildcard_403` (`admin_wildcard_required`, recipient check not reached: a non-staff recipient still answers 403, no staff-status oracle) |
| 2d | Revoke needs `admin:*`, self-revoke forbidden | `test_revoke_designated_without_wildcard_403`, `test_self_revoke_designated_403`, `test_revoke_designated_happy_204` |
| 2e | Every other grant path refuses the designated role | `test_invitation_create_designated_role_403` (`InviteRoleForbiddenError`, no email queued); `test_invitation_accept_after_designation_token_invalid` (invite created before designation → accept answers the existing invalid-token error, invitation stays pending); `test_system_actor_grant_refused` (calls `_grant_locked` with `SYSTEM_JOIN` → `admin_wildcard_required`; covers join, approval, provisioning, org create, which only grant default / owner roles anyway, DB CHECK `super_admin_role_distinct`) |
| 3 | Manifest sync cannot create, alter, delete or re-permission the designated role, nor change the designation | `tests/test_app_manifest_api.py` › `test_manifest_designated_role_409` (`roles[]` with the designated key → 409 `role_not_manifest_managed`, role unchanged), `test_manifest_super_admin_role_id_field_422` (`extra="forbid"`), `test_manifest_new_permission_does_not_touch_designated_role` (new org permission → member rebuild; designated role's permission set unchanged), `test_manifest_never_changes_designation` (sync → `apps.super_admin_role_id` unchanged). Manifest has no delete path (additive, `manifest.py:207-221`) |
| 4a | The designated role cannot be deleted | `test_delete_designated_role_409` (`super_admin_role_designated`) |
| 4b | `org_mode` cannot leave `required` while designated | `test_org_mode_none_while_designated_409` (`super_admin_role_designated`); `test_org_mode_none_after_clear_ok` |
| 4c | Role permissions of the designated role (Q1) | `test_replace_permissions_designated_needs_wildcard_403` (any change, also non-admin permissions; denied audit row), `test_replace_permissions_designated_org_permission_409`, `test_replace_permissions_designated_wildcard_409`, `test_replace_permissions_designated_unchanged_reput_200` (no `admin:*` needed), `test_permission_scope_flip_to_org_held_by_designated_409` |
| 5 | Bulk route (USR-36) refuses the designated role at request level | Only if USR-36 is merged before this ticket's IMPLEMENT: `tests/test_rbac_assignment_batches_api.py` › `test_batch_designated_role_422` (`super_admin_role`, nothing written). Otherwise: ADR-0029 amendment line is in the diff (reviewer reads it) and the follow-up is recorded for USR-36 (see Assumptions) |
| 6a | One privilege predicate in three places | `test_privilege_predicates_agree` (table: admin-app role, `is_admin`-permission role, designated role, plain role, ex-designated role after clear → `RoleDefinitionRead.privileged`, `_guard_privileged` 403-or-not for a non-`admin:*` actor, and `user_has_privileged_assignment` of a holder all agree); `test_role_definitions_is_super_admin_flag` |
| 6b | G5 covers holders | `test_disable_holder_needs_wildcard_403` (`PATCH /users/{id}` status), `test_revoke_holder_sessions_needs_wildcard_403` (staff session revocation, `sessions/staff_service.py:54`) |
| 6c | Shared holder helper for USR-39/40 | `test_app_super_admin_access_true_for_live_holder` and `test_app_super_admin_access_false_when` parametrized: no designation, assignment expired, assignment revoked, designation cleared, holder lost `admin:super-admin`, holder disabled, holder unverified, app disabled, `admin` app |
| 7 | Migration up/down works; `alembic check` clean | `tests/test_super_admin_role_migration.py` › `test_upgrade_downgrade_upgrade` (plain `def`, pattern of `tests/test_rbac_migrations.py:81-91`: downgrade to `d4a8e2c6f175` with a designated row present → column gone; upgrade head → column back, NULL); `tests/test_rbac_schema.py` › `test_super_admin_requires_org_mode_check`, `test_super_admin_not_admin_app_check`, `test_super_admin_role_distinct_check`, `test_super_admin_role_same_app_fk` (raw SQL, `IntegrityError`); `uv run alembic check` clean |
| 8 | ADR-0030 and D42 written | Reviewer reads `docs/adr/0030-app-super-admin-role.md`, the D42 row in `docs/identity-architecture.md`, the amendment lines in ADR-0008 / ADR-0013 / ADR-0015 / ADR-0029 and the §5.16 / §8 spec edits |
| — | Everything | `make check` green; `uv run alembic check` clean |

## Ming verification after merge
- Deploy runs the migration step per `docs/deploy.md` (Ming).
- After deploy (OQ90): create `simap:super-admin` with the staff API (app-scope permissions only under Q1's default),
  designate it with `PATCH /api/v1/apps/simap` `{"super_admin_role_id": …}`, then have **another** `admin:super-admin`
  grant it to you (self-assignment is forbidden, Q10; a lone super-admin cannot self-grant). Check the role
  definitions view shows `is_super_admin: true` and `privileged: true`. Nothing is usable by simap until USR-39 ships.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261126_e7b2c4f9a316_usr38_apps_super_admin_role.py` (new) | `revision = "e7b2c4f9a316"`, `down_revision = "d4a8e2c6f175"` (current head, verified: no revision points at it); add column, FK, 3 CHECKs; reverse `downgrade()` | R2 | **High** |
| `app/modules/rbac/models.py` | `App.super_admin_role_id: Mapped[uuid.UUID \| None]` (after :117, comment "USR-38, ADR-0030"); in `__table_args__` (:63-90) `ForeignKeyConstraint(["super_admin_role_id","id"], ["roles.id","roles.app_id"], name="fk_apps_super_admin_role_same_app", use_alter=True)` and 3 `CheckConstraint`s with the migration's names | Model matches migration (`alembic check`) | **High** |
| `app/modules/rbac/authz.py` | Pure `is_designated_super_admin_role(app, role_id) -> bool` next to `permits` (:12); `AuthzService.app_super_admin_access(user_id, app) -> bool` (shared contract, step 9) | One predicate for service, USR-39, USR-40 | **High** |
| `app/modules/rbac/repository.py` | `user_has_privileged_assignment` (:768-782) gains `App.super_admin_role_id == Role.id` in the `or_`; new `is_active_staff_super_admin(user_id)` (reuses `_counting_super_admin` :797-807 with a scalar subquery for the `admin:super-admin` role id); new `app_super_admin_access(user_id, role_id)` (one `EXISTS`: unexpired app-scope assignment of `role_id` AND live staff super-admin) | G5, recipient check, shared helper (R1: SQL only here) | **High** |
| `app/modules/rbac/service.py` | `AppService._update` (:431-546) designation rules; `role_is_privileged` (:1065) new signature; `_definitions` (:1094-1117) `is_super_admin`; `RoleService.delete` (:1249-1254) refusal; `replace_permissions` (:1313, :1332-1347) G2 + Q1 checks; `PermissionService.update` (:980-1002) scope-flip refusal; `_grant_locked` (:1567-1584) grant rules; `_guard_privileged` (:1614-1623) designation | Business rules (R1) | **High** |
| `app/modules/rbac/schemas.py` | `AppUpdate.super_admin_role_id` (+ null-allowed set :78); `AppFields.super_admin_role_id: uuid.UUID \| None = None` (:84-97); `RoleDefinitionRead.is_super_admin: bool` (+ widen `privileged` description :243-248) | Request / read models, additive (R9) | **High** |
| `app/modules/rbac/manifest.py` | Tuple at :162 gains `app.super_admin_role_id` | Manifest cannot touch the designated role | **High** |
| `app/modules/rbac/router.py` | PATCH `/{key}` (:140-151): `super_when` text and 409/422 description name the designation; PATCH role-permissions / role delete error texts mention the designated role | OpenAPI accuracy (api rec 9) | **High** |
| `app/modules/invitations/service.py` | `create` (:179) and `_recheck` (:373) also refuse `role.id == app.super_admin_role_id` | Outside the ticket's file list but needed for the ticket's own grant rule (Q5): otherwise an org member holding the role's permissions invites into it, an email goes out, and accept fails with a 422 from `_grant_locked` | no |
| `app/modules/audit/diff.py` | `APP` tuple (:13-22) gains `"super_admin_role_id"` | R4: set / clear appear in the `app.updated` diff | no |
| `docs/adr/0030-app-super-admin-role.md` (new) | ADR covering USR-38..40 (content in step 10) | Ticket scope reference | no |
| `docs/identity-architecture.md` | D42 row after D41 (:339) | Ticket scope reference | no |
| `docs/adr/0008-admin-api-authentication.md` | One amendment line under G2/G4/G5 (:88-91): "privileged also by designation (ADR-0030)" | ADR-0030 amends it | no |
| `docs/adr/0013-org-scoped-identity.md`, `docs/adr/0015-org-roles-claim-and-permission-scope.md` | Header line "Amended by ADR-0030 (USR-38..40)"; ADR-0015 §Consequences (:53-58) residual-risk line points to ADR-0030 | ADR-0030 amends them | no |
| `docs/adr/0029-bulk-role-assignment.md` | Decision §3 (:23-27): add request-level 422 `super_admin_role` (ADR-0030) | Bulk rule for USR-36 | no |
| `docs/spec-v1.md` | §5.16 Designation / Granting bullets (:415-416) → decided with Ming's gate answers (dated); §8 OQ80–OQ83, OQ90 (:558-561, :568) ticked with the answers; Q1 deviation recorded | Spec stays the source of truth (spec wins; record the decision) | no |
| `tests/test_rbac_super_admin_role.py` (new) | Every test in the table above except the files named below | AC1–2, 4, 6 | no |
| `tests/test_super_admin_role_migration.py` (new) | Up/down/up | AC7 | no |
| `tests/test_rbac_schema.py` | 4 constraint tests | AC7 | no |
| `tests/test_app_manifest_api.py` | 4 manifest tests | AC3 | no |
| `tests/test_openapi_contract.py` | Only if it pins the PATCH `/apps/{key}` or `RoleDefinitionRead` shape | Keep `make check` green | no |

No change to `app/core/**` (no new error class: 422 via `UnprocessableError` details, 409 via `ConflictError`
details, 403 via the existing `AdminWildcardRequiredError` / `SelfAssignmentForbiddenError`), `app/db/models.py`
(`App` already registered), `.env.example`, `users/service.py`, `sessions/staff_service.py` (they call the changed
repository method).

## Data model / migration
`apps.super_admin_role_id uuid NULL` (no default). No new index (database consult rec 4: holder check served by
`uq_role_assignments_user_role_org`; no lookup by this column; re-check EXPLAIN in USR-40).

| Constraint (model `name=` → DB name) | Definition |
|---|---|
| `fk_apps_super_admin_role_same_app` (model `use_alter=True`) | `(super_admin_role_id, id) → roles(id, app_id)`, no `ON DELETE` (a role delete is refused by the DB too); target `uq_roles_id_app_id` (`models.py:155`) |
| `super_admin_requires_org_mode` → `ck_apps_super_admin_requires_org_mode` | `super_admin_role_id IS NULL OR org_mode = 'required'` |
| `super_admin_not_admin_app` → `ck_apps_super_admin_not_admin_app` | `super_admin_role_id IS NULL OR key <> 'admin'` |
| `super_admin_role_distinct` → `ck_apps_super_admin_role_distinct` | `super_admin_role_id IS NULL OR ((default_role_id IS NULL OR super_admin_role_id <> default_role_id) AND (org_owner_role_id IS NULL OR super_admin_role_id <> org_owner_role_id))` |

`ck_apps_default_role_not_org_owner` stays unchanged.

Migration (template `20261117_b4d82f6a1c93_usr06a_org_foundation.py:74-94`): `upgrade()` = `op.add_column` →
`op.create_foreign_key("fk_apps_super_admin_role_same_app", "apps", "roles", ["super_admin_role_id", "id"], ["id",
"app_id"])` → three `op.create_check_constraint(op.f("ck_apps_…"), "apps", …)`. No data step (every row NULL).
`downgrade()` = drop the three CHECKs → drop the FK → drop the column. Docstring: "Lossy: the designation is lost;
assignments of the formerly designated role remain as ordinary app-scope assignments (no preflight)." Hand-review
names so `alembic check` reports no drift.

## API contract
No new route. R3 class 1 (Bearer) for all; no change to the public role catalog (`GET /apps/{key}/roles`, api rec 7).

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| PATCH | `/api/v1/apps/{key}` | `AppUpdate` + `super_admin_role_id: uuid \| null` (absent = unchanged, null = clear) | `AppRead` + `super_admin_role_id` | 200; 401; 403 `permission_denied`, 403 `admin_wildcard_required` (field changes, set or clear; a no-op needs nothing, Q12); 404 app; 409 `role_has_assignments`, 409 `super_admin_role_designated` (`org_mode` → `none` while set); 422 reasons below | `admin:apps:write` + `admin:*` when the designation changes |
| GET | `/api/v1/apps`, `/api/v1/apps/{key}` | — | `super_admin_role_id` added (additive) | unchanged | unchanged |
| GET | `/api/v1/apps/{key}/role-definitions[/{role_id}]` | — | `is_super_admin: bool` added; `privileged` true for the designated role | unchanged | unchanged |
| POST | `/api/v1/users/{user_id}/role-assignments` | unchanged | unchanged | for the designated role: 403 `admin_wildcard_required`; 403 `self_assignment_forbidden`; 422 `org_id_not_allowed` (field `org_id`); 422 `recipient_not_staff_super_admin` (field `user_id`) | `admin:roles:assign` + `admin:*` |
| DELETE | `/api/v1/users/{user_id}/role-assignments/{id}` | — | 204 | designated role: 403 `admin_wildcard_required`, 403 `self_assignment_forbidden` | `admin:roles:assign` + `admin:*` |
| DELETE | `/api/v1/apps/{key}/roles/{role_id}` | — | 204 | designated: 409 `super_admin_role_designated` | unchanged |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | unchanged | unchanged | designated, any change: 403 `admin_wildcard_required`; 409 `role_has_org_permissions`, 409 `wildcard_not_allowed` (Q1) | `admin:roles:write` + `admin:*` on change |
| PATCH | `/api/v1/apps/{key}/permissions/{id}` | unchanged | unchanged | `scope` → `org` while held by the designated role: 409 `super_admin_role_holds_permission` (Q1) | unchanged |
| PUT | `/api/v1/apps/{key}/manifest` | unchanged (`extra="forbid"`) | unchanged | designated key in `roles[]`: 409 `role_not_manifest_managed` (existing reason) | app credential `manifest` |

**Error reasons (Q11, one set; `details[{field, reason}]`):**
- PATCH `/apps/{key}`, field `super_admin_role_id`, **422** (bad input), checked in this order: `admin_app_not_allowed`
  → `unknown_role` (unknown or another app's role) → `org_mode_required` (resulting `org_mode`) → `system_role`
  (`is_system`, e.g. `<key>:member`) → `same_as_default_role` → `same_as_org_owner_role` → `role_has_org_permissions`
  → `wildcard_not_allowed` (holds `<key>:*`). **409** (state): `role_has_assignments` (any row, app or org scope,
  expired included).
- PATCH `/apps/{key}`, fields `default_role_id` / `org_owner_role_id` pointing at the designated role: 422
  `same_as_super_admin_role`.
- PATCH `/apps/{key}` field `org_mode`: 409 `super_admin_role_designated`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_super_admin_role.py` › `test_set_designation_happy`, `test_clear_designation_happy`, `test_set_designation_with_org_mode_in_same_request` | 1a |
| › `test_set_designation_admin_app_422`, `…_org_mode_none_422`, `…_other_app_role_422`, `…_unknown_role_422`, `…_without_wildcard_403`, `test_clear_designation_without_wildcard_403`, `…_without_apps_write_403`, `…_no_token_401`, `…_unknown_app_404` | 1b, 401/403/404 |
| › `…_system_role_422`, `…_default_role_422`, `…_org_owner_role_422`, `test_set_default_role_to_designated_422`, `test_set_org_owner_role_to_designated_422`, `…_role_with_org_permission_422`, `…_role_with_wildcard_422`, `…_role_with_assignments_409` (param: app, org, expired) | 1c, 409/422 |
| › `test_designation_change_is_audited`, `test_noop_designation_needs_no_wildcard`, `test_change_designation_to_other_role` (Q3: old role becomes ordinary, new role must pass the set rules) | 1d, Q3, Q12 |
| › `test_grant_recipient_not_staff_422` (param), `test_grant_to_staff_super_admin_201`, `test_grant_with_expiry_201`, `test_renew_expired_designated_assignment_rechecks_recipient` | 2a, 2b, Q7 |
| › `test_grant_with_org_id_422`, `test_self_grant_403`, `test_grant_without_wildcard_403` | 2c, every refusal order |
| › `test_revoke_designated_without_wildcard_403`, `test_self_revoke_designated_403`, `test_revoke_designated_happy_204` | 2d |
| › `test_invitation_create_designated_role_403`, `test_invitation_accept_after_designation_token_invalid`, `test_system_actor_grant_refused` | 2e, every grant path |
| › `test_delete_designated_role_409`, `test_org_mode_none_while_designated_409`, `test_org_mode_none_after_clear_ok` | 4a, 4b |
| › `test_replace_permissions_designated_needs_wildcard_403`, `…_org_permission_409`, `…_wildcard_409`, `…_unchanged_reput_200`, `test_permission_scope_flip_to_org_held_by_designated_409` | 4c, Q1 |
| › `test_privilege_predicates_agree`, `test_role_definitions_is_super_admin_flag` | 6a (three predicates pinned together) |
| › `test_disable_holder_needs_wildcard_403`, `test_revoke_holder_sessions_needs_wildcard_403` | 6b (G5) |
| › `test_app_super_admin_access_true_for_live_holder`, `test_app_super_admin_access_false_when` (param) | 6c (shared contract) |
| › `test_is_designated_super_admin_role_unit` (plain, no DB: `None` designation, other id, same id) | pure logic first |
| `tests/test_app_manifest_api.py` › `test_manifest_designated_role_409`, `test_manifest_super_admin_role_id_field_422`, `test_manifest_new_permission_does_not_touch_designated_role`, `test_manifest_never_changes_designation` | 3 |
| `tests/test_rbac_schema.py` › `test_super_admin_requires_org_mode_check`, `test_super_admin_not_admin_app_check`, `test_super_admin_role_distinct_check`, `test_super_admin_role_same_app_fk` | 7 (DB constraints) |
| `tests/test_super_admin_role_migration.py` › `test_upgrade_downgrade_upgrade` | 7 (migration up/down) |
| `tests/test_rbac_assignment_batches_api.py` › `test_batch_designated_role_422` | 5 (only if USR-36 is merged first) |

Test notes: a denied request rolls the shared test transaction back (`test_rbac_role_definitions_api.py:165`), so
put the denied call last in a test. "Staff super-admin" fixture = a user with an active, verified account and an
app-scope `admin:super-admin` assignment; the actor for happy paths has `admin:*` and is a different user.

## Approach
1. **Migration + model** (R2): as "Data model". Run `uv run alembic upgrade head`, `downgrade -1`, `upgrade head`,
   `uv run alembic check` on the test DB.
2. **Pure predicate** in `authz.py`: `is_designated_super_admin_role(app, role_id) := app.super_admin_role_id is not
   None and app.super_admin_role_id == role_id`. Every Python check below uses it (no inline comparisons).
3. **One privilege rule, three places (amends ADR-0008 G4/G5):** `role_is_privileged(app: App, role_id, holds_admin)`
   = admin app OR `holds_admin` OR designated; `_definitions` passes `app, r.id`; `_guard_privileged` returns early
   only if none of the three holds, then `require_super(actor)`, and the self ban applies to admin-app roles **and**
   the designated role (`SelfAssignmentForbiddenError("Cannot change your own super-admin roles")`); repository
   `user_has_privileged_assignment` adds `App.super_admin_role_id == Role.id` to the `or_`. Update the docstrings
   ("Mirrors … change all three together") and pin with `test_privilege_predicates_agree`.
4. **Designation in `AppService._update`** (already under `lock_app(key)` via `_locked_app`, :435). After the
   admin-app block (:446-457) and before the owner-role block, when `"super_admin_role_id" in changes`:
   `require_super(actor)` (inside the existing `DENIED_ERRORS` → `record_denied` wrapper in `update`, :414-418); if
   the new value is not None, run the 422 checks in the listed order (use `repo.get_role`, `role.is_system`, resulting
   `default_role_id` / `org_owner_role_id` / `org_mode` from `changes`, `repo.role_has_org_permission`, wildcard =
   `repo.permissions_by_keys(app.id, [f"{app.key}:*"])` held by the role via `role_holds_permission`), then 409
   `role_has_assignments` via `repo.role_has_assignments`. Clearing (null) only needs `admin:*` (Q3). Also: if the
   resulting designation is set and `default_role_id` / `org_owner_role_id` change to it → 422
   `same_as_super_admin_role`; if `changes.get("org_mode") == "none"` and the resulting designation is set → 409
   `super_admin_role_designated` (place next to `org_mode_in_use`, :513-525). The DB CHECKs back every rule;
   `IntegrityError` already maps to 409 at :540-543. Audit: unchanged code path, the `diff.APP` tuple now includes the
   field (R4, same transaction).
5. **Role write guards (RoleService / PermissionService):** `delete` — after the owner check (:1253-1254) refuse the
   designated role 409 `super_admin_role_designated`. `replace_permissions` — extend the `require_super` condition
   (:1313-1316) with `(designated and (added or removed))`; after the existing default/owner checks, for the
   designated role refuse any `found` permission with `scope == org` (409 `role_has_org_permissions`) or key
   `f"{app.key}:*"` (409 `wildcard_not_allowed`), as `ConflictError` with `details[{field: "permission_keys",
   reason}]` (same 409 style as the default-role rule at :1336-1341). `PermissionService.update` — next to :996-1002,
   `changes.get("scope") == "org"` and `role_holds_permission(app.super_admin_role_id, perm.id)` → 409
   `super_admin_role_holds_permission`.
6. **Manifest:** add `app.super_admin_role_id` to the tuple at `manifest.py:162` (existing reason
   `role_not_manifest_managed`). `ManifestPut` is `extra="forbid"` (:382), so the designation cannot be sent; the
   manifest never writes `apps` role columns (`service.py:788`). The member rebuild cannot hit the designated role
   (`<key>:member` is `is_system`, refused at designation).
7. **Grant rules in `_grant_locked`** (single enforcement point for all seven grant paths, security finding 2). At the
   top, when designated: `org_id is not None` → 422 `org_id_not_allowed` (field `org_id`, before
   `org_exists_key_share`); then the existing expiry block (optional, no cap — Q7; the `admin:super-admin`
   "cannot expire" rule is **not** copied); then `_guard_privileged` (403 `admin_wildcard_required`, 403
   `self_assignment_forbidden`); then, only for the designated role, `repo.is_active_staff_super_admin(user_id)` else
   422 `recipient_not_staff_super_admin` (field `user_id`). The check runs before the expired-duplicate renewal, so
   renewals are re-checked. **Lock rule (ADR-0003 §10, ADR-0030):** plain read of the admin app's assignment rows
   under the *target* app's lock; never `lock_app("admin")` from this path (two `apps` row locks in one transaction
   could deadlock with an `admin:super-admin` revoke). The race with a concurrent `admin:super-admin` revoke is
   accepted and closed by the mint-time re-check (USR-39, Q8). 422 refusals are not denied-audited
   (`UnprocessableError` is not in `DENIED_ERRORS`; no `app/core` change); the 403s are, as today.
8. **Invitations (Q5):** `create` (:179) — `or is_designated_super_admin_role(app, role.id)` →
   `InviteRoleForbiddenError` before any email work; `_recheck` (:373) — same → `_token_invalid()`. Why here: the
   designated role must never be granted at org scope, and invitations are the only org-member-driven path to an
   arbitrary role.
9. **Shared contract for USR-39 / USR-40 (USR-38 ships it; the siblings import it, never re-implement it):**
   - `app.modules.rbac.authz.is_designated_super_admin_role(app: App, role_id: uuid.UUID) -> bool` (pure).
   - `RbacRepository.is_active_staff_super_admin(user_id: uuid.UUID) -> bool` — active, unexpired (DB clock),
     app-scope `admin:super-admin`, account `active` and email verified (`_counting_super_admin` semantics).
   - `RbacRepository.app_super_admin_access(user_id: uuid.UUID, role_id: uuid.UUID) -> bool` — one `EXISTS`: an
     app-scope (`org_id IS NULL`), unexpired assignment of `role_id` **and** `is_active_staff_super_admin`.
   - `AuthzService.app_super_admin_access(user_id: uuid.UUID, app: App) -> bool` — False unless `app.key != "admin"`,
     `app.status == active`, `app.org_mode == required`, `app.super_admin_role_id is not None`; then the repository
     call. Plain reads, takes no lock. **This is the holder predicate USR-39 (mint) and USR-40 (picker) must call.**
   - **Not in USR-38:** the "org of the app" predicate (OQ89) — USR-39 ships it (`RbacRepository` method), USR-40
     reuses it. USR-38 does not need it.
10. **ADR-0030 "Per-app super-admin role for org apps"** (Status: Accepted for §1–§6 with Ming's gate answers; §7–§8
    "Accepted in principle, details fixed by USR-39 / USR-40 plans"). Sections: Context (SIM-043, §5.16, owner
    decision: per-app role, never `admin:super-admin` in an app token); §1 Designation by data
    (`apps.super_admin_role_id`, constraints, preconditions, `is_system = false`, Q2); §2 Privileged by designation
    (amends ADR-0008 G2/G4/G5; one predicate, three places); §3 Permission content (Q1 answer; deviation from the
    spec proposal recorded); §4 Grant rules and every grant path (incl. invitations, bulk `super_admin_role`, manifest);
    §5 Lifecycle and expiry (OQ82 b: no cascade revoke, **mandatory** live re-check at mint and picker via the shared
    helper; OQ83); §6 Lock rules (target app lock only, never two `apps` rows, plain read of `admin:super-admin`); §7
    Org-token exception (amends ADR-0013 / ADR-0015: opt-in claim, `org_roles: []`, member path wins, shorter TTL,
    mint audit — names and values per USR-39's gate); §8 Picker (`access` field, per USR-40's gate); Consumer contract
    pointer (`docs/consumers.md`, USR-39); Rejected options (`admin:super-admin` in app tokens; reserved key
    `<app>:super-admin` auto-created; cascade revoke from the `admin` lock path — reverses lock order); Risks (OQ87
    end-user channel blast radius, stale holders, kill switch).
    **D42** row: one paragraph summarising §1–§6 and pointing to ADR-0030 for §7–§8.
11. **Docs:** ADR-0008/0013/0015/0029 amendment lines; spec §5.16 bullets and OQ80–83, OQ90 updated with the dated
    answers (Q1 deviation explicit).
12. `make check`, `uv run alembic check`; impl-log notes the USR-36 / USR-25 hotspots.

Rules: R1 (rules in services, SQL in repository, router text only), R2 (step 1), R3 (no new route, class 1), R4
(`app.updated`, `role_assignment.granted`, denied rows in the same transaction), R5/R11 (no secrets, no emails in
errors or logs), R6 (existing envelope, reasons in `details`), R8 (tests per endpoint behaviour), R9 (additive fields
only; `privileged` widens — noted in the field description).

## Specialist input used
- **software-architect:** rec 1 schema — adopted; rec 2 one predicate in three places — adopted; rec 3 PATCH rules —
  adopted with api's reason names (`system_role` instead of `manifest_managed_role`, 409 instead of 422 for `org_mode`
  to match `org_mode_in_use`); "clearing always allowed" — adopted as Q3 default; rec 4/5 grant order and lock rule
  — adopted; rec 6 — adopted (permission PUT needs `admin:*`, Q1 checks); rec 7 shared helper — adopted (step 9);
  rec 8 bulk `super_admin_role` — adopted; rec 9 ADR content — adopted.
- **database-optimizer:** recs 1–5 (FK, CHECK names, lossy downgrade, no index, lock rules) — adopted; rec 6 reject
  `is_admin` permissions at designation — rejected (the role is privileged by designation; only `<key>:*` is refused,
  Q1); rec 7 helper — adopted under the name `is_designated_super_admin_role`; rec 8 table-driven guard tests —
  adopted (`test_privilege_predicates_agree` + one test per org-owner site).
- **api-platform-engineer:** recs 1, 2, 6, 7, 8, 9 — adopted; rec 1 "`require_super` even for a no-op" — rejected
  (Q12: G1 precedent "a no-op PATCH needs no admin:*", and full-form PATCHes from admin-pan would 403 for non-super
  staff); rec 3 reasons — adopted plus `system_role`, `wildcard_not_allowed`; rec 4 409 reasons — adopted as
  `super_admin_role_designated` (one name for delete and `org_mode`) and existing `role_not_manifest_managed`; rec 5
  `org_id_not_allowed` — adopted; `recipient_not_staff` — renamed `recipient_not_staff_super_admin` (architect,
  security); `designated_role_bulk_forbidden` — renamed `super_admin_role` (ADR-0029 `admin_app_role` style).
- **identity-access-engineer:** rec 1 incl. invitations — adopted (Q5); rec 2 order — adopted (`org_id_not_allowed`
  instead of `app_scope_only`); rec 3 zero assignments — adopted (Q4); rec 4 refuse clear while held — rejected as
  default, offered as Q3 alternative; rec 5 no cascade — adopted; rec 6 optional expiry, no copy of the super-admin
  rule — adopted; rec 7 refuse org / wildcard permissions — adopted (Q1).
- **security-architect:** recs 1, 2, 4, 5, 6, 7 — adopted; rec 3 "409 if pending invitations use the role" —
  rejected (pending invitations are made unusable by `_recheck`, no extra query); squatting mitigation — default
  "no provenance rule" (Q6); OQ83 90-day cap — offered as Q7 alternative; OQ87 items — USR-39 scope.

## Split proposal
None. Estimate: ~220 changed lines of application code + migration, ~450 lines of tests, ~150 lines of docs; one
review cycle. If review needs a smaller diff, slice B = invitations (step 8) + permission scope-flip guard (step 5,
last part) can follow as its own PR in the same ticket.

## Blocking questions for Ming
1. **OQ80/OQ88 — permission content (spec conflict).** Spec §5.16 proposes the role "holds all of the app's
   permissions (org-scope ones included)"; all consultants say org permissions would reach every token of the app via
   the `roles` union (ADR-0015). *Default: refuse `scope = org` permissions and `<app>:*` on the designated role (at
   designation, role PUT and permission scope flip); consumers grant org powers from the USR-39 opt-in claim. ADR-0030
   and the spec record the deviation.* (If you keep the spec text: drop those three checks and Q3 flips to "refuse
   clear while held".)
2. **OQ80 — `is_system`.** Spec proposes `is_system`; architect says keep `false` (protection comes from the
   designation, and staff must be able to edit the role's permissions with `admin:*`). *Default: `is_system = false`.*
3. **Clearing / changing the designation while the role is held.** Architect: always allowed (kill switch: blocks
   every USR-39 mint at once); identity: refuse (409) while assignments exist, because the role then becomes an
   ordinary, unguarded role still held. *Default: always allowed with `admin:*`, audited; changing to another role
   needs the new role to pass every set rule; ADR-0030 tells staff to revoke the former holders.* Safe only with Q1's
   default.
4. **Designating a role that already has assignments.** *Default: 409 `role_has_assignments` (any row: app, org,
   expired).*
5. **Invitations.** The ticket's file list omits `invitations/service.py`, but an org member holding all the role's
   permissions could invite into it today (`service.py:179`, `_recheck` :373). *Default: include — refuse at create
   (403 `InviteRoleForbiddenError`, no email) and at accept (existing invalid-token answer).*
6. **Manifest pre-designation squatting.** A manifest key can create `simap:super-admin` before staff designate it.
   *Default: no provenance rule; the designation preconditions (zero assignments, no org / wildcard permissions,
   staff `admin:*` review) defuse it; ADR-0030 recommends creating the role with the staff API.* Alternative: a
   provenance column (more schema) — not proposed.
7. **OQ83 — expiry.** *Default: optional, no cap (spec proposal).* Alternative: optional but ≤ 90 days (security) →
   422 `expires_too_far`.
8. **OQ82 — lifecycle and counts.** *Default: (b) — no cascade revoke (the `admin:super-admin` revoke / disable path
   holds the `admin` lock; per-app locks after it reverse the lock order); USR-39 mint and USR-40 picker must re-check
   live via `AuthzService.app_super_admin_access`; holders count as ordinary members in `member_count` and appear in
   the users list like any member.*
9. **OQ81 — eligible recipients.** *Default: only accounts with an active, unexpired app-scope `admin:super-admin`
   (active, verified); no lesser staff tier in v1.*
10. **Self-assignment and self-revoke.** *Default: both forbidden for the designated role (like admin-app roles,
    aligned with USR-25). Consequence: a lone super-admin cannot grant it to themself; a second super-admin must.*
11. **Error reasons (one set).** *Default as in "API contract": 422 `admin_app_not_allowed`, `unknown_role`,
    `org_mode_required`, `system_role`, `same_as_default_role`, `same_as_org_owner_role`, `role_has_org_permissions`,
    `wildcard_not_allowed`, `same_as_super_admin_role`, `org_id_not_allowed`, `recipient_not_staff_super_admin`; 409
    `role_has_assignments`, `super_admin_role_designated`, `super_admin_role_holds_permission`, (PUT)
    `role_has_org_permissions` / `wildcard_not_allowed`, (manifest) `role_not_manifest_managed`; bulk 422
    `super_admin_role`; 403 existing `admin_wildcard_required`, `self_assignment_forbidden`.*
12. **`admin:*` for a no-op designation field.** *Default: needed only when the value changes (G1 precedent); a PATCH
    resending the same value needs only `admin:apps:write`.*
13. **OQ90 — rollout.** *Default: P2; designation per app, simap first; no data migration seeds it — staff designate
    after deploy (see "Ming verification").*

## Assumptions
- **Hotspots:** `app/modules/rbac/service.py` (`_grant_locked`, `_guard_privileged`) and `repository.py` are also
  planned by USR-25 (self-assignment ban, BACKLOG), USR-36 (bulk, BACKLOG; ADR-0029 already accepted), USR-37
  (assignment POST errors, BACKLOG) and USR-39 / USR-40 (this batch; they import the step 9 contract). Expect a rebase
  for whichever merges second. If USR-36 merges first, this ticket adds the request-level `super_admin_role` check
  and its test; if this ticket merges first, the ADR-0029 amendment makes it part of USR-36's plan (record as
  follow-up in impl-log).
- `docs/identity-architecture.md` and `docs/spec-v1.md` are edited by USR-39 / USR-40 too (ADR-0030 §7–§8 and D42
  pointers); rebase on docs only.
- No `app/core/**` change: all new refusals reuse `UnprocessableError` / `ConflictError` with `details`, and the
  existing 403 classes. Denied-audit coverage stays as today (403s audited, 422s not).
- The migration is the head after `d4a8e2c6f175`; if another migration lands first, `down_revision` is updated at
  IMPLEMENT (record it).
- Role rename (`PATCH /roles/{id}` name) of the designated role keeps today's rule (`admin:roles:write`; key is
  immutable). Not listed as a guard because it changes no power.
- The public role catalog keeps listing the designated role (its key is needed by consumers, OQ88); the designation
  itself is staff-only (`AppRead`, `RoleDefinitionRead`).
- Join, join approval, provisioning and org create only grant the default or org-owner role, which the DB CHECK keeps
  distinct from the designated role; `_grant_locked` refuses system actors anyway (`require_super`).
- Holding the designated role at app scope is app membership (spec line 416); `effective_roles` needs no change.
