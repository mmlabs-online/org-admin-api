Status: READY

> Blocked only on the owner choices in `## Blocking questions` (each has a default). If Ming accepts
> the defaults at the plan gate, this plan is READY as written; no other change is needed.

# Plan — ADM-043: Staff usage reads

## Scope summary
- Spec §5.14, OQ65 (docs/spec-v1.md:285): "v1: inside each app only — the app backend reads the user's
  limits and usage with a `usage:read`-scoped key. A central self-read across apps is later (§5.7).
  **Staff see the current period and the retained history (OQ69).**"
- Spec §5.14, OQ69 (docs/spec-v1.md:289): "Per-use events kept **90 days** (purge job); per-period
  totals per user + app + metric kept indefinitely in v1."
- Spec §5.14, OQ61 (docs/spec-v1.md:281): "Super-admin side: `admin:usage:read` and
  `admin:usage:write` (Bearer, `aud=admin`), both covered by `admin:*`."
- ADR-0024 §1: metrics are never deleted, only archived, "so usage history is never orphaned"; the
  `admin` app has no metrics (404). §3: `periods.py` is the only place that computes periods.
- ADR-0024 §8 / router.py:30: `READ = "admin:usage:read"`.
- Ticket scope: "Bearer `admin:usage:read` routes: current period per user (limit, used, remaining,
  period_end), history from per-period totals and the 90-day ledger; `Page[T]`."
- `work/decisions.md`: nothing on usage reads.

Read-only ticket: no write, so no audit entry (R4 applies to mutating actions only), no migration.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Current-period read returns limit, used, remaining, period_start/period_end per metric; an active override replaces the default | `tests/test_usage_staff_read_api.py › test_current_active_override_applies`, `test_current_default_without_override`, `test_current_zero_override_blocks` |
| 2 | An expired override falls back to the default and is shown `active: false` | `› test_current_expired_override_falls_back` |
| 3 | Staff see archived metrics (with `status`) and non-members (`member: false`) — Q1 | `› test_current_includes_archived_metric`, `› test_current_non_member_200` |
| 4 | History from per-period totals, `Page[T]`, newest first, filters `metric`/`from`/`to`, no limit field | `› test_periods_paged_newest_first`, `› test_periods_filters`, `› test_periods_has_no_limit_field` |
| 5 | Ledger (90-day events), `Page[T]`, newest first, filters incl. `state`, never `idempotency_key` | `› test_events_paged_newest_first`, `› test_events_filters_state`, `› test_events_never_return_idempotency_key` |
| 6 | 401 / 403 | `› test_routes_401_without_token`, `› test_routes_401_with_app_credential`, `› test_routes_403_without_read`, `› test_routes_200_with_admin_star` |
| 7 | 404 matrix (unknown app, `admin` app, unknown user, unknown metric filter) and 422 (bad UUID, bad metric key, naive/inverted range, limit > 100, bad state) | `› test_404_matrix` (parametrized over 3 routes), `› test_422_validation` (parametrized) |
| 8 | Route-class registry lists the 3 new routes as Bearer `admin:usage:read` | `uv run pytest tests/test_route_auth_classes.py` |
| 9 | All checks green | `make check` |

