# Implementation log — ADM-012

## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| `app/core/cors.py` | Frozen `CORSPolicy`, `IDENTITY_POLICY` (current values exactly), `ADMIN_POLICY`, `RouteMatcher`; `DynamicCORSMiddleware(..., policy=IDENTITY_POLICY, path_prefix=None, matcher=None)` with exactly one scope selector (else `ValueError`); `Access-Control-Allow-Credentials` only when `policy.credentials` |
| `app/modules/rbac/origin_registry.py` | Same load also cached per key (`_by_key`); `is_allowed_for(key, origin)`, `for_app(key) -> AppOriginView`; failure clears both |
| `app/modules/rbac/repository.py` | `active_app_origins()` → `active_app_origins_by_key()` (active apps, key → origins) |
| `app/modules/admin_auth/route_classes.py` (new) | `dependant_calls`, `is_bearer_dependant`, `BearerRouteMatcher` (lazy, router order, first `Match.FULL` decides) |
| `app/main.py` | Second middleware instance: `registry=origin_registry.for_app(ADMIN_APP_KEY)`, `policy=ADMIN_POLICY`, `matcher=BearerRouteMatcher(app)`; comment updated |
| `tests/test_admin_cors.py` (new) | Real-app admin CORS tests with minted tokens (AC1–12) |
| `tests/test_cors.py` | Admin-policy unit tests with `FakeRegistry` + `FakeMatcher`; existing tests untouched |
| `tests/test_auth_cors.py` | Loader patches renamed to `active_app_origins_by_key` (lists wrapped as `{f"app{i}": o}`, assertions unchanged); old no-CORS test updated to `test_role_catalog_no_cors_admin_path_refuses_app_origin` (R7, plan §8); per-app view tests added |
| `tests/test_route_auth_classes.py` | Uses shared `dependant_calls` / `is_bearer_dependant`; adds `test_cors_matcher_agrees_with_bearer_map` |
| `docs/adr/0011-admin-cors.md` (new) | Decision record |
| `docs/adr/0006-dynamic-cors.md` | Amendment pointer to ADR-0011 |
| `docs/consumers.md` | "Calling the admin API from the admin UI" (Bearer only, no cookies on admin endpoints) |
| `docs/deploy.md` | One sentence in "Browser origins (CORS)" + "Admin UI CORS (ADM-012)" operator steps |

### Tests added / changed
- `tests/test_cors.py` › `test_admin_policy_preflight_allowed`, `test_admin_policy_header_case_insensitive` (4 params), `test_admin_policy_actual_request`, `test_admin_policy_existing_cache_control_kept`, `test_matcher_out_of_scope_untouched`, `test_matcher_receives_request_method_for_preflight`, `test_policy_requires_one_scope` (2 params)
- `tests/test_admin_cors.py` › `test_preflight_admin_origin_allowed` (4 params), `test_preflight_never_reaches_auth`, `test_preflight_disallowed_method_or_header` (4 params), `test_unknown_origins_refused` (5 params), `test_actual_get_with_bearer`, `test_actual_patch_with_bearer`, `test_error_envelopes_readable` (5 params: 401 no token, 401 bad token, 403, 404, 422), `test_no_origin_only_vary_and_no_store`, `test_no_allow_credentials_on_any_bearer_route` (25 params, every `BEARER` route), `test_cookie_only_is_401`, `test_other_app_origin_refused_on_admin_but_ok_on_auth`, `test_admin_origin_still_credentialed_on_auth`, `test_role_catalog_and_jwks_unchanged`, `test_roles_post_preflight_is_admin`, `test_loopback_admin_origin_refused_in_production`, `test_disabled_or_empty_admin_origins_refused`, `test_set_admin_origins_applies_in_process`
- `tests/test_auth_cors.py` › `test_registry_per_app_view`, `test_registry_per_app_view_ttl` (new); `test_role_catalog_and_admin_paths_have_no_cors` → `test_role_catalog_no_cors_admin_path_refuses_app_origin` (updated)
- `tests/test_route_auth_classes.py` › `test_cors_matcher_agrees_with_bearer_map` (new)

Test items in the four CORS/route files: 99 → 168 (+69).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`:
  - ruff: `All checks passed!`; format: `222 files already formatted`
  - mypy: `Success: no issues found in 101 source files`
  - pytest: `1691 passed, 1 warning in 98.21s` (warning: pre-existing `forkpty` DeprecationWarning in `test_cli_create_account.py`)
- alembic check: not applicable (no schema change). Local Postgres was already running.

### Out-of-plan files
None.

### Deviations from plan (minor)
- `test_preflight_disallowed_method_or_header`: the "method not served" case (`PUT /api/v1/apps` → no CORS headers) is asserted inside each parametrized case rather than as its own parameter.
- `test_admin_cors.py` imports `BEARER` and `_fill` from `tests/test_route_auth_classes.py` (the plan names `_fill`), so the route list stays defined once.
- In `test_registry_per_app_view_ttl` the "failing loader → view fails closed" check uses a second registry instance.

### Follow-ups (noticed, not done)
- From the plan: `X-Content-Type-Options: nosniff` on admin JSON responses; a Bearer endpoint listing an app's roles for the admin UI; `/auth/*` not echoing the admin origin when `X-MM-App` ≠ `admin`; CORS-readable 500s on admin routes; CLAUDE.md R11 cookie wording; deployment repo `TURNSTILE_ALLOWED_HOSTNAMES` += `admin-portal.mmlabs.online`; `admin.mmlabs.online` → `admin-portal.mmlabs.online` in ADR-0008 / deploy.md (ADM-014).
- `docs/deploy.md` "Set admin origins (ADM-002c)" still uses `https://admin.mmlabs.online` in its examples and host policy (left for ADM-014, as the plan says).

### Scope self-check
- Could the diff be smaller? No: every file is in the plan's list. Production code is close to the estimate.
- Diff size: production +181 / −32 (`cors.py` +80/−24, `origin_registry.py` +30/−2, `repository.py` +4/−4, `main.py` +12/−2, `route_classes.py` +55 new); tests +585 / −24 (`test_admin_cors.py` +379 new, `test_cors.py` +115/−1, `test_auth_cors.py` +67/−11, `test_route_auth_classes.py` +24/−12); docs +98 / −1 (ADR-0011 +53 new, ADR-0006 +6, consumers +20, deploy +19/−1).
