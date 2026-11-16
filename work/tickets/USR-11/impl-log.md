
## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| app/modules/admin_auth/schemas.py (new) | strict `MePermissions` response model (AC1) |
| app/modules/admin_auth/service.py | `AdminSelfView` + `self_view()` (R1) |
| app/modules/admin_auth/me_router.py (new) | the endpoint, `no-store`, no permission (AC1/AC2) |
| app/main.py | mount `me_router` |
| tests/test_me_permissions.py (new) | behaviour tests (AC1-AC3, AC6) |
| tests/test_route_auth_classes.py | `SELF_SCOPED` allow-list threaded through the sweeps (AC4) |
| tests/test_openapi_contract.py | `checked` assertion + `test_me_permissions_operation` (AC5) |
| docs/adr/0008-admin-api-authentication.md | USR-11 amendment + §6 pointer |
| docs/spec-v1.md, CLAUDE.md, docs/identity-architecture.md (§4 row, D23), docs/consumers.md | per plan; owner confirms spec/R3/R9 wording at PR |

### Tests added / changed
- tests/test_me_permissions.py: all 17 tests listed in the plan (incl. `test_org_scoped_admin_assignment_ignored`; the DB accepts an org-scoped admin-app assignment, so the test runs).
- tests/test_route_auth_classes.py: modified classification, permission-map, OpenAPI, CORS-matcher and 401 sweeps; new `test_self_scoped_routes_have_no_permission`, `test_self_scoped_allow_list_is_exact`.
- tests/test_openapi_contract.py: `test_every_error_response_references_envelope` (modified), `test_me_permissions_operation` (new).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff, format, mypy clean; pytest `1757 passed, 1 warning` (warning is a pre-existing forkpty DeprecationWarning). The docs-only edits came after that run.
- alembic check: not applicable (no migrations).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Per-`sub` rate limit for Bearer admin routes.
- `admin-pan` (PAN-011b) switches UI gating to this endpoint.

### Scope self-check
- Could the diff be smaller? No; every file is in the plan.
- Diff size: see `git show --stat`.

## Should-fix (after PASS review)

- `tests/test_route_auth_classes.py`: `test_self_scoped_routes_have_no_permission` now asserts `_input_params(dependant) == []` for every SELF_SCOPED route. FastAPI in this env has no `get_flat_dependant`, so a small recursive helper collects `path_params`, `query_params` and `body_params` through sub-dependencies. Verified it detects params on other routes (`users/{user_id}` -> `['user_id']`, `GET organizations` -> `['limit', 'offset']`, `POST organizations` -> `['body']`) and returns `[]` for `/me/permissions`.
- `docs/identity-architecture.md` §4: `/me/permissions` row moved out of the identity (`/api/v1/auth`) rows into the Admin rows, labelled "Admin (self-scoped)".
- Checks: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` -> `1757 passed, 1 warning`; ruff, format, mypy clean.
- Out-of-plan files: none. Follow-ups: none.
- Follow-up from security re-check: `_input_params` also collects `header_params` and `cookie_params`, so a self-scoped route cannot take its subject from a custom header or cookie; still `[]` for `/me/permissions`. `make check`: 1757 passed.
