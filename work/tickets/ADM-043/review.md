## Attempt 1 — 2026-11-25
Verdict: PASS

Reviewed commit 37d0af0 (branch claude/festive-volta-rikup7) against origin/main; batch bookkeeping under `work/` ignored.

### Checks (run by reviewer)
- `uv run pytest -q` (full suite) — `4220 passed, 2 warnings in 881.37s`, exit 0. The suite was re-run after the implementer's test-only fix and agrees with impl-log (4219 passed + 1 test fixed = 4220).
- `uv run ruff check` / `ruff format --check` on the 7 changed .py files — `All checks passed!`, `7 files already formatted`. (The kit lint failure in `.github/scripts/risk_tier.py` is out of scope and the file is untouched.)
- `uv run mypy app` — `Success: no issues found in 215 source files`.
- `uv run alembic upgrade head` on the LOCAL dev DB (`localhost:5432/org_admin`, the config default; no `.env`) — `c91e4b7a3d58 -> d4a8e2c6f175`. Then `uv run alembic check` — `No new upgrade operations detected.` The diff has no migration or model change.
- `python3 .github/scripts/risk_tier.py --base origin/main --explain` — **medium**. Every app file is "default" (medium). No High path is in the diff: `app/api/router.py` is not `app/api/deps.py`, and nothing under `app/core`, `app/db`, `migrations`, `admin_auth` or `rbac` changed.

### Acceptance criteria
- [x] 1 Current period with limit/used/remaining/bounds; active override replaces default — `test_current_default_without_override`, `test_current_active_override_applies`, `test_current_zero_override_blocks` pass. Effective-limit CASE at `app/modules/usage/staff_read_repository.py:71-82`.
- [x] 2 Expired override falls back and shows `active: false` — `test_current_expired_override_falls_back` passes. Expiry is checked on the DB clock (`func.now()`, repository.py:72).
- [x] 3 Archived metrics are listed with `status`; a non-member gets 200 with `member: false` — `test_current_includes_archived_metric` and `test_current_non_member_200` pass (tests/test_usage_staff_read_api.py:131-140, override applies to the outsider). There is no member filter in the query (repository.py:86-98), and `member` comes from `is_active_app_member` (staff_read_service.py:70).
- [x] 4 History is `Page[T]`, newest first, filtered, with no limit field — `test_periods_paged_newest_first`, `test_periods_filters`, `test_periods_has_no_limit_field` pass. Order at repository.py:132; schema `UsagePeriodTotalRead` (staff_read_schemas.py:36-41) has no `limit`.
- [x] 5 Ledger is `Page[T]`, newest first, with a `state` filter and never `idempotency_key` — the explicit column select (repository.py:159-170) omits it, and `UsageEventRead` (schemas.py:44-56) has no such field. `test_events_never_return_idempotency_key` (test file:267-291) asserts that neither the key name nor the raw key value is in the body and checks the exact key set. The other two responses come from models with no idempotency field (schemas.py:10-41).
- [x] 6 401/403 — `test_routes_401_without_token`, `test_routes_401_with_app_credential` (an `mmak_` `usage:read` key gets 401 on all 3 routes), `test_routes_403_without_read` (`admin:usage:write` gets 403 naming `admin:usage:read`) and `test_routes_200_with_admin_star` all pass.
- [x] 7 404 matrix and 422 — `test_404_matrix` covers 4 cases × 3 routes: unknown app, `admin` app (`resolve_app`, service.py:45-51), unknown user (staff_read_service.py:49-50) and unknown metric (`usage_metric_not_found`, :53-55; archived keys are found via `find_metric` any status, meter_repository.py:81-87). `test_422_validation` covers naive `from`/`to`, `from >= to`, bad metric, `limit=101`/`0`. `test_422_bad_uuid_metric_and_state` covers a bad UUID on 3 routes and `state=bogus`. All pass.
- [x] 8 Registry — tests/test_route_auth_classes.py:177-179 adds the 3 routes to `BEARER` with `"admin:usage:read"`, and the registry tests pass in the full run.
- [x] 9 Checks green — see above.

### Auth / permission review (no review-trigger agent for this ticket)
- The router-level `dependencies=[Depends(current_admin)]` (staff_read_router.py:23-25) enforces Bearer `aud=admin` on every route. Each of the 3 GETs declares `**require_permission(READ, ...)` (staff_read_router.py:46, 61, 86), with `READ = "admin:usage:read"` (app/modules/usage/router.py:30), which `admin:*` covers.
- The routes use one auth class (R3 class 1). The `user-usage` prefix does not collide with the class-4a `/apps/{key}/usage/users/{user_id}` app-credential route. App credentials get 401 (tested).
- The routes are read-only, so no audit entry is needed (R4) and there is no logging of request values. Responses contain no email, token or idempotency key (R5/R11).
- Lists use `PageDep` (`limit` 1..100, app/core/pagination.py:13). Current-period reads use one query (no N+1) on the existing indexes.

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1 layering holds: the router only calls the service, the service raises only `AppError` subclasses, and the repository is SQLAlchemy only. R7 scope holds: every changed non-`work/` file is in the plan's file list. R9: the exception is documented in CLAUDE.md. D36 is updated in docs/identity-architecture.md:334.

### Blockers from review agents
None (no review-trigger agent run for this ticket).

### Nits (do not block)
- staff_read_repository.py:135 and :176 use `# type: ignore[arg-type]` when building row dataclasses from tuples. Typed `Row` unpacking or `.tuples()` would avoid the ignores.
- `check_range` runs before `_resolve` (staff_read_service.py:105, :143), so an inverted range on an unknown app returns 422 rather than 404. This is acceptable, but the order is not documented.
- The plan named Alembic head `c91e4b7a3d58`. The head is now `d4a8e2c6f175` (from USR-33). There is no impact.

### Follow-ups (out of scope)
- Admin web UI for these reads (plan: "Ming verification after merge: None").

### Review agents (attempt 1)
- None triggered (Areas `api` only; no Review-trigger path in the diff). The reviewer checked the auth/permission aspects directly.
