## Attempt 1

**Security review: BUG-001 (MATERIALIZED CTE for the purge batches)**

I checked this by reading the code and the diff only. As you asked, I did not run `make check` or pytest.

**Findings**

- 🟡 should-fix: `/home/user/org-admin-api/tests/purge_plan.py:23-26`. `explain()` is never called; no test imports it. It builds SQL with `literal_binds=True` and passes it to `text("EXPLAIN " + sql)`. It is test-only and every input is trusted, so there is no real risk today. Still, it is dead code that sets a string-built SQL pattern someone could copy later. Fix: delete `explain()` and the `postgresql` and `Executable` imports it uses (R7, minimal diff).
- 💭 note: `tests/purge_plan.py:20`. `text(f"SET LOCAL {guc} = off")` interpolates names from the module-level constant tuple `GUCS`, not input, so it is not injectable. `SET LOCAL` only lasts for the current transaction. The `db_session` fixture (`tests/conftest.py:99-111`) runs each test in an outer transaction that is always rolled back, so the planner settings cannot leak to other tests. Nothing under `app/` imports `tests.purge_plan`.
- 💭 note: there are stray `tests/__pycache__/_explore_tmp*.pyc` files in the working tree. Make sure no `_explore_tmp.py` gets committed. The diff stat shows it is not in the branch.

**Focus points checked**

- **Predicates unchanged, no wider deletes.** All four sites keep their original filters: `_purgeable(cutoff)`, `_send_log_where(older_than)`, `_token_where(older_than)` and `_where(retention, cooldown_seconds)`. Ordering and `limit(batch)` are also the same. The only change is moving the id selection from `.scalar_subquery()` to `.cte("batch").prefix_with("MATERIALIZED")`, then `IN (SELECT id FROM batch)`.
  - Audit: `cutoff()` still refuses anything under the 90-day `MIN_DAYS`. The DB trigger floor is untouched. `open_batch`/`close_batch` still turn the `audit.allow_purge` latch on and off with `SET LOCAL`.
  - Join requests: the retention floor check is still there (`test_retention_floor` covers it). Pending rows are excluded by `_where`, which this diff does not touch.
  - The fix narrows behaviour: one batch can no longer delete more than `batch` rows.
- **SKIP LOCKED / lock_timeout preserved.** `with_for_update(skip_locked=True)` is still on the inner select at all four sites. With MATERIALIZED, the rows are selected and locked once, which is the intended behaviour. `SET LOCAL lock_timeout = '2s'` is unchanged: in `open_batch` for audit, and `set_lock_timeout()` for the auth repository (`app/modules/auth/repository.py:276-278`) and the join-requests repository (`app/modules/join_requests/repository.py:144-146`). Both are still called from their retention services.
- **No raw SQL injection.** All four sites are built only with the SQLAlchemy Core API and bound parameters.
- **Regression tests.** There is one test per site (four in total). Each forces a nested-loop rescan and asserts that exactly `batch` rows were deleted and the newer rows remain.

**Follow-ups (out of scope)**

- None.

**Verdict:** PASS from a security standpoint. No blockers; one optional cleanup (remove the unused `explain()` helper).
