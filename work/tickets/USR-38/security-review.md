# Security review — USR-38 (Attempt 1, appsec-engineer, 2026-11-26)

Saved by the orchestrator (the appsec agent is read-only at REVIEW).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: `git diff 985319c d041d91` against ADR-0030 §1–§6 and CLAUDE.md R3, R4, R5, R11.
Checks: `pytest tests/test_rbac_super_admin_role.py tests/test_super_admin_role_migration.py tests/test_app_manifest_api.py tests/test_rbac_schema.py` → 158 passed; `alembic check` → no drift. Full `make check` left to the reviewer (R8).

## Verified
- **One grant gate.** `RbacRepository.add_assignment` has one caller, `AssignmentService._grant_locked` (`app/modules/rbac/service.py:1720`); all paths reach it: staff `assign` (:1597), CLI bootstrap (:1654, admin app only), provisioning (`auth/provisioning.py:80`), join (`membership/service.py:146`), join approval (`join_requests/service.py:113`), org create (`organizations/auth_service.py:129`), invitation accept (`invitations/service.py:319,336`).
- In `_grant_locked`: designated role + `org_id` refused before the org lookup (:1675-1679); `_guard_privileged` requires `admin:*` and forbids self-grant (:1696, :1733-1750) — system actors for join/org create/invitations are `privileged=False`; `SYSTEM_CLI` only grants `admin:super-admin`. Recipient check `is_active_staff_super_admin` runs before the expired-duplicate lookup, so renewals are re-checked (:1698-1703); it requires an unexpired app-scope assignment held by an active, verified user (`repository.py:802-836`); tests cover no admin role, lesser admin role, expired, disabled, unverified. Join/approval/provisioning/org-create can only grant default or org-owner roles, kept distinct from the designated role by CHECK `super_admin_role_distinct` and service checks (:519-534, :595-605).
- **Invitations:** refused at create (403 `invite_role_forbidden`, before any email; `invitations/service.py:179-184`) and at accept (invalid token, :375-380), covering a role designated after sending (tested).
- **Privilege predicates agree:** `role_is_privileged` (:1148), `_guard_privileged` (:1733), `user_has_privileged_assignment` (`repository.py:768-788`); `test_privilege_predicates_agree`. G5 (disable holder, revoke sessions) needs `admin:*` (tested). Self-revoke refused (:1764, tested).
- **Manifest:** cannot alter or re-permission the designated role (matched by id → `role_not_manifest_managed`, `manifest.py:160-165`), cannot change the designation (`extra="forbid"`), cannot delete roles, cannot change scope (`scope_mismatch`). CLI register sync never sends `super_admin_role_id` / `org_mode` for an existing app (:766-773).
- **Permission content app-scope only, no wildcard:** at designation (:607-616), role PUT (:1438-1448; any change needs `admin:*`, :1409), scope flip to org (:1077-1085). Role keys immutable.
- **Designation:** set/clear needs `admin:*` (:472-475), no-op needs nothing; refusal audited `app.updated` outcome `denied` (:410-417, tested); success audited via `diff.APP` incl. `super_admin_role_id` (`audit/diff.py:22`) in the same transaction (R4). Role delete (:1344) and `org_mode` → none (:535-540) refused while designated (DB CHECK backs `org_mode`).
- **Lock order:** only the target app's lock (`lock_app` with `populate_existing`, `repository.py:97-109`); `role_has_assignments` and `_grant_locked` serialized under it; `admin:super-admin` read is plain; never `lock_app("admin")` in the grant path.
- **Migration** `20261126_e7b2c4f9a316_usr38_apps_super_admin_role.py`: composite same-app FK NO ACTION; three CHECKs; working lossy-documented `downgrade()`; `alembic check` clean.
- **Secrets/PII (R5, R11):** no new log lines; error details are field/reason codes only.

## Findings
🔴 none. 🟡 none.

🟢 Notes / follow-ups (R7):
1. USR-36 bulk route: ADR-0030 §4 requires request-level 422 `super_admin_role` — add as an AC to USR-36 (per-item path already refuses non-staff via `_grant_locked`).
2. Moving the designation A → B (`service.py:472-475`) leaves A's holders with an ordinary role (owner's "no cascade" decision). Consumers must use only the USR-39 opt-in claim, never the role key; optional admin-UI warning when the old role still has assignments.
3. 422 `recipient_not_staff_super_admin` refusals are not audited as denied (`DENIED_ERRORS` covers 403 only, `app/core/errors.py:102`) — consistent; consider `record_denied` in a later audit ticket.
4. Test gap: no test deletes an app with a designated role (same FK pattern as `org_owner_role_id`; low risk).
5. By design: a holder's app token already carries the designated role and its app-scope permissions (ADR-0030 §3, §5); USR-39 must not widen it.
