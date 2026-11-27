Status: READY

> READY on the defaults in `## Blocking questions for Ming` (Q1-Q6). The owner's choice of **option (b)** (ticket
> "Notes / answers from owner", 2026-11-19) is binding and is not re-asked. The ticket is **High**
> (`app/modules/rbac/**`), so it stops at PLAN for Ming's approval anyway and is never merged by an agent.
> Batch 2026-11-27-1. **Hotspot:** USR-26 and USR-27 (same batch) also edit `app/modules/rbac/service.py`; this diff
> is confined to the privilege predicate helpers (`_role_is_privileged` new, `_require_privileged_grant`,
> `_check_grant_request`, `_grant_user_locked` first line + one kwarg, `_guard_privileged`, `_forbid_self`) and the
> two call sites that thread the result (`_grant_locked`, `_assign_batch_locked`).

# Plan — USR-25: Scope of `self_assignment_forbidden` beyond the admin app

## Scope summary
Ticket `work/tickets/USR-25/ticket.md`:
- Goal (line 14): "Close (or deliberately accept) a separation-of-duties gap: today `self_assignment_forbidden` applies
  only to admin-app roles, so a staff member with `admin:roles:assign` can grant themselves any non-admin product role,
  and with `admin:*` also privileged product roles."
- Owner answer (line 29, binding): "**option (b)** — forbid self-grant and self-revoke of privileged roles (any role
  holding an `is_admin` permission, plus every admin-app role as today) in every app; non-privileged roles (e.g.
  `<app>:member`) may still be self-assigned."
- AC 2 (line 21): "the 403 code stays `self_assignment_forbidden`; refused attempts are audited as `denied` (existing
  pattern); ADR-0008 records the rule."
- AC 3 (line 22): "The guard applies to every path that grants or revokes on the caller (assignment create/revoke,
  join-request approval if it can grant to self)."
- AC 4 (line 23): "Tests per app type (admin app, non-admin app with and without `is_admin` permissions, org-scoped
  role)."

ADR-0008 amendment ADM-002b, lines 89-92 (the G4 definition of "privileged" this ticket reuses): "granting (including
renewing an expired row) or revoking a privileged role — an admin-app role or one holding an `is_admin` permission
(G4) … A role is also privileged by designation as an app's super-admin role (ADR-0030)." Lines 93-95 (the rule being
widened): "**Self-actions (403, also for super-admins):** `self_assignment_forbidden` for granting or revoking one's own
admin-app roles … To step down, another super-admin revokes the role."

ADR-0030 §4 (line 40-41): "self-grant and self-revoke forbidden (403 `self_assignment_forbidden`)" for the designated
role — kept. ADR-0030 §2 (lines 29-31): "One predicate in three places that change together: `role_is_privileged`
(read model), `AssignmentService._guard_privileged` and `RbacRepository.user_has_privileged_assignment`".

ADR-0029 §3 (lines 30-31): per-user `failed` includes "`self_assignment_forbidden` and any other per-user rule of the
single POST"; §6: "`denied` rows exactly where the single POST writes them". Spec §5.15 line 383: "`failed` — `not_found`
(unknown user) and, once USR-25 lands, `self_assignment_forbidden` (the caller's own privileged role)". Spec line 338:
"`self_assignment_forbidden` (own `admin`-app role; USR-25, owner option (b) of 2026-11-19, widens it to privileged
roles in every app)".

`CLAUDE.md` R1 (service raises `AppError`), R4 (denied rows), R6 (403 in the code list), R7, R8. `work/decisions.md`:
nothing on self-assignment.

**Resulting rule (one sentence, for ADR-0008):** nobody — super-admins included — grants (incl. renewing an expired
row) or revokes a role on themselves when that role is privileged by the G4 definition: a role of the `admin` app, a
role holding any `is_admin` permission (app or org scope), or the app's designated super-admin role; evaluated on the
role's current content under the app lock, at app or org scope alike. Non-privileged roles stay self-assignable.

