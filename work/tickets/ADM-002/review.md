# Review — ADM-002

## Attempt 1 — 2026-11-15
Verdict: PASS

Judged against `plan.md` slice 1 (guard sites + flag removal = ADM-002b, admin origins CLI = ADM-002c).

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff `All checks passed!`; format `364 files already formatted`; mypy `Success: no issues found in 88 source files`; pytest `1148 passed in 65.51s`. Matches impl-log (1148 passed). Format count differs (impl-log says 362, reviewer saw 364), but both pass and the tree is clean, so this is not a blocker.
- alembic check: `uv run alembic check` → `No new upgrade operations detected.`; single head `5e2a9c7d1f04`. Each test session runs downgrade, because `tests/conftest.py:67-68` does `command.downgrade(cfg, "base")` and then `upgrade head`. That executes the new `downgrade()` (`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py:51-56`) on every run, and the run is green.

### Acceptance criteria
- [x] AC1: no token, bad token or wrong `aud` → 401 envelope + `WWW-Authenticate`. Evidence:
  - `tests/test_admin_auth.py::test_no_token_401`, `::test_rejected_tokens_401[expired, wrong_aud, wrong_iss, unknown_kid, alg_none, hs256, typ_jwt, not_a_jwt]`, `::test_unknown_sub_401`, `::test_non_uuid_sub_401`, `::test_disabled_user_401`, `::test_unverified_user_401` all pass.
  - Verifier unit tests (`tests/test_access_token.py`, 28 tests) cover `typ`, missing kid, required claims, `email_verified` exactly `True`, wrong signature with the right kid, future `iat`, garbage input, and no token in `str`/`repr`.
  - The verifier checks `typ` (`app/core/access_token.py:36`), `alg` == ES256 before decode (`:38`), kid membership (`:41`), `algorithms=[ES256]` with aud/iss/leeway/require (`:44-51`), and `email_verified is True` (`:65`).
- [x] AC2: Bearer only, so the cookie never authenticates. `current_admin`'s only credential input is `Security(bearer_scheme)` (`app/modules/admin_auth/deps.py:52`, `HTTPBearer(auto_error=False)`), so a non-Bearer scheme gives `None` → 401 (`:54`). Evidence:
  - `test_admin_auth.py::test_cookie_only_401`, `::test_query_token_401`, `::test_basic_scheme_401`, `::test_empty_bearer_401`.
  - `test_route_auth_classes.py::test_every_bearer_route_401_with_cookie_only[×24]` (valid device cookie on every guarded operation → 401 `unauthorized`).
- [x] AC3: 403 `permission_denied` with `details.required` (`deps.py:97`); `admin:*` grants everything; permissions are reloaded from the DB per request (`app/modules/admin_auth/service.py:27-33`). Evidence: `test_missing_permission_403`, `test_leaf_permission_allows_only_its_routes`, `test_super_admin_wildcard_allows_all`, `test_other_app_wildcard_does_not_grant_admin`, `test_role_revoked_after_issue_403`, `test_expired_assignment_403`, `test_disabled_after_issue_401`.
- [x] AC4: no admin route escapes the guard. Every router has router-level `Depends(current_admin)`: organizations `router.py:16-18`, users `router.py:19`, rbac `router.py:28`, assignments `assignments_router.py:18-20`. Every route has exactly one `**require_permission(...)`.
  - `test_route_auth_classes.py::test_every_route_is_classified` enumerates through `iter_route_contexts` with both flags on (`:107-120`). It denies by default: any route that is not in PUBLIC/COOKIE and lacks `current_admin` plus exactly one `RequirePermission` fails, and non-APIRoute paths must be a subset of DOCS.
  - `::test_bearer_route_permission_map` pins all 24 operations to their keys and checks that each matches `x-permission`.
  - `::test_allow_lists_have_no_stale_entries` and `::test_public_and_cookie_routes_have_no_bearer_guard` pass.
- [x] AC5: public and cookie routes stay reachable without Bearer. Evidence: `test_public_routes_reachable_without_token`, `test_cookie_routes_not_bearer_guarded` (a valid admin Bearer does not stand in for the cookie: still `session_invalid`, no `WWW-Authenticate`), plus the existing auth, health, jwks and catalog suites, all green.
- [x] AC6: admin token issuance. The refusal is removed (`app/modules/tokens/service.py:54-56`). Evidence: `tests/test_auth_token.py::test_token_admin_audience_issued` (:305), `::test_token_admin_not_member` (:323), `::test_token_admin_unverified` (:337), and `test_admin_auth.py::test_issued_admin_token_end_to_end`.
- [x] AC7: seed migration with a working downgrade. Evidence: `tests/test_rbac_schema.py::test_admin_leaf_permissions_seeded` (:52; checks the 8 keys, `is_admin`, and that super-admin still holds only `admin:*`), the downgrade/upgrade cycle in the session fixture, and `alembic check` clean.
- [x] AC8: OpenAPI. Evidence: `test_route_auth_classes.py::test_openapi_bearer_scheme` (scheme shape, `security` + 401/403 on every Bearer op, none on public/cookie ops).
- [x] AC9: users/RBAC routers stay unmounted in staging/production.
  - Mounting is still behind the flags (`app/main.py:91-96`), and the validator still refuses them in staging/production (`app/core/config.py:146-153`, message now says ADM-002b).
  - Tests: `tests/test_config.py::test_rbac_flag_refused_in_staging_and_production`, `tests/test_users_admin.py::test_users_api_flag_refused_in_staging_and_production` (:69), `::test_users_api_not_mounted_by_default` (:60). All are unchanged and green.
- [x] AC10: `make check` green (above).

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: routers only add dependencies, and the user/permission reload lives in `AdminAuthService` → `UserRepository`/`AuthzService`.
- R2: data-only migration with a reversible `downgrade()`, and its known limitation is documented in the docstring.
- R3: class 1 is implemented and the enumeration test enforces it.
- R5: no new config.
- R6: 401/403 go through `AppError` with headers.
- R7: only two out-of-plan files (`tests/test_errors.py`, `tests/test_device_cookie.py`), both justified in impl-log because they hit `/organizations`.
- R11: denial logs hold only the reason, jti and user_id (`deps.py:45-46`, `:90-95`), checked by `test_denials_logged_without_secrets`; `InvalidAccessTokenError` carries only the reason.

### Nits (do not block)
- 💭 `verify_access_token` uses PyJWT non-strict `aud`, so a list `aud` containing `admin` passes. This is not exploitable, because only this server's key signs and it always emits a string `aud`. It is already listed as a follow-up (`strict_aud=True` together with `docs/consumers.md`).
- 💭 `test_public_routes_reachable_without_token` uses a loose assertion (`status != 401 or code != "unauthorized"`). The extra no-`WWW-Authenticate` check makes it adequate.
- 💭 `_deny("user", ...)` merges unknown, disabled and unverified users into one log reason. A finer reason would help ops, but it is not needed.

### Follow-ups (out of scope)
- ADM-002b (guards, removing the flags, `/docs` decision) and ADM-002c (admin origins CLI), as in the plan.
- CLAUDE.md R3 wording, including `mm_device` → `__Host-mm_device`; spec §6.1 accepted-risk line is now obsolete (A8).
- `strict_aud=True` in the verifier and in consumers.md; optional admin TTL ceiling (A3); staff 2FA (OQ3).
- `mypy` does not cover `tests/` (Makefile).
