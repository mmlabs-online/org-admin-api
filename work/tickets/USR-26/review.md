# Review — USR-26

## Attempt 1 — 2026-11-27
Verdict: PASS

Reviewed: implementation commit `2438687` (diff vs `f101d92`), branch `claude/festive-volta-rikup7`. Tier: **high** (`python3 -I .github/scripts/risk_tier.py --base origin/main` → `high`; rbac paths).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed (the 13 pre-existing errors in kit-managed `.github/scripts/risk_tier.py` are outside this scope).
- `uv run ruff format --check app tests migrations` — 449 files already formatted.
- `uv run mypy app` — Success: no issues found in 216 source files.
- `uv run pytest -q` (local Postgres, `org_admin_test`) — **4522 passed**, 2 warnings, 747.81 s. Matches impl-log (4522). That is the USR-25 baseline of 4514 plus 8 new tests: 5 role-definition, 1 DELETE race, 2 OpenAPI.
- `uv run alembic check` (local dev DB) — No new upgrade operations detected.
- `python3 -I .github/scripts/risk_tier.py --base origin/main` — `high`.

### Acceptance criteria
- [x] **`assignment_count` = the rows the delete guard counts** (all rows: active, expired, app- and org-scoped). Repository `assignment_counts` at `app/modules/rbac/repository.py:303-312` uses the same `role_id` predicate with no expiry filter as `role_has_assignments` (`:299-301`), and each docstring points at the other. Evidence: `tests/test_rbac_role_definitions_api.py:303` `test_role_definitions_assignment_count_counts_all_rows` (active + expired + org-scoped → 3, none → 0). `:329` `test_assignment_count_zero_means_deletable` (0 → DELETE 204). `:338` `test_assignment_count_expired_only_blocks_delete` (expired-only → 1 and DELETE 409 `role_has_assignments`; this pins the guard and the count to the same definition). Schema description: `app/modules/rbac/schemas.py:269-277` (includes "expired").
- [x] **One grouped query on list and detail.** It is called once in `_definitions` (`app/modules/rbac/service.py:1201`), and both list (`:1232`) and detail (`:1237`) go through it. `tests/test_rbac_role_definitions_api.py:254` `test_role_definitions_bounded_queries` now gives every role an assignment and checks that the statement count is the same for `limit=1` and `limit=6`. The detail equals the list item (`:228` `test_role_definition_detail`, `:317` `test_role_definition_detail_assignment_count`).
- [x] **`is_org_owner`** = `r.id == app.org_owner_role_id` (`service.py:1210`), declared next to `is_default` (`schemas.py:256-258`). Evidence: `tests/test_rbac_role_definitions_api.py:287` `test_role_definitions_is_org_owner`. With no owner role every flag is false. With an owner set, the owner/default flags are independent: (True, False) and (False, True).
- [x] **DELETE 409s carry `details[0].reason`, all 6 reasons** (`service.py:1351-1373`), each pinned with exact `details`:
  - `system_role`: `tests/test_rbac_roles_api.py:262`, for both the admin and stoktok system roles
  - `default_role`: `:280`
  - `org_owner_role`: `:563`
  - `super_admin_role_designated`: `tests/test_rbac_super_admin_role.py:569`. It keeps the shipped `{"field": "role_id", …}`.
  - `role_has_assignments`: `tests/test_rbac_roles_api.py:291`
  - `changed_concurrently`: new `:303` `test_delete_role_integrity_race_is_409`. It monkeypatches the guard, the real FK raises inside the savepoint, the role survives and the session stays usable.
  - Code is `conflict` for all six.
- [x] **PUT permissions 409s carry `details[0].reason`, all 8 reasons** (`service.py:1445-1486`, `:215`):
  - `default_role_admin_permission`: `tests/test_rbac_roles_api.py:461`, `:500`
  - `org_owner_admin_permission`: `:574`
  - `default_role_org_permission`: `:612`
  - `role_has_org_permissions`: `tests/test_rbac_super_admin_role.py:600`. Shipped `field: permission_keys`, unchanged.
  - `wildcard_not_allowed`: `:611`. Shipped `field: permission_keys`, unchanged.
  - `super_admin_must_keep_wildcard`: `tests/test_rbac_roles_api.py:438`
  - `role_has_assignments`: `tests/test_admin_guards.py:426`. Not modified, and still pins code `role_has_assignments` with `[{"field": "permission_keys", "reason": "role_has_assignments"}]`.
  - `changed_concurrently`: `tests/test_rbac_roles_api.py:529`
  - The PUT `role_has_assignments` code is unchanged, as Ming approved under Q3. Code is `conflict` for the others.
