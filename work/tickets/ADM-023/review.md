## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): green, exit 0; 2556 passed, 1 warning (pre-existing `forkpty` DeprecationWarning in `tests/test_cli_create_account.py`). Matches impl-log.md.
- alembic check (if migrations changed): n/a, no schema or migration changes.

### Acceptance criteria
- [x] 404 declared on the organizations, entitlements, app-credentials and join-requests routes that can return it. Evidence: organizations already done by ADM-020 (skipped per the ticket's dependency note); `app/modules/entitlements/router.py` (all 4 routes), `app/modules/app_credentials/router.py` (all 3), `app/modules/join_requests/router.py` (all 3); widened to apps/permissions/roles/role-definitions/role-assignments/users per plan. `tests/test_openapi_contract.py::test_admin_declared_error_responses` (exact `{404,409}` set per operation + `ErrorEnvelope` `$ref`) and `::test_parameterized_admin_routes_declare_404` (reverse guard over every `x-permission` route with a path param) pass.
- [x] 409 declared on every route that can return it, with the envelope (R6). Evidence: POST/PATCH/DELETE apps, POST/PATCH/DELETE permission, POST/PATCH/DELETE role, PUT role permissions, POST/DELETE role-assignment, entitlement grant, credentials POST, join-request approve/reject, PATCH user; same table test asserts presence and `$ref`. PATCH/DELETE entitlement correctly omit 409 (no 409 path in `entitlements/service.py`).
- [x] `admin:*` requirement machine-readable. Evidence: `require_permission(..., requires_super=...)` in `app/modules/admin_auth/deps.py` emits `x-requires-super` (`always`/`conditional`) + `x-requires-super-when` and a 403 description naming `admin_wildcard_required`. I cross-checked every `require_super(actor)` call site: `rbac/service.py:441,904,913,963,1029,1155,1189,1224,1296,1565` (assign + revoke via `_guard_privileged`), `users/service.py:62`, `app_credentials/service.py:109` map one-to-one onto the flagged operations; `sessions/staff_service.py` routes are already `admin:*` and correctly unflagged. `::test_requires_super_extension` asserts exact presence and absence everywhere else.
- [x] POST /apps with a taken key returns 409 `app_key_taken` with `details=[{"field":"key","reason":"app_key_taken"}]`, documented in OpenAPI. Evidence: `app/core/errors.py` `AppKeyTakenError`; `rbac/service.py:382,397`; `tests/test_rbac_apps_api.py::test_create_app_duplicate_key_409` (plain + `admin` key), `::test_create_app_integrity_race_is_409`; `tests/test_openapi_contract.py::test_post_apps_409_documents_codes`.
- [x] A test walks the OpenAPI document and asserts the above; existing tests still pass. Evidence: the five new contract tests plus green full suite.

### Issues
None.

### Rules R1–R11
No violations found. R1: only router metadata changed; service raises an `AppError` subclass. R3: every route keeps one auth class and its `x-permission`; manifest route untouched. R6: envelope unchanged, `$ref` attached by `core/openapi.py`. R7: all changed files are in the plan's file list (plus ticket/backlog state bookkeeping). R8: new tests present and green. R9: the only client-visible runtime change is the taken-key `code` going from `conflict` to `app_key_taken` (status stays 409); the plan flags this as an owner decision with a one-line fallback, and the PR description must call it out. CLI mapping still falls through to `MSG_CONFLICT` because `AppKeyTakenError` subclasses `ConflictError` (`app/cli.py:552-555`).

### Nits (do not block)
- `docs/identity-architecture.md:653`: the new sentence is not wrapped like the surrounding paragraph.
- `app/modules/rbac/router.py`: some 409 descriptions (PATCH `/apps/{key}`, role/permission routes) still list causes without the stable `details[0].reason` values, unlike POST/DELETE `/apps`; harmless, but admin-pan can only map the documented ones.
- `tests/test_openapi_contract.py::test_require_permission_rejects_bad_arguments` imports the private `_ERROR_RESPONSES`; acceptable for a mutation guard.

### Follow-ups (out of scope)
- Org slug conflict -> `slug_taken` code/reason, for symmetry with `app_key_taken`.
- Declare 404 on `PUT /apps/{key}/manifest` (`manifest.py:109`).
- Migrate `organizations/router.py` and `audit/router.py` local helpers to `require_permission(errors=...)`.
- Consider listing `app_key_taken` among the `ErrorBody.code` examples.
- Owner to confirm the `x-requires-super` shape (`always`/`conditional` + `-when` text) with admin-pan.
