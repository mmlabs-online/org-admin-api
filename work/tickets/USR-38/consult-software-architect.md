# Consult — software-architect — USR-38 (2026-11-26)

**Findings**
- Template: `apps.org_owner_role_id` (composite FK to the same app + CHECKs, `rbac/models.py:75-89`); PATCH guards `rbac/service.py:471-501`.
- Privilege is decided in three unlinked places: `role_is_privileged` (`service.py:1065`, `RoleRead.privileged` at :1111), `_guard_privileged` (:1614-1623) and the SQL of `user_has_privileged_assignment` (`repository.py:768-782`, feeding G5 in `users/service.py:66,107`, `sessions/staff_service.py:54,81`). All check only "admin app or `is_admin` permission" — a designated role is invisible to all three.
- Self-assignment ban admin-app only (:1622).
- Manifest refuses system, default, owner roles (`rbac/manifest.py:162`) and the member rebuild (:167-174); never writes `apps` role columns.
- Role delete refuses default and owner (`service.py:1251-1254`); `replace_permissions` per-designated-role guards (:1332-1341).
- Expiry refused only for `admin:super-admin` (:1576).
- "Active staff super-admin" (app scope, unexpired, user active and verified) exists as `_counting_super_admin` (`repository.py:797`).
- PATCH guard for the `admin` app at :446-457; no other field needs `admin:*` today.
- USR-36 bulk code not in `app/` (not merged).

**Recommendations**
1. Schema: nullable `apps.super_admin_role_id`; FK `fk_apps_super_admin_role_same_app (super_admin_role_id, id) → roles(id, app_id)` `use_alter`; CHECKs `super_admin_requires_org_mode`, `super_admin_not_admin_app`, NULL-safe differs from `default_role_id` and `org_owner_role_id`. Downgrade drops constraints then column. Add to `audit/diff.py:18-21` (`app.updated` diff). `is_system` false.
2. One privilege predicate: `role_is_privileged(app, role_id, holds_admin)` true also when `role_id == app.super_admin_role_id`; `_guard_privileged` and the SQL (`Role.id == App.super_admin_role_id`) match; a test pins all three. Disabling a holder or revoking their sessions then needs `admin:*`.
3. PATCH `/apps/{key}`: set/clear calls `require_super` (403, denied audit row). 422: `admin` app; `org_mode != required`; role of another app (`unknown_role`); default or owner role; `<key>:member` (`manifest_managed_role`). 409 `role_has_assignments` when the role already has assignments. Clearing always allowed (kill switch). `org_mode` leaving `required` while designated → 422 `super_admin_role_set`.
4. Grant in `_grant_locked` for the designated role: `org_id` → 422 `org_scope_not_allowed`; self → `SelfAssignmentForbiddenError`; recipient must pass `repo.is_active_staff_super_admin(user_id)` (reuses `_counting_super_admin`) else 422 `recipient_not_staff_super_admin`. Plain read under the target app's lock only, no `lock_app("admin")`.
5. Locks (ADR-0030): never two `apps` rows in one transaction; order app → rows (ADR-0003 §10); race with a concurrent revoke closed by the USR-39 mint re-check (OQ82 b). Mint and picker take no app lock.
6. Manifest and role writes: add `app.super_admin_role_id` to the tuple at `manifest.py:162`; role delete 409; `replace_permissions` on the designated role needs `admin:*` and refuses `scope = org` permissions (OQ80).
7. Shared contract: one rbac read `app_super_admin_access(user_id, app) -> bool` — app active, `org_mode = required`, designation set, active app-scope assignment of it, user still active staff super-admin. USR-39 and USR-40 import it. Expose `super_admin_role_id` on `AppRead`, `is_super_admin` on `RoleRead` (additive, R9).
8. Bulk route (if USR-36 merges first): request-level 422 `super_admin_role`, as ADR-0029 does for `admin_app_role`.
9. ADR-0030 + D42: designation by data; privilege by designation (amends ADR-0008 G4/G5); grant rules and lifecycle (mint re-check, no cascade); org-token exception (amends ADR-0013/0015: opt-in claim, `org_roles: []`, member path wins, shorter TTL, mint audit); lock rules; consumer contract; rejected option (`admin:super-admin` in app tokens).

**Risks**
- Drift between the three privilege checks → holder disabled / sessions revoked without `admin:*`.
- Designated role with org permissions leaks them into every org token via `roles` (ADR-0015).
- Clearing the designation leaves its assignments as a plain role — harmless only without org permissions.
- Without the mint re-check, an ex-staff holder keeps access to every org.

**Open questions for Ming (defaults)**
- OQ80 permissions: all the app's permissions (spec) vs refuse `scope = org` — spec wins unless Ming chooses; default refuse org-scope, consumers grant from the opt-in claim.
- Designating a role that already has holders — refuse (409).
- OQ82 auto-revoke — no; mint-time re-check.
- OQ83 expiry — optional, ≤ 90 days if set.
- Self-revoke of the designated role — forbidden, like admin-app roles.
- OQ90 — simap only at first, designated by staff after deploy.
