## Attempt 1

## Security review: ADM-015 (`GET /api/v1/stats/dashboard`)

I reviewed `git diff origin/main...HEAD` (commit ddcf13c) by reading code only. I did not run `make check` or pytest, as you asked.

**Verdict:** PASS with one 🟡 should-fix that needs an owner decision. There are no 🔴 blockers.

### Findings

**🟡 1. `members_per_app` exposes the app catalog, not only counts.**
- **Where:** `app/modules/stats/schemas.py:55-58` (`AppMembers.app_key`, `AppMembers.name`), `app/modules/stats/service.py:70` and `app/modules/stats/router.py:68-70`.
- **Risk:** Someone who holds only `admin:stats:read` gets the key and display name of every app. That includes disabled apps and the `admin` app. Today that list sits behind `admin:apps:read` (`app/modules/rbac/router.py:40,63`). The public role catalog returns 404 for disabled and `admin` apps, so this is a real step up.
- The owner's OQ4 says "counts only". OQ4 also puts `member_count` (ADM-016) under `admin:apps:read`.
- Adding `name` was the planner's choice ("saves the SPA a second call", `plan.md:155`), not the owner's.
- Impact is low: these are internal staff and only metadata leaks, with no PII. But it breaks the "counts only" promise the permission description makes in the migration and in D26.
- **Fix (pick one):**
  - (a) Get explicit owner acceptance. Record in D26, in `docs/identity-architecture.md` §4/§8 and in the endpoint description that `admin:stats:read` also reveals app keys, names and the disabled-app list.
  - (b) Drop `name` and keep only `app_key`. Better, show only active apps in `members_per_app`, or gate the per-app list on `admin:apps:read`.
  - My recommendation is (a): the dashboard needs labels, and staff with stats access are the same population.

**💭 2. Failed sign-in counts and the size of the `admin` app's membership are visible to a stats-only holder.** The owner accepted counts (OQ4/OQ5), and nothing per-email or per-IP comes out. `count_actions_since` returns only `{action: count}` (`app/modules/audit/repository.py:81-89`). No ids, emails, actors or audit rows reach the response. Nothing to fix.

**💭 3. Raw SQL is safe.** In `app/modules/stats/repository.py:11-22`:
- `:now`, `:months` and `:tz` are all bound parameters.
- `tz` and `months` come from service constants (`service.py:20-22`), never from the request.
- The endpoint takes no path, query or body input.
- `make_interval(months => :months - 1)` and `AT TIME ZONE :tz` are bound, not formatted into the string. There is no injection path.
- If the time zone ever becomes a request or config value, validate it against `pg_timezone_names` first, or Postgres will return a 500.

**💭 4. Query cost and DoS are acceptable.**
- The audit count is bounded by `ix_audit_log_action (action, occurred_at, id)` over the 24 h window, and a test checks this with EXPLAIN.
- There is no `users.created_at` index, so the sign-up series does a seq scan of `users` (possibly 12 times, as a nested loop over months). The same goes for the user, org and member aggregates.
- That is fine at the current size and for an authenticated, permission-gated endpoint. If `users` grows large (100k+), check p95 and consider a `created_at` index or a single scan with `date_trunc(... AT TIME ZONE)` bucketing.
- There is no rate limit beyond the Bearer and permission guard. That matches the other admin reads. Follow-up only.

**💭 5. Authorization is correct.**
- The router has `dependencies=[Depends(current_admin)]` (`router.py:22`) plus `**require_permission("admin:stats:read")` (`router.py:39`). The key passes `_PERMISSION_RE`.
- The route is in the BEARER map (`tests/test_route_auth_classes.py:69`). It is not added to the ADR-0008 self-scoped list, so there is no carve-out.
- `permits` lets `admin:*` cover the new key (a test checks this).
- The 401 and 403 tests exist, and the 403 test uses neighbouring permissions such as `admin:apps:read` and `admin:audit:read`.
- The `admin:stats:read` test mints a real token through `make_staff`, so it exercises the real verification path.

**💭 6. The permission seed is correct.** In `migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py:30-45`:
- The new permission has `is_admin = true`, is attached to the fixed admin app id, and uses `ON CONFLICT DO NOTHING`.
- It is granted to no role, and `tests/test_stats_migration.py` asserts `(True, 0)`.
- The downgrade is parameterised with an expanding bind. Its loss of super-admin-added `role_permissions` through the cascade is documented in the migration docstring, which is acceptable.

**💭 7. Caching and audit are correct.**
- `Cache-Control: no-store` is set on the 200 response (`router.py:43`). 401/403 bodies hold nothing sensitive.
- No audit row is written (there is no `AuditContextDep`, and a test counts audit rows before and after the call).
- The new audit repository method is read-only, so the module's append-only shape still holds.
- `member_counts` and `list_app_summaries` are pure SELECTs with no tenant or user input.

### Follow-ups (out of scope)
- Per-principal rate limiting on admin Bearer reads in general, including stats.
- Re-check sign-up query cost once `users` gets large. Add a `created_at` index if p95 moves toward 200 ms.
- When ADM-016 adds `member_count` under `admin:apps:read`, revisit whether the dashboard's per-app list should follow the same gate, depending on the owner's answer to finding 1.

**Final verdict:** PASS. There are no blockers. Finding 1 needs an explicit owner decision or a small schema trim before merge.