## Acceptance criteria → verification
New tests are in `tests/test_rbac_self_assignment.py` unless another file is named.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Owner option (b) implemented (binding, 2026-11-19). | Diff: `_forbid_self` refuses iff `privileged and user_id == actor_user_id(actor)`, where `privileged` is the single predicate of Approach step 1. Tests of AC 4. |
| 2a | 403 code stays `self_assignment_forbidden`, `details` null, one generic message. | `test_self_grant_product_admin_role_403` asserts `code == "self_assignment_forbidden"`, `details is None`, `message == "Cannot change your own privileged roles"`; existing `tests/test_admin_guards.py › test_self_grant_admin_role_403` (asserts `details is None`) green unchanged. |
| 2b | Refused attempts audited as `denied` (existing pattern), one row each. | `tests/test_audit_denied.py › test_denied_writes_one_row[assign_self_product]` and `[revoke_self_product]` (new SITES); `test_batch_self_privileged_failed_one_denied_row`; existing `[assign_self]` unchanged. |
| 2c | ADR-0008 records the rule. | Diff of `docs/adr/0008-admin-api-authentication.md` lines 93-95 (Files table). |
| 3 | Guard applies on every grant/revoke path on the caller: single assign (incl. renewal), revoke, bulk batch per item; join approval (default role only, never privileged) still allowed for self; system actors never trip it. | `test_self_renew_expired_product_admin_role_403`, `test_self_revoke_product_admin_role_403`, `test_batch_self_privileged_failed_one_denied_row`, `test_join_approve_own_request_allowed`, `test_forbid_self_system_actor_never_refuses`; existing `tests/test_rbac_super_admin_role.py › test_self_grant_403` / `test_self_revoke_designated_403` green unchanged. |
| 4 | Tests per app type: admin app; non-admin app with an `is_admin` role (refused) and without (allowed); org-scoped role; designated role. | Admin app: `tests/test_admin_guards.py › test_self_grant_admin_role_403`, `test_self_revoke_admin_role_403` (unchanged). Non-admin with `is_admin`: `test_self_grant_product_admin_role_403` (param `perm_scope` = `app`, `org`), `test_admin_guards.py › test_self_grant_product_admin_role_403` (renamed/flipped from `test_self_grant_non_admin_app_role_ok`). Without: `test_self_grant_and_revoke_member_role_ok`, `test_batch_self_non_privileged_role_created` (existing). Org-scoped: `test_self_grant_org_scoped_privileged_role_403`. Designated: existing USR-38 tests. |
| 5 | Check order unchanged: a caller without `admin:*` self-granting a privileged role gets `admin_wildcard_required` (not `self_assignment_forbidden`), audited `denied` with that reason. | `test_self_grant_product_admin_role_without_wildcard_403_wildcard` (asserts code and denied row `changes.reason == "admin_wildcard_required"`); batch: `test_batch_self_privileged_without_wildcard_request_level_403`. |
| 6 | Predicate computed once and passed in (no extra `is_admin` query per user in a batch; no re-query in `_forbid_self`). | `tests/test_rbac_assignment_batches_api.py › test_batch_query_count_bounded` adds: exactly one statement over `role_permissions` with `is_admin` per batch. Diff: `_forbid_self` is synchronous, takes `privileged: bool`, never touches `self.repo`. |
| 7 | G3 still closes the "strip `is_admin` → self-assign → re-add" bypass. | `test_g3_chain_strip_self_assign_readd_409` and `test_g3_chain_flip_is_admin_after_self_assign_409`. |
| 8 | `make check` green. | `make check`. |

## Ming verification after merge
- Run the read-only report query of Approach step 6 (added to `docs/deploy.md`) once against production with the
  one-off `psql` from "Operations"; it lists self-granted privileged assignments (user ids only, no emails). Decide per
  row whether another super-admin revokes it (Q4: no automatic revocation). The orchestrator adds this step to
  `work/ming-todo.md`.
