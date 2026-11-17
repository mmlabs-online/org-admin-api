# Implementation log — USR-03g

## Attempt 1

### Files touched
Migration / model
- `migrations/versions/20261117_a9d4c27e6b13_usr03g_join_requests.py` (new, `down_revision = f3a8d51c9e27`, the single head from `alembic heads`) — AC1, R2
- `app/modules/join_requests/__init__.py`, `models.py` (`AppJoinRequest`, `JoinRequestStatus`) — AC1
- `app/db/models.py` — register the model (R2)

Staff API (new module)
- `app/modules/join_requests/repository.py`, `schemas.py`, `service.py`, `router.py` — AC3, R1, R3, R4
- `app/main.py` — mount the router

Cookie side / shared
- `app/modules/membership/service.py` — `usable_default_role`, `join_available`, approval branch, `JoinResult.request_id`, cooldown — AC2, AC5
- `app/modules/membership/errors.py` — `JoinRejectedError`; docstring of `JoinApprovalUnavailableError`
- `app/modules/membership/schemas.py` — `JoinPendingResponse`
- `app/modules/auth/router.py` — join 202 + cooldown; token passes cooldown
- `app/modules/tokens/service.py`, `app/modules/organizations/auth_service.py`, `app/modules/organizations/auth_router.py` — use `join_available` with the cooldown
- `app/modules/audit/actions.py` — `JOIN_REQUEST` target, 3 actions
- `app/core/config.py`, `.env.example` — `JOIN_REJECT_COOLDOWN_SECONDS` (R5)

Docs: `docs/consumers.md`, `docs/identity-architecture.md`

### Tests added / changed
- New: `tests/join_request_factories.py`, `tests/test_join_requests_migration.py`, `tests/test_join_requests_join.py`, `tests/test_join_requests_api.py`
- Changed: `tests/test_auth_join.py` (approval case dropped, closed only), `tests/test_auth_token.py` (approval cases), `tests/test_auth_orgs.py` (approval `join_available`), `tests/test_route_auth_classes.py` (3 BEARER entries, `request_id`), `tests/test_audit_coverage.py` (approve/reject, `len(AUDITED) == 20`, per-route success status, comment on the join entry)
- Test-only deviations from the plan: the 401/403 test is parametrised over the 3 routes in `test_join_requests_api.py`; `test_not_pending_409`, `test_second_approve_409` and `test_unknown_and_cross_app_404` are parametrised/merged versions of the planned names.

### Check results
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff check "All checks passed!", ruff format "290 files already formatted", mypy "Success: no issues found in 133 source files", pytest "2180 passed, 1 warning in 194.85s". No flake occurred.
- `alembic upgrade head` -> `downgrade -1` -> `upgrade head` all ran; `alembic check`: "No new upgrade operations detected."; `alembic heads`: `a9d4c27e6b13 (head)`.

### Out-of-plan files
None.

### Notes
- Staff tests that approve need `admin_user=<real user>`: the override principal otherwise has a random id and `granted_by`/`decided_by` FKs fail (a real staff token always has an existing user).
- `join_requests.created_at` is `now()` (transaction time), so rows created in one transaction order by `id`; the list test accounts for this.
- Size: no split needed.

### Follow-ups
As listed in `plan.md` (cancelled status on disable, retention purge, pending cap, deny-list, reject reason, `details.join_pending`). `ticket.md` stale wording is left to the orchestrator.

### Scope self-check
Every file above is in the plan's file list. No refactors beyond extracting `usable_default_role`. Nothing committed.
