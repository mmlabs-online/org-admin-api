# Security review — USR-25 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only — the reviewer ran the suite).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit 502de6d (diff `c8927c6..502de6d`).

## Verified
1. **One predicate:** `AssignmentService._role_is_privileged` (`rbac/service.py:1828-1833`) = `role_is_privileged(app, role_id, False)` (1155-1163) else `repo.role_has_admin_permission`; `_require_privileged_grant` (1835-1841) and `_forbid_self` (1843-1847) share the result; matches `user_has_privileged_assignment` (`repository.py:807-827`, which also counts expired rows — correct for G5).
2. **Every path guarded:** single POST (`_grant_locked` → `_check_grant_request` → `_grant_user_locked`, `_forbid_self` at 1779 before both RENEWED 1795 and CREATED; renewal test `tests/test_rbac_self_assignment.py:121`); DELETE (`_guard_privileged` 1821-1826, 2001, current content under the app lock, audited via `DENIED_ERRORS`); batch (admin-app/designated refused first 1897-1906; `privileged` once at 1910; self row fails in its savepoint with `record_denied_in_tx`; non-`admin:*` → request-level refusal with per-user denied rows); join approval (`join_requests/service.py:113`, staff actor, guard applies but can't fire — `usable_default_role` refuses `is_admin` roles `membership/service.py:58-66`, default/owner never designated `service.py:530-536, 602-607`; test :248); open join, invitations, org create, provisioning, CLI bootstrap use system actors (`actor_user_id` None, `rbac/actor.py:40-41`) — intended, test :262, privileged roles still refused by `require_super`; org member removal (`member_service.py:132-134`) already refuses self-removal. No other `RoleAssignment` writer found.
3. **Later-privileged bypass closed by existing guards:** `is_admin` permission onto an assigned role → 409 (1463); flip a permission to `is_admin` while assigned → 409 (1093); designate a role with assignments → refused (625); G3 chain tests (:209, :228); un-privileging then self-revoke needs `admin:*` (1418, test :192).
4. **`privileged` cannot go stale:** computed after `lock_app` / `_locked_app`; every role-permission, permission and designation change takes the same lock (ADR-0003 §10); the only lock-free batch path (`admin` app) is refused before writes.
5. **Org-scoped privileged roles covered:** `role_has_admin_permission` ignores scope; tests :84 (parametrized app/org) and :175.
6. **Audited:** `SelfAssignmentForbiddenError` in `DENIED_ERRORS` (`core/errors.py:102-108`); new sites `assign_self_product`, `revoke_self_product` in `tests/test_audit_denied.py`.
7. **No leak:** the new message is only reachable after `require_super` (caller already `admin:*`); non-`admin:*` callers get `admin_wildcard_required` as before; no email/token.
8. **OpenAPI:** `_REVOKE_KWARGS["responses"][403]` mutates a fresh dict per `require_permission` call (`admin_auth/deps.py:126`) — same pattern as `_ASSIGN_KWARGS`.

## Findings (all 🟢)
- Stale module docstring `rbac/assignments_router.py:3-4` ("their own admin-app roles") — update next time the file is touched.
- Earlier self-grants stay; `docs/deploy.md` read-only query may miss rows because `granted_by` is overwritten on renewal (doc points to the audit log) — Ming step (M-9).
- Check order on revoke documented in ADR-0008, consistent across POST, DELETE, batch.

## Follow-ups (out of scope)
- M-9 (already in `work/ming-todo.md`): run the query; another super-admin revokes unwanted rows.
- Join approval has no denied audit for `self_assignment_forbidden` (unreachable today); add the `DENIED_ERRORS` handler if the default-role invariant ever loosens.
