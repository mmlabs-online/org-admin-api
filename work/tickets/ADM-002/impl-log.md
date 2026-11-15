# Implementation log — ADM-002

## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `app/core/access_token.py` (new) | Pure verifier `verify_access_token` + `InvalidAccessTokenError(reason)` (AC1) |
| `app/core/jwt_keys.py` | `KeySet.public_keys` (signing + extra kids, `repr=False`) for the verifier |
| `app/core/errors.py` | `UnauthorizedError` (401 `unauthorized`) |
| `app/core/permissions.py` (deleted) | Replaced by `require_permission` (one source for declare + enforce) |
| `app/modules/admin_auth/__init__.py` (new) | New module |
| `app/modules/admin_auth/service.py` (new) | `AdminPrincipal`, `AdminAuthService.principal()` reloads user (active, verified) + admin permissions (R1) |
| `app/modules/admin_auth/deps.py` (new) | `bearer_scheme`, `current_admin`, `RequirePermission`, `require_permission`, `AdminDep`; denial logging without token/email |
| `app/modules/organizations/router.py` | Router-level `current_admin`; `admin:organizations:read\|write` per route |
| `app/modules/users/router.py` | Router-level `current_admin`; `permission()` → `require_permission()`; docstring |
| `app/modules/rbac/router.py` | Same (13 routes) |
| `app/modules/rbac/assignments_router.py` | Same (3 routes) |
| `app/modules/rbac/catalog_router.py` | Docstring only: public by design, never add `current_admin` |
| `app/main.py` | Comments only (flags → ADM-002b, `/docs` note) |
| `app/core/config.py` | Comments + validator message "before ADM-002" → "before ADM-002b" |
| `app/modules/tokens/service.py` | Removed the `admin` audience refusal (AC6) |
| `app/modules/tokens/errors.py` | Removed unused `AudienceNotSupportedError` |
| `migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py` (new) | Seeds the 8 admin leaf permissions (`is_admin=true`, `ON CONFLICT DO NOTHING`); downgrade deletes them (AC7) |
| `tests/admin_auth_factories.py` (new) | `make_staff`, `claims_for`, `mint`, `mint_raw`, `bearer`, `KEY_SET`, `DROP` |
| `tests/conftest.py` | `admin_permissions=` on `make_client` / `make_strict_app` / `make_strict_client` (overrides `current_admin`); `admin_client` fixture |
| `tests/test_access_token.py` (new) | Verifier unit tests |
| `tests/test_admin_auth.py` (new) | API-level auth tests on real tokens |
| `tests/test_route_auth_classes.py` (new) | Route enumeration, allow-lists, permission map, OpenAPI, runtime 401 sweeps |
| `tests/test_organizations.py` | `client` → `admin_client` |
| `tests/test_users_admin.py` | `users_client` adds `admin_permissions`; removed `test_users_routes_declare_permissions` (moved to central map) |
| `tests/test_rbac_apps_api.py` | `admin_permissions`; removed `test_rbac_routes_declare_permissions` (central map) |
| `tests/test_rbac_assignments_api.py` | `admin_permissions`; removed `test_route_permission_map` (central map) |
| `tests/test_rbac_permissions_api.py`, `tests/test_rbac_locking.py`, `tests/test_role_catalog_api.py`, `tests/test_auth_cors.py` | `admin_permissions=["admin:*"]` on admin-route clients |
| `tests/test_rbac_roles_api.py` | `admin_permissions`; `test_put_super_admin_without_wildcard_409` uses the seeded `admin:apps:read` |
| `tests/test_auth_token.py` | `test_token_admin_audience_refused` → three admin cases (AC6) |
| `tests/test_rbac_schema.py` | `test_admin_leaf_permissions_seeded` |
| `docs/adr/0008-admin-api-authentication.md` (new) | ADR for Bearer-only admin auth |
| `docs/adr/0005-access-token-signing.md` | Items 8–9: admin audience issued; `audience_not_supported` dropped |
| `docs/identity-architecture.md` | §4: one row per admin router group, admin auth paragraph; `/auth/token` also issues `aud=admin` |
| `docs/deploy.md` | "Admin access (ADM-002)" section + runbook; one-time setup step 4 wording |