- [x] **Shipped field/reason values and messages unchanged.** The diff only swaps string literals for `Final` constants with identical values (`service.py:1513-1523`). Every message string is byte-identical in the diff. `_role_has_assignments` (`service.py:184-188`) keeps its value through `REASON_ROLE_HAS_ASSIGNMENTS`.
- [x] **OpenAPI lists the reasons, built from the constants.** `app/modules/rbac/router.py:394-399` joins `ROLE_DELETE_CONFLICT_REASONS` and `ROLE_PERMISSIONS_CONFLICT_REASONS` (`service.py:1524-1540`) into the strings and wires them into the `409` entries of `errors=`. Evidence: `tests/test_openapi_contract.py:113` `test_role_delete_409_documents_reasons` and `:120` `test_role_permissions_put_409_documents_reasons` iterate the same tuples, so the docs cannot drift from the constants. `:90` `test_role_definitions_operations` sets required to the old set plus the 2 new fields and checks that descriptions are non-empty and include "expired".
- [x] **Additive only (R9).** There are two new required response fields and `details` only where none shipped before. No code, field, reason or message was renamed. Existing tests changed only by added assertions; `tests/test_rbac_super_admin_role.py:613-620` restructures one line so it can add an assertion, with the same check. The ETag is unaffected because the derived fields do not touch `updated_at`, as documented.
- [x] **Docs.** `docs/consumers.md:109-120` has a USR-26 bullet covering the fields, count semantics, snapshot caveat, ETag, both reason sets and the code exception. `docs/identity-architecture.md:186` updates the USR-16 row.
- [x] **No USR-25 regression.** The diff touches nothing in the assignment write paths (`_forbid_self`, `_role_is_privileged`, grant/renew/revoke, batch). The full suite is green with 4522 passed, which includes every USR-25 test.

### Deviations judged
- **`org_mode="required"` in the `is_org_owner` test.** Accepted. It is forced by the DB check `ck_apps_org_owner_requires_org_mode` (`migrations/versions/20261117_b4d82f6a1c93_usr06a_org_foundation.py:84`), and the plan's test row already said "Org app". The test still covers the "no owner role → all false" case, because it asserts before setting `org_owner_role_id`.
- **Helper placement.** Accepted. `_role_conflict` sits at `service.py:1544`, next to `_assignment_conflict` and the reason constants, instead of near `_role_has_assignments` (~189). It is referenced only inside function bodies, so the order of module-level definitions does not matter. Keeping it next to the constants matches the plan's own placement of the tuples (after `REASON_CHANGED_CONCURRENTLY`). No behavioural effect.

### Issues
None (no 🔴, no 🟡).

### Rules (CLAUDE.md)
No violations found.
- R1: the count is in the repository and the flags/reasons are in the service; the router only builds doc strings.
- R2: no schema change, `alembic check` clean.
- R3/R4: permissions and audit paths unchanged; 409s write no audit, as before.
- R6: the error envelope goes through `ConflictError` / `RoleHasAssignmentsError`.
- R7: all changed app/test/docs files are in the plan's file list. The `work/` files are orchestrator bookkeeping.
- R8: green suite; every new reason and both new fields are tested.
- R9: additive.

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡 (one 🟢 owner decision: per-role counts visible under `admin:apps:read`).

### Nits (do not block)
- 🟢 `_role_conflict` (`service.py:1544-1545`) is byte-identical to `_assignment_conflict` (`:1548-1549`). The plan chose this to avoid churn in USR-25/USR-27 code. One shared `_reason_conflict` could replace both later.
- 🟢 The PUT 409 description (`router.py:395-399`) first lists `role_has_assignments` under "code=conflict; details[0].reason: …" and only then says it answers `code=role_has_assignments` instead. That is accurate but reads awkwardly. Leaving `role_has_assignments` out of the first list (or splitting the tuple) would read cleaner.
- 🟢 `tests/test_rbac_roles_api.py:500`, `:574`, `:612` assert the exact `details` but not `error.code == "conflict"`. The other reason tests do assert it. The code is still correct, since every raise site is `ConflictError`.

### Follow-ups (out of scope)
- An optional `active_assignment_count` (plan Q1 default), if admin-pan finds that the expired-inclusive count looks stale.
- Merge `_role_conflict` and `_assignment_conflict` into one reason-only conflict helper once USR-27 has merged.
