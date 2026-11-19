## Attempt 1 — 2026-11-19

Environment: no Docker; local Postgres 16 started with `service postgresql start` (databases org_admin and org_admin_test, default TEST_DATABASE_URL). Plan approved by Ming 2026-11-19 with all defaults (412 details null, roles.updated_at + If-Match, order 404 -> 412 -> 422 -> 403 -> 409, rename on bumped_updated_at). USR-23 had merged; plan line numbers were re-located by intent.

### Files touched
| File | Required because |
|---|---|
| app/modules/rbac/repository.py | `touch_role` (strict bump via `bumped_updated_at`); `get_role` uses `populate_existing` (AC1, AC4) |
| app/modules/rbac/service.py | bump in `_set_role_permissions` on non-empty diff; `precondition` check in `replace_permissions` after 404; `updated_at` in `RoleRead` (create/_read/PUT); rename uses `bumped_updated_at` |
| app/modules/rbac/schemas.py | `RoleRead.updated_at`; new `RoleDefinitionRead.updated_at` description |
| app/modules/rbac/router.py | `ETag` on GET role-definition and PUT permissions, `If-Match` param, 412 declared |
| docs/spec-v1.md, docs/consumers.md | USR-24 note and consumer bullet (plan Approach 7) |
| tests/test_rbac_roles_if_match.py | new tests |
| tests/test_rbac_roles_api.py | `ROLE_READ_KEYS` gains `updated_at` |
| tests/test_app_manifest_api.py | manifest bumps role version test |
| tests/test_openapi_contract.py | 412/If-Match/ETag declarations |

### Tests added / changed
- tests/test_rbac_roles_if_match.py: all plan tests (ETag on GET; bump on changing PUT; noop no bump; matching/stale/list/star/weak/malformed/noop-stale; no header unchanged; 404 beats 412; 412 before 422, 409, 403 (no denied audit); 401/403 with If-Match; SQL lock order; rename bump; default-role rebuild; touch_role monotonic; get_role populate_existing).
- tests/test_app_manifest_api.py › test_manifest_change_bumps_role_version
- tests/test_openapi_contract.py › test_role_put_412_and_if_match_declared; test_etag_header_declared extended
- tests/test_rbac_roles_api.py › ROLE_READ_KEYS constant

### Checks (actual output summary)
- ruff check (`--exclude .github/scripts`): All checks passed
- format:check (app tests): 271 files already formatted
- typecheck: Success: no issues found in 150 source files
- alembic check: No new upgrade operations detected
- test (full `make test`): 2739 passed, 1 warning in 359.58s

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- If-Match on PATCH/DELETE roles; ETag header on POST/PATCH roles; If-Match on permission and app PATCH.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +75 production lines, +400 test lines.
