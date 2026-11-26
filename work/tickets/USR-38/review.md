## Attempt 1 — 2026-11-26
Verdict: PASS

Reviewed: commit d041d91 (diff `985319c..d041d91`, 25 files, +1423 / -34), branch `claude/festive-volta-rikup7`.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed. (The 13 known errors in kit-managed `.github/scripts/risk_tier.py` are out of scope.)
- `uv run ruff format --check app tests migrations` — 441 files already formatted.
- `uv run mypy app` — Success: no issues found in 216 source files.
- `uv run pytest -q` (Postgres `org_admin_test`) — **4320 passed**, 2 warnings, 0 failed (813 s). My first full run had 2 failures (`test_app_org_members.py::test_success_writes_one_audit_row_counts_only`, `test_audit_coverage.py::test_failure_writes_no_row[POST …/role-assignments]`) with `relation "apps" does not exist` / `column "super_admin_role_id" … does not exist`. Another process (the parallel appsec run) was using the same test DB at the time. A targeted re-run of those two files plus every USR-38 test file gave 275 passed, and a clean second full run was green.
- `alembic upgrade head` (local dev DB `org_admin` only) — at `e7b2c4f9a316 (head)`, single head; `alembic check` — "No new upgrade operations detected."
- `python3 -I .github/scripts/risk_tier.py --base 985319c` — **high** (`app/modules/rbac/**`, `migrations/**`). This PR is never merged by an agent.
- Mutation probe (scratch copy outside the repo, not committed): I removed the `_recheck` designation guard (`app/modules/invitations/service.py:378`). `test_invitation_accept_after_designation_token_invalid` then **fails** on `row.accepted_at is None`, so the test does detect the guard. With the guard in place, the real answer is `422 token_invalid`. I also confirmed `DELETE /api/v1/apps/{key}` of a designated app with no assignments answers 204, so the FK does not block app delete.

### Acceptance criteria
- [x] Staff with `admin:*` can set / clear the designation. Refused for the `admin` app, `org_mode != required`, another app's role, and without `admin:*` (403). The change is audited. Evidence: `service.py:472-475`, `:576-620`; tests `test_set_designation_happy` (:144), `test_clear_designation_happy` (:152), `test_set_designation_with_org_mode_in_same_request` (:161), `…_admin_app_422` (:173), `…_org_mode_none_422` (:179), `…_other_app_role_422` (:191), `…_unknown_role_422` (:199), `…_unknown_app_404` (:203), `…_no_token_401` (:208), `…_without_apps_write_403` (:214), `…_without_wildcard_403` with a denied audit row (:222), `test_clear_designation_without_wildcard_403` (:232), 1c rules (:241-312, assignments parametrized app/org/expired), `test_designation_change_is_audited` (:315; `diff.py:22`), `test_noop_designation_needs_no_wildcard` (:326), `test_change_designation_to_other_role` (:336).
- [x] Granting to an account without an active `admin:super-admin` is refused. Granting to a staff super-admin succeeds and writes `role_assignment.granted`. Org-scoped grants and self-assignment are refused. Evidence: `service.py:1675-1703`, `:1739-1750`; `test_grant_recipient_not_staff_422` (:401, 5 cases, nothing written), `test_grant_to_staff_super_admin_201` (:357, one audit row), `test_grant_with_expiry_201` (:368), `test_renew_expired_designated_assignment_rechecks_recipient` (:419), `test_grant_with_org_id_422` (:429, known and unknown org id), `test_self_grant_403` (:440, denied row), `test_grant_without_wildcard_403` (:451, no staff-status oracle). Revoke: `:464-490`. Other grant paths: `test_system_actor_grant_refused` (:496), `test_invitation_create_designated_role_403` (:504, no email), `test_invitation_accept_after_designation_token_invalid` (:530).
- [x] The manifest sync cannot create, alter, delete or re-permission the designated role, and cannot change the designation. Evidence: `manifest.py:163`; `tests/test_app_manifest_api.py` › `test_manifest_designated_role_409`, `test_manifest_super_admin_role_id_field_422`, `test_manifest_new_permission_does_not_touch_designated_role`, `test_manifest_never_changes_designation`. The manifest cannot flip a permission's scope (`scope_mismatch`, `manifest.py:151`), and the member rebuild only touches `<key>:member` (is_system, refused at designation).
- [x] The designated role cannot be deleted, and `org_mode` cannot leave `required` while designated. Evidence: `service.py:1344-1348`, `:534-540`; `test_delete_designated_role_409` (:569), `test_org_mode_none_while_designated_409` (:577), `test_org_mode_none_after_clear_ok` (:584). The DB also refuses the delete (NO ACTION FK).
- [x] Bulk route (USR-36, if merged): USR-36 is **not merged**, so this criterion is conditional. The plan (AC5, Assumptions) allows the ADR-0029 amendment plus a recorded follow-up. Evidence: `docs/adr/0029-bulk-role-assignment.md` Decision §3 now lists 422 `super_admin_role` ("USR-36 implements it"); the follow-up is in impl-log.
- [x] Migration up/down works, `alembic check` is clean, and ADR-0030 and D42 are written. Evidence: `tests/test_super_admin_role_migration.py::test_upgrade_downgrade_upgrade` (downgrade with a designated row present, then upgrade back to NULL), 4 constraint tests in `tests/test_rbac_schema.py`, my `alembic check` run, `docs/adr/0030-app-super-admin-role.md` (§1–§8, rejected options, risks), D42 row in `docs/identity-architecture.md`, amendment lines in ADR-0008/0013/0015/0029, and spec §5.16 plus OQ80–83 and OQ90 ticked with the Q1 deviation recorded.
- [x] Plan extras: 4c role PUT / scope flip (`service.py:1409`, `:1438-1448`, `:1077-1085`; tests `:597-639`). 6a one predicate in three places (`service.py:1148-1155`, `:1739`, `repository.py:784`; `test_privilege_predicates_agree` :645, `test_role_definitions_is_super_admin_flag` :681). 6b G5 (`:691`, `:702`). 6c shared helper (`authz.py:27`, `:77`; `repository.py:825`, `:838`; tests `:716-776`, 9 false cases plus the admin app). Pure unit test (`:132`).

