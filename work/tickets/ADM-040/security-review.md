# Security review — ADM-040

## Attempt 1
Verdict: PASS

Static review of 5f4d7d8 against origin/main. I did not run pytest or `make check`, as instructed. I found no blockers. One should-fix about audit accuracy is small enough to fix in this ticket or as a follow-up.

**Checked and correct**
- **Permissions (R3):** The router uses `READ = "admin:usage:read"` for both GETs and `WRITE = "admin:usage:write"` for PUT and DELETE (app/modules/usage/router.py:30-31, 365, 387, 412, 443). `current_admin` is on the router. All 4 routes are in the BEARER registry with matching permissions (tests/test_route_auth_classes.py:150-162).
- **404 matrix:** No existence leaks across apps.
  - The `admin` app and unknown apps both get "App not found" (`resolve_app`, app/modules/usage/service.py:547-553).
  - A metric of another app gets the same "Usage metric not found" as an unknown metric, because the lookup is scoped by `app.id` (service.py:623-628).
  - Every override query is keyed by the resolved `metric.id`, never by the raw path id.
- **Reason (ADR-0024 §5):**
  - It is required on both PUT and DELETE, and on DELETE it comes in the JSON body (`UsageLimitOverrideRemove`, schemas.py:486-489), not the query string.
  - It is stored only in audit `changes.reason` (service.py:773).
  - It is not in `UsageLimitOverrideRead` and there is no DB column for it.
  - The log lines carry ids and keys only (service.py:713-720, 748-754).
- **Validation:**
  - `extra="forbid"` is set on both request models.
  - `Limit` is strict int, 0..MAX_LIMIT, and the DB CHECK matches the migration text exactly.
  - `expires_at` must be `AwareDatetime` and is compared with the DB `now()` (service.py:665).
  - The 422 handler strips `input` and `ctx` (app/core/errors.py:215-217), so submitted values are not echoed back.
- **Upsert race:** `SELECT … FOR UPDATE`, then `INSERT … ON CONFLICT DO NOTHING RETURNING`, then a re-lock if another insert won. This is correct under READ COMMITTED, because the new statement sees the committed winner. The before-snapshot is taken after the lock with `populate_existing`, and a no-op request writes neither a row nor an audit entry.
- **Audit:** Each audit entry is written in the same session and transaction as its change. It holds only ids, `limit_value` and `expires_at`, with `org_id=None` and no email. DELETE uses `DELETE … RETURNING`, so the "from" values are read atomically.
- **Migration:** It matches the model.
  - User FK is CASCADE, the same as `role_assignments`. That is the right call for personal data, and the audit log keeps the history.
  - Metric FK is RESTRICT. Metrics are never deleted, and `usage_metrics.app_id` is already RESTRICT, so this adds no new block on deleting an app.
  - `created_by` is SET NULL.
  - `downgrade()` drops the index, then the table. It loses all overrides, but this is documented and the audit log keeps the history.
- **periods.py `is` → `==`:** Correct, and it fixes a real latent bug. `UsageMetric.period` is a plain `str` column (models.py:63), so with `is` a DB value like `"week"` or `"month"` would never match and would silently fall through to day logic. A regression test exists (`test_plain_string_period_matches_enum`).

**Findings**

🟡 **Replace audit records a false `expires_at` change when the client sends a non-UTC offset** (app/modules/usage/service.py:695-703).
- The no-op check compares datetimes by instant, which is correct.
- But the audit "before" value comes from the DB row, which asyncpg returns in UTC (`…+00:00`). The "after" value is `diff.to_json(data.expires_at)`, which keeps the client's offset.
- Example: the current value is `2026-12-01T00:00:00+00:00` and the client sends `2026-12-01T07:00:00+07:00` (the same instant) with a new limit. The `.updated` entry then shows `expires_at: {from: "…T00:00:00+00:00", to: "…T07:00:00+07:00"}`, which is a change that did not happen. A genuine change is also recorded in the client's offset instead of UTC.
- The existing test (tests/test_usage_overrides_api.py:412-426) only sends `limit`, so it does not catch this.
- Fix: normalise once, before the no-op compare, the diff and `apply`:
  ```python
  expires_at = None if data.expires_at is None else data.expires_at.astimezone(UTC)
  ```
  Alternatively, convert to UTC in an `AfterValidator` on `UsageLimitOverrideSet.expires_at`.
- Add a test: set `expires_at` in UTC, then PUT the same instant written as `+07:00` with a new limit, and assert the audit changes contain only `limit_value` and `reason`.

💭 **Rare 500 in a three-way race** (app/modules/usage/service.py:691-694). T1's insert hits `DO NOTHING` because T2's row has committed. If T3 deletes that row and commits before T1's second `get_for_update`, `existing` is `None` and the assert raises a 500. This is extremely unlikely with staff-only callers. A hardening option is to loop insert-then-lock once more, or raise a 409 instead of asserting.

💭 **404 messages say which part was missing** ("App not found", "Usage metric not found", "User not found"; service.py:549-552, 627, 635, 650). This does not leak anything across apps, because a metric of another app reads the same as an unknown metric. The only thing it reveals is whether a user id exists, and only to staff who hold `admin:usage:read` and know the UUID. That is acceptable, and it matches the plan's single shared 404 description.

💭 **`db_now()` returns the transaction start time** (`now()`; repository.py:197-200). So the "must be in the future" check has the same clock as `_ACTIVE` and `effective_limit`. That is consistent; nothing to change.

**Follow-ups (out of scope)**
- ADM-042 / ADM-044: when a `SystemActor` sets overrides, `created_by` will be `NULL`. Make sure the audit actor still identifies the key that made the change.
