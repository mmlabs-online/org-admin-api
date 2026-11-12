# Implementation log — USR-03e

## Attempt 1 — 2026-11-12

### Files touched
| File | Required because |
|---|---|
| `app/core/cors.py` (new) | `OriginRegistry` protocol + pure-ASGI `DynamicCORSMiddleware` (scope `/api/v1/auth/`, raw exact match, preflight answered in middleware, `Vary` merge, `no-store`, `Retry-After` exposed). AC1–4, 6, 8. |
| `app/modules/rbac/origin_registry.py` (new) | `AppOriginRegistry` (TTL 60 s, single-flight lock, generation check, fail closed 5 s, 5 s load timeout), `WeakSet` + `invalidate_all()`, `after_commit` / `after_soft_rollback` listeners on `session.info`. AC5, 7. |
| `app/modules/rbac/origins.py` | `is_loopback_origin` (moved from auth service) and `allowed_origins(origins, app_env)`. AC5. |
| `app/modules/rbac/repository.py` | `APPS_CHANGED_KEY`, `active_app_origins()`, `mark_apps_changed()`. R1. |
| `app/modules/rbac/service.py` | `AppService.create` / `update` (only when changed, after save) / `delete` call `mark_apps_changed()`. AC7. |
| `app/modules/auth/service.py` | `AppContextService.check` uses `allowed_origins`; `_is_loopback` and `_LOOPBACK_HOSTS` removed. Behaviour unchanged. |
| `app/main.py` | `_CredentialedCORS` / `CORSMiddleware` removed; registry on `app.state.origin_registry` (session factory `SessionLocal`); middleware added unconditionally with `path_prefix=f"{api_prefix}/auth/"`. |
| `app/core/config.py` | `cors_origins` and `_no_wildcard_origin` removed. AC9. |
| `app/modules/tokens/jwks_router.py` | Docstring points at `app.core.cors`. |
| `.env.example` | `CORS_ORIGINS` removed. |
| `tests/conftest.py` | `_use_test_session(app, db_session)` helper called from `make_client` and `make_strict_app`: registry reads through the test transaction. |
| `tests/test_cors.py` (new) | Middleware unit tests (fake registry, tiny inner ASGI app). |
| `tests/test_auth_cors.py` (new) | Real app + DB: endpoints, registry, invalidation. |
| `tests/test_rbac_origins.py` | Helper tests. |
| `tests/test_config.py` | Wildcard test replaced by `test_cors_origins_env_ignored`; section heading fixed. |
| `tests/test_jwks.py` | `test_jwks_no_credentials_with_cors_origin` replaced. |
| `docs/adr/0006-dynamic-cors.md` (new) | ADR. |
| `docs/deploy.md` | "Browser origins (CORS)" section. |
| `docs/consumers.md` | "Calling the identity API from the browser" section; prerequisites line. |
| `docs/identity-architecture.md` | S4 pointer to ADR-0006. |
| `docs/adr/0004-device-sessions-and-login.md` | §8 last sentence updated. |

### Tests added / changed
- `tests/test_cors.py` › `test_preflight_allowed`, `test_preflight_unknown_origin`, `test_preflight_bad_method_or_header` (PUT, DELETE, `authorization`, `x-mm-app, x-other`), `test_preflight_allowed_header_variants` (case/space, absent ACRH), `test_preflight_never_reaches_app`, `test_actual_request_allowed_origin_echoed`, `test_actual_request_unknown_origin_no_acao`, `test_null_and_missing_origin_no_acao` (incl. two `Origin` headers), `test_case_variant_origin_not_echoed`, `test_vary_origin_always_and_merged`, `test_no_store_added_when_absent`, `test_existing_cache_control_kept`, `test_out_of_scope_paths_untouched`, `test_prefix_lookalike_untouched`, `test_non_http_scope_passthrough`
- `tests/test_auth_cors.py` › `test_preflight_token_from_registered_origin`, `test_login_cross_origin_credentialed_echo`, `test_error_envelopes_readable_cross_origin` (401/403/422/429), `test_disabled_app_origin_not_echoed`, `test_loopback_origin_not_echoed_in_production`, `test_role_catalog_and_admin_paths_have_no_cors`, `test_registry_ttl_and_single_flight`, `test_registry_fails_closed`, `test_registry_invalidation_during_refresh`, `test_invalidated_after_commit_not_on_rollback`, `test_app_patch_takes_effect_before_ttl`
- `tests/test_rbac_origins.py` › `test_is_loopback_origin`, `test_allowed_origins_drops_loopback_in_staging_production`
- `tests/test_config.py` › `test_cors_origins_env_ignored` (replaces `test_cors_origins_wildcard_refused`)
- `tests/test_jwks.py` › `test_jwks_wildcard_without_credentials_for_registered_origin` (replaces `test_jwks_no_credentials_with_cors_origin`)

### Checks (actual output summary)
- Postgres was already accepting connections on localhost:5432 (no action needed).
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 3 times: ruff "All checks passed!", "270 files already formatted", mypy "Success: no issues found in 71 source files", pytest "767 passed" (34.50 s, 34.50 s, 31.85 s).
- Extra: `mypy tests/test_cors.py tests/test_auth_cors.py tests/conftest.py` → no issues.
- alembic check: n/a (no schema change).
- Plan AC1 grep: `CORSMiddleware`/`_CredentialedCORS` gone; `cors_origins` only matches the plan-mandated log key `cors_origins_refresh_failed` and the test name `test_cors_origins_env_ignored` (and `DynamicCORSMiddleware` matches `CORSMiddleware` as a substring). `app/core/cors.py` imports nothing from `app.modules`.

### Out-of-plan files
None.

### Deviations / notes
- `tests/conftest.py`: one small `_use_test_session` helper shared by `make_client` and `make_strict_app` instead of two inline copies.
- `test_registry_fails_closed` re-enables the module logger via monkeypatch: Alembic's `fileConfig` (session migrate fixture) disables loggers created before it ran, so `caplog` saw nothing otherwise.
- Registry tests patch `RbacRepository.active_app_origins` (the "counting loader") rather than faking a session.
- On invalidation during an in-flight load, the loaded set is stored but `_expires_at = 0`, so the next call reloads (as planned).

### Follow-ups (noticed, not done)
- Plan F1–F4 (origin uniqueness / admin exclusivity; admin UI CORS from `apps[admin].origins`; remove leftover `CORS_ORIGINS` from `mmlabs-online/deployment`; shared cache before multiple workers).
- Owner: CLAUDE.md R11 still describes `mm_device`, `Path=/api/v1/auth` (D20 changed it) and could mention ADR-0006 (plan A4).
- Loggers created at import are disabled by `migrations/env.py` `fileConfig` in the test process (and possibly any process that runs migrations in-process); consider `disable_existing_loggers=False`.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +920 / -66 lines across 21 files (excluding this log); code ~200, tests ~450, docs ~110.
