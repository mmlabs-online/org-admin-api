# ADM-023 — Security review

## Attempt 1

Static, read-only review: no pytest or make check run.

**Overall: PASS.** No blockers and nothing that must be fixed. The change only affects OpenAPI metadata, plus one more specific error code. Every admin route still enforces authentication and its permission at runtime.

### What was verified
- **Authorization at runtime is unchanged.** The app was built with `uv run python` (no DB) and each route's dependency tree was walked.
  - Every Bearer admin route under `/api/v1` still has `current_admin` plus exactly one `RequirePermission(key)`, and its `x-permission` matches the enforced key. This covers users, sessions (`admin:*`), apps, permissions, role definitions, roles, credentials, role assignments, join requests, audit logs, organizations, entitlements and stats.
  - The only routes without a permission dependency are the expected ones: `PUT /apps/{key}/manifest` (app credential class) and `GET /me/permissions` (self-scoped, ADR-0008). Neither is touched by this diff.
- **`require_permission` refactor** (`app/modules/admin_auth/deps.py:116-150`). It still returns `dependencies=[Depends(RequirePermission(key))]` on every call. The new kwargs only affect `responses` and `openapi_extra`.
  - The guards reject 401/403 overrides through `errors=`, codes outside 4xx, `requires_super` on `admin:*`, and a `super_when` without `conditional`.
  - The per-call copy `{c: dict(d)}` is safer than the old shallow `dict(_ERROR_RESPONSES)`.
- **`admin:*` service guards are untouched.** The `require_super(actor)` calls in `rbac/service.py`, `users/service.py:62`, `app_credentials/service.py:109` and `sessions/staff_service.py` are not in the diff.
  - Each `x-requires-super-when` text was checked against the guard conditions it describes: admin-app PATCH G1 (`:441`); permission create/update/delete (`:904`, `:963`, `:1029`); role create/rename/delete/PUT (`:1155`, `:1189`, `:1224`, `:1296`); `_guard_privileged` (`:1565`); G5 user status. They match.
- **No new information disclosure.**
  - `AppKeyTakenError` (`app/core/errors.py:88-95`) has a fixed message and `details=[{"field":"key","reason":"app_key_taken"}]`, with no input values or secrets (R5/R11). The old `conflict` response already said the key was taken; only the code is new, and it is reachable only by holders of `admin:apps:write`.
  - The new descriptions and `x-requires-super-when` strings are static policy text.
  - `/openapi.json` stays disabled in production unless `API_DOCS_ENABLED=true` (`app/main.py:50-54`, ADM-002b).
- **The IntegrityError race path** (`rbac/service.py:396-397`) now raises `AppKeyTakenError`. Same 409 status, still inside the savepoint, and the test asserts the session stays usable.

### Findings
- **GREEN (note), `app/modules/admin_auth/deps.py:141-144`.** When `requires_super` is set, the 403 description lists only `permission_denied`/`admin_wildcard_required`. The assignment routes (`assignments_router.py:49-58`, `:73-78`) and user PATCH (`users/router.py:53-62`) can also return 403 `self_assignment_forbidden` / `self_status_change_forbidden`. This is a documentation gap, not a security issue.
- **GREEN (note), `app/modules/app_credentials/router.py:20-31`.** `issue_credential` locks and looks up the app before `require_super` (`app_credentials/service.py:107-109`). A non-super holder of `admin:apps:write` therefore gets 404 for an unknown key and 403 for a known one. This is pre-existing and admin-only, so the enumeration risk is negligible.

### Follow-ups (out of scope)
- Optional: a static contract test that every `/api/v1` Bearer route has exactly one `RequirePermission` dependency whose key equals `x-permission`. Routers are wrapped lazily (`_IncludedRouter.effective_candidates`), so a walk over `app.routes` alone misses them.
