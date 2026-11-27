# Consult — security-architect — USR-25 (2026-11-27)

One helper (`_forbid_self`) changes and every grant and revoke path picks it up. The "role made privileged after the caller already holds it" bypass is already closed by G3 — no new refusal needed.

**Findings**
- `rbac/service.py:1826-1832`: `_forbid_self` fires only for admin-app roles and the designated super-admin role; synchronous; never asks `repo.role_has_admin_permission` (repository.py:269) — the gap.
- All grants go through it: single assign (1628 → 1709 → 1766), bulk batch (1910), join approval (`join_requests/service.py:113`), CLI/provisioning (`auth/provisioning.py:80`, service.py:1687); renewals pass 1766. Revoke via `_guard_privileged` (1813, 1985).
- `_require_privileged_grant` (1815) already runs the `is_admin` query; "privileged" lives in three places: `role_is_privileged` (1155), `_require_privileged_grant`, `user_has_privileged_assignment` (repository.py:807).
- Making a held role privileged later is closed for every actor: adding `is_admin` to a role with any assignment → 409 `role_has_assignments` (1462); flipping a permission's `is_admin` false→true while an assigned role holds it → 409 (1093); designating a role with assignments → refused (626). "Remove is_admin, self-assign, add it back" fails at G3.
- No other path grants a privileged role: join approval only the default role (never `is_admin`, 1435 `usable_default_role`); invitations refuse privileged roles (`invitations/service.py:179-184`, `375-380`); org create refuses an admin owner role (`organizations/auth_service.py:109`); org self-removal forbidden (`member_service.py:129`); the batch refuses admin-app and designated roles (1885-1892) but a privileged product role can still be bulk-assigned by an `admin:*` caller — a self entry must fail per item.
- Denied attempts already audited: `SelfAssignmentForbiddenError` in `DENIED_ERRORS` (`core/errors.py:102`); assign (1631), revoke (1993), batch items (1922 `record_denied_in_tx`). Join approval doesn't wrap `DENIED_ERRORS` — can't hit this guard.
- Order: `_require_privileged_grant` before `_forbid_self`; non-`admin:*` caller → `admin_wildcard_required`; `admin:*` caller → `self_assignment_forbidden`; both audited denied.

**Recommendations**
1. `_forbid_self(privileged: bool, user_id, actor)`; compute `privileged = role_is_privileged(app, role.id, holds_admin)` once in `_check_grant_request` / `_guard_privileged` (reuse the existing query), under the app lock; pass into `_grant_user_locked` — one source of truth, one query per batch.
2. Code `self_assignment_forbidden`, neutral message ("Cannot change your own privileged roles"), no reason in `details`.
3. Keep the order (wildcard, then self); pin both outcomes with tests.
4. Tests: admin app; non-admin app with an `is_admin` role (blocked) and without (`<app>:member` self-assign allowed); designated role; org-scoped privileged role; self-renewal of an expired privileged row; self-revoke; batch containing the caller (one failed item + one denied row, others created); regression for "strip is_admin → self-assign → re-add is_admin → 409"; `actor_user_id is None` never trips the guard.
5. Update ADR-0008 lines 93-95 (and ADR-0030 if it restates the rule); ADR-0009:75 unchanged; `role_is_privileged` docstring notes `_forbid_self` mirrors it.
6. One-off read-only query for existing self-granted privileged assignments (`granted_by = user_id` on privileged roles); hand the list to Ming; no migration.

**Risks**
- With one `admin:*` staff member, nobody can grant them a privileged product role or let them step down without a second super-admin.
- Predicate drift silently weakens the guard (tests in 4).
- One extra query per revoke — negligible.

**Open questions for Ming (defaults)**
1. Staff with `admin:roles:assign` may approve their own join request — yes (default role only).
2. Block self-revoke (stepping down) of a privileged role — yes (as decided; another super-admin revokes).
3. Revoke earlier self-grants retroactively — no, report only (6).
4. CLI break-glass for product privileged roles — no; only `grant-super-admin` for the admin app.
