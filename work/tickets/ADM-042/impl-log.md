## Attempt 1 — 2026-11-22

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261122_c91e4b7a3d58_adm042_usage_counters_events.py` | R2: `usage_counters`, `usage_events`, scope CHECK widened; `down_revision = b7d3f1a9c542` (ADM-038 head, verified with `alembic heads`) |
| `app/db/models.py` | registry import and `__all__` |
| `app/modules/usage/models.py` | `UsageCounter`, `UsageEvent`, `UsageEventState`, `IDEMPOTENCY_KEY_RE` |
| `app/modules/usage/metering.py` | pure rules |
| `app/modules/usage/meter_repository.py`, `meter_service.py`, `meter_schemas.py`, `app_router.py`, `errors.py` | R1 layers, 4 routes, 7 error classes |
| `app/api/router.py` | mount |
| `app/modules/app_credentials/models.py`, `deps.py`, `service.py` | three scopes, scope-set auth, failed-auth hook, `limits:write` issuance refused |
| `app/modules/rbac/repository.py` | `is_active_app_member` (OQ5 predicate) |
| `app/modules/audit/actions.py` | denied-only `usage.*` actions |
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | usage buckets, `enforce_failure_limit`, 10 settings |
| `CLAUDE.md`, `docs/identity-architecture.md` (D36), `docs/consumers.md` | R3/R9 text, decision row, consumer contract |
| tests (see below) | R8 |

### Tests added / changed
- New: `test_usage_metering.py`, `test_usage_consume_api.py`, `test_usage_settle_refund_api.py`, `test_usage_read_api.py`, `test_usage_auth.py`, `test_usage_concurrency.py` (committed rows, deleted by id), `test_usage_ledger_db.py`, `test_usage_ledger_migration.py`, `usage_factories.py`.
- Changed: `test_app_credential_scope_migration.py` (`'bogus:scope'` literal, `test_usage_scopes_round_trip`), `test_app_credentials_api.py` (3 tests; unknown-scope test uses `bogus:scope`), `test_app_credential_auth.py` (scope-set tests), `test_route_auth_classes.py` (registry to frozensets, 4 routes), `test_audit_coverage.py` (4 entries, no-row test), `test_rate_limit.py` (`test_enforce_failure_limit`).
- The two caplog tests (`test_idempotency_key_never_logged`, `test_refund_writes_info_log_without_key`) re-enable the loggers and assert an app-module line was captured.

### Checks (actual output summary)
- ruff check: only the 13 known kit errors in `.github/scripts/risk_tier.py` (untouched); format --check: only that file; none in my files.
- typecheck (`mypy app`): Success: no issues found in 209 source files.
- `alembic check`: No new upgrade operations detected.
- test (full, run after the last code change): 4044 passed, 1 warning in 748.79s.

### Out-of-plan files
None.

### Deviations from the plan
- Migration `down_revision` is ADM-038's `b7d3f1a9c542` (not `a4c81e6d27f3`); `PREVIOUS` in the migration test likewise.
- Service/repository files are named `meter_*` as planned; the failed-auth bucket counts 401 and 403 from the credential check (incl. wrong scope and mismatch).
- `test_usage_consume_api.py` keeps the 129-char key case in the API test only; the DB CHECK test omits it (varchar(128) rejects it first).
- ADM-038 touched `releases/` and `bug_reports/` and added D37: no overlap; D36 row sits between D35 and D37.

### Follow-ups (noticed, not done)
- Move `read_json_body` / `inline_schema` from `releases/body.py` to `app/core` (imported cross-module now).
- ADM-045 purge job must keep events for 90 days; ADM-044 enables `limits:write` issuance.
- `make lint` fails on `.github/scripts/risk_tier.py` (kit file).

### Scope self-check
- Could the diff be smaller? No: every file is in the plan list.
- Diff size: about +2900 / -25 lines including tests and docs.