### Deviations stated in impl-log (judged)
1. **No bulk code or `test_batch_designated_role_422`.** Accepted. USR-36 is unmerged and the plan's AC5 explicitly allows the amendment-plus-follow-up route.
2. **Out-of-plan exact-key test edits** (`tests/test_rbac_apps_api.py` APP_READ_KEYS, `tests/test_rbac_role_definitions_api.py` KEYS). Accepted. These are one-line additions forced by the additive fields (R9) and are listed under Out-of-plan files.
3. **Three service-level tests** (`test_revoke_holder_sessions_needs_wildcard_403`, `test_privilege_predicates_agree`, `test_system_actor_grant_refused`). Accepted. The session routes already require `admin:*`, so HTTP cannot reach the G5 branch, and a denied HTTP call rolls back the shared transaction. The system-actor test was planned as a direct `_grant_locked` call.
4. **Role PUT tests use `admin:roles:manage`.** Accepted. That is the real route permission (`router.py:215`, `:233`, `:261`); the plan's `admin:roles:write` was a naming slip. No code change.
5. **Invitation accept test only asserts a 4xx.** Accepted, with Issue 1 (🟡). The pending-row assertion does catch removal of the guard (mutation probe), but the status set also contains the fallback `422 unprocessable/org_id_not_allowed` from `_grant_locked`.
6. **Plain `ConflictError` for `role_has_assignments`.** Accepted. It matches the plan's API contract (409, `details[{field: super_admin_role_id, reason: role_has_assignments}]`, `service.py:615-619`), and `RoleHasAssignmentsError`'s message would be misleading.

### Issues
#### Issue 1 — 🟡 should-fix — test precision
- **Where**: `tests/test_rbac_super_admin_role.py:560`
- **Expected**: The test pins the existing invalid-token answer, as the plan says ("accept answers the existing invalid-token error").
- **Actual**: `assert resp.status_code in (400, 401, 404, 410, 422)`. The real answer is `422 {"code": "token_invalid", "details": {}}` (verified). Without the `_recheck` guard the endpoint answers `422 unprocessable` (`org_id_not_allowed`), and the status assertion alone cannot tell the two apart.
- **Fix instruction**: Replace the line with `_assert(resp, 422, "token_invalid")` (keep the `accepted_at is None` check). Do this in the next touch of this file (e.g. USR-39 rebase) if no rework round happens.

### Rules (CLAUDE.md)
No violations found.
- R1: rules are in services, SQL in the repository, and the router changes only OpenAPI text.
- R2: hand-written migration with a reverse `downgrade()` and a lossy note. The model matches (`alembic check` is clean). `App` is already registered.
- R3: no new route; all class 1.
- R4: `app.updated` diff gains the field (`diff.py:22`), `role_assignment.granted` is unchanged, and denied 403s are audited in the same transaction.
- R5/R11: no secrets and no emails in errors.
- R6: existing envelope, with reasons in `details`.
- R7: every file is in the plan or justified.
- R8: green suite, with tests for 401/403/404/409/422 and the happy path.
- R9: additive fields only.

### Blockers from review agents
None. `security-review.md` (appsec-engineer) verdict PASS, no 🔴 or 🟡.

### Nits (do not block)
- `RbacRepository.app_super_admin_access` (`repository.py:838-848`) runs two queries; the plan said "one `EXISTS`". The result is the same; fold it into one statement when USR-39/40 call it on a hot path.
- `test_app_super_admin_access_false_when`: the `no_designation` and `cleared` cases are identical (`:750`).
- `test_change_designation_to_other_role` (:336) does not assert that the old role is now ordinary. `test_privilege_predicates_agree` covers this, at the predicate level only.
- Spec §5.16 "Granting" bullet still ends "Lifecycle … and whether `expires_at` is mandatory: OQ82, OQ83" although both are now decided. The OQ ticks say "plan defaults" without restating the answer (ADR-0030 §5 has it).
- The ADR-0015 residual-risk bullet is now a long single line (docs formatting only).

### Follow-ups (out of scope)
- USR-36: implement and test the request-level 422 `super_admin_role` (ADR-0029 §3 as amended).
- Rebase hotspots in `_grant_locked` / `_guard_privileged` / `user_has_privileged_assignment` with USR-25, USR-36, USR-37, USR-39 and USR-40.
- Test-infra: parallel review agents running `pytest` against the same `org_admin_test` DB interfere: the migration test downgrades the schema mid-run. Use a per-agent test DB or serialise the runs.
- Kit-managed `.github/scripts/risk_tier.py`: 13 ruff errors; fix in spaceM project-kit.
- Ming after merge: run the deploy migration step, then follow the "Ming verification after merge" steps in plan.md (create `simap:super-admin` with app-scope permissions only, designate it, and have a second super-admin grant it).
