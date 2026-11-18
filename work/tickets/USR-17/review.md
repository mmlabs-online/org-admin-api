# Review — USR-17

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): `service postgresql start`, then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` → ruff "All checks passed!", format clean, mypy "Success: no issues found in 147 source files", pytest "2456 passed, 1 warning in 324.64s", exit 0. Matches impl-log (2456 passed). The flaky `tests/test_stats_repository.py::test_login_count_uses_action_index` passed on the first run, so no rerun was needed.
- alembic: `alembic upgrade head` ok; `alembic check` → "No new upgrade operations detected." (no schema change, no migration expected).

### Acceptance criteria
- [x] Bearer admin endpoint `GET /api/v1/apps/{key}/role-definitions/{role_id}/assignments`, `admin:users:read` in `x-permission`. Evidence: `app/modules/rbac/router.py:217-244` (`require_permission` via `_with_404`); `tests/test_route_auth_classes.py` BEARER row (route-class suite green); `tests/test_openapi_contract.py::test_role_holders_operation`; `test_holders_401_without_token`, `test_holders_403_without_users_read`, `test_holders_403_with_other_permission_only[admin:apps:read|admin:roles:assign]`, `test_holders_admin_star_passes`.
- [x] `Page[T]` (`limit` ≤ 100, `offset`), order `created_at DESC, id`, items = `AssignmentRead` fields + `user_id`, `email`, `display_name` (+ `user_status`, plan D3), no N+1. Evidence: `repository.py:512-542` (join-free count + one rows query with explicit `User` columns, `order_by(created_at.desc(), id)`); `schemas.py:295` `RoleHolderRead(AssignmentRead)`; `test_holders_item_shape` (exact key set, no `password_hash`), `test_holders_order_and_id_tiebreak`, `test_holders_paging`, `test_holders_limit_over_100_is_422`, `test_holders_bounded_queries`, `test_page_role_holders_two_statements`.
- [x] Filters `active` (default = all rows, expired marked `active=false`) and `org_id`. Evidence: `repository.py:500-504` shared `_is_active()` also used by `_assignment_rows()` (per-user list tests still green); `test_holders_expired_default_and_active_filters`, `test_holders_active_boundary`, `test_holders_org_filter`, `test_holders_unknown_org_is_empty_page`, `test_holders_filters_combine_and_total`, `test_holders_same_user_multiple_rows`, `test_holders_only_this_role`.
- [x] 404 for unknown app or a role of another app. Evidence: `service.py:1110-1125` reuses `_app` / `_get` (`get_role(app.id, role_id)`); `test_holders_404_unknown_app`, `test_holders_404_unknown_role`, `test_holders_404_other_app_role_same_body` (whole JSON bodies equal).
- [x] Tests: 401, 403, 404s, expired rows, org-scoped rows, each filter, paging. All present in `tests/test_rbac_role_holders_api.py` and green; plus `test_holders_invalid_params_422`, `test_holders_cache_control_no_store`, `test_holders_disabled_user_listed`, `test_holders_admin_app_super_admin_listed`, `test_holders_disabled_app_listed`.

### Issues
None.

### Rules R1–R11
No violations found. R1: SQLAlchemy only in the repository, `NotFoundError` from the service, router is HTTP only. R2: no schema change; `alembic check` clean. R3: class 1 Bearer + `admin:users:read`, classified in the route-auth test; doc row added to `docs/identity-architecture.md` §4. R4: read-only, no audit needed. R5/R11: no secrets, nothing logged, `Cache-Control: no-store` set. R6: existing error envelope; 401/403/404 refs asserted in OpenAPI. R7: changed source/test/doc files match the plan's file list. The `_with_404` helper sits inside the planned `router.py`, mirrors `_with_409`, and is justified in impl-log. `work/backlog.md` and `ticket.md` changes are workflow state plus the planner's recorded AC3 wording correction. R8: green, endpoint fully tested. R9: `Page[T]`, UUIDs, D18 key path, additive schema (`AssignmentRead` untouched).

### Nits (do not block)
- `tests/test_rbac_role_holders_api.py::test_holders_filters_combine_and_total`: `assert live.id` checks nothing. Asserting that the returned id is one of the two live org rows would make the test stronger.
- `test_holders_bounded_queries`: compares the two counts only. Adding `assert counts[0] > 0` would guard against the capture silently recording nothing (same nit as USR-16).
- `RoleHolderRead` redeclares `active` only to add a description. Pydantic keeps the inherited position, so the order is unchanged. Moving the description to `AssignmentRead.active` would also document it on the per-user list, but that is a change to an existing schema, so leave it for now.

### Follow-ups (out of scope)
- As in plan.md: `scope=app|org` filter, `email_verified_at` on holder items, `(role_id, created_at DESC, id)` index at scale, replace the remaining inline `active` predicates with `_is_active()`, explicit `no-store` on `GET /users*`.
- Owner decisions recorded in plan.md (R-a: `admin:users:read` can list super-admin holders; `user_status` included; `total` counts assignments).
- `tests/test_stats_repository.py::test_login_count_uses_action_index` was reported flaky by the implementer (it passed in this review run). Track it as a bug if it recurs.
