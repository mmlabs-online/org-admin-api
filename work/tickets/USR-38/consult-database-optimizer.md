# Consult — database-optimizer — USR-38 (2026-11-26)

**Findings**
- `apps.org_owner_role_id` (`app/modules/rbac/models.py:75-89, 117`): composite FK `(org_owner_role_id, id) -> roles(id, app_id)` `fk_apps_org_owner_role_same_app`, `use_alter=True`, no ON DELETE (role delete refused by the DB); CHECKs `org_owner_requires_org_mode`, `default_role_not_org_owner`. `roles` has `uq_roles_id_app_id` (:155).
- Migration template: `migrations/versions/20261117_b4d82f6a1c93_usr06a_org_foundation.py:74-94` (`add_column` → `create_foreign_key` → `create_check_constraint(op.f("ck_apps_<name>"))`; downgrade drops checks → FK → column). `d6f03e8b2a15` (:40, :73) preflights data and recreates `default_role_not_org_owner`.
- Alembic head is `d4a8e2c6f175` (usr33) → new `down_revision`.
- `role_assignments` (`models.py:198-209`): `uq_role_assignments_user_role_org (user_id, role_id, org_id)` NULLS NOT DISTINCT, `ix_role_assignments_role_id`, `ix_role_assignments_org_id`; `role_permissions` `ix_role_permissions_permission_id`; `org_entitlements` `ix_org_entitlements_app_id` (`entitlements/models.py:32`); `roles` `uq(app_id, key)`.
- `repo.lock_app` (`rbac/repository.py:97-109`) = `FOR NO KEY UPDATE`; does not block FK checks; every RBAC writer takes it first (ADR-0003 §9/§10).
- `_guard_privileged` (`service.py:1614-1623`) only checks `role_has_admin_permission` or the admin app; self-assignment ban admin-app only (:1622).
- `user_has_privileged_assignment` (`repository.py:768`) ignores designation.
- `org_owner_role_id` special cases to mirror: `rbac/manifest.py:162,172`, `service.py:315, 472-500, 1253, 1334`; `audit/diff.py:21` lists audited app fields.
- Holder check `(user_id, role_id, org_id IS NULL)` is served by the unique constraint.

**Recommendations**
1. `apps.super_admin_role_id uuid NULL`; FK `fk_apps_super_admin_role_same_app (super_admin_role_id, id) -> roles(id, app_id)`, no ON DELETE, `use_alter=True` in the model.
2. CHECKs (`op.f("ck_apps_...")`, mirrored in `__table_args__`): `super_admin_requires_org_mode` (`IS NULL OR org_mode = 'required'`), `super_admin_not_admin_app` (`IS NULL OR key <> 'admin'`), `super_admin_role_distinct` (differs from `default_role_id` and `org_owner_role_id` when set). Leave `default_role_not_org_owner` unchanged.
3. `downgrade()` drops the three CHECKs → FK → column; no preflight; docstring says lossy (designation lost, assignments remain ordinary). Up/down/up test.
4. No new indexes (holder check via the unique constraint + expiry filter; USR-40 orgs-of-app via `org_entitlements(app_id)` or `role_assignments` ⋈ `roles` by `app_id` + `ix_role_assignments_org_id`; re-check EXPLAIN at USR-40). No index on `apps(super_admin_role_id)`.
5. Locking: set, clear, grant, role delete, `org_mode` change, manifest sync all under `lock_app(app.key)`. Read `admin:super-admin` with a plain query inside that lock; never `lock_app("admin")` from the grant path (ADR-0003 §10). Re-read the target role after the lock, as `revoke` does (`service.py:1628-1633`).
6. Setter validation: reject `is_admin` permission (mirror :485), admin app, `org_mode != required`, role of another app, default/org-owner role — 422 with field details. `is_system` false. Audit in the same transaction (R4); add `super_admin_role_id` to `audit/diff.py`.
7. One helper `role_is_designated(app, role_id)` used in `_guard_privileged` and `user_has_privileged_assignment`. Self-assignment banned for the designated role. Recipient check: active app-scope `admin:super-admin` with `expires_at` filtered. Org-scoped grants 422.
8. Manifest-change guard pins the designated role (role/permission change, role delete at `service.py:1253`) — a `role_is_designated` branch at each org-owner site.

**Risks**
- Missing the field in any guard at `manifest.py:162`, `service.py:315/472/1253/1334` silently opens a hole — table-driven tests over every org-owner site.
- A recipient who loses `admin:super-admin` keeps the assignment (OQ82 b) — safety depends on USR-39's mint-time re-check.
- TOCTOU on the unlocked `admin:super-admin` read — covered by the mint-time check.
- `alembic check` drift if model and migration CHECK names differ — use identical names.

**Open questions for Ming (defaults)**
1. OQ80 designated role holds only app-scope permissions — yes.
2. OQ83 expiry — optional (not forbidden).
3. OQ82 — mint-time re-check, no cascade revoke.
4. OQ90 — P2, simap the first and only designated app.
5. Designated-role holders count in the app's member counts — yes, as ordinary members.
