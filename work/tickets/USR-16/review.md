# Review — USR-16

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` → ruff ok, "313 files already formatted", mypy "Success: no issues found in 146 source files", pytest "2345 passed, 1 warning in 257.17s". Matches impl-log (2345 passed). (A first run overlapped another agent's pytest on the shared `org_admin_test` DB and showed unrelated sqlalchemy errors in other modules; rerun with the DB idle was fully green.)
- Targeted: `tests/test_rbac_role_definitions_api.py tests/test_openapi_contract.py tests/test_route_auth_classes.py` → 171 passed.
- alembic check: not applicable (no schema change, no migration).

### Acceptance criteria
- [x] New Bearer admin endpoint, non-colliding path, one `admin:*` permission in `x-permission`, no rate limit — evidence: `app/modules/rbac/router.py:183-205` (`/{key}/role-definitions[/{role_id}]` on the Bearer `router`, `require_permission("admin:apps:read")`, no `rate_limit`); `tests/test_route_auth_classes.py:96-97` BEARER entries (classification, permission map, CORS matcher, 401 sweeps green); `tests/test_openapi_contract.py::test_role_definitions_operations` asserts `x-permission == "admin:apps:read"`; `test_catalog_unchanged_and_consistent` shows the public catalog unchanged.
- [x] `Page[T]`, `limit` ≤ 100, `offset`, stable order with `id` tie-breaker; item fields — evidence: `repository.py` `page_roles` orders by `Role.key, Role.id`; `schemas.py` `RoleDefinitionRead` has exactly the 10 fields; `test_role_definitions_item_shape` (exact key set, sorted permissions), `test_role_definitions_paging_and_order`, `test_role_definitions_limit_over_100_422`, `test_role_definitions_is_default`, `test_role_definitions_privileged_flags`.
- [x] Lists `admin` app and disabled apps; 404 only for unknown app key — evidence: `RoleService._app` uses `get_app_by_key` with no status/admin filter (`service.py`); `test_role_definitions_admin_app_listed_with_leaf_permission`, `test_role_definitions_disabled_app_listed`, `test_role_definitions_unknown_app_404`, `test_role_definitions_malformed_key_404`.
- [x] Optional items — detail route included (`test_role_definition_detail`, `test_role_definition_detail_404_unknown_role_and_other_app`, `test_role_definition_detail_bad_uuid_422`); `assignment_count` deliberately omitted per plan D5 (absence locked by the exact key set test). No N+1: `role_permission_rows` single `IN` query; `test_role_definitions_bounded_queries`.
- [x] Tests: 401, 403, 404, admin app, disabled app, paging, `is_default`, `privileged`, OpenAPI schema — evidence: `test_role_definitions_401_before_404`, `test_role_definitions_403_without_permission` (403 before 404, `details.required`), the tests above, `test_privileged_flag_matches_assignment_guard` (cross-check vs real G4 guard), `test_role_definitions_operations` (Page_RoleDefinitionRead_ / RoleDefinitionRead refs, required fields).

### Issues
None.

### Rules R1–R11
No violations found. R1: SQLAlchemy only in `repository.py`, `NotFoundError` raised in the service, router HTTP-only. R2: no schema change. R3: class 1 Bearer + `admin:apps:read`, classified by test. R4: read-only, no audit needed. R6: errors through existing envelope. R7: changed source/test/doc files match the plan's file list exactly; `catalog_router`, `RoleCatalogService` and `_guard_privileged` untouched. Other `work/` files on the branch belong to other tickets (backlog rows for USR-16..22 / ADM-020..025) and are outside this review. R8: green. R9: `Page[T]`, UUID role ids, `timestamptz` via existing columns; path under the D18 key exception, recorded in `docs/identity-architecture.md`.

### Nits (do not block)
- `tests/test_rbac_role_definitions_api.py::test_role_definitions_bounded_queries` compares the two counts only; adding `assert counts[0] > 0` would guard against the capture silently recording nothing.
- The new routes do not declare `404` in OpenAPI (implementer's documented deviation, consistent with existing Bearer routes); ADM-023 covers this repo-wide.

### Follow-ups (out of scope)
- As listed in plan.md: `assignment_count` with an owner-chosen definition (or via USR-17 `Page.total`), `is_org_owner` flag, `admin:apps:read` seed description, bump `roles.updated_at` on permission replace, refactor `_guard_privileged` to use `role_is_privileged`.
- Test infra: concurrent pytest runs (several agents) against the shared `org_admin_test` DB interfere (e.g. `test_audit_migrations` downgrades); consider a per-run test DB or a lock.