## Ming verification after merge
None (admin-pan UI for these reads is a follow-up, not part of this ticket).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/usage/staff_read_schemas.py` | new: `StaffMetricOverride`, `StaffMetricUsage`, `StaffUserUsageRead`, `UsagePeriodTotalRead`, `UsageEventRead` | response models (R1, R9) | No |
| `app/modules/usage/staff_read_repository.py` | new: `StaffUsageReadRepository` with `current(...)`, `page_periods(...)`, `page_events(...)` (SQLAlchemy only) | R1 repository layer; staff variant of `read_user_usage` (meter_repository.py:219-259) without the active-only filter (line 254) | No |
| `app/modules/usage/staff_read_service.py` | new: `StaffUsageReadService` (`current`, `periods`, `events`) + pure `check_range(from_, to)` | R1 service: resolves app/user/metric, 404s, period bounds via `periods.py` / `metering.counter_period` | No |
| `app/modules/usage/staff_read_router.py` | new: `APIRouter(prefix="/apps/{key}/user-usage", tags=["usage"], dependencies=[Depends(current_admin)])`, 3 GETs with `require_permission(READ, errors={404: ...})` | R3 class 1, R9 | No |
| `app/api/router.py` | modify (lines 17-35): import and `include_router(staff_usage_read_router)` | mount the routes | No (only `app/api/deps.py` is High) |
| `tests/test_usage_staff_read_api.py` | new: API tests + `check_range` unit test | R8 | No |
| `tests/test_route_auth_classes.py` | modify: 3 entries in `BEARER` (after line 175) → `"admin:usage:read"` | R3 registry | No |
| `CLAUDE.md` | modify R9: add the exception text (Q6) | R9 (single document not `Page[T]`; `user-usage` path) | No |
| `docs/identity-architecture.md` | modify D36 row (line 333): append one sentence on the staff reads (ADM-043) | decision log of usage routes | No |

Tier: no High path → Medium (app code). Reuses without editing: `service.resolve_app` (service.py:45),
`UserRepository.get`, `RbacRepository.is_active_app_member` (rbac/repository.py:894),
`metering.remaining` / `metering.counter_period` (metering.py:11, 47), `UsageMetricNotFoundError`
(errors.py:8), `MetricKey` (meter_schemas.py:11), `Page`/`PageDep`.

## Data model / migration
None. Alembic head stays `c91e4b7a3d58`. Queries use existing indexes:
`uq_usage_counters_user_id_metric_id_period_period_start` (user_id leading) for history, and
`ix_usage_events_user_id_metric_id_created_at` (models.py:191) for the ledger.

## API contract
All three: Bearer (`aud=admin`), `require_permission("admin:usage:read")` (covered by `admin:*`),
`{key}` with `Path(pattern=APP_KEY_RE)`, `user_id: uuid.UUID`. Error envelope per R6.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/user-usage/{user_id}` | query `metric?: MetricKey` | `StaffUserUsageRead` (single document) | 200, 401, 403, 404, 422 | `admin:usage:read` |
| GET | `/api/v1/apps/{key}/user-usage/{user_id}/periods` | query `metric?`, `from?`, `to?` (AwareDatetime, compared to `period_start`, `from` inclusive, `to` exclusive), `limit` ≤ 100, `offset` | `Page[UsagePeriodTotalRead]` | 200, 401, 403, 404, 422 | `admin:usage:read` |
| GET | `/api/v1/apps/{key}/user-usage/{user_id}/events` | query `metric?`, `from?`, `to?` (compared to `created_at`), `state?: UsageEventState`, `limit`, `offset` | `Page[UsageEventRead]` | 200, 401, 403, 404, 422 | `admin:usage:read` |

Schemas (staff_read_schemas.py):
- `StaffMetricOverride {limit: int, expires_at: datetime | None, active: bool}`
- `StaffMetricUsage {metric, display_name, unit, status: "active"|"archived", period, period_start, period_end, limit, used, remaining, override: StaffMetricOverride | None}` — `limit` is the effective limit (unexpired override, else `default_limit`); `remaining = metering.remaining(limit, used)`.
- `StaffUserUsageRead {user_id, member: bool, metrics: list[StaffMetricUsage]}` — all metrics of the app (both statuses), `key` order. `member` = `is_active_app_member` (OQ5 predicate).
- `UsagePeriodTotalRead {metric, period, period_start, period_end, used}` — no `limit` (not stored historically; Q4). `period_end` from `metering.counter_period(period, period_start)`.
- `UsageEventRead {id, metric, amount, settled_amount, charged, state, period, period_start, period_end, created_at, settled_at, refunded_at}` — no `idempotency_key`, no `app_id`/`user_id` (in the path). `charged = metering.charged(...)`.

404s (`errors={404: ...}` text): unknown app or `admin` app (`not_found`, via `resolve_app`);
unknown user — no `users` row (`not_found`, "User not found"); unknown `metric` key for the app
(`usage_metric_not_found`; archived keys are found). Non-member or disabled user: 200 (Q5).
422: malformed UUID, `metric` not matching `METRIC_KEY_RE`, naive `from`/`to`, `from >= to`
(`details=[{"field": "from", "reason": "not_before_to"}]`), `limit` > 100 / < 1, unknown `state`.

Descriptions state: "`used` of history rows is the period total; the limit that applied then is not
stored"; "events are kept 90 days (OQ69, purge ADM-045); `idempotency_key` is never returned".

## Tests to write
File `tests/test_usage_staff_read_api.py`; setup with `tests/usage_factories.py`
(`world`, `make_metric`, `make_member`, `override(..., expired=True)`, `consume`/`settle`/`refund`) and
direct `UsageCounter`/`UsageEvent` inserts for past periods; staff client via
`make_client(admin_permissions=["admin:usage:read"])`.

