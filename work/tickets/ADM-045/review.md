# Review — ADM-045

## Attempt 1 — 2026-11-26
Verdict: REWORK

### Checks (run by reviewer)
- `uv run ruff check` + `ruff format --check` on the 7 changed .py files — "All checks passed!", "7 files already formatted". (Repo-wide ruff: only the known kit file `.github/scripts/risk_tier.py`, out of scope.)
- `uv run mypy app` — "Success: no issues found in 216 source files".
- `uv run pytest -q` (full suite, local DB) — "4244 passed, 2 warnings in 786.88s", exit 0. Matches impl-log.md:23.
- `uv run alembic check` (local test DB) — "No new upgrade operations detected."
- Order check: `uv run pytest -q tests/test_usage_events_retention.py tests/test_cli_usage_events_purge.py tests/test_audit_logs_api.py tests/test_usage_concurrency.py` — "7 failed, 54 passed" (see Issue 1 and Follow-ups).

### Acceptance criteria
- [x] 1 Old events deleted — `tests/test_usage_events_retention.py:70` passes; `app/modules/usage/retention.py:57-70`.
- [x] 2 Recent events kept (`<` cutoff) — `tests/test_usage_events_retention.py:78`; `retention.py:61`.
- [x] 3 Counters untouched — `tests/test_usage_events_retention.py:85`; `retention.py` never references `UsageCounter`.
- [x] 4 Dry run is the default and changes nothing — `app/cli.py` `_usage_events_purge` (`--apply` store_true, default False); `retention.py:85-88` READ ONLY + count only; `tests/test_usage_events_retention.py:105,121`, `tests/test_cli_usage_events_purge.py:74`. Passes in default order only — see Issue 1.
- [x] 5 Batched, SKIP LOCKED, commit per batch, cutoff once on DB clock — `retention.py:35-42` (`SELECT now() - make_interval`), `:89-90` cutoff computed once then commit, `:62-66` MATERIALIZED CTE `FOR UPDATE SKIP LOCKED` LIMIT, `:107` commit per batch; `tests/test_usage_events_retention.py:133,176`.
- [x] 6 Counts only, summary per deleting batch — `retention.py:94-104` changes = {cutoff, retention_days, deleted}, actor SYSTEM_CLI; `tests/test_usage_events_retention.py:152`, `tests/test_cli_usage_events_purge.py:108` (no key, user id, `@`); CLI error path prints a fixed line, never `str(exc)` (`app/cli.py` `_usage_events_purge` except branch; test `:121`).
- [x] 7 Config floor 90 and >= adjust window — `app/core/config.py:149` (`ge=90`), `:281-285` validator; `.env.example:115` key without a value; `tests/test_config.py › test_usage_event_retention_floor_and_window`.
- [x] 8 Locked rows skipped, not waited on — `tests/test_usage_events_retention.py:188` passes.
- [x] 9 Purged key consumes again — `tests/test_usage_events_retention.py:228`.
- [ ] 10 `make check` green — green in the default order; the new tests are order-dependent (Issue 1).

Scope: every changed file is in the plan's file list (`plan.md` "Files to create / modify"). No migration, no model change.

### Issues
#### Issue 1 — 🔴 blocker — test isolation (committed-row cleanup breaks another new test in reverse order)
- **Where**: `tests/test_cli_usage_events_purge.py:91-94` together with `tests/test_usage_events_retention.py:188-211`.
- **Expected**: `test_skips_locked_rows` commits an append-only `usage_events.purged` summary row that cannot be cleaned up. Other tests in this ticket must not depend on that row's absence, in either file order.
- **Actual**: `test_default_is_dry_run` asserts that no `usage_events.purged` row exists anywhere in `audit_log`. Running `pytest tests/test_usage_events_retention.py tests/test_cli_usage_events_purge.py` (retention file first) fails it with `FAILED tests/test_cli_usage_events_purge.py::test_default_is_dry_run`. It passes in the full suite only because `test_cli_…` sorts before `test_usage_…`.
- **Fix instruction**: in `test_default_is_dry_run`, only look at rows from this transaction, the same way `_summaries` does in `tests/test_usage_events_retention.py:62-67`. Import and use `_summaries(db_session)` (or add `AuditLog.occurred_at >= func.now()` to the query), then assert it is `[]`. Re-run the two files in both orders plus the full suite, and record the output in impl-log.md.

### Rules (CLAUDE.md)
No violations found. R1: the SQL is in `UsageEventRetentionRepository` and the rules are in the service. R4: the summary row is written in the batch transaction (`retention.py:94-107`). R5: the config value comes from env and is listed in `.env.example:115` without a value. R11: the output has counts and the cutoff only. R7: changes stay in scope.

### Blockers from review agents
None.

### Nits (do not block)
- `tests/test_usage_events_retention.py:203`: `result.deleted >= 2` is loose on purpose (committed old rows in the shared DB). That is fine; a comment saying why would help.

### Follow-ups (out of scope)
- `tests/test_audit_logs_api.py` asserts exact unfiltered `total` counts, so it fails whenever a test that commits audit rows runs first. This was already true before this ticket: running all existing `tests/test_*concurrency*.py` first gives "5 failed". `test_skips_locked_rows` adds one more such row (6 failures when it runs first). Make those assertions immune to committed rows (filter by test-owned ids or by the transaction's `now()`).

## Attempt 2 — 2026-11-26
Verdict: PASS

### Checks (run by reviewer)
- `uv run pytest tests/test_usage_events_retention.py tests/test_cli_usage_events_purge.py -p no:randomly` (retention first, the attempt-1 failing order) — 23 passed.
- `uv run pytest tests/test_cli_usage_events_purge.py tests/test_usage_events_retention.py -p no:randomly` (reverse order) — 23 passed.
- `uv run pytest` (full suite) — 4244 passed, 2 warnings in 740 s.
- `uv run ruff check app tests migrations` — All checks passed. `uv run ruff format --check app tests migrations` — 438 files already formatted. (Known kit lint issue in `.github/scripts/risk_tier.py` is out of scope.)
- `uv run mypy app` (Makefile `typecheck`) — Success: no issues found in 216 source files.
- `uv run alembic check` — No new upgrade operations detected.

### Acceptance criteria
- Attempt-1 criteria evidence unchanged (no source change since eb2b111); see Attempt 1.
- [x] Attempt-1 Issue 1 fixed — evidence: `tests/test_cli_usage_events_purge.py:89` now asserts `await _summaries(db_session) == []`, and `_summaries` (`tests/test_usage_events_retention.py:62-67`) filters `AuditLog.occurred_at >= func.now()`, so only this transaction's rows count; both file orders pass (above).

### Scope
`git diff eb2b111..8bfc001`: only `tests/test_cli_usage_events_purge.py` (fix + removed unused `select`/`AuditLog` imports) and `work/tickets/ADM-045/impl-log.md`. Commit 3c398f4 touches only `work/tickets/ADM-045/ticket.md` (state). No app, migration or config change. impl-log claims (23 passed both orders, 4244 full suite) match the reviewer's run.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.

### Blockers from review agents
None.

### Nits (do not block)
- Attempt-1 nit on `tests/test_usage_events_retention.py:203` still applies.

### Follow-ups (out of scope)
- Same as Attempt 1 (`tests/test_audit_logs_api.py` order sensitivity to committed audit rows).

### Review agents (attempt 2)
- appsec-engineer: attempt 1 `Verdict: PASS` (no 🔴/🟡, 3 💭) stands; the attempt-2 rework (8bfc001) changed only `tests/test_cli_usage_events_purge.py` and `impl-log.md`. No blockers from review agents.
