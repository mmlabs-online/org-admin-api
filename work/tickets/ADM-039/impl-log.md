
## Attempt 1 — 2026-11-20

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261120_8c2f6a1d4e93_adm039_usage_metrics.py` | R2; table + seed of `admin:usage:read/write`; chained on head `5b7d1e9c3a42` (confirmed with `alembic heads`); working downgrade |
| `app/db/models.py`, `app/api/router.py` | models registry, mount |
| `app/modules/usage/{__init__,models,periods,schemas,errors,repository,service,router}.py` | the module per plan |
| `app/modules/audit/actions.py`, `app/modules/audit/diff.py` | R4 actions/target, allowlist |
| `docs/adr/0024-usage-metrics.md`, `docs/identity-architecture.md` (§4 row, D33), `docs/deploy.md` (query 2a) | ADR / API map / seed audit query |
| `tests/test_usage_periods.py`, `test_usage_schemas.py`, `test_usage_metrics_api.py`, `test_usage_metrics_schema_db.py`, `test_usage_metrics_migration.py` | plan tests |
| `tests/test_route_auth_classes.py`, `test_openapi_contract.py`, `test_audit_coverage.py`, `test_rbac_schema.py` | registries (AUDITED recounted 30 -> 32; `metric_id` added to `_fill`) |

### Tests added / changed
- New files above (periods, schemas, API incl. 401/403/404/409/412/422 and audit/reason, DB constraints, migration round trip); registries extended.
- Pydantic checks `min_length` before `strip`, so `Reason`/`DisplayName` use an after-validator `_trimmed` (blank "   " is 422).
- Audit rows of one transaction share `occurred_at`; tests compare them order-insensitively. Test keys use a counter, no randomness.

### Checks (actual output summary)
- ruff (`. --exclude .github/scripts`): All checks passed
- format:check (app tests migrations): 364 files already formatted
- typecheck: Success: no issues found in 189 source files
- alembic check: No new upgrade operations detected; downgrade -1 / upgrade head round trip OK
- test (full `make test`): 3317 passed, 1 warning in 527.43s
- env: no Docker; `service postgresql start` (was already accepting connections).

### Out-of-plan files
None. (`tests/test_route_auth_classes.py::_fill` gained `metric_id`, part of the planned registry edit.)

### Follow-ups (noticed, not done)
- F1 `DELETE /apps/{key}` with metrics gives the generic 409 message; add `app_has_usage_metrics` reason.
- F2 admin-pan screens and rendering of `changes.reason`.
- F3 optional `apps.usage_default_period`.

### Scope self-check
- Could the diff be smaller? No.
