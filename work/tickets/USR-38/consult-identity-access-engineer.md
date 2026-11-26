# Consult — identity-access-engineer — USR-38 (2026-11-26)

**Findings**
- `rbac/service.py:1614-1623` `_guard_privileged`: privileged = admin app or `is_admin` permission; self-assignment ban (:1622) admin-app only; revoke uses the same guard (:1637).
- `service.py:1065` `role_is_privileged(app_key, holds_admin_permission)` must change with the guard; feeds `privileged` (:1111).
- `repository.py:768-782` `user_has_privileged_assignment` drives G5 (`users/service.py:66,107`, `sessions/staff_service.py:54,81`); unaware of a designated role.
- `service.py:1575-1583` expiry special-cases only `admin:super-admin`; an expired duplicate is renewed in place (:1587-1598) through `_grant_locked`.
- `repository.py:797-807` `_counting_super_admin` = "active staff super-admin" (app scope, unexpired by DB clock, account active, verified).
- **Gap outside the ticket's file list:** `invitations/service.py:173-182` lets an org member invite into any role without `is_admin` permissions whose permissions they hold; same at accept (:372-373).
- USR-36 bulk route not in the code yet.
- ADR-0015 §Consequences (53-58) already names "a staff-granted app-scope role that holds `org` permissions" as residual risk, with "refuse such grants" as follow-up.

**Recommendations**
1. Helper `is_designated_super_admin(app, role_id)` used in: `_guard_privileged` (require `admin:*`, self-assignment ban for grant and revoke, existing `self_assignment_forbidden`); `role_is_privileged` (third param); `user_has_privileged_assignment` (OR `App.super_admin_role_id == Role.id`, G5 still protects a holder after losing `admin:super-admin`); invitation create and accept (`InviteRoleForbiddenError`); role permission PUT (G2, `admin:*`, manifest refused); role delete refused.
2. `_grant_locked` order for the designated role: `org_id` → 422 `app_scope_only` (before org lookups); expiry rule (6); `_guard_privileged` (403); recipient → 422 `recipient_not_super_admin` (plain read, `_counting_super_admin` semantics, no `admin` lock). Renewals checked too; 422 audited as denied.
3. Setting the designation: same app; `org_mode = required`, not `admin`; not default/org-owner (CHECK like `ck_apps_default_role_not_org_owner`); **zero assignments** (else 409 `role_has_assignments`).
4. Clearing or changing the designation refused while active assignments exist (409) — otherwise the role silently becomes ordinary and stays held without guards.
5. OQ82 option (b) only: mint re-check (USR-39) + grant check. No cascade from the `admin:super-admin` revoke/disable path (holds the `admin` lock; per-app locks after it reverse the lock order). Disable already revokes sessions. Follow-up: "stale holders" list/filter.
6. OQ83: allowed, not required — explicit carve-out so the `admin:super-admin` "cannot expire" rule (:1576) is not copied.
7. OQ80/OQ88: refuse `scope = org` permissions and `<app>:*` on the designated role at designation and on every permission PUT (422 `role_has_org_permissions`). `roles` is the D10 union, so every token for that app (including member tokens of their own orgs, no opt-in claim) carries the role. Deviates from spec §5.16 ("holds all of the app's permissions") — record in ADR-0030.

**Risks**
- Any grant path missing the check (invitations, bulk, manifest, future CLI) leaks the role — regression test per path.
- Concurrent revoke race — accepted, mint re-checks.
- XSS / stolen device session on simap (OQ87) — outside this ticket.

**Open questions for Ming (defaults)**
- OQ80/OQ88 org-scope permissions on the designated role — **no**.
- OQ81 lesser staff roles — no.
- OQ82 cascade — no; re-check at grant and mint.
- OQ83 — optional, no cap (90-day cap optional).
- Designation preconditions (zero assignments to set; refuse clear/change while held) — yes.
- OQ90 — per app, simap first.