### Tests added / changed
- `tests/test_access_token.py` (28): `test_valid_token_returns_claims`, `test_extra_key_token_verifies`, `test_expired_rejected`, `test_expiry_within_leeway_accepted`, `test_future_iat_rejected`, `test_wrong_audience_rejected[stoktok, [stoktok]]`, `test_wrong_issuer_rejected`, `test_unknown_kid_rejected`, `test_missing_kid_rejected`, `test_alg_none_rejected`, `test_hs256_rejected`, `test_wrong_typ_rejected[JWT, missing]`, `test_missing_claim_rejected[exp,iat,sub,jti,iss,aud]`, `test_email_verified_must_be_true[false, missing, string]`, `test_wrong_signature_rejected`, `test_garbage_rejected[abc, a.b.c, ""]`, `test_error_carries_no_token`
- `tests/test_admin_auth.py` (27): `test_no_token_401`, `test_basic_scheme_401`, `test_empty_bearer_401`, `test_query_token_401`, `test_rejected_tokens_401[expired, wrong_aud, wrong_iss, unknown_kid, alg_none, hs256, typ_jwt, not_a_jwt]`, `test_unknown_sub_401`, `test_non_uuid_sub_401`, `test_disabled_user_401`, `test_unverified_user_401`, `test_cookie_only_401`, `test_auth_runs_before_validation`, `test_missing_permission_403`, `test_leaf_permission_allows_only_its_routes`, `test_super_admin_wildcard_allows_all`, `test_other_app_wildcard_does_not_grant_admin`, `test_role_revoked_after_issue_403`, `test_expired_assignment_403`, `test_disabled_after_issue_401`, `test_issued_admin_token_end_to_end`, `test_denials_logged_without_secrets`
- `tests/test_route_auth_classes.py` (55): `test_every_route_is_classified`, `test_bearer_route_permission_map`, `test_allow_lists_have_no_stale_entries`, `test_public_and_cookie_routes_have_no_bearer_guard`, `test_openapi_bearer_scheme`, `test_every_bearer_route_401_without_token[×24]`, `test_every_bearer_route_401_with_cookie_only[×24]`, `test_public_routes_reachable_without_token`, `test_cookie_routes_not_bearer_guarded`
- `tests/test_auth_token.py`: `test_token_admin_audience_issued`, `test_token_admin_not_member`, `test_token_admin_unverified` (replace `test_token_admin_audience_refused`)
- `tests/test_rbac_schema.py`: `test_admin_leaf_permissions_seeded`
- Removed (moved into `test_bearer_route_permission_map`): `test_users_admin.py::test_users_routes_declare_permissions`, `test_rbac_apps_api.py::test_rbac_routes_declare_permissions`, `test_rbac_assignments_api.py::test_route_permission_map`. `test_role_catalog_api.py::test_only_catalog_route_listed_by_default` kept: it tests flag-off mounting, not the map.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff `All checks passed!`; format `362 files already formatted`; mypy `Success: no issues found in 88 source files`; pytest `1148 passed in 60.93s` (baseline before the change: 1038 passed).
- alembic: `alembic upgrade head` → `downgrade -1` → `upgrade head` on `org_admin_test` OK; `alembic check` → `No new upgrade operations detected.` on both `org_admin_test` and the default `org_admin` DB. The test session fixture also runs `downgrade base` + `upgrade head`.
- Local Postgres was already running; nothing set up.

### Notes on the plan
- Route enumeration: `RouteContext` exposes `dependant` and `path`/`methods` via `__getattr__` on FastAPI 0.162.2, so no `original_route` fallback was needed (only used to tell `APIRoute` from the Starlette docs routes).
- `test_cookie_routes_not_bearer_guarded`: without `X-MM-App` every cookie route answers `app_header_required`; with the app headers, with or without a valid admin Bearer token, every one answers 401 `session_invalid` with no `WWW-Authenticate`.
- Migration ids use `gen_random_uuid()` in SQL; `app_id` and keys are bound parameters.
- `aud` matching mirrors `tests/jwt_verifier.py` (PyJWT non-strict: a list `aud` containing `admin` would pass). We sign every token with a string `aud`, so this only matters for forged tokens, which the signature already blocks.

### Out-of-plan files
- `tests/test_errors.py` — `test_validation_error_details_have_no_input_or_ctx` posts to `/api/v1/organizations`, which now needs auth; `client` → `admin_client`.
- `tests/test_device_cookie.py` — `test_non_auth_route_ignores_cookie` GETs `/api/v1/organizations` and expected 200; `client` → `admin_client` (the test still proves the cookie is not touched on non-auth routes).

### Follow-ups (noticed, not done)
- CLAUDE.md R3 wording (plan Assumptions; orchestrator/owner applies), including `mm_device` → `__Host-mm_device` in class (2).
- spec §6.1 accepted-risk line is obsolete after merge (A8, product-manager).
- ADM-002b / ADM-002c per the plan's split; staff 2FA (OQ3); optional `ADMIN_ACCESS_TOKEN_TTL_SECONDS` (A3).
- `/docs` and `/openapi.json` stay public (A7, decided in ADM-002b).
- Consider `strict_aud=True` in the verifier and in `docs/consumers.md` together, so the server and consumers keep one rule set.
- `mypy` runs on `app` only (Makefile); the new test helpers are not type-checked by `make check`.

### Scope self-check
- Could the diff be smaller? No. Most of the diff is new tests and the docs the plan asks for. App code is about 250 lines.
- Diff size: +1326 / -179 lines (38 files, including new files).
