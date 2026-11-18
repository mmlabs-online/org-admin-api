# Review — ADM-019

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0. ruff "All checks passed!", format check clean, mypy "Success: no issues found in 147 source files", pytest `2420 passed, 1 warning in 248.67s`. BUG-001 flake did not occur (no rerun needed). Matches impl-log (2420 passed).
- `alembic upgrade head && alembic check`: "No new upgrade operations detected." No schema change in the diff.

### Acceptance criteria
- [x] AC1 — PATCH required→none returns 409 `conflict`, details `[{"field":"org_mode","reason":"org_mode_in_use"}]` when org entitlements or org-scoped assignments exist — evidence: guard at `app/modules/rbac/service.py:486-498`; repository `app_has_org_assignments` at `app/modules/rbac/repository.py:115-122`; tests `test_patch_org_mode_none_with_entitlement_409` (disabled entitlement still blocks), `test_patch_org_mode_none_with_org_assignment_409`, `test_patch_org_mode_none_with_expired_org_assignment_409` (each asserts status, code, exact details, `org_mode` unchanged) — all pass.
- [x] AC2 — no change when nothing org-scoped exists; none→required unaffected — evidence: `test_patch_org_mode_none_without_org_data_ok`, `test_patch_org_mode_none_with_only_app_scope_assignments_ok`, `test_patch_org_mode_none_and_clear_owner_without_org_data_ok`, `test_patch_org_mode_required_with_org_data_unaffected`, `test_patch_org_mode_same_value_with_org_data_ok`; existing `test_patch_org_mode_none_while_owner_set_422` still passes. 422 `org_mode_required` precedence over the 409 and no partial write pinned by `test_patch_org_mode_none_owner_set_and_org_data_422_wins`.
- [x] AC3 — refused attempt writes no audit row; allowed case audited — evidence: `_assert_org_mode_in_use` asserts zero `app.updated` rows; allowed test asserts exactly one row with `changes == {"org_mode": {"from": "required", "to": "none"}}`.
- [x] AC4 (plan) — grant serialised with PATCH via the app row lock — evidence: `app/modules/entitlements/service.py:64-66` refuses `admin` before `lock_app`, then `rbac.lock_app(data.app_key)` (`FOR NO KEY UPDATE`); PATCH path locks via `_locked_app` (`service.py:408`). `test_grant_locks_app_row` passes; `test_ungrantable_app_409` (all 3 reasons) still passes. Only entitlement insert site is `grant` (grep of `OrgEntitlement(` in `app/`).
- [x] AC5 — `make check` green (above).

### Issues
None.

### Scope
All changed code/doc files are in the plan's file list. `work/backlog.md` and `ticket.md` changes are workflow state updates. No out-of-plan files.

### Rules R1–R11
No violations found. R1: EXISTS query in repository, guard in service raising `ConflictError`. R2: no schema change, `alembic check` clean. R3: auth classes unchanged (`admin:apps:write`, `admin:entitlements:write`). R4: refused 409 writes no row (tested). R6: standard envelope, 409 `conflict`. R9: no existing reason renamed; 409 added to OpenAPI description (`router.py:86-88`).

### Nits (do not block)
- impl-log "Scope self-check" says "no commit made", but commit 6b83dd1 exists on the branch (presumably made by the orchestrator); wording only.
- `docs/consumers.md:418` appended sentence makes the line long; other lines in that paragraph are wrapped at ~100 chars.

### Follow-ups (out of scope)
- As in plan.md: pending invitations as a blocker (or cancel on flip); bulk clear of an app's org data; one-off report of `none` apps already holding org data.
