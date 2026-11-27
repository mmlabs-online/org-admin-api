# Consult — identity-access-engineer — USR-25 (2026-11-27)

**Findings**
- `_forbid_self` (`rbac/service.py:1826-1832`), synchronous: refuses a self-change only for the `admin` app or the designated super-admin role; never checks `is_admin` permission content — the whole gap.
- The privileged predicate exists in three copies to keep in step: `role_is_privileged` (:1155), `_require_privileged_grant` (:1815, via `repo.role_has_admin_permission`, repository.py:269), `repo.user_has_privileged_assignment` (repository.py:807).
- Grants: `_grant_locked` → `_grant_user_locked` (:1766 calls `_forbid_self` before the existing-row check — renewals covered). Revoke: `_guard_privileged` (:1985).
- Batch (:1863): `_check_grant_request` once, `_grant_user_locked` per user in a savepoint; a `DENIED_ERRORS` exception → `record_denied_in_tx` + `failed` item (:1922-1933). `SelfAssignmentForbiddenError` already in `DENIED_ERRORS` (core/errors.py:102) — no new audit code.
- System actors (join membership/service.py:146, invitation accept invitations/service.py:319,336, org create organizations/auth_service.py:129, provisioning auth/provisioning.py:80) have `actor_user_id` None (rbac/actor.py:40) — self check never fires; roles already non-privileged (invitations :180/:377, org owner auth_service.py:109, default role service.py:1435, :526-540).
- Join approval (join_requests/service.py:113) passes the staff actor, grants only the default role (never privileged) — unchanged, covered automatically.
- `bootstrap_super_admin` (:1687) runs as `SYSTEM_CLI` (no user id) — unaffected.
- End-user org member removal (organizations/member_service.py:129) has its own `self_removal_forbidden` — outside this rule.
- Order: `require_super` before `_forbid_self` — a caller without `admin:*` self-granting a privileged role gets `admin_wildcard_required`; only `admin:*` callers reach `self_assignment_forbidden` (as today on the admin app).

**Recommendations**
1. One helper `async _role_is_privileged(app, role_id) -> bool` = `role_is_privileged(app, role_id, await repo.role_has_admin_permission(role_id))`, used by `_require_privileged_grant` and `_forbid_self`. Rule: admin app OR any `is_admin` permission (either scope) OR designated super-admin role. Update the ADR-0030 "change all together" comments.
2. Pass the privilege result into `_forbid_self` (no re-query); in the batch compute once in `_check_grant_request` / `_assign_batch_locked` and thread into `_grant_user_locked` — stable under the app lock (adding `is_admin` to an assigned role is already 409).
3. Keep code `self_assignment_forbidden`, one generic message ("Cannot change your own privileged roles"); keep the check order; document it.
4. Apply with or without `org_id` (org-scoped role with an org-scope `is_admin` permission is privileged).
5. Tests: admin app grant/revoke unchanged; non-admin app role with `is_admin`: self grant, renew, revoke → 403 + `denied` row, other target succeeds; plain member role self grant/revoke still succeed; designated role refused; org-scoped privileged role refused; batch: super-admin includes themself → `failed self_assignment_forbidden` + exactly one denied row, others `created`; caller without `admin:*` → request-level `admin_wildcard_required`; join approval of own request still allowed.
6. ADR-0008 line 93: "one's own admin-app roles" → "one's own privileged roles (G4 definition, every app, either scope)"; cross-reference in ADR-0029 §3.

**Risks**
- Predicate copies drifting → inconsistent refusals (1 reduces it to one Python function + the SQL mirror).
- Per-user admin-permission lookups in a batch (2 avoids it).
- Reordering checks changes the code non-`admin:*` staff see — don't.

**Open questions for Ming (defaults)**
1. Staff may approve their own join request (default role only) — yes.
2. Message "admin roles" → "privileged roles", code unchanged — yes.
3. A role becomes self-revocable once its `is_admin` permissions are removed — yes (evaluated on current content).
