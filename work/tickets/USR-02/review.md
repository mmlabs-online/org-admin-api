# Review — USR-02 (slice USR-02a: RBAC core)

## Attempt 1 — 2026-11-11
Verdict: PASS

Scope judged: plan.md slice **USR-02a** (ACs 1–7 of the plan). Ticket ACs moved to USR-02b / USR-02c (`work/tickets/USR-02b/ticket.md`, `work/tickets/USR-02c/ticket.md`) are out of scope for this attempt.
Base: `origin/main...HEAD` (branch `claude/blissful-wozniak-fx4lm9`, HEAD `c2a5d3c`). Working tree clean.

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest), with `APP_VERSION`/`DATABASE_URL`/`APP_ENV` unset, local Postgres 16 online:
  - `ruff check .` → `All checks passed!`
  - `ruff format --check .` → `133 files already formatted` (impl-log says 132; the result is the same, a pass, so this is not a mismatch in outcome)
  - `mypy app` → `Success: no issues found in 45 source files`
  - `pytest -q` → `142 passed in 4.51s` (matches impl-log: 142 passed)
- alembic (`DATABASE_URL=…/org_admin_test`): `current` → `b638a7c84c31 (head)`; `alembic check` → `No new upgrade operations detected.`; `downgrade -1` → `Running downgrade b638a7c84c31 -> 7b847bdc5230, create rbac` (OK); `upgrade head` → OK; `alembic check` → `No new upgrade operations detected.`

### Acceptance criteria
- [x] **AC1** — five tables + one migration per identity-architecture §3; defaults; nullable `org_id` without FK; `unique (app_id, key)` on permissions/roles; `UNIQUE NULLS NOT DISTINCT (user_id, role_id, org_id)`; working downgrade; `alembic check` clean — evidence: `migrations/versions/20261111_b638a7c84c31_create_rbac.py:52-185` (tables, deferred FK), `:238-248` (downgrade, verified by the downgrade/upgrade run above); `app/modules/rbac/models.py:52-191`; tests `test_app_defaults` (raw SQL insert, so the server defaults are exercised), `test_app_check_constraints` (8 cases), `test_duplicate_permission_key_rejected`, `test_duplicate_role_key_rejected`, `test_duplicate_app_scope_assignment_rejected`, `test_org_scoped_assignments_unique_per_org`, all passed. The 33-char key raises `DataError` (varchar(32)) and not `IntegrityError`; the test expects `DBAPIError` and impl-log explains why. Acceptable.
- [x] **AC2** — database rules: same-app role permissions and default role; key grammar; `<app>:*` must be `is_admin` — evidence: composite FKs `models.py:62-67,143-154`, migration `:118-129,179-185`; CHECK regexes identical in model and migration (`models.py:31-33` = `migration:28-30`); tests `test_role_permission_cross_app_rejected` (both `app_id` variants rejected), `test_default_role_must_belong_to_same_app`, `test_permission_key_format_valid` (3) / `_invalid` (9, asserting `ck_permissions_key_format`), `test_role_key_format_valid` (2) / `_invalid` (4), `test_wildcard_permission_must_be_admin`, all passed.
- [x] **AC3** — `RoleCatalogService.build_default_role` creates or updates `<app>:member` with exactly the non-admin permissions, sets `default_role_id` only when it is NULL, is idempotent, returns the diff, and runs only when called — evidence: `app/modules/rbac/service.py:22-59`; tests `test_build_creates_member_with_non_admin_permissions`, `test_build_is_idempotent`, `test_build_reflects_data_changes`, `test_build_keeps_existing_default_role`, `test_build_unknown_app_raises_not_found`, `test_new_permission_not_added_until_rebuild`, all passed.
- [x] **AC4** — seed: `admin` app (closed, `org_mode` none, `origins` {}, `default_role_id` NULL, active), `admin:*` (`is_admin`), `admin:super-admin` (`is_system`) holding exactly `admin:*`, no `admin:member` — evidence: `migration:187-235`; `test_seed_admin_app` passed.
- [x] **AC5** — `effective_roles` / `effective_permissions` / `permits` — evidence: `app/modules/rbac/repository.py:60-110` (DB `func.now()` for expiry; scope `org_id IS NULL` or `(IS NULL OR = :org)`; `DISTINCT … ORDER BY`; permission join also requires `p.app_id = a.id`); `app/modules/rbac/authz.py:11-46` (prefix filter; `permits` has no ORM import, no global `*`); the 10 DB tests in `tests/test_rbac_authz.py` + `test_permits` (9 cases), all passed.
- [x] **AC6** — deletes: app delete cascades the catalog even with a default role; a role with assignments cannot be deleted; deleting a user cascades their assignments and sets `granted_by` to NULL — evidence: FK actions `migration:90-92,106-108,150-165,179-185`; `test_delete_app_cascades_catalog`, `test_role_with_assignments_cannot_be_deleted` (asserts `fk_role_assignments_role_id_roles`), `test_delete_user_cascades_and_nulls_granted_by`, all passed.
- [x] **AC7** — models registered in `app/db/models.py:5,8-17`; `make check` green; `alembic check` clean (above).

### Scope
Every code, test, migration and ADR file is in the plan's file list. The `work/` changes are orchestrator bookkeeping that the plan's FU1/FU2 call for: `work/backlog.md` (state, USR-02b/02c rows, dependencies), `work/tickets/USR-02b|USR-02c/ticket.md` (new), the ADM-002 ticket dependency line, the USR-02 ticket state and split note, plus the consult/plan/impl-log files. No HTTP route, config, `.env.example`, `app/main.py` or `app/core/*` change (plan: "No other files"). Verified with `git diff origin/main...HEAD --stat`.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: SQL only in `repository.py`. The service raises `NotFoundError`. The builder sets `app.default_role_id` and calls `self.repo.session.flush()` (`service.py:51-53`), the same pattern as the reference `OrganizationService.update`.
- R2: one hand-edited migration; downgrade verified; models registered; `alembic check` clean.
- R3/R6/R9: no endpoint in this slice. UUID PKs, all times `timestamptz`.
- R4: no exposed admin mutation; `# R4` marker at `service.py:31`.
- R5/R10/R11: no config, secrets, credentials or logging added. The seed uses fixed non-secret UUIDs.
- R8: tests cover every plan AC. 401/403 do not apply (no HTTP surface).

### Nits (do not block)
- `role_assignments.granted_by` has no index, so deleting a user runs the `ON DELETE SET NULL` check as a sequential scan of `role_assignments`. This is fine at the current scale. Consider an index if user deletion becomes common.
- `ruff format --check` file count is 133 here against 132 in impl-log. The outcome is the same; record the exact output next time.

### Follow-ups (out of scope)
- **USR-02b/USR-03:** `effective_roles` does not filter role keys by the requested app prefix, while `effective_permissions` does (`authz.py:39-46`). The `ck_roles_key_format` CHECK accepts e.g. a `stoktok` role keyed `admin:super-admin`, which would then appear in the `stoktok` token's `roles` claim. USR-02b must enforce "role key prefix == app key" (as the plan already says for permissions and roles). Consider the same second-check filter in `effective_roles` when USR-03 consumes it.
- **USR-02b/USR-05:** `build_default_role` is not safe against concurrent runs for the same app: two first builds race on `uq_roles_app_id_key`, and two rebuilds race on `pk_role_permissions`. Callers should map `IntegrityError` → `ConflictError` or serialize (e.g. `SELECT … FOR UPDATE` on the app row).
- Plan FU1–FU5 stand (FU1 is already partly done: the USR-02b/02c tickets and backlog rows exist).
