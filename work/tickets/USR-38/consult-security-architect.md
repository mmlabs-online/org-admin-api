# Consult — security-architect — USR-38 (2026-11-26)

**Findings**
1. `rbac/service.py:1614-1623` `_guard_privileged` requires `admin:*` only for admin-app roles or `is_admin` permissions; self-assignment ban admin-app only. A designated role with ordinary/org permissions passes with no `admin:*` check — any staff with role-write could grant it. `role_is_privileged` (:1065) has the same gap.
2. All seven grant paths go through `_grant_locked`: staff (1490), CLI (1547), provisioning (`auth/provisioning.py:80`), join (`membership/service.py:146`), join approval (`join_requests/service.py:113`), invite (`invitations/service.py:317,334`), org create (`organizations/auth_service.py:129`) — the single enforcement point.
3. `invitations/service.py:367-374` `_recheck` refuses only roles with admin permissions: an org owner holding all the designated role's org-scope permissions could invite someone into it at org scope.
4. `rbac/manifest.py:161-164`: "not manifest-managed" = `is_system`, default, org-owner; manifest roles additive (217-219). A manifest key holder can create `simap:super-admin` with any org-scope permissions **before** designation (squatting).
5. `rbac/repository.py:768-782` `user_has_privileged_assignment` (G5: disable/enable, display name, session revocation `sessions/staff_service.py:54,81`) does not cover designated roles.
6. `audit/diff.py:21` app diff whitelist lacks the designation field.
7. ADR-0008:89-93 (G4, self-actions) defines "privileged" only by app or permission content — ADR-0030 must amend it.

**Recommendations**
1. `is_designated(app, role) := app.super_admin_role_id == role.id` in `_guard_privileged`, `role_is_privileged` and `user_has_privileged_assignment` (join on `App.super_admin_role_id == Role.id`), kept in step.
2. `_grant_locked` for the designated role, in order: `org_id` → 422 `org_scope_not_allowed`; self → 403 `self_assignment_forbidden`; recipient without active unexpired app-scope `admin:super-admin` or not active+verified → 422 `recipient_not_staff_super_admin`. Plain read, never `lock_app("admin")`. Covers join, approval, invite, CLI, bulk. Audit denied attempts with `record_denied`.
3. Designation PATCH needs `admin:*` (403). 409 `role_has_assignments` if the role has any assignment (app or org scope); 409 if pending invitations use the role; 422 if `is_system`, default or org-owner (DB CHECKs), `admin` app, `org_mode != required`. Audit `app.updated`, add to `diff.py`.
4. Add `super_admin_role_id` to the manifest exclusion set (`manifest.py:162`); staff edits of its permissions (role PUT, permission delete) need `admin:*` (extends G2); delete refused.
5. OQ88: refuse `scope = org` permissions on the designated role; power from the opt-in claim.
6. Refuse the designated role at invitation create and in `_recheck`.
7. ADR-0030 requires the USR-39 mint to re-check `admin:super-admin`, account status and email verification on every mint (OQ82 b, mandatory).

**Risks**
- OQ87 (High): XSS on an allowed simap origin or a stolen `mm_device` session mints org tokens for every simap org; Origin / `X-MM-App` don't help on the legitimate origin. Largest blast radius after the admin app.
- Rule drift across three places.
- Super-admin org-scope assignments must never count as super-admin access at mint.

**Open questions for Ming (defaults)**
- OQ80 app-scope permissions only — refuse org-scope.
- OQ81 only `admin:super-admin` holders, no support tier in v1.
- OQ82 (b) mandatory mint re-check, no cascade; "holder no longer staff" flag in the users list.
- OQ83 optional, cap 90 days.
- OQ87 recent password re-auth (15 min) before a super-admin mint, ~20 mints/h per account, 1 h TTL, recommend a separate staff account; revisit with staff MFA (OQ3).
- Pre-designation squatting: refuse a manifest-created role unless staff re-PUT its permissions first (alt: role must be staff-created).
