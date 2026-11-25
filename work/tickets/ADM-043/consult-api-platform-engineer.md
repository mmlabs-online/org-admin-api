# Consult — api-platform-engineer — ADM-043 (2026-11-25)

ADM-043 consult (api). Read-only; nothing edited.

## Findings
- The app read route `GET /apps/{key}/usage/users/{user_id}` already exists as class 4a (app_router.py:65; `UserUsageRead` in meter_schemas.py:53-67). R3 allows one auth class per endpoint, so the staff Bearer routes need a different path.
- Staff routes use prefix `/apps/{key}/usage-metrics` (router.py:26), `READ = "admin:usage:read"` (router.py:30) and `require_permission(READ, errors=...)`. `Page[T]` and `PageDep` are in app/core/pagination.py, limit ≤ 100.
- `read_user_usage` (meter_repository.py:219) returns active metrics only (line 254). It already falls back to the default for expired overrides (line 245). It does not check membership. Membership is checked in `_require_member` (meter_service.py:61).
- `usage_counters` (models.py:125): `(user_id, metric_id, period, period_start, used)`, kept indefinitely (OQ69). It stores no limit.
- `usage_events` (models.py:159): purged after 90 days (ADM-045). It has `idempotency_key` (unique per app), `amount`, `settled_amount`, `state`, and `created_at`/`settled_at`/`refunded_at`. Index `(user_id, metric_id, created_at)` at line 191.
- The module's 404 style is "unknown app / metric not of it / unknown user" (router.py:33-35).

## Recommendations
1. Paths, all Bearer `admin:usage:read` GETs: `/api/v1/apps/{key}/user-usage/{user_id}` (current), `.../{user_id}/periods` (history) and `.../{user_id}/events` (ledger).
   - Avoid `usage-metrics/users/...`, because it sits next to `/{metric_id}` (router.py:99).
   - Add the routes to the R9 exceptions list (D36) and to tests/test_route_auth_classes.py.
2. Current: a single document, not `Page[T]`. Reuse the `UserUsageRead` / `UserMetricUsage` shape (limit, used, remaining, period_end), with an optional `metric` filter.
   - Staff also see archived metrics and non-members. Add `status` and an `override {limit, expires_at, active}` summary per metric. This needs a staff variant of the repository query.
3. History: `Page[...]` over `usage_counters` joined to the metric key, ordered by `period_start DESC`.
   - Filters: `metric` (key), `from` and `to` (aware datetimes, compared to `period_start`; 422 if `from` ≥ `to`).
   - Fields: `metric`, `period`, `period_start`, `period_end`, `used`. No limit, because it is not stored historically. Say so in the description.
4. Events: `Page[...]` ordered by `created_at DESC`, with the same filters plus `state`.
   - Fields: `id`, `metric`, `amount`, `settled_amount`, `state`, `period`, `period_start`, `created_at`, `settled_at`, `refunded_at`.
   - Never return `idempotency_key`. It is an app-chosen value that may embed identifiers, and R11 says it is never logged. Staff can match on `id`.
5. Metric addressing: use the metric `key` as a query filter, to match the app route. An unknown key returns 404 `usage_metric_not_found`. Archived metrics are allowed, so history stays visible.
6. 404 matrix:
   - Unknown app or the `admin` app: 404.
   - Unknown user (no `users` row): 404.
   - Unknown metric filter: 404.
   - Non-member user: 200 with default or empty data, because overrides may exist for non-members (router.py:198-203).
   - 422 for a malformed UUID, bad datetimes, or `limit` above 100.
7. Tests (R8):
   - Current: an active override, an expired override that falls back to the default, and a zero-limit override.
   - History and events: filters and paging.
   - Auth and 404: 401; 403 (no permission, an app credential, an end-user token); each 404.
   - Events: assert `idempotency_key` is absent.
8. Docs: update the spec §5.14 / OQ65 note, D36, the OpenAPI descriptions, and the R3/R9 text.

## Risks
- The ledger is a 90-day window only. Document this in the route description.
- Counter queries are covered by the unique constraint. The events listing relies on `ix_usage_events_user_id_metric_id_created_at`, which is fine for a single user.
- Offset paging is acceptable at this size.

## Open questions for Ming (defaults)
1. Should staff see archived metrics and non-members? Default: yes, with a `status` field.
2. Should the ledger omit `idempotency_key`? Default: omit it.
3. Should the path be `/user-usage/{user_id}...`? Default: yes.
4. Should history include the limit that applied then? Default: no, because it is not stored.
