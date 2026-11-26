## Attempt 1 — 2026-11-26

### Files touched
| File | Required because |
|---|---|
| app/modules/usage/retention.py (new) | repository + service for the batched purge |
| app/modules/audit/actions.py | `USAGE_EVENTS_PURGED` summary action (R4) |
| app/core/config.py | `usage_event_retention_days` (ge=90) + validator vs adjust window |
| .env.example | `USAGE_EVENT_RETENTION_DAYS=` (R5) |
| app/cli.py | `usage-events-purge [--apply]` command, docstring, constants |
| Makefile | `usage-events-purge` target |
| docs/deploy.md | runbook section |
| tests/test_usage_events_retention.py, tests/test_cli_usage_events_purge.py (new), tests/test_config.py | plan tests |

### Tests added / changed
- test_usage_events_retention.py: all 12 planned service/repository tests (incl. SKIP LOCKED on committed data, cleaned by id; the append-only `usage_events.purged` summary row from that test stays, so `_summaries` only reads rows of the current transaction).
- test_cli_usage_events_purge.py: usage errors, default dry run, apply, counts only, db failure, DB_ECHO.
- test_config.py: test_usage_event_retention_floor_and_window.

### Checks (actual output summary)
- ruff check / format --check app tests: clean. mypy app: Success (216 files).
- alembic heads: d4a8e2c6f175 (no migration); alembic check: no drift.
- pytest (full, once after last change): 4244 passed, exit 0.
- `make check` not run as a whole (known kit lint failure in risk_tier.py); steps run directly.

### Out-of-plan files
None. Deviation: head is d4a8e2c6f175 (plan said c91e4b7a3d58); `.env.example` key placed after USAGE_ADJUST_WINDOW_SECONDS.

### Follow-ups (noticed, not done)
- docs/identity-architecture.md audit-action list; flag style differs from `cleanup`/`audit-purge`.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: ~+260 source/docs, ~+380 tests.

## Attempt 2 (REWORK)

- Files touched: `tests/test_cli_usage_events_purge.py` (required because: review blocker; `test_default_is_dry_run` now asserts via `_summaries` from `tests/test_usage_events_retention.py`, which only sees this transaction's audit rows; removed the now-unused `select`/`AuditLog` imports).
- Committed summary row from `test_skips_locked_rows` not cleaned up: the audit log is append-only; the filtered query makes it harmless.
- Checks: ruff format/check clean; both file orders: 23 passed; full suite: 4244 passed.
- Out-of-plan files: none. Follow-up: pre-existing `test_audit_logs_api` order sensitivity (separate).
- Scope self-check: only the listed issue fixed.