- admin-pan: after the next release a staff member self-assigning a product role that holds an `is_admin` permission
  sees 403 `self_assignment_forbidden` (admin-pan already handles the code for admin-app roles).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/service.py` | (1) `role_is_privileged` docstring (lines 1155-1157): name `AssignmentService._role_is_privileged` as its async mirror, used by both `_require_privileged_grant` and `_forbid_self`. (2) New `AssignmentService._role_is_privileged(app, role_id) -> bool`. (3) `_require_privileged_grant` (1815-1824) returns the `bool`. (4) `_check_grant_request` (1718-1750) returns that `bool` (`-> bool`). (5) `_grant_user_locked` (1752-1766) gets required kw-only `privileged: bool`; line 1766 → `self._forbid_self(privileged, user_id, actor)`. (6) `_grant_locked` (1709-1713) threads it. (7) `_guard_privileged` (1808-1813) computes once, passes it. (8) `_forbid_self` (1826-1832) new signature/body/message. (9) `_assign_batch_locked` (1895-1919) threads it. | AC 1, 3, 5, 6 | **High** (`app/modules/rbac/**`) |
| `app/modules/rbac/assignments_router.py` | `_ASSIGN_403` text (lines 49-53): "… or the caller's own privileged role (self_assignment_forbidden)". Revoke route (95-100): add `errors`-independent 403 description the same way (`responses[403]`), text "Missing permission (permission_denied), admin:* required for a privileged role (admin_wildcard_required), or the caller's own privileged role (self_assignment_forbidden)". Text only. | OpenAPI must describe the widened rule (spec line 392) | **High** |
| `tests/test_rbac_self_assignment.py` | New: the USR-25 matrix (Tests table). | AC 1-5, 7; R8 | no (low) |
| `tests/test_admin_guards.py` | `test_self_grant_non_admin_app_role_ok` (539-547) encodes the closed gap (`stoktok:admin` holds `stoktok:*`, `is_admin`): rename to `test_self_grant_product_admin_role_403` and assert 403 `self_assignment_forbidden` + `_state` unchanged (pattern of 523-536). Intentional behaviour change. | AC 4; old test asserts the bug | no (low) |
| `tests/test_rbac_assignment_batches_api.py` | (a) `test_batch_mid_batch_denied_keeps_other_grants` stub (494-499) to the new signature `(self, privileged: bool, uid, a)`; (b) spy `_check` in `test_batch_reuses_single_grant_logic` (451-453) must `return await check(...)` with `-> bool` (else `privileged` becomes `None`); (c) `test_batch_query_count_bounded` (583-585) adds the one-`is_admin`-query assertion; (d) new `test_batch_self_privileged_failed_one_denied_row`, `test_batch_self_privileged_without_wildcard_request_level_403`. | AC 2b, 3, 5, 6; signature change | no (low) |
| `tests/test_audit_denied.py` | New SITES `assign_self_product` and `revoke_self_product` (after `_assign_self`, line 221; pattern `_assign_org_scoped` 238-261 and `_revoke` 284). | AC 2b | no (low) |
| `docs/adr/0008-admin-api-authentication.md` | Lines 93-95: rule text of "Resulting rule" (Scope summary), "(USR-25, owner option (b), 2026-11-19)"; note check order (wildcard 403 before self 403) and that a role whose `is_admin` permissions were removed is evaluated on current content. | AC 2c | no (low) |
| `docs/adr/0030-app-super-admin-role.md` | §2 lines 30-31: the three places are `role_is_privileged`, `AssignmentService._role_is_privileged` (used by `_require_privileged_grant` and `_forbid_self`) and `user_has_privileged_assignment`. §4 line 40-41 unchanged in meaning; add "(ADR-0008 self-actions rule, USR-25)". | Keep "change together" list true | no (low) |
| `docs/adr/0029-bulk-role-assignment.md` | §3 line 30: `self_assignment_forbidden` "(the caller's own privileged role, ADR-0008, USR-25)". | Cross-reference (identity rec 6) | no (low) |
| `docs/identity-architecture.md` | Line 206: "(nobody changes their own privileged roles or status)". | Stale restatement | no (low) |
| `docs/spec-v1.md` | Line 338: "`self_assignment_forbidden` (own privileged role in any app — admin-app, `is_admin` or designated; USR-25, owner option (b) of 2026-11-19)"; line 383: drop "once USR-25 lands,". | Spec in sync | no (low) |
| `docs/deploy.md` | Line 427: "own privileged roles"; new subsection "USR-25 self-granted privileged assignments (read-only)" after line 477 with the query of Approach step 6. | Q4 report step for Ming | no (low) |

Not touched: `app/modules/rbac/repository.py` (`role_has_admin_permission` reused as-is; `user_has_privileged_assignment`
already has the same predicate), `app/core/errors.py` (`SelfAssignmentForbiddenError` already in `DENIED_ERRORS`, line
104), `app/modules/join_requests/service.py`, invitations, organizations, provisioning, CLI (system actors; consult
findings 8-10), migrations (none).

**Risk tier: High** (`app/modules/rbac/**`). Confirm with
`python3 -I .github/scripts/risk_tier.py --files app/modules/rbac/service.py app/modules/rbac/assignments_router.py tests/test_rbac_self_assignment.py docs/adr/0008-admin-api-authentication.md`
(expected: `high`). `appsec-engineer` review applies (rbac). Never auto-merged.

## Data model / migration
None.

## API contract
No new endpoint, no schema change. Behaviour change (additive refusal, same code) on:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/users/{user_id}/role-assignments` | unchanged | unchanged | + 403 `self_assignment_forbidden` when `user_id` = caller and the role is privileged in any app | `admin:roles:assign`; `admin:*` for privileged (unchanged) |
| DELETE | `/api/v1/users/{user_id}/role-assignments/{assignment_id}` | — | 204 | same | same |
| POST | `/api/v1/apps/{key}/roles/{role_id}/assignment-batches` | unchanged | 200 | caller's own id in a privileged (`is_admin`) role → item `failed` `{code: "self_assignment_forbidden", message: "Cannot change your own privileged roles"}` | unchanged |

Message for every `self_assignment_forbidden`: "Cannot change your own privileged roles" (replaces "Cannot change your own
admin roles" / "… super-admin roles"; Q2). `details`: null. No reason in `details` (security rec 2).

## Tests to write
Fixtures/factories: `make_client`, `make_strict_client`, `db_session`; `tests/rbac_factories.py` (`make_app`,
`make_permission(…, is_admin=, scope=)`, `make_role`, `grant`, `assign`, `make_org`, `make_user`);
`tests/auth_factories.py` (`make_login_user`); join-request factories as imported by `tests/test_join_requests_api.py`
(`make_approval_app`, `make_join_request`) and its `STAFF` permission list. Setups that expect a refused write must
`await db_session.commit()` first (a denied write rolls back the pending work, ADM-004b; pattern
`test_admin_guards.py:528`). "Unchanged" = assignment rows snapshot equal before/after.

| Test (file › name) | Covers |
|---|---|
| `test_rbac_self_assignment.py › test_self_grant_product_admin_role_403` (param `perm_scope` = `app`, `org`; app `org_mode="optional"` for `org`, grant at app scope) | `admin:*` caller, own id, `stoktok` role holding one `is_admin` permission of that scope → 403 `self_assignment_forbidden`, `details` null, exact message; no assignment; one `denied` row `changes == {"reason": "self_assignment_forbidden", "role_key": …}`. Then same request for another user → 201 (only self is refused). |
| `› test_self_grant_product_admin_role_without_wildcard_403_wildcard` | Caller `admin:roles:assign` only → 403 `admin_wildcard_required` (order kept), `denied` row reason `admin_wildcard_required`. |
| `› test_self_renew_expired_product_admin_role_403` | Caller holds an **expired** row of the privileged role → 403 `self_assignment_forbidden`; row's `expires_at`/`granted_by` unchanged. |
| `› test_self_revoke_product_admin_role_403` | Caller holds the privileged role → DELETE own assignment → 403 `self_assignment_forbidden`, row kept, `denied` row on `role_assignment.revoked`; another `admin:*` staff revokes it → 204 (step-down path). |
| `› test_self_grant_and_revoke_member_role_ok` | Role with only non-`is_admin` permissions (and a role with none) → self grant 201 (`granted_by` = caller), self revoke 204. |
| `› test_self_grant_org_scoped_privileged_role_403` | App `org_mode="required"`, org-scope `is_admin` permission on the role, grant with `org_id` → 403 `self_assignment_forbidden`; `denied` row carries `org_id`. Same role to another user in that org → 201. |
| `› test_self_revoke_role_after_is_admin_removed_ok` | Caller holds role R (privileged was granted by another super-admin); R's `is_admin` permission removed via `PUT …/roles/{id}/permissions` — expect **409 `role_has_assignments` is not raised for removal**; then self-revoke → 204 (evaluated on current content, Q3 of identity consult). If removal from an assigned role is itself refused, implementer uses a role whose `is_admin` permission is removed by a direct factory edit and records it in impl-log. |
| `› test_g3_chain_strip_self_assign_readd_409` | `admin:*` staff S; role R holds `is_admin` P, unassigned. S PUT R permissions without P → 200; S self-assigns R → 201 (not privileged now); S PUT R permissions with P → 409 `role_has_assignments` (`details == [{"field": "permission_keys", "reason": "role_has_assignments"}]`); R still not privileged, S holds it without `is_admin`. |
| `› test_g3_chain_flip_is_admin_after_self_assign_409` | R holds non-admin P; S self-assigns R → 201; `PATCH …/permissions/{P}` `{"is_admin": true}` → 409 `role_has_assignments` (field `is_admin`). |
| `› test_join_approve_own_request_allowed` | Staff user with a pending join request on an approval app approves it themselves (`STAFF` perms, and again with `admin:*` on a second app) → 200, default role granted, `granted_by` = self (Q1). |
| `› test_forbid_self_system_actor_never_refuses` | Unit: `AssignmentService(db_session)._forbid_self(True, uuid4(), actor)` for `SYSTEM_CLI`, `SYSTEM_JOIN`, `SYSTEM_ORG_CREATE`, `SYSTEM_ORG_INVITE` → no exception; `_forbid_self(False, …)` with any actor → no exception. |
| `test_rbac_assignment_batches_api.py › test_batch_self_privileged_failed_one_denied_row` | `make_strict_client`, `admin:*` actor; `stoktok` role with `is_admin` permission; `user_ids` = [u1, actor, u2] (unsorted) → 200; statuses `created, failed, created`; actor item `error == {"code": "self_assignment_forbidden", "message": "Cannot change your own privileged roles"}`, `assignment_id`/`expires_at` null; exactly one `denied` row (target `("user", actor.id)`, reason, `role_key`, `request_id == batch_id`); two `granted` rows; no assignment for actor. Replaces the stub as the real-path test (the stub test stays). |
| `› test_batch_self_privileged_without_wildcard_request_level_403` | Caller `admin:roles:assign` only, own id in the batch → request-level 403 `admin_wildcard_required` (order kept), nothing granted. |
| `› test_batch_query_count_bounded` (extended) | `sum("role_permissions" in s and "is_admin" in s for s in ten) == 1`. |
| `› test_batch_mid_batch_denied_keeps_other_grants` (stub updated) | New `_forbid_self` signature. |
| `› test_batch_reuses_single_grant_logic` (spy updated) | Spy returns the `bool`. |
| `test_admin_guards.py › test_self_grant_product_admin_role_403` (was `test_self_grant_non_admin_app_role_ok`) | Flipped: 403, `_state` unchanged. |
| `test_audit_denied.py › test_denied_writes_one_row[assign_self_product]` | POST own id, `stoktok:admin` holding `stoktok:*` (`is_admin`), `SUPER` → 403 `self_assignment_forbidden`, one row, target `("user", actor.id)`, `app_key="stoktok"`, `changes={"reason": "self_assignment_forbidden", "role_key": "stoktok:admin"}`. |
| `test_audit_denied.py › test_denied_writes_one_row[revoke_self_product]` | DELETE own assignment of that role → one row on `role_assignment.revoked`, target `("role_assignment", ra_id)`. |

## Approach
1. **One predicate, computed once** (`rbac/service.py`):
   ```python
   async def _role_is_privileged(self, app: App, role_id: uuid.UUID) -> bool:
       """ADR-0008 G4 via `role_is_privileged`; queries `is_admin` only when the app or the
       designation does not already decide (same queries as today)."""
       if role_is_privileged(app, role_id, holds_admin_permission=False):
           return True
       return await self.repo.role_has_admin_permission(role_id)
   ```
   Admin app / designated role short-circuit without a query, exactly like today's `_require_privileged_grant`, so no
   query count changes. `role_has_admin_permission` (repository.py:269) matches any `is_admin` permission regardless of
   scope ("either scope").
2. `_require_privileged_grant(app, role_id, actor) -> bool`: `privileged = await self._role_is_privileged(...)`; `if
   privileged: require_super(actor)`; `return privileged`. Docstring: "Returns the G4 result for `_forbid_self`."
3. `_check_grant_request(...) -> bool`: last line `return await self._require_privileged_grant(app, role.id, actor)`
   (order of all earlier checks unchanged).
4. `_grant_user_locked(..., existing, privileged: bool)`: first statement `self._forbid_self(privileged, user_id,
   actor)` — still before the designated-recipient check and the existing-row check (renewals covered; held self-target
   in a batch shows `failed`, not `unchanged`, as USR-36 Q9).
5. Callers: `_grant_locked` → `privileged = await self._check_grant_request(...)`, pass `privileged=privileged`;
   `_assign_batch_locked` → same inside the existing `try … except DENIED_ERRORS` (line 1894-1900), pass into the
   per-user call (line 1910-1919). Computed under the app lock; stable for the whole batch because G3 (409 on adding
   `is_admin` to an assigned role / flipping a held permission) and the designation rule need the same app lock.
   `_guard_privileged` → `privileged = await self._require_privileged_grant(app, role_id, actor)`;
   `self._forbid_self(privileged, user_id, actor)`. `revoke`, `assign`, `bootstrap_super_admin`, join approval bodies
   unchanged.
6. `_forbid_self`:
   ```python
   def _forbid_self(self, privileged: bool, user_id: uuid.UUID, actor: Actor) -> None:
       """Nobody grants, renews or revokes their own privileged role (ADR-0008 G4 definition, every
       app, either scope), super-admins included. System actors have no user id: never refused."""
       if privileged and user_id == actor_user_id(actor):
           raise SelfAssignmentForbiddenError("Cannot change your own privileged roles")
   ```
   Stays an instance method (tests monkeypatch it).
7. **Report query** (`docs/deploy.md`, Ming runs it, read-only, prints ids only):
   ```sql
   BEGIN READ ONLY;
   -- USR-25: assignments of privileged roles whose last granter is the holder
   SELECT ra.id, ra.user_id, a.key AS app, r.key AS role, ra.org_id, ra.created_at, ra.expires_at
   FROM role_assignments ra JOIN roles r ON r.id = ra.role_id JOIN apps a ON a.id = r.app_id
   WHERE ra.granted_by = ra.user_id
     AND (a.key = 'admin' OR a.super_admin_role_id = r.id OR EXISTS (
          SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
          WHERE rp.role_id = r.id AND p.is_admin))
   ORDER BY ra.created_at;
   ROLLBACK;
   ```
   Note in the doc: `granted_by` is overwritten on renewal, so a row renewed by someone else no longer shows; the audit
   log (`role_assignment.granted`, `actor_user_id`) is the full history.
8. Router text, docs, tests as listed; `make check`.

Rules: **R1** service raises `SelfAssignmentForbiddenError` (an `AppError`), no HTTP; repository unchanged. **R2** no
schema change. **R3** no route/auth-class change. **R4** denied rows through the existing `record_denied` /
`record_denied_in_tx` calls (no new audit code). **R6** 403, existing code. **R7** confined to the predicate; the
retroactive cleanup is a report (Q4), break-glass not built (Q5). **R8** tests above. **R9** no contract break (an
extra refusal with an existing documented code). **R11** no email or token in messages or rows.

Expected diff: app ~35 lines, tests ~260, docs ~35.

## Specialist input used
- **identity-access-engineer**: adopted rec 1 (one helper `_role_is_privileged` built on `role_is_privileged`; update
  the ADR-0030 "change together" list), rec 2 (computed once, threaded into `_grant_user_locked`), rec 3 (code kept,
  one generic message, order kept), rec 4 (org scope included), rec 5 (whole test list), rec 6 (ADR-0008 line 93 text,
  ADR-0029 §3 cross-reference). Open questions 1-3 → Q1, Q2, Q3 defaults.
- **security-architect**: adopted rec 1 (`_forbid_self(privileged, user_id, actor)`, reuse the existing query), rec 2
  (neutral message, no `details`), rec 3 (pin both outcomes), rec 4 (incl. self-renewal, `actor_user_id is None`, G3
  regression chain), rec 5 (ADR-0008; ADR-0030; ADR-0009:75 unchanged; `role_is_privileged` docstring), rec 6 (report
  query, no migration). Finding "G3 already closes the later-privileged bypass" verified (service.py:1093-1096, 1462-1463;
  existing tests `test_is_admin_flip_on_assigned_role_409`, `test_is_admin_onto_assigned_role_409`) — no new refusal,
  regression tests only. Risk "single super-admin cannot step down" → Q3 default (accepted, as for admin-app roles
  today). Open questions 1-4 → Q1, Q3, Q4, Q5.
- Rejected: nothing. Not adopted as code: a CLI break-glass (Q5).

## Split proposal
None (~35 app lines).

## Blocking questions
None that block — see `## Blocking questions for Ming`; every question has a default and this plan is written on the
defaults. The scope decision itself (option (b)) was made by the owner on 2026-11-19 and is not re-opened.

## Blocking questions for Ming
Confirm or override at the plan-approval gate.
1. **Self-approval of one's own join request.** Staff with the join-request permission may approve their own pending
   request (grants only the app's default role, which can never hold an `is_admin` permission —
   `default_role_unusable`). **Default: allowed** (not a privileged grant).
2. **Message text.** One message for every `self_assignment_forbidden`: "Cannot change your own privileged roles"
   (was "… admin roles" / "… super-admin roles"); code and `details` (null) unchanged. **Default: yes.**
3. **Self-revoke (stepping down) of a privileged product role.** **Default: blocked**, as for admin-app roles today —
   another `admin:*` staff member revokes it; with a single super-admin there is no self step-down (accepted risk). A
   role becomes self-revocable once its `is_admin` permissions are gone (evaluated on current content).
4. **Earlier self-grants.** **Default: no retroactive revocation**; a read-only report query (Approach step 7, in
   `docs/deploy.md`) for Ming to run once and act on per row.
5. **CLI break-glass for product privileged roles.** **Default: none**; only `grant-super-admin` (admin app) exists.
6. **Check order.** Keep `admin_wildcard_required` before `self_assignment_forbidden` (a non-`admin:*` caller never
   learns the self rule). **Default: yes.**

## Assumptions
- **Hotspot `app/modules/rbac/service.py`:** USR-26 (role definitions: assignment count, org-owner flag, 409 reasons)
  and USR-27 (assignments: role/org names) are planned in parallel in batch 2026-11-27-1 and edit the same file
  (likely `RoleService._definitions` near `role_is_privileged`, and assignment read helpers). This diff touches only
  the helpers listed at the top and the `role_is_privileged` docstring; expect a trivial rebase. USR-27 may also edit
  `app/modules/rbac/assignments_router.py` (here: only the `_ASSIGN_403` string and a revoke 403 description) and
  `tests/test_rbac_assignment_batches_api.py` / `tests/test_audit_denied.py` (append-only registries).
- Line numbers are as on `main` after USR-36/USR-37 (service.py `_grant_locked` 1697, `_forbid_self` 1826,
  `_assign_batch_locked` 1863, `revoke` 1973).
- No other test grants a caller their own `is_admin` product role through the API besides
  `test_self_grant_non_admin_app_role_ok`; if `make check` shows another, it is flipped the same way and recorded in
  impl-log (it would be asserting the closed gap).
- A `scope="org"` permission may carry `is_admin=True` (factory sets columns directly; `role_has_admin_permission`
  ignores scope). If a DB constraint forbids it, the org param of `test_self_grant_product_admin_role_403` uses an
  org-scope non-admin permission plus an app-scope `is_admin` one and the implementer records it.
- Removing an `is_admin` permission from an assigned role is allowed (G3 refuses only additions); if not,
  `test_self_revoke_role_after_is_admin_removed_ok` follows its fallback.
- System actors (`SYSTEM_JOIN`, `SYSTEM_ORG_CREATE`, `SYSTEM_ORG_INVITE`, `SYSTEM_CLI`) never grant a privileged
  product role (consult findings 8-10); the guard is a no-op for them by `actor_user_id(actor) is None`.
- End-user org member self-removal (`self_removal_forbidden`, `organizations/member_service.py:129`) is a different
  rule and out of scope.
- Follow-ups (not in this diff, R7): none required; optional later — an audit-log query by actor = target for the
  full self-grant history.
