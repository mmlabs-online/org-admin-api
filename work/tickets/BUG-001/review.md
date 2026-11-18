## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (local Postgres 16.15) passed: ruff "All checks passed!", format "322 files already formatted", mypy "Success: no issues found in 147 source files", pytest **2424 passed** in 247.93s. The manifest flake (`test_changing_push_writes_one_row`) did not show up, so no rerun was needed.
- `uv run alembic check` reported "No new upgrade operations detected." (no migration in this diff).
- **ADR-0012 fail-before-fix, re-run by the reviewer.** I made a scratch worktree at HEAD and checked out the `origin/main` versions of `app/modules/audit/purge.py`, `app/modules/auth/repository.py` and `app/modules/join_requests/repository.py`. Then I ran `pytest -k rescanned` on the four new tests, 3 times. Each run gave **3 failed, 1 passed**:
  - `test_delete_batch_respects_limit_when_subquery_rescanned`: `assert 5 == 2`
  - `test_purge_send_log_respects_limit_when_subquery_rescanned`: `assert 5 == 2`
  - `test_purge_tokens_respects_limit_when_subquery_rescanned`: `assert 5 == 2`
  - the join_requests test passed, which matches the impl-log note "not forced, fixed by analogy".
  
  After I restored the fixed `app/` files in the worktree, the purge, cleanup, join-retention and email-lock-concurrency files gave 59 passed. I then removed the worktree. The main working tree is clean.

### Acceptance criteria
- [x] **The regression test reproduces the bug.** It fails on main and passes after the fix. Evidence: `tests/test_cli_audit_purge.py::test_delete_batch_respects_limit_when_subquery_rescanned` (my re-run above). The deterministic `assert 5 == 2` matches the CI signature `[5]` (one batch drained all 5 rows). The impl-log records the step-0 EXPLAIN (`Nested Loop Semi Join`, inner side `Limit -> LockRows -> Index Scan`, no Materialize) and 10/10 failures before the fix.
- [x] **Expected behaviour holds.** `test_purge_batches_share_cutoff` is unchanged and passed in my full run. The impl-log records 5 consecutive green `make check` runs (runs 2–6). The one failure in run 1 was the unrelated manifest flake.
- [x] **`make check` and `alembic check` pass**, as shown above.
- [x] **Plan criterion 2 (all four sites).** None of the three purge files has a `scalar_subquery` purge statement left. Each site now uses `.cte("batch").prefix_with("MATERIALIZED")` + `id IN (SELECT batch.id)`, and the where clause, ORDER BY, `skip_locked=True` and `synchronize_session=False` are unchanged (`app/modules/audit/purge.py:61-72`, `app/modules/auth/repository.py:304-332`, `app/modules/join_requests/repository.py:170-184`).

### Issues
None.

### Rules R1–R11
No violations found. The change stays in the repository layer (R1). There is no schema change (R2). The summary rows and their transaction are unchanged (R4). R7: the three non-audit sites, `tests/purge_plan.py` and the backlog/ticket state rows are all in the plan.

### Nits (do not block)
- `tests/purge_plan.py:23-26`: `explain()` is unused. The plan allowed it as an optional helper, and appsec flagged it too. Deleting it later would be fine.
- Compared with the original, the `tokens` regression test (`tests/test_cli_cleanup.py:290`) has no docstring.

### Follow-ups (out of scope)
- **CI runs `postgres:17-alpine`; local is PG 16.** The forcing recipe was only checked on PG 16. If PG 17 plans differently, the regression tests could pass vacuously on CI. The fix still holds, because a materialized CTE locks once whatever the plan. It would still be worth one CI-side EXPLAIN check, or an `explain()`-based plan-shape assertion that skips when the shape is not reached.
- Open a separate bug for the `tests/test_app_manifest_api.py::test_changing_push_writes_one_row` flake: the `'c' not in` assertion fails when random hex contains `c`.
- Severity S3 → S2 needs the owner's decision (plan recommendation).

## Attempt 2 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- Scope: I reviewed only `git diff HEAD~1 -- tests`, as instructed. It changes two files: `tests/purge_plan.py` and `tests/test_cli_cleanup.py`.
- `service postgresql start` (PG 16), then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`. Results: ruff "All checks passed!", format "322 files already formatted", mypy (`uv run mypy app`) "Success: no issues found in 147 source files", pytest **2424 passed** in 220.88s with 1 warning (a pty DeprecationWarning that was already there). The manifest flake did not show up, so no rerun was needed.
- alembic check: not applicable, because this attempt has no migration or app change.

### Acceptance criteria
- [x] **The 🟡 from security-review.md Attempt 1 is fixed.** `explain()` is gone from `tests/purge_plan.py`. Its now-unused imports (`Executable`, `sqlalchemy.dialects.postgresql`) were removed with it. `grep` shows that no test references `explain`. The only import from `tests.purge_plan` is still `force_rescanned_semijoin`, in `tests/test_cli_audit_purge.py:20`, `tests/test_cli_cleanup.py:22` and `tests/test_join_requests_retention.py:17`.
- [x] **The missing docstring has been added** to `test_purge_tokens_respects_limit_when_subquery_rescanned` (`tests/test_cli_cleanup.py:293`).
- [x] The attempt-1 acceptance criteria still hold: the full suite is green, including all four `*_respects_limit_when_subquery_rescanned` regression tests.

### Issues
None.

### Rules R1–R11
No violations found. This is a tests-only diff, inside the rework scope (R7).

### Nits (do not block)
- None.

### Follow-ups (out of scope)
- These are unchanged from Attempt 1: the PG 17 CI plan-shape check, the separate bug for the `test_changing_push_writes_one_row` flake, and the S3 → S2 severity decision for the owner.
