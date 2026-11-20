# Consult — api-platform-engineer — ADM-039 (2026-11-20)

**API consult for ADM-039 (usage metrics, staff config).** I read the ticket and spec §5.14 and checked the module layout. I did not read the code for the apps/rbac router or the existing audit helper.

**Findings**
- The spec fits the ticket. It adds Bearer staff CRUD only. The app-credential scopes (`usage:read`, `usage:write`, `limits:write`) wait for the ADR-0018 amendment (ADM-041), and no usage route ships before that.
- The sub-resource path `/api/v1/apps/{key}/usage-metrics` follows the D18 pattern (apps addressed by `key`). It is a plural noun, so R9 needs no new exception.
- `app/modules/` has no `apps` router. Apps are probably served from `rbac` or another module. The planner must confirm where app lookup lives. It should reuse that lookup for the 404 on an unknown app and the refusal for the `admin` app.
- `require_permission` in `app/modules/admin_auth/deps.py` already writes `x-permission`. Use `admin:usage:read` on list and detail, and `admin:usage:write` on create, PATCH and archive.
- The spec says apps may change defaults and periods through `<app>:usage:manage` with `on_behalf_of`. That belongs to later tickets (ADM-040/042+), not this one. Design the service so the later caller can reuse it. Each mutation should take an actor object (staff user, or app key plus user), and audit should record the reason.

**Recommendations**
1. **Contract**
   - `metric key`: immutable, slug pattern (for example `^[a-z][a-z0-9_]{1,63}$`), unique per app.
   - Duplicates return 409 `usage_metric_exists`.
   - PATCH may change `display_name`, `unit`, `default_limit`, `period` and `status`. `key` and `unit` are better kept immutable, because changing a unit silently changes what the numbers mean for later usage rows. If `unit` stays mutable, treat it as a breaking-semantics risk. I recommend an immutable `unit`.
   - `default_limit`: integer >= 0, with a sane maximum such as 2^31-1 (or bigint). 0 is legal (blocks the metric). Define whether `null` means unlimited. I recommend no null in v1, to keep the contract simple.
   - `period` is an enum with values `day|week|month`. The ticket says `day|week|month`, but the spec text says `daily|weekly|monthly`. Pick one and use it everywhere. Later API surfaces (consume, grants) are frozen by the first choice. I recommend the ticket's `day|week|month`, and the spec wording should be reconciled.
   - `period` on create is optional. It defaults to the app's setting, else `week`. No app period setting exists yet (grep found none). Either add a nullable `apps.usage_period`, or hard-code `week` and note that the app setting comes later. Do not add a column silently.
   - Return the resolved period in the response so clients never see null.
2. **Archive**
   - The spec says "archive", not delete. Use `status` (`active|archived`) through PATCH, or `POST .../archive`. Return 204 only for a true delete.
   - Prefer PATCH `status` to keep the surface small. Document that archived metrics stay readable, and that their key stays reserved so history is not orphaned.
   - Add a `status` filter on the list, defaulting to all or active. State the choice.
3. **Concurrency**
   - Use `If-Match` (412, ADM-024) only if other PATCH endpoints already do. Otherwise skip it, for consistency.
4. **Errors**
   - 404 for an unknown app and for the `admin` app (consistent with the role-catalog rule).
   - 422 for validation.
   - 409 for a duplicate key.
   - 401 and 403 as usual.
5. **Period helper**
   - Put one pure function in the repository or a core util. It takes `(period, now: datetime)` and returns a tz-aware start (UTC-converted for `timestamptz` comparison).
   - Use `ZoneInfo("Asia/Ho_Chi_Minh")`. It has no DST, so there is no ambiguity.
   - Reject naive datetimes.
   - Test: Sunday 23:59:59 ICT versus Monday 00:00 ICT, 31 Dec to 1 Jan, 28/29 Feb, and the UTC input that falls on a different ICT day (17:00 UTC is already the next ICT day).
6. **Migration**
   - Seed the two permissions with `is_admin` and grant them to no role.
   - Add a CHECK constraint for `period` and `status`, and a unique constraint on `(app_id, key)`.
   - Use an FK to apps, UUID ids and `timestamptz`.
   - Provide a working `downgrade()` that also removes the seeded permissions.
   - Import the model in `app/db/models.py`.
7. **Audit (R4)**
   - Write the entry in the same transaction for create, PATCH and archive, with before/after values.
   - ADM-004 may not be fully wired in. Check how the other modules call it.
   - The spec (OQ67) requires a reason on adjustments. I recommend an optional `reason` (1–500 chars) in the body here, since this is metric config rather than a user adjustment. Decide it now, because adding a required field later would break clients.

**Risks**
- Naming drift between `day|week|month` and `daily|weekly|monthly`, as described above.
- A mutable `unit` or `key` corrupts later usage history (ADM-042+).
- Changing `period` mid-period needs a stated rule. The spec leaves it to the planner. Suggested rule: it applies immediately, and the current period is recomputed from the new boundaries. Record this in the plan, because consume and usage tickets depend on it.
- Changing `default_limit` applies to the current period (spec "Mid-period changes"). Do not snapshot the default per user.
- This is a High path (migration plus RBAC seed), so the plan goes to Ming for approval.

**Open questions**
1. Is there an app-level period setting today, or is `week` hard-coded?
2. Is `unit` immutable?
3. Is archive PATCH `status` or a separate action?
4. Is `reason` accepted on staff metric config?
5. Does the list default to hiding archived metrics?
6. Which module owns app lookup for the `{key}` path?
