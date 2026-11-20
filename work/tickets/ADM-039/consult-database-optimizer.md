# Consult — database-optimizer — ADM-039 (2026-11-20)

**Findings**
- Nothing usage-related exists in `app/`. The `apps` table (`app/modules/rbac/models.py:62`) has no period setting. The spec says "the app's setting (default weekly)", but ADM-039 has no column for it. Conclusion: "defaults to the app's setting" is just a constant `weekly` unless you add `apps.usage_default_period`.
- Latest migration head is `c4e8a2f7b916` (ADM-029). It is the model to copy for the permission seed: `INSERT ... ON CONFLICT (app_id, key) DO NOTHING` into the admin app (id `00000000-0000-4000-8000-0000000000a1`), with `is_admin=true`. Its downgrade deletes by key.
- `bug_reports` (`b3f7a9c1d452`) is the table template: UUID `id`, `created_at`/`updated_at` timestamptz, named CHECK constraints via `op.f`, FK `RESTRICT` to `apps`.
- Later tickets add per-user grants, period totals, a 90-day events table and an atomic consume (OQ62, OQ66, OQ69). The metric table's key and id design is the foundation for them.

**Recommendations**
1. **Table `usage_metrics`.**
   - Columns: `id` uuid, `app_id` FK `apps.id` ON DELETE RESTRICT, `key` varchar(64), `display_name` varchar(100), `unit` varchar(32), `default_limit` bigint, `period` varchar(10), `status` varchar(10), `created_at`, `updated_at`.
   - Constraints:
     - `UNIQUE (app_id, key)`. Also add `UNIQUE (id, app_id)` if later tables want a composite FK to prove same-app, as `apps` does for roles.
     - `key ~ '^[a-z][a-z0-9_]{0,63}$'`.
     - `default_limit >= 0`. Use bigint because seconds and counts can grow. 0 is valid (it blocks the metric, OQ62).
     - `period IN ('day','week','month')`. Spec §5.14 and the ticket both use `day|week|month` in the data model, while the spec prose says daily/weekly/monthly. Store `day|week|month`, as the ticket says.
     - `status IN ('active','archived')`.
   - Make `key` immutable. Reject it in PATCH, since `unit` and `key` feed historical totals. Consider also freezing `unit` once any usage exists. That is a later-ticket concern; note it now.
2. **The `admin` app refusal.** A CHECK cannot do this cleanly because it would need a cross-table lookup. Enforce it in the service with 404, the same as the role catalog (D17 treats `admin` as 404). Optionally add a trigger, but the service check is enough for R1.
3. **Period default.** Column `period` NOT NULL with `server_default 'week'`. The API schema defaults to `week`. Do not add a column to `apps` in this ticket (R7). Instead, state in the plan that the per-app period setting is deferred, and ask Ming (see Open questions).
4. **Archive vs delete.** Use `DELETE` => 204 or `PATCH status=archived`. The ticket says "archive", so use `POST .../{metric_key}/archive` or PATCH. I recommend PATCH `status`, which is idempotent and fits R9. Never hard-delete, because totals and events reference the metric. Use RESTRICT FKs in later tables.
5. **Addressing.** Use `/api/v1/apps/{key}/usage-metrics/{metric_key}` (metric key, not UUID) as the D18 pattern for apps. R9 allows UUIDs, but app-scoped metric keys are stable. Add this to the R9 exceptions list in `CLAUDE.md` (via the plan) so the reviewer doesn't flag it. Return 404 for an unknown app, a disabled app, or the `admin` app. A duplicate `(app, key)` gives 409.
6. **Permissions.** `admin:usage:read` (GET list and detail) and `admin:usage:write` (POST and PATCH). Add a new migration with the same pattern as ADM-029, with a downgrade that deletes by key. Declare `x-permission` and `require_permission` on every route.
7. **Audit (R4).** Write the entry in the same transaction for create, patch and archive, with before/after of `default_limit`, `period` and `status`. The spec requires a reason of 1-500 characters on every staff adjustment (OQ67). Make `reason` required on every write body. This is the cheapest time to establish that contract for ADM-040+.
8. **Period helper.** Put it in the repository layer, or a pure function in `app/modules/usage/periods.py` that the repository re-exports. Use `zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")`. ICT has no DST (fixed UTC+7), so it is simple. Compute the start in ICT and return an aware UTC datetime. Define an exclusive `period_end` as well, because ADM-042+ will need it. Take `now` as a parameter for testing. Tests: Sun 23:59:59.999999 and Mon 00:00:00 ICT, month end 30/31 Dec to Jan 1 and Feb 28/29, and a UTC instant that is already the next ICT day (e.g. 17:00Z).
9. **Indexes.** `UNIQUE (app_id, key)` covers app-scoped lookups and the FK. A list ordered by `created_at, id` for pagination is fine without more indexes at this size.
10. **Changing `period` mid-period.** This is a rule for later tickets. Recommend that the plan state "applies from the next boundary of the new period, computed from calendar start", so that totals keyed by `(period_start)` stay consistent. Store the `period` on the totals row later.

**Risks**
- Dropping `usage_metrics` in downgrade is lossy for later tables. Later migrations must chain their own downgrades first.
- Without a `reason` column or field now, later tickets will diverge. Settle it in this plan.
- Changing `default_limit` mid-period takes effect immediately (spec: "apply to the current period"). That is safe only if the limit is read live. Do not copy it into per-user rows. This constrains ADM-040 (grants must store the user-specific override, not a snapshot).
- Allowing `unit` or `period` edits after usage exists can corrupt totals. Decide the policy now.
- Every High path (migration + RBAC) stops at PLAN for Ming's approval.

**Open questions**
1. Do we add `apps.usage_default_period`, or is `weekly` a constant for v1? The spec says "the app's setting". Absent any column, I suggest a constant.
2. Is `unit` free text or an enum (count, seconds, ...)? I suggest a short free-text slug with a length limit.
3. Archive: PATCH `status` or a dedicated endpoint? Does unarchive need to be allowed?
4. Is `reason` required on staff CRUD of metrics themselves, or only on per-user adjustments (OQ67)? I recommend requiring it everywhere.
5. Should the `period` and `unit` of an existing metric be editable once usage rows exist?
