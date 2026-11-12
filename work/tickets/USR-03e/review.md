# Review — USR-03e

## Attempt 1 — 2026-11-12
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff "All checks passed!", "271 files already formatted", mypy "Success: no issues found in 71 source files", pytest "767 passed in 32.16s". Matches impl-log (767 passed; formatter count 271 vs 270 reported is immaterial, tree clean).
- alembic check: n/a (no model/schema change; `git diff` touches no `models.py` or `migrations/`).
- Plan AC1 greps: `CORSMiddleware`/`_CredentialedCORS` only remain as the substring of `DynamicCORSMiddleware`; `cors_origins` only in the log key `cors_origins_refresh_failed` and the test name; `grep -n "app.modules" app/core/cors.py` empty.

### Acceptance criteria
- [x] AC1 Pure-ASGI `DynamicCORSMiddleware` behind `OriginRegistry` replaces the static middleware — evidence: `app/core/cors.py:25-66`, `app/main.py:36-43`; `tests/test_cors.py` all green.
- [x] AC2 Scope exactly `/api/v1/auth/` — evidence: `app/core/cors.py:36`, `app/main.py:42`; `test_cors.py::test_out_of_scope_paths_untouched`, `::test_prefix_lookalike_untouched`, `test_jwks.py::test_jwks_wildcard_without_credentials_for_registered_origin`, `test_auth_cors.py::test_role_catalog_and_admin_paths_have_no_cors`.
- [x] AC3 Raw exact echo + credentials + `Expose-Headers: Retry-After`; `Vary: Origin` always, merged — evidence: `app/core/cors.py:50-62`; `test_cors.py::test_actual_request_allowed_origin_echoed`, `::test_actual_request_unknown_origin_no_acao`, `::test_vary_origin_always_and_merged`, `::test_null_and_missing_origin_no_acao`, `::test_case_variant_origin_not_echoed`; `test_auth_cors.py::test_login_cross_origin_credentialed_echo`.
- [x] AC4 Preflight answered in middleware: 204, `GET, POST`, `Content-Type, X-MM-App`, `Max-Age 600`; denied → 204 without `Access-Control-Allow-*`; preflight `Vary` — evidence: `app/core/cors.py:47-49,68-87`; `test_cors.py::test_preflight_allowed`, `::test_preflight_unknown_origin`, `::test_preflight_bad_method_or_header`, `::test_preflight_allowed_header_variants`, `::test_preflight_never_reaches_app`; `test_auth_cors.py::test_preflight_token_from_registered_origin`.
- [x] AC5 Disabled apps not echoed; loopback ignored in staging/production via the shared helper — evidence: `app/modules/rbac/origins.py:63-74`, `app/modules/rbac/repository.py:34-38` (`status = active`), `app/modules/auth/service.py:92`; `test_auth_cors.py::test_disabled_app_origin_not_echoed`, `::test_loopback_origin_not_echoed_in_production`; `test_rbac_origins.py::test_is_loopback_origin`, `::test_allowed_origins_drops_loopback_in_staging_production`; existing `test_auth_app_context.py` green.
- [x] AC6 401/403/422/429 envelopes carry CORS headers (500 documented as not) — evidence: middleware added via `add_middleware` (inside ServerErrorMiddleware, outside ExceptionMiddleware); `test_auth_cors.py::test_error_envelopes_readable_cross_origin` (all four statuses, envelope, `no-store`, `Retry-After` exposed); ADR-0006 Consequences.
- [x] AC7 Registry: TTL 60 s, single-flight, fail closed 5 s, generation check, invalidated after commit not on rollback — evidence: `app/modules/rbac/origin_registry.py:30-98`, `app/modules/rbac/service.py:207,250,265`; `test_auth_cors.py::test_registry_ttl_and_single_flight`, `::test_registry_fails_closed` (log has class name only, no DSN), `::test_registry_invalidation_during_refresh`, `::test_invalidated_after_commit_not_on_rollback`, `::test_app_patch_takes_effect_before_ttl` (origin change and disable both effective without advancing the clock).
- [x] AC8 `Cache-Control: no-store` when absent, existing kept — evidence: `app/core/cors.py:61-62`; `test_cors.py::test_no_store_added_when_absent`, `::test_existing_cache_control_kept`.
- [x] AC9 `CORS_ORIGINS` removed (setting, validator, `.env.example`, tests); stale env ignored — evidence: `app/core/config.py` diff, `.env.example` diff; `test_config.py::test_cors_origins_env_ignored`.
- [x] AC10 Docs — evidence: `docs/deploy.md` "Browser origins (CORS)", `docs/consumers.md` "Calling the identity API from the browser", `docs/adr/0006-dynamic-cors.md`, `docs/identity-architecture.md` S4, `docs/adr/0004-device-sessions-and-login.md` §8, `app/modules/tokens/jwks_router.py` docstring. Content matches the implementation.
- [x] AC11 `make check` green — see Checks.

Ticket AC "`CORS_ORIGINS` removed, or kept only for admin (owner decides)": removed; recorded as owner confirmation A1 in plan and ADR-0006 (non-blocking, reversible).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: `app/core/cors.py` imports nothing from `app/modules`; SQLAlchemy queries only in `RbacRepository` (`active_app_origins`, `mark_apps_changed`); service calls the repo. The `after_commit` listener in `rbac/origin_registry.py` is infrastructure wiring, not a service/router ORM access.
- R2: no schema change.
- R3: no new endpoint; credentialed CORS limited to `/api/v1/auth/*`; admin routes and role catalog get none; JWKS keeps `ACAO: *` uncredentialed.
- R5/R11: one env key removed from `.env.example`; refresh failure logs exception class only (tested); rejected origin logged at debug, truncated; ACAO echoes only `apps.origins` with credentials; CSRF check (`AppContextService.check`) unchanged and still uncached.
- R6: envelopes unchanged, now readable cross-origin.
- R7: every changed code/doc file is in the plan's file list; `work/` and the earlier `solo:` deploy.md commit are outside this ticket's code.
- R8: tests cover every AC (see above).

### Nits (do not block)
- 💭 `app/core/cors.py:80` compares `Access-Control-Request-Method` case-sensitively. Correct per Fetch (browsers uppercase GET/POST), just noting it is intentional.
- 💭 `app/modules/rbac/origin_registry.py:51` creates `asyncio.Lock()` in `__init__`; fine on 3.12 (loop bound lazily), and one registry per `create_app`.
- 💭 impl-log reports "270 files already formatted"; the reviewer run shows 271. No functional impact.

### Follow-ups (out of scope)
- Plan F1: origin uniqueness across apps / `admin` app origins exclusive, before ADM-002 enables `aud=admin`.
- Plan F2: admin UI (Bearer, non-credentialed) CORS from `apps[admin].origins`, with ADM-002 / admin UI ticket.
- Plan F3: delete any leftover `CORS_ORIGINS` from `mmlabs-online/deployment`.
- Plan F4: shared cache or pub/sub invalidation before more than one worker/replica.
- Owner: CLAUDE.md R11 still says cookie `mm_device`, `Path=/api/v1/auth` (D20 made it `__Host-mm_device`, `Path=/`); could also reference ADR-0006 (plan A4).
- Owner: confirm A1 (`CORS_ORIGINS` removed entirely) and A3 (ricotdin staging as second origin vs separate app key; affects USR-07).
- `migrations/env.py` `fileConfig` disables loggers created before it runs (seen in tests); consider `disable_existing_loggers=False` so `cors_origins_refresh_failed` warnings are not silenced in any process that runs migrations in-process.