| Test (file › name) | Covers |
|---|---|
| `› test_check_range_unit` | pure: `None`s ok; `from < to` ok; `from >= to` → `UnprocessableError` |
| `› test_current_default_without_override` | limit = default, used after a consume, remaining, period bounds equal `periods.period_bounds` |
| `› test_current_active_override_applies` | override replaces default; `override.active: true` |
| `› test_current_expired_override_falls_back` | expired override → default limit; `override.active: false`, `expires_at` shown |
| `› test_current_zero_override_blocks` | override 0 → `limit 0, remaining 0` |
| `› test_current_includes_archived_metric` | archived metric listed with `status: "archived"` |
| `› test_current_non_member_200` | non-member with an override → 200, `member: false`, override applies |
| `› test_current_metric_filter` | `?metric=` narrows to one; archived key accepted |
| `› test_current_remaining_never_negative` | used > limit (counter set directly) → remaining 0 |
| `› test_periods_paged_newest_first` | 3 counters across periods; `period_start DESC`, `total`, `limit/offset` |
| `› test_periods_filters` | `metric`, `from` inclusive, `to` exclusive |
| `› test_periods_has_no_limit_field` | response keys exactly the schema; other user's / other app's counters absent |
| `› test_events_paged_newest_first` | consumed, settled, refunded rows; `charged` per state; paging |
| `› test_events_filters_state` | `state`, `metric`, `from`/`to` |
| `› test_events_never_return_idempotency_key` | no `idempotency_key` key and the raw key string absent from the body |
| `› test_routes_401_without_token` | 3 routes, no header |
| `› test_routes_401_with_app_credential` | `usage:read` `mmak_` key on the 3 routes (real Bearer check, `make_client()` without permissions) → 401 |
| `› test_routes_403_without_read` | `admin_permissions=["admin:usage:write"]` → 403 naming `admin:usage:read` |
| `› test_routes_200_with_admin_star` | `admin:*` → 200 |
| `› test_404_matrix` | parametrized: unknown app, `admin` app, unknown user, unknown metric filter × 3 routes; codes `not_found` / `usage_metric_not_found` |
| `› test_422_validation` | parametrized: bad UUID, bad metric key, naive datetime, `from >= to`, `limit=101`, `limit=0`, `state=bogus` |
| `tests/test_route_auth_classes.py` (existing tests) | 3 new `BEARER` entries classified with `admin:usage:read` |

## Approach
1. Schemas (above) in `staff_read_schemas.py`; `from_attributes` not needed (service builds them).
2. Repository `staff_read_repository.py`:
   - `current(app_id, user_id, starts: dict[str, datetime], metric_key)` — copy of
     meter_repository.py:227-259 with: no `m.status` filter; override joined on
     `(o.metric_id == m.id) & (o.user_id == user_id)` without the expiry condition; select
     `m.status`, `o.limit_value`, `o.expires_at`, `active = o.expires_at IS NULL OR o.expires_at > now()`
     and effective limit `CASE WHEN o.id IS NOT NULL AND active THEN o.limit_value ELSE m.default_limit`.
     One query (no N+1). Expiry on the DB clock, like `_LIMIT_SQL`.
   - `page_periods(app_id, user_id, metric_id|None, from_, to, limit, offset)` — `usage_counters`
     join `usage_metrics` (for `key`), `WHERE c.app_id = :app AND c.user_id = :user`, order
     `period_start DESC, m.key`; count query with the same filters.
   - `page_events(...)` — same on `usage_events`, `created_at` range, optional `state`, order
     `created_at DESC, id DESC`; select explicit columns, never `idempotency_key`.
3. Service `staff_read_service.py` (HTTP-free, R1): `_resolve(app_key, user_id, metric_key)` →
   `resolve_app` (404) → `UserRepository.get` (404 "User not found") → if `metric_key`,
   `UsageMeterRepository.find_metric` or `UsageMetricRepository.find_by_key` (any status) else
   `UsageMetricNotFoundError`. `current`: `db_now` → bounds per `UsagePeriod` via `period_bounds`
   (as meter_service.py:79-82) → repo → build schema; `member` via `is_active_app_member`.
   `periods`/`events`: `check_range` → repo → map with `metering.counter_period` / `metering.charged`.
   No logging of request values beyond ids.
