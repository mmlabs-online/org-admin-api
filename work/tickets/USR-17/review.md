# Review — USR-17

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): ruff "All checks passed!", format "319 files already formatted", mypy "Success: no issues found in 147 source files", pytest `2420 passed, 1 warning in 274.53s`, exit 0. Matches impl-log.md.
- alembic check: not applicable (no model or migration change; D7).

### Acceptance criteria
- [x] AC1 Bearer admin endpoint, path consistent with USR-16, `admin:users:read` in `x-permission` — `app/modules/rbac/router.py:209-242` (`GET /{key}/role-definitions/{role_id}/assignments`, `**require_permission("admin:users:read")`, on the `rbac` router with router-level `current_admin`); `tests/test_route_auth_classes.py:98` BEARER entry (classification, permission map, CORS matcher, 401 sweeps all green); `tests/test_openapi_contract.py::test_role_holders_operation` asserts `x-permission`; `test_role_holders_403_without_permission`.
- [x] AC2 `Page[T]`, `limit` ≤ 100, order `created_at DESC, id`, `AssignmentRead` fields + `user_id`, `email`, `display_name`, no N+1 — `repository.py` `page_role_holders` (one joined select + one count; `order_by(RoleAssignment.created_at.desc(), RoleAssignment.id)`); `schemas.py` `RoleHolderRead(AssignmentRead)`; tests `test_role_holders_item_shape` (exact key set, no extra user columns), `test_role_holders_paging_and_order` (tie broken by id, no duplicates across pages), `test_role_holders_limit_over_100_422`, `test_role_holders_bounded_queries` (equal statement count for 1 vs 6 holders).
- [x] AC3 filters `active` (default includes expired with `active=false`) and `org_id` — shared `_not_expired()` predicate used for both field and filter; tests `test_role_holders_expired_rows_default`, `test_role_holders_active_filter`, `test_role_holders_active_partition`, `test_role_holders_org_filter`, `test_role_holders_org_filter_unknown_org_empty`, `test_role_holders_filters_combined`, `test_role_holders_bad_query_values_422`; `total` asserted against filtered rows.
- [x] AC4 404 for unknown app or role not of that app — `service.py` `list_holders` reuses `_app` / app-scoped `_get`; tests `test_role_holders_404_unknown_app_and_malformed_key`, `test_role_holders_404_unknown_role`, `test_role_holders_404_role_of_other_app` (that role has holders; no email leak).
- [x] AC5 tests 401, 403, 404 x3, expired, org-scoped, each filter, paging — all present above plus `test_role_holders_401_before_404_and_422`; all pass in the run.

### Issues
None.

### Rules R1–R11
No violations found. R1: router HTTP + header only, service does 404s and assembly, repository holds all SQLAlchemy. R2: no schema change. R3: class 1 Bearer + `admin:users:read`, classified by test; 403 precedes 404/422. R4: read-only. R5/R11: no emails in errors/logs, `Cache-Control: no-store`, explicit fields (no `password_hash`). R6: existing envelope for 401/403/404/422. R7: diff limited to the plan's file list (the `work/` changes for ADM-022/024/025 belong to other tickets on this shared branch and were not reviewed). R8: green. R9: plural noun under D18, `Page[T]`, UUIDs, timestamptz; additive.

### Nits (do not block)
- 💭 `tests/test_openapi_contract.py::test_role_holders_operation` asserts the envelope for 401/403/422 but not 404, although the plan listed 404. The implementer explained why in impl-log: `require_permission` documents no 404 on any route, and USR-16's contract test also skips it. That is consistent with the codebase. Documenting 404 across all routes is a follow-up.
- 💭 `user_status` is typed `str`. A `Literal["active", "disabled"]` would give admin-pan an enum in OpenAPI, but `JoinRequestRead` also uses `str`, so `str` matches the existing pattern.

### Follow-ups (out of scope)
- As listed in plan.md (scope filter, composite index if measured, `no-store` on other user reads, `admin:roles:read` / admin-app gate decision, reuse `_not_expired()` elsewhere).
- Document 404 responses in OpenAPI for routes that can return it (cross-cutting).
- Owner-visible residual risks 1–5 from plan.md must go into the PR description.
