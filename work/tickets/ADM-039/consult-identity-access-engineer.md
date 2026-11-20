# Consult — identity-access-engineer — ADM-039 (2026-11-20)

### Findings
1. **Permission enforcement already exists.** `require_permission()` in `app/modules/admin_auth/deps.py:116` both declares `x-permission` and enforces it. Its key regex (`^admin:[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$`) accepts `admin:usage:read` and `admin:usage:write`. `admin:*` coverage comes from `permits()`, so nothing new is needed for the wildcard.
2. **There is a template for the seed migration.** `migrations/versions/20261120_c4e8a2f7b916_adm029_bug_report_permissions.py` does exactly what this ticket needs: it inserts into the fixed admin app id, sets `is_admin=true`, grants to no role, uses `ON CONFLICT DO NOTHING`, and its downgrade deletes the rows (lossy cascade, documented).
3. **There is a template for refusing the admin app.** `app/modules/app_credentials/service.py:115-118` refuses `ADMIN_APP_KEY` with a typed code (`admin_app`) and also refuses disabled apps.
4. **Audit needs no migration for new actions.** The audit CHECKs only enforce the format of `action`, not a fixed list (`app/modules/audit/models.py:33-35`). Adding new `AuditAction` values is code only. `record_denied` exists for 403s from guards.
5. **No "app's period setting" exists.** I found no usage or period column in the apps module. The ticket says the period defaults "to the app's setting / weekly" (spec §5.14, OQ55/OQ64), but nothing stores that app-level value today.
6. **The ticket scope leaves out the required reason.** Spec OQ67 requires a reason (1–500 chars) on *every* staff and app-admin adjustment, stored in the audit entry. Changing a metric's default limit or period is an adjustment.
7. **The audit table cannot record an app-key actor yet.** `actor_kind` allows only `user|system|anonymous`. That is fine for ADM-039, which is Bearer only. But ADM-044 (app-admin action through a `limits:write` key with `on_behalf_of`) must record both the key and the user, so the design should not block that.

### Recommendations
- **R1 (AC1, R3).** Use one permission per route: `admin:usage:read` on list and detail, `admin:usage:write` on create, PATCH and archive. Do not use `requires_super`, because OQ61 says `admin:*` covers both. Add tests that:
  - a principal with no permission gets 403 `permission_denied`;
  - a principal with only `read` gets 403 on writes;
  - `admin:*` passes;
  - no token, or a token with `aud≠admin`, gets 401.
- **R2 (scope, OQ64/OQ55).** Decide at PLAN where the app-level default period lives. My preference is a nullable `apps.usage_period` column with `weekly` as the effective default, added in this migration. The alternative is to drop "app's setting" for v1 and default each metric to `weekly`. Either way, the metric stores its own period; copy the value once and do not inherit it live.
- **R3 (OQ67, R4).** Require `reason` (1–500 chars, trimmed, not blank) on create, PATCH and archive. Store it in `changes.reason` along with before/after values. Never put it in logs. If the owner reads OQ67 as covering only per-user grants, record that as a decision.
- **R4 (AC1).** Refuse the `admin` app with 404, not 409. That matches the D17 role-catalog convention (unknown, disabled or `admin` → 404) and does not reveal anything about the admin app. Treat disabled apps the same way on writes, or state a rule.
- **R5 (ADM-042/044).** Keep metric `key` immutable and unique per app (`UNIQUE(app_id, key)`, so a duplicate → 409). Use the same `[a-z][a-z0-9_-]` shape as permission keys. Consume calls address metrics by `(app key, metric key)`, so renaming a key would silently break app backends.
- **R6 (ADM-044 seam).** Put the business rules in a service method that takes a generic `Actor`, not an `AdminPrincipal`. ADM-044 can then call the same method with an app-key-plus-user actor without copying the code.
- **R7 (AC2).** Add a period helper with signature `period_start(metric_period, at: datetime) -> datetime`. It should take an aware UTC datetime, convert with `zoneinfo("Asia/Ho_Chi_Minh")`, and return an aware datetime (`timestamptz`). Reject naive datetimes. Test these boundaries:
  - Sun 23:59:59 / Mon 00:00:00 ICT;
  - 31 Jan, 28/29 Feb, 30 Apr;
  - the UTC 16:59:59 / 17:00:00 crossing, because ICT is UTC+7 with no DST.

  The ticket calls it a "repository helper", but it is pure domain logic. Put it in the service or a domain module (R1 layering), so the SQL side only receives the computed start.
- **R8 (concurrency).** Use `If-Match` on PATCH (ADM-024, 412) to match the other admin resources. Two staff editing a limit at the same time is a real risk.

### Risks
- **Mid-period changes.** Changing a period (weekly → monthly) or archiving a metric while usage rows exist (ADM-042) has no stated rule. Define it now even though no usage table exists yet. A safe rule: the new period applies from the next boundary, or immediately with usage counted from the new period's start. Also decide whether archived metrics refuse consume.
- **Zero limit.** `default_limit = 0` blocks the metric for everyone. That is valid (OQ62 allows 0), but a typo makes it a silent outage across the whole app. Consider returning the previous value in the response and the audit entry.
- **Units.** Seconds versus counts, with an integer limit only: decide whether `BIGINT` and `>= 0` are enough. Do not use floats.

### Open questions
1. Where is the app-level default period stored: a new `apps` column, or none in v1? (Finding 5)
2. Does OQ67's reason apply to metric create/PATCH/archive, or only to user grants? (Finding 6)
3. Should disabled apps return 404 or 409 on metric writes?
4. Archive: does it set `status=archived` only? Can it be reversed through PATCH? Is the key reusable afterwards? (I recommend no.)