4. Router `staff_read_router.py`: 3 GETs, `**require_permission(READ, errors={404: _404})` with
   `READ` imported from `app/modules/usage/router.py`; `from_: Annotated[AwareDatetime | None,
   Query(alias="from")]`; `metric: Annotated[MetricKey | None, Query()]`; `PageDep` for lists.
   Path `/apps/{key}/user-usage/{user_id}` cannot collide with `/apps/{key}/usage-metrics/{metric_id}`
   or `/apps/{key}/usage/users/{user_id}` (class 4a; one auth class per endpoint, R3).
5. Mount in `app/api/router.py`; add the 3 rows to `BEARER` in `tests/test_route_auth_classes.py`.
6. Docs: R9 exception text (Q6) in `CLAUDE.md`; one sentence appended to D36.
7. `make check`.

Rules: R1 (layering above), R3 (class 1 only, registry updated), R6 (AppError subclasses only),
R7 (no edits to the app route or metering), R8 (tests listed), R9 (Page[T] for lists, `limit` ≤ 100,
UUID ids, timestamptz; single-document exception documented), R11 (no `idempotency_key`, no emails).
Review focus: no N+1, aware datetimes only, unbounded lists impossible (`PageDep`).

## Specialist input used
`consult-api-platform-engineer.md`:
- Adopted rec 1 (paths), 2 (single document + `status` + override summary), 3 (history over
  `usage_counters`, no limit), 4 (ledger, no `idempotency_key`), 5 (metric by key, archived allowed),
  6 (404 matrix), 7 (tests), Risks (90-day note in descriptions).
- Changed 7: "403 for an app credential / end-user token" → **401**. A `mmak_` key or a token with
  another `aud` fails the Bearer check before any permission check (R3: one class per endpoint).
- Added `member: bool` and `charged` (computed by the existing pure `metering` helpers) so staff need
  not reimplement the rules.
- Narrowed 8 (docs): no edit of `docs/spec-v1.md`. OQ65 already says "Staff see the current period
  and the retained history". Only R9 and D36 change (R7).

## Assumptions
- `admin:usage:read` exists from ADM-039 (router.py:30); no RBAC/permission seeding change.
- Disabled apps are readable (as for metrics, ADR-0024 Consequences).
- History `from`/`to` filter on `period_start`. A metric whose period kind changed has rows of both
  kinds (ADR-0024 §4). Both are returned, each with its own `period`.
- Ledger rows older than 90 days vanish once ADM-045 runs. ADM-045 is independent: this ticket
  reads `usage_events` and does not depend on the purge.
- Hotspots with open tickets in the same batch: `tests/test_route_auth_classes.py`, `CLAUDE.md` (R3/R9
  text) and `docs/identity-architecture.md` (D-rows) are likely also edited by USR-33 (builds first);
  `app/api/router.py` if USR-33/ADM-045 add a router. Expect a rebase on these; conflicts are
  additive (separate rows/lines). No shared `app/modules/usage/` file with ADM-045 is planned here.
- Size: about 400 product lines and 350 test lines. That is at the split guide, but the three
  read-only routes share one resolver, so this plan keeps them together.

## Blocking questions
Owner choices. The defaults are the plan above.
1. **Archived metrics and non-members.** Should staff see archived metrics (`status` per metric) and
   users who are not members (`member: false`, 200 instead of 403)? Default: **yes**.
2. **Ledger and `idempotency_key`.** Should the ledger return the app's `idempotency_key`?
   Default: **never**. Staff match events on `id`.
3. **Paths.** Default: `GET /api/v1/apps/{key}/user-usage/{user_id}` returns the current period as a
   single document. `/periods` returns history as `Page[T]`, and `/events` returns the 90-day ledger
   as `Page[T]`. The other option, `.../usage-metrics/users/...`, sits next to `/{metric_id}`.
4. **Historic limit.** Should history rows show the limit that applied in each past period?
   Default: **no**. It is not stored, and storing it would need a migration and a separate ticket.
   The route description says so.
5. **404 matrix.** Default: 404 for an unknown app or the `admin` app, an unknown user (no `users`
   row) and an unknown `metric` key; archived keys are found. A non-member or disabled user gets
   200. A malformed UUID, a bad datetime or a bad `limit` gives 422.
6. **R9 exception text** to append in `CLAUDE.md`. Default: "`GET /api/v1/apps/{key}/user-usage/{user_id}`
   (staff, a single document, not `Page[T]`), with `/periods` and `/events` as `Page[T]` (ADM-043;
   `user-usage` is not a plural noun, D36)."

## Split proposal
None. If Ming prefers a smaller diff, `/events` can move to a follow-up ticket (about −120 lines).
