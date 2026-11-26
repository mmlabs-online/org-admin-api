## Attempt 1 — 2026-11-21

### Files touched
| File | Required because |
|---|---|
| `app/modules/usage/models.py` | `UsageLimitOverride` model |
| `app/db/models.py` | R2 registry import |
| `migrations/versions/20261121_911eb99ddf8b_adm040_usage_limit_overrides.py` | new table, `down_revision = 58a494f0449e` (ADM-034 head, verified with `alembic heads`) |
| `app/modules/usage/schemas.py` | Set / Remove / Read schemas |
| `app/modules/usage/repository.py` | `UsageLimitOverrideRepository` incl. `effective_limit()`, `db_now()` |
| `app/modules/usage/service.py` | module-level `resolve_app`; `UsageLimitOverrideService` |
| `app/modules/usage/router.py` | 4 routes under `/{metric_id}/overrides` |
| `app/modules/audit/actions.py` | target + 3 actions |
| `app/modules/audit/diff.py` | `USAGE_LIMIT_OVERRIDE` allowlist |
| `app/modules/usage/periods.py` | `is` -> `==` (4 lines, ADM-039 nit) |
| `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py` | R3 / R4 registries (AUDITED count 32 -> 34) |
| `docs/identity-architecture.md` | endpoint row |
| `tests/test_usage_overrides_api.py`, `_db.py`, `_migration.py` | new |
| `tests/test_usage_schemas.py`, `tests/test_usage_periods.py` | added cases |

### Tests added / changed
- test_usage_overrides_api.py (34 cases incl. 401/403/404 matrix, 201/200/204, no-op, audit rows, reason not logged, OpenAPI)
- test_usage_overrides_db.py (CHECK, unique, FKs, six `effective_limit` cases)
- test_usage_overrides_migration.py › test_downgrade_removes_table (PREVIOUS = `58a494f0449e`)
- test_usage_schemas.py › override set/remove cases; test_usage_periods.py › test_plain_string_period_matches_enum
- Audit coverage + auth-class registry entries for PUT and DELETE.

### Checks (actual output summary)
- ruff check app tests migrations: All checks passed; `ruff format --check`: 394 files already formatted. Repo-wide ruff only fails in the known `.github/scripts/risk_tier.py` (untouched).
- mypy app: Success, no issues found in 199 source files.
- alembic heads: `911eb99ddf8b (head)`; alembic check: No new upgrade operations detected.
- pytest (full, before final test fixes): 3772 passed, 3 failed (test-only bugs: interval bound as str; audit rows ordered by a constant txn `now()`). Fixed; re-ran the affected files 3x: 433 passed each, plus usage test files 123 passed.

### Out-of-plan files
None.

### Deviations from plan (merged code differed)
- `down_revision` is ADM-034's `58a494f0449e` (plan said `d7a3f5b2e801`); `PREVIOUS` in the migration test likewise.
- AUDITED count was 32 on main (not 32+2 of ADM-034's own); now 34. `_SUCCESS_STATUS_BY_ROUTE` gets the PUT -> 201 entry.
- Tests do not assert row order for audit/list rows created in one test: `created_at`/`occurred_at` are the constant transaction time under the rolled-back test session.
- The DELETE audit diff has three fields (`expires_at` null -> null is not a change).

### Follow-ups (noticed, not done)
- Plan follow-ups stand (admin UI, expired-row purge, `created_by` index, `admin:usage:write` seed description).
- `mypy tests` has ~140 pre-existing errors (not part of `make check`, which runs `mypy app`).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: see `git diff --stat`.
