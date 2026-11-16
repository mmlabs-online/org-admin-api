# Review — ADM-012

## Attempt 1 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): ruff `All checks passed!`; format `222 files already formatted`; mypy `Success: no issues found in 101 source files`; pytest `1691 passed, 1 warning in 103.13s` (pre-existing `forkpty` DeprecationWarning in `test_cli_create_account.py`). Matches impl-log.
- alembic check: not applicable (no model/migration change; `repository.py` change is a query only).

### Acceptance criteria
- [x] AC1 preflight from an admin origin: 204, ACAO, 3-part Vary, `DELETE, GET, PATCH, POST, PUT`, `Authorization, Content-Type`, Max-Age 600, no ACAC, never routed — evidence: `tests/test_admin_cors.py:89` `test_preflight_admin_origin_allowed` (4 routes), `:105` `test_preflight_never_reaches_auth` (no token → 204, no `WWW-Authenticate`), `tests/test_cors.py` `test_admin_policy_preflight_allowed` (`inner.calls == []`); code `app/core/cors.py:99-101,121-143`.
- [x] AC2 strict method/header checks, case-insensitive — `test_admin_cors.py:116` (`x-mm-app`, `cookie`, `if-match`, mixed → no `Allow-*`; `PUT /apps` → no CORS at all), `test_cors.py` `test_admin_policy_header_case_insensitive`.
- [x] AC3 actual requests carry ACAO + `Expose-Headers: Retry-After, ETag, X-Request-ID`, no ACAC on 200/201/401 (no/bad token, `WWW-Authenticate` kept)/403/404/422 — `test_admin_cors.py:161,171,195`.
- [x] AC4/AC5 only `apps[admin].origins`: unknown, stoktok (other app), `null`, duplicate, case variant refused on preflight and actual; loopback dropped in production; empty/disabled admin row refused — `test_admin_cors.py:137,277,336,351`; registry per-key view `app/modules/rbac/origin_registry.py:62-65,89-93,99-107` (admin instance wired with `for_app(ADMIN_APP_KEY)`, `app/main.py:63-68`; union `_origins` never consulted by the view). `test_auth_cors.py` `test_registry_per_app_view`.
- [x] AC6/AC7 no `Origin` → no ACAO, `Vary: Origin` + `Cache-Control: no-store` (also on 401); route-set Cache-Control kept — `test_admin_cors.py:225`, `test_cors.py` `test_admin_policy_existing_cache_control_kept`; code `cors.py:107-118`.
- [x] AC8 no ACAC on any Bearer route, preflight or actual — `test_admin_cors.py:247` parametrized over all 25 `BEARER` routes with a real token; ACAC emitted only under `if policy.credentials` (`cors.py:113,137`), `ADMIN_POLICY.credentials=False` (`cors.py:42-49`).
- [x] AC9 classification by route: catalog `GET /apps/{key}/roles` no CORS and keeps `ETag`/`no-cache`, `POST` preflight same path is admin, JWKS keeps `ACAO: *` — `test_admin_cors.py:305,320`; `test_route_auth_classes.py` `test_cors_matcher_agrees_with_bearer_map`. Matcher built lazily on first call, after all routers are included (`app/modules/admin_auth/route_classes.py:48-50`), first `Match.FULL` decides (`:51-55`); the same `is_bearer_dependant` definition is used by `test_route_auth_classes._guard`.
- [x] AC10 `/auth/*` unchanged — `IDENTITY_POLICY` (`cors.py:34-41`) equals the removed constants byte-for-byte: methods `{GET,POST}` → emitted `GET, POST` (sorted, as before), headers `{content-type,x-mm-app}` / `"Content-Type, X-MM-App"`, expose `Retry-After`, max-age 600, ACAC `true`; header emission order unchanged in both preflight and actual paths; `path_prefix` scope check unchanged (`cors.py:78-79`); identity instance constructed exactly as before (`main.py:58-62`). Existing `tests/test_cors.py` tests untouched (diff is the import line + appended admin section only). `test_admin_cors.py:291` admin origin still credentialed on `/auth/token`.
- [x] AC11 cookie-only → 401, readable, no ACAC — `test_admin_cors.py:261`.
- [x] AC12 `set-admin-origins` applies in-process after commit and within the 60 s TTL cross-process; `--clear` removes ACAO; fails closed — `test_admin_cors.py:364`, `test_auth_cors.py` `test_registry_per_app_view_ttl` (+59 s still allowed, +60 s refused; broken loader → refused). View is stateless (`origin_registry.py:99-107`), so `after_commit` invalidation applies.
- [x] AC13 scopes disjoint — `test_cors_matcher_agrees_with_bearer_map` asserts no `BEARER` path under `/api/v1/auth/`.
- [x] AC14 docs — `docs/adr/0011-admin-cors.md` (decision §1–7, 500 caveat, Max-Age upper bound, catalog/JWKS, consequences), `docs/adr/0006-dynamic-cors.md` amendment, `docs/consumers.md` "Calling the admin API from the admin UI" (Bearer only, `credentials: "omit"`, no cookie, no `X-MM-App`), `docs/deploy.md` "Admin UI CORS (ADM-012)" operator steps.
- [x] AC15 `make check` green (above).

### Issues
None blocking.

### Scope
Every `app/`, `tests/` and `docs/` file (except `docs/spec-v1.md`) is in the plan's file list. The deliberate change of `test_role_catalog_and_admin_paths_have_no_cors` and the loader-patch rename in `tests/test_auth_cors.py` are justified in plan §8. Other branch files (`work/backlog.md`, `work/tickets/ADM-002e|ADM-013..017|USR-08|USR-10/*`, `docs/spec-v1.md` §5.9–5.11) are Markdown ticket/spec bookkeeping from `/idea`/`/ticket`, no code.

### Rules R1–R11
No violations found. R1: no router/service change; middleware is HTTP plumbing; repository stays SQLAlchemy-only. R2: no schema change. R3: no route changes class; classification reuses the R3 definition. R5: no new config. R6: envelopes unchanged. R11: admin CORS echoes only `apps[admin].origins`, never credentialed; log line unchanged (truncated origin, debug).

### Nits (do not block)
- Plan assumption "HEAD on a Bearer GET route is classified admin" is not true: FastAPI `APIRoute` does not add `HEAD`, so `BearerRouteMatcher(..., "HEAD")` returns False for `/api/v1/apps` (checked by reviewer). Harmless (HEAD is 405 there anyway), but ADR-0011 could say "no FULL match incl. HEAD".
- `route_classes.py` relies on `fastapi.routing.iter_route_contexts` / `RouteContext` (FastAPI 0.162 internals) while `pyproject.toml` allows `fastapi>=0.115`. The lock pins 0.162.2 so builds are fine; consider raising the floor.
- `_in_scope` builds `Headers` for every HTTP request before the scope check (negligible cost).

### Follow-ups (out of scope)
- As listed in plan/impl-log: `nosniff` on admin JSON; Bearer endpoint for an app's roles; `/auth/*` not echoing the admin origin when `X-MM-App` ≠ `admin`; CORS-readable 500s; CLAUDE.md R11 cookie wording; deployment `TURNSTILE_ALLOWED_HOSTNAMES`; `admin.mmlabs.online` → `admin-portal.mmlabs.online` (ADM-014).
- Raise `fastapi` lower bound in `pyproject.toml` to the version providing `iter_route_contexts`.
