ADM-015 DB consult (read-only; based on models, spec §5.9, ticket; I did not read the migrations or run EXPLAIN).

FINDINGS
- users: only `status IN ('active','disabled')`, `email_verified_at`, `created_at`. The only index is the unique one on email. Org status is `active|suspended`. App status is `active|disabled`. "Unverified" is `email_verified_at IS NULL`, not a status. These tables are tiny (staff-sized, low thousands of rows at most), so a seq scan is fine and an index is not needed.
- audit_log (app/modules/audit/models.py): `ix_audit_log_action (action, occurred_at, id)` already exists. `WHERE action = 'auth.login_succeeded' AND occurred_at >= now() - interval '24 hours'` is a bounded range scan on that index. There is also `ix_audit_log_occurred_at_id`, and a partial `ix_audit_log_outcome` for failure/denied only. No new index and no migration are needed. The `action` column is plain Text, so there is no collation or cast issue.
- Retention is at least 90 days (ADM-004c), so a 24 h window is always within retained data.
- role_assignments: has `ix_role_assignments_role_id`, `ix_role_assignments_org_id` and a unique (user_id, role_id, org_id) constraint. `expires_at` is nullable, with no index. Active means `expires_at IS NULL OR expires_at > now()`, the same predicate as rbac/repository.py `_assignment_rows`. Role -> app goes through `roles.app_id`, and `uq_roles_app_id_key` indexes it.

RECOMMENDATIONS
1. Count each audit action with an index-only count. One query covers both actions and avoids a second round trip:
   `SELECT action, count(*) FROM audit_log WHERE action IN ('auth.login_succeeded','auth.login_failed') AND occurred_at >= now() - interval '24 hours' GROUP BY action`
   - Use `func.now()` (DB clock), as the rest of the repo does. Do not use Python time.
   - Use a half-open range with no upper bound, or `< now()`. Rows with `occurred_at = now() - 24h` exactly are included (`>=`). State the boundary in the docs and tests.
   - Missing actions: default both counts to 0 in Python.
   - Put the query in the audit repository, not in a stats module. Use the AuditAction enum values, not string literals.
2. users, one query: `SELECT status, count(*), count(*) FILTER (WHERE email_verified_at IS NULL) FROM users GROUP BY status`. Total = the sum. Fill both statuses with 0. Unverified is a single number, taken as the sum across statuses.
3. Orgs and apps: `SELECT status, count(*) FROM organizations GROUP BY status`, and the same for apps. Fill missing statuses with 0 in Python, using the StrEnums.
4. Sign-ups per month, in the time zone:
   ```
   SELECT date_trunc('month', created_at AT TIME ZONE 'Asia/Ho_Chi_Minh') AS m, count(*)
   FROM users
   WHERE created_at >= :start_utc
   GROUP BY 1 ORDER BY 1
   ```
   - `created_at AT TIME ZONE 'Asia/Ho_Chi_Minh'` on a timestamptz returns a local timestamp without time zone, so `date_trunc` buckets by local month correctly. Do not use `date_trunc('month', created_at)` directly: it uses the session time zone, which is UTC on asyncpg.
   - Compute `:start_utc` in SQL. Do not use a Python datetime built from now. For example: `(date_trunc('month', now() AT TIME ZONE tz) - interval '11 months') AT TIME ZONE tz`. This gives the start of the 12-month window (the current month plus the 11 before it) as a timestamptz. It is also sargable against a future index on created_at.
   - Fill empty months in SQL with `generate_series`, or in Python from the returned local-month keys. SQL option: `LEFT JOIN generate_series(start_local, date_trunc('month', now() AT TIME ZONE tz), interval '1 month') s(m) ON ...`. Always return exactly 12 entries, oldest to newest.
   - Month key format in the document: `YYYY-MM` string. This is an API-doc decision for the planner, not a DB one.
   - Viet Nam has no DST (fixed UTC+7), so there are no 23/25-hour edge cases.
   - The users table is small, so the seq scan under the filter is fine. Do not add an index on `users.created_at` now. If the table grows past about 100k, add `ix_users_created_at` by migration (CONCURRENTLY in prod, with its downgrade).
5. Members per app, shared with ADM-016. Active = `expires_at IS NULL OR expires_at > now()`. Join users and filter on status, per OQ5:
   ```
   SELECT r.app_id, count(DISTINCT ra.user_id)
   FROM role_assignments ra
   JOIN roles r ON r.id = ra.role_id
   JOIN users u ON u.id = ra.user_id AND u.status = 'active'
   WHERE (ra.expires_at IS NULL OR ra.expires_at > now())
   [AND r.app_id = ANY(:ids)]
   GROUP BY r.app_id
   ```
   - `count(DISTINCT user_id)` is required: a user may hold several roles or several orgs.
   - Do not filter on `ra.org_id`: app scope and any org both count.
   - Put one repository method in the rbac repository, `member_counts(app_ids: Sequence[UUID] | None) -> dict[UUID, int]`. Both ADM-015 and ADM-016 call it. Return 0 for apps absent from the result. ADM-016 passes the page's app ids, which gives one batched query per list page and no N+1. For app detail, pass `[app.id]`.
   - The scan covers all assignments when `app_ids` is None (the dashboard). That table is small and a seq scan is fine. No new index is needed (the existing role_id index is enough for the per-app filter).
   - The dashboard also needs the app key and name. Make one query for apps (id, key, name, status) and one for the member counts. Join in Python and include all apps, including those with 0 members, and the `admin` app (= staff count). Order by key.
   - The `admin:super-admin` assignment cannot expire (service.py:1406), so staff counts are stable.
