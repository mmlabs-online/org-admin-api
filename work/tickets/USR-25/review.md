## Attempt 1 — 2026-11-27
Verdict: PASS

Scope reviewed: commit 502de6d (diff `c8927c6..502de6d`); HEAD 2cc0dee differs from 502de6d only in
`work/backlog.md` and `work/tickets/USR-25/ticket.md` (state field).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations`: All checks passed.
- `uv run ruff format --check app tests migrations`: 449 files already formatted.
- `uv run mypy app`: Success, no issues in 216 source files.
- `uv run pytest -q` (full suite, local Postgres `org_admin_test`): **4514 passed**, 2 warnings (pty/forkpty DeprecationWarning, pre-existing), 853.61 s. 0 failed, 0 errors. Matches impl-log (4514 passed).
- `uv run alembic check` (local dev DB `localhost:5432/org_admin`, config default): No new upgrade operations detected.
- `python3 -I .github/scripts/risk_tier.py --base origin/main`: `high` (as planned: `app/modules/rbac/**`). Never auto-merged; Ming merges.
- Extra: the `docs/deploy.md` report query ran on the local dev DB inside `BEGIN READ ONLY … ROLLBACK`: valid SQL, 0 rows.

### Acceptance criteria
- [x] **AC 1 — owner option (b), one privileged predicate.** `AssignmentService._role_is_privileged` (`app/modules/rbac/service.py:1828-1833`) = `role_is_privileged(app, role_id, holds_admin_permission=False)` (admin app or designated role, no query) else `repo.role_has_admin_permission(role_id)` (any `is_admin` permission, scope ignored). `_require_privileged_grant` (1835-1841) computes it once and returns it; `_forbid_self` (1843-1847) is synchronous, takes `privileged: bool`, never touches `self.repo`, refuses iff `privileged and user_id == actor_user_id(actor)`. The `role_is_privileged` docstring (1155-1158) and ADR-0030 §2 "change together" list name the new helper.
- [x] **Computed once per request (batch reuse).** Single POST: `_grant_locked` 1710-1724 threads `privileged` from `_check_grant_request` (1762 returns it) into `_grant_user_locked` (1779). Batch: `_assign_batch_locked` 1910 computes once inside the existing `DENIED_ERRORS` handler, passes `privileged=privileged` per user (1925-1934). Evidence: `tests/test_rbac_assignment_batches_api.py:638` asserts exactly one `role_permissions`/`is_admin` statement for a 10-user batch; green.
- [x] **Check order unchanged.** `_require_privileged_grant` (→ `require_super`) is the last step of `_check_grant_request` and runs before `_forbid_self` in `_grant_user_locked`; on revoke `_guard_privileged` (1821-1826) calls `require_super` first. Tests: `tests/test_rbac_self_assignment.py:107` (403 `admin_wildcard_required`, denied row reason `admin_wildcard_required`), batch `tests/test_rbac_assignment_batches_api.py:561` (request-level 403 `admin_wildcard_required`, nothing granted).
- [x] **Code unchanged, new message, details null.** `SelfAssignmentForbiddenError("Cannot change your own privileged roles")` (service.py:1847); `tests/test_rbac_self_assignment.py:85-104` asserts code, `details is None`, exact message; batch item error asserted at `tests/test_rbac_assignment_batches_api.py:525-558`. No test or code still carries the old messages (grep).
- [x] **Every grant/revoke/renew path covered.** All grant paths go through `_grant_locked` → `_grant_user_locked` (`_forbid_self` at 1779, before the designated-recipient and existing-row checks, so renewal is covered): single POST, batch, join approval (`join_requests/service.py:113`), open join, invitations, org create, provisioning, CLI. Revoke: `revoke` → `_guard_privileged` (service.py:2001). Tests: grant `:85`, renewal of an expired row `:121` (row id/expiry/granted_by unchanged), revoke `:137` (row kept, then a peer super-admin revokes → 204), batch `test_batch_self_privileged_failed_one_denied_row`.
- [x] **System actors unaffected.** `actor_user_id` is None for system actors; unit test `tests/test_rbac_self_assignment.py:262` (`SYSTEM_CLI`, `SYSTEM_JOIN`, `SYSTEM_ORG_CREATE`, `SYSTEM_ORG_INVITE`, both `privileged` values).
- [x] **Join self-approval still allowed** (Q1 default). `tests/test_rbac_self_assignment.py:248` (leaf staff perms and `admin:*`, two apps → 200, `granted_by` = self). The default role can never hold `is_admin` (`usable_default_role`), so the guard cannot fire there.
- [x] **Audit `denied` rows.** Single: `tests/test_audit_denied.py` sites `assign_self_product` (target user, `app_key="stoktok"`, `changes={"reason": "self_assignment_forbidden", "role_key": "stoktok:admin"}`) and `revoke_self_product` (target `role_assignment`, action `role_assignment.revoked`); also `tests/test_rbac_self_assignment.py:101-104`, `:151-152`. Batch item: exactly one denied row with `request_id == batch_id`, target `("user", actor)`, plus two granted rows (`test_rbac_assignment_batches_api.py:546-555`). No new audit code: `SelfAssignmentForbiddenError` already in `DENIED_ERRORS`.
- [x] **G3 regression chain.** `tests/test_rbac_self_assignment.py:209` (strip `is_admin` → self-assign 201 → re-add → 409 `role_has_assignments`, `details[0].field == "permission_keys"`) and `:228` (self-assign non-admin role → flip `is_admin` → 409, field `is_admin`). Self-revoke after `is_admin` removal allowed (Q3): `:192`.
- [x] **Tests per app type** (ticket AC 4). Admin app: `tests/test_admin_guards.py:523` / `:552` (unchanged, green). Non-admin app with `is_admin`: `tests/test_rbac_self_assignment.py:85` (param app/org), `tests/test_admin_guards.py:539` (flipped from the test that encoded the gap). Without `is_admin`: `tests/test_rbac_self_assignment.py:157` (role with a plain permission and an empty role: self grant 201, self revoke 204). Designated role: `tests/test_rbac_super_admin_role.py:440`, `:483` (unchanged, green). **Org-scoped role: `tests/test_rbac_self_assignment.py:175`** — see "Deviation" below.
- [x] **Docs.** ADR-0008 self-actions rule rewritten (`docs/adr/0008-admin-api-authentication.md:93-99`: every app, app or org scope, renewal included, current content under the lock, check order, step-down by another super-admin). ADR-0029 §3 cross-reference (line 30); ADR-0030 §2 change-together list and §4 cross-reference; `docs/identity-architecture.md:206`; `docs/spec-v1.md:338`, `:383`. `docs/deploy.md:479-498` read-only report query (`BEGIN READ ONLY … ROLLBACK`, ids/keys only, no emails; notes that `granted_by` is overwritten on renewal and points to the audit log). Ming step M-9 already in `work/ming-todo.md:19`.
- [x] **OpenAPI.** Assign 403 text and a new revoke 403 description (`app/modules/rbac/assignments_router.py:49-52`, `91-97`); `require_permission` builds a fresh `responses` dict per call (`app/modules/admin_auth/deps.py:126`), so mutating `_REVOKE_KWARGS["responses"][403]` does not leak to other routes.
- [x] **`make check` equivalent green** (above).

**Deviation judged (org-scoped role).** The plan's `perm_scope=org` param asked for `org_mode="optional"`; that value does not exist (`OrgMode` = `none|required`, `app/modules/rbac/models.py:49-51`), so the implementer used `org_mode="required"` with an app-scope grant, and recorded it in impl-log. That param on its own is not an org-scoped grant. But the separate plan test `test_self_grant_org_scoped_privileged_role_403` (`tests/test_rbac_self_assignment.py:175-189`) **is** one: app `org_mode="required"`, a role holding a `scope="org"`, `is_admin=True` permission, granted with `org_id` → 403 `self_assignment_forbidden`; the same body for another user → 201; the denied row carries `org_id`. So the "org-scoped role" part of AC 4 is met. The param deviation is justified and harmless (it still checks that an org-scope `is_admin` permission makes the role privileged). Not a 🔴 and not a 🟡.

### Issues
None (no 🔴, no 🟡).

### Rules (CLAUDE.md)
No violations found. R1: the service raises `SelfAssignmentForbiddenError` (an `AppError`); the router changes only OpenAPI text; repository unchanged. R2: no schema change, `alembic check` clean. R3: no route or auth-class change. R4: denied rows through the existing `record_denied` / `record_denied_in_tx`. R5/R11: no secrets, emails or tokens in the message, details or rows. R6: existing 403 code, `details` null. R7: every changed file is in the plan's file list; impl-log "Out-of-plan files: None" confirmed. R8: suite green; new behaviour tested (happy path, refusal, order, audit). R9: no contract break (an extra refusal with an existing documented code).

### Blockers from review agents
None. `security-review.md` (appsec-engineer, Attempt 1): PASS, no 🔴, no 🟡.

### Nits (do not block)
- `app/modules/rbac/assignments_router.py:3-4` module docstring still says "nobody grants or revokes their own admin-app roles"; should say "privileged roles" (also raised by appsec as 🟢).
- `tests/test_rbac_self_assignment.py:175-189` does not assert that the caller holds no row afterwards or what `changes` the denied row has (the `:85` test does both); add `assert await _rows(db_session, me_id) == []` next time the file is touched.
- `tests/test_rbac_self_assignment.py:85` param `org`: `org_mode="required"` is not needed for an app-scope grant of an org-scope permission and makes the test look like an org-scoped grant; `org_mode="none"` would test the same predicate more clearly (or add an `ids=` note).
- `tests/test_rbac_assignment_batches_api.py:561-576` checks only the assignment count; a denied-row assertion (reason `admin_wildcard_required`) would mirror the single-POST test (the `batch_wildcard` site in `tests/test_audit_denied.py` already covers the row).
- `docs/adr/0029-bulk-role-assignment.md:30` and `docs/adr/0030-app-super-admin-role.md:42` lines are now longer than the surrounding wrap width.

### Follow-ups (out of scope)
- M-9 (`work/ming-todo.md:19`): Ming runs the `docs/deploy.md` USR-25 report query once on production after deploy and decides per row (no automatic revocation, Q4).
- Join approval has no `DENIED_ERRORS` handler for `self_assignment_forbidden` (unreachable today because default roles cannot hold `is_admin`); add one if that invariant ever loosens (from appsec).
- Optional: an audit-log query (actor = target on `role_assignment.granted`) for the full self-grant history, since `granted_by` is overwritten on renewal.

### Ming verification after merge (carry to PR)
- Run the read-only report query in `docs/deploy.md` ("USR-25 self-granted privileged assignments") once against production; another super-admin revokes unwanted rows.
- admin-pan: after the release, a staff member self-assigning a product role that holds an `is_admin` permission sees 403 `self_assignment_forbidden` with message "Cannot change your own privileged roles".
