# Implementation log — ADM-043

## Attempt 1

### Files touched
- `app/modules/usage/staff_read_schemas.py` (new) — response models.
- `app/modules/usage/staff_read_repository.py` (new) — R1 repository; staff variant of `read_user_usage` without the active-only/expiry filters.
- `app/modules/usage/staff_read_service.py` (new) — R1 service, `check_range`.
- `app/modules/usage/staff_read_router.py` (new) — 3 Bearer `admin:usage:read` GETs.
- `app/api/router.py` — mount router.
- `tests/test_usage_staff_read_api.py` (new) — plan's tests (404 matrix and 422 parametrized).
- `tests/test_route_auth_classes.py` — 3 `BEARER` entries.
- `CLAUDE.md` — R9 exception text (Q6).
- `docs/identity-architecture.md` — D36 sentence.

### Checks
- `ruff check app tests`: All checks passed; `ruff format app tests`: unchanged.
- `mypy app`: Success, no issues in 215 source files.
- `pytest tests/test_usage_staff_read_api.py tests/test_route_auth_classes.py`: 370 passed.
- Full pytest: 4219 passed, 1 failed (test ordering tie on frozen now(), fixed in the test; file re-run 44 passed). Full suite not re-run after that test-only fix.
- `alembic heads`: d4a8e2c6f175 (no migration; plan said c91e4b7a3d58, USR-33 moved head). `alembic check` could not run: local DB not upgraded ("Target database is not up to date").
- Known kit lint failure in `.github/scripts/risk_tier.py` untouched.

### Out-of-plan files
None.

### Deviations
- `_resolve` uses `UsageMeterRepository.find_metric` (any status) and `UserRepository.get`; no deviation from plan otherwise.

### Follow-ups
None.

### Scope self-check
Diff limited to the plan's file list.