6. Query count per request: 6 queries total, all read-only and on the same session. Users (1), orgs (1), apps by status (1, or fold it into the app list query: you already need the app list for members), audit counts (1), sign-ups (1), members (1) + app list (1). That is at most 7. Run them sequentially on the one AsyncSession. Do not use asyncio.gather on one session, because asyncpg does not allow concurrent use of one connection. Alternatively, wrap them in one `REPEATABLE READ` read-only snapshot if you want a consistent document. That is optional and not worth it for dashboard counts. Say "approximate, not a snapshot" in the docs.
7. Test seeding for the 24 h boundary:
   - Insert audit_log rows with explicit `occurred_at` via raw SQL (as tests/test_audit_schema.py does: `now() - CAST(CAST(:age AS text) AS interval)`). Inside the test transaction `now()` is the transaction start and is constant for the whole test, so offsets from `now()` are deterministic.
   - Rows to seed: `now() - interval '23 hours 59 minutes'` (in), `now() - interval '24 hours 1 minute'` (out), and exactly `now() - interval '24 hours'` (in, because `>=` in the same transaction compares against the same now()). Also add rows of another action (`auth.logout`) and failure rows, to prove that only the two actions count.
   - audit_log is append-only via a trigger, and the test DB is rolled back per test. INSERT is fine. Do not DELETE and do not commit. Check the app's role/trigger allows INSERT with an arbitrary `occurred_at`: the existing tests do this, so it is fine.
   - actor_consistent CHECK: for `auth.login_failed` use `actor_kind='anonymous'`. For `auth.login_succeeded` use `actor_kind='user'` with a real `actor_user_id` (no FK, but the CHECK requires non-null).
   - Sign-ups: insert users with `created_at` explicitly set to the last second of a month (`'2026-10-31 16:59:59+00'` is still October 31 in HCM; `'2026-10-31 17:00:00+00'` is November 1 in HCM). This proves the zone boundary. Also test an empty month in the middle (it is filled with 0), exactly 12 entries, and a user older than 12 months (excluded). Freeze the "current month" by seeding relative to now(), or accept a clock-dependent test. Better to have the repository take a `now` parameter, or to compute the window in SQL with a clock fixture that the test overrides.
   - Members: one user with two roles on the same app, one in two orgs (counts as 1); an expired assignment (not counted); a disabled user (not counted); an unverified active user (counted); an app with 0 members (present with 0).

RISKS
- `date_trunc` on timestamptz without AT TIME ZONE would bucket in the session time zone (UTC): a silent off-by-7-hours bug near month ends. The boundary tests in point 7 cover it.
- `count(*)` on `audit_log` for an action with heavy traffic: at 24 h the range is small, and it is an index-only scan only when the visibility map is current. Even a heap fetch is bounded by the 24 h range. If failed-login floods (brute force) become heavy, the count is still O(rows in 24 h). Acceptable for p95 < 200 ms up to roughly hundreds of thousands of rows. Add an EXPLAIN check in the review that confirms `Index Scan/Only Scan using ix_audit_log_action`.
- The audit-log read must not go through the audit service's `admin:*`-gated list endpoint. Use a repository count with only counts exposed. The permission is `admin:stats:read` (new). That needs a migration or seed to add the permission to the `admin` app and to any roles that should have it (super-admin has `admin:*`, which covers it). I did not check how permissions are seeded, so please check this against the existing permission catalogue and its tests. It is an R2 and R3 item, not an index item.
- `member_count` on the app list (ADM-016) with `admin:apps:read`: the query joins users and role_assignments, so the shared helper must not leak anything beyond a number. It does not.
- Missing `AuditAction` values: confirm the enum names match `auth.login_succeeded` and `auth.login_failed` exactly (ADM-004b). They are in docs/adr/0009.

OPEN QUESTIONS
1. Does the 12-month window include the current partial month (my recommendation: yes, 12 entries ending at the current month)? The owner said "last 12 months" only.
2. Should disabled users be counted in "sign-ups per month"? Spec says `users.created_at` with no status filter, so I assume all users are counted.
3. Is a total-per-status breakdown needed even for zero-count statuses (my recommendation: always return both `active` and `disabled`, and `active`/`suspended` for orgs)?
4. Is the new permission `admin:stats:read` seeded via migration or the CLI/manifest sync? That determines whether the ticket needs a data migration (with its downgrade) even though no schema or index changes.

No migration is needed for the schema or indexes; the only possible migration is the permission seed (open question 4).

Files read: /home/user/org-admin-api/work/tickets/ADM-015/ticket.md, /home/user/org-admin-api/app/modules/audit/models.py, /home/user/org-admin-api/app/modules/rbac/models.py, /home/user/org-admin-api/app/modules/users/models.py, /home/user/org-admin-api/app/modules/organizations/models.py, /home/user/org-admin-api/app/modules/rbac/repository.py (assignment predicates), /home/user/org-admin-api/docs/spec-v1.md §5.9.
