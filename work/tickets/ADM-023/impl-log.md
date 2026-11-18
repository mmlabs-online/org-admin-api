
## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| app/modules/admin_auth/deps.py | `require_permission` gains `errors`, `requires_super`, `super_when` (fresh dicts per call, guards) |
| app/core/errors.py | `AppKeyTakenError` (`app_key_taken`, details reason) |
| app/modules/rbac/service.py | two raises of the taken-key conflict use `AppKeyTakenError` |
| app/modules/rbac/router.py | 404/409 and `x-requires-super` per plan; local `_with_*` helpers removed |
| app/modules/rbac/assignments_router.py | 404/409/super declarations; inline `_REVOKE` removed |
| app/modules/app_credentials/router.py | 404 on all routes, `requires_super="always"` on POST; `_with_409` removed |
| app/modules/entitlements/router.py | 404 on all routes; `_with_409` removed |
| app/modules/join_requests/router.py | 404 on all routes; helper now `_assign` (404+409) |
| app/modules/users/router.py | 404 on GET, 404/409/conditional super on PATCH; sessions route via helper |
| tests/test_openapi_contract.py | new contract tests |
| tests/test_rbac_apps_api.py | taken-key expectations updated to `app_key_taken` |
| docs/identity-architecture.md | `app_key_taken` code and `x-requires-super` sentence |

### Tests added / changed
- tests/test_openapi_contract.py › test_admin_declared_error_responses, test_parameterized_admin_routes_declare_404, test_requires_super_extension, test_post_apps_409_documents_codes, test_require_permission_rejects_bad_arguments
- tests/test_rbac_apps_api.py › test_create_app_duplicate_key_409, test_create_app_integrity_race_is_409 (updated)

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): green; 2556 passed, 1 warning
- alembic check: n/a (no migrations)

### Out-of-plan files
None

### Follow-ups (noticed, not done)
- Org slug conflict -> `slug_taken`; audit manifest-route 404; migrate organizations/audit local helpers to `require_permission(errors=...)`.
- POST role-assignment: unknown role is 422 (not 404), so 404 there documents only the unknown user.

### Scope self-check
- Could the diff be smaller? No
- Diff size: not measured (no git repo here); roughly +330 / -60
