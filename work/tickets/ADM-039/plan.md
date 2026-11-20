Status: READY

# Plan — ADM-039: Usage metrics, defaults and periods (staff config)

Batch 2026-11-20-1, plan round. High path (migration + `app/db/models.py`) → stops for Ming's plan approval.
The defaults below are enough to build; the questions for the batch round are under `## Blocking questions`.

## Scope summary
Spec `docs/spec-v1.md` §5.14 (scope decided 2026-11-20), quoted:

- "**App-defined usage metrics.** Each app has its own metrics, each with its own unit [...] One configuration model must fit every app; metric keys, units and names below are examples only, not decisions." (l. 266)
- "**Reset period:** `daily`, `weekly` or `monthly`; the app's setting (**default `weekly`**, OQ55) is the starting value for each metric, and one metric may differ (OQ64)." (l. 270)
- "Every change is a mutating admin action → audited (R4), with a required reason (OQ67)." (l. 271)
- OQ61: "Super-admin side: `admin:usage:read` and `admin:usage:write` (Bearer, `aud=admin`), both covered by `admin:*`. No new RBAC scoping." App admins (`<app>:usage:manage` through a `limits:write` key with `on_behalf_of`) "may [...] change their app's defaults and periods" — **ADM-044, not here**; this plan only keeps the seam open.
- OQ63: "Calendar periods in `Asia/Ho_Chi_Minh`: daily at 00:00, weekly on **Monday** 00:00, monthly on the 1st at 00:00. No rolling windows."
- OQ64: "Each metric has its own default limit and its own reset period, initialised from the app's period setting (default `weekly`)."
- OQ67: "A reason (1–500 chars) is required on every staff and app-admin adjustment and stored in the audit entry (R4)."
- OQ68: "Staff define metrics, defaults and periods in the admin API in v1 (`admin:usage:write`) [...] Manifest-declared metrics are later (§5.7)."
- Concept table: "Usage metric | per app | key, unit (count, seconds, …), display name"; "App default | per app + metric | free-trial limit every member gets per period".
- Facts PLAN must weigh: "A change to a default or grant should apply to the current period; how a period-setting change (e.g. weekly → monthly) affects the running period is a planner rule to state." → stated in Approach §Rules and ADR-0024.
- "No usage route ships before that amendment is accepted" applies to **app-credential** usage routes (ADM-041/042); this ticket ships Bearer (R3 class 1) staff routes only, so it is not gated by ADM-041.

Ticket `work/tickets/ADM-039/ticket.md` Scope: metrics table (app, key, display name, unit, default limit, period `day|week|month`, status) refused for `admin`; permissions `admin:usage:read|write` (seed, `is_admin`, granted to no role); Bearer CRUD under `/api/v1/apps/{key}/usage-metrics` (`Page[T]`, detail, create, PATCH, archive), audited; one period-start helper in Asia/Ho_Chi_Minh tested at boundaries.

`work/decisions.md`: nothing on usage. Not a §3 non-goal conflict (spec l. 319: non-monetary counters).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | CRUD list/detail/create/PATCH(+archive) works; 401/403/404/409/412/422 | `tests/test_usage_metrics_api.py` (all tests listed below) green in `make check` |
| 2 | Audit row on every change, same transaction, with `reason` | `test_create_writes_audit_row_with_reason`, `test_patch_writes_updated_row`, `test_archive_and_restore_rows`, `test_noop_patch_writes_nothing`; `tests/test_audit_coverage.py` (new `AUDITED` entries, count 27 → 29) |
| 3 | `x-permission` declared on every route (`read` on GET, `write` on POST/PATCH) | `tests/test_route_auth_classes.py::test_bearer_route_permission_map` + `test_every_route_is_classified` with the 4 new `BEARER` entries; `tests/test_openapi_contract.py::test_admin_declared_error_responses` |
| 4 | `admin` app refused | `test_admin_app_404_on_every_route` |
| 5 | Period helper correct at Sun 23:59:59 / Mon 00:00 ICT and month end | `tests/test_usage_periods.py` (all listed below) |
| 6 | Permissions seeded `is_admin`, granted to no role, `admin:*` covers them | `tests/test_rbac_schema.py::test_admin_leaf_permissions_seeded` (set extended), `test_usage_metrics_migration.py::test_downgrade_removes_table_and_permissions`, `test_admin_wildcard_allows_all` |
| 7 | Migration round-trip; `alembic check` clean | `tests/test_usage_metrics_migration.py`; reviewer runs `uv run alembic check` and `uv run alembic downgrade -1 && uv run alembic upgrade head` on the local DB |
| 8 | DB constraints back the API rules | `tests/test_usage_metrics_schema_db.py` |

## Ming verification after merge
None (no hosted dependency; the staff UI lives in admin-pan). Optional: after deploy, run the `docs/deploy.md` query 2a and confirm the two new keys are listed as expected seed rows.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261120_<rev>_adm039_usage_metrics.py` | create: table `usage_metrics` + seed `admin:usage:read|write`; `downgrade()` drops both | R2; ticket scope bullets 1–2 | **High** (`migrations/**`) |
| `app/db/models.py` | modify: import `UsageMetric` | R2 models registry | **High** (`app/db/**`) |
| `app/modules/usage/__init__.py` | create (empty) | new domain module (copy organizations / bug_reports shape) | no |
| `app/modules/usage/models.py` | create: `UsageMetric`, `UsagePeriod`, `UsageMetricStatus`, key/unit regex constants | data model | no |
| `app/modules/usage/periods.py` | create: pure `period_start` / `period_end` / `period_bounds`, `USAGE_TZ` | ticket scope bullet 4 (see Assumptions A3 on "repository helper") | no |
| `app/modules/usage/schemas.py` | create: `UsageMetricCreate`, `UsageMetricUpdate`, `UsageMetricRead`, `Reason` type | API contract | no |
| `app/modules/usage/errors.py` | create: `UsageMetricExistsError(ConflictError)` code `usage_metric_exists` | typed 409 without touching `app/core/errors.py` | no |
| `app/modules/usage/repository.py` | create: `UsageMetricRepository` (SQLAlchemy only) | R1 | no |
| `app/modules/usage/service.py` | create: `UsageMetricService` (actor-generic, audits) | R1, R4 | no |
| `app/modules/usage/router.py` | create: 4 Bearer routes | R1, R3 class 1 | no |
| `app/api/router.py` | modify: `include_router(usage_metrics_router)` | mount | no |
| `app/modules/audit/actions.py` | modify: `AuditTarget.USAGE_METRIC`, 4 `AuditAction` values | R4 (no migration: `action` CHECK is format-only, `audit/models.py:33-35`) | no |
| `app/modules/audit/diff.py` | modify: `USAGE_METRIC` allowlist tuple | allowlisted diffs (ADR-0009 §4) | no |
| `docs/adr/0024-usage-metrics.md` | create: ADR (Proposed) — the rules that ADM-040, ADM-042..046 build on | decision outlives the ticket (loop.config Domain focus) | no |
| `docs/identity-architecture.md` | modify: §4 API table row (after the bug-reports row, l. 161) + decision-log row **D33** (after D29 / any D30–D32 rows) | keeps the API map and decision log current | no |
| `docs/deploy.md` | modify: query 2a allowlist (l. 449-451) adds `'admin:usage:read','admin:usage:write'` | seed audit query must not flag the new keys | no |
| `tests/test_usage_periods.py` | create | AC5 | no |
| `tests/test_usage_metrics_api.py` | create | AC1–4 | no |
| `tests/test_usage_metrics_schema_db.py` | create | AC8 | no |
| `tests/test_usage_metrics_migration.py` | create | AC6–7 | no |
| `tests/test_usage_schemas.py` | create | pure validation (reason, key, unit, limit) | no |
| `tests/test_route_auth_classes.py` | modify: 4 `BEARER` entries (after l. 123) | AC3 | no |
| `tests/test_openapi_contract.py` | modify: `_EXPECTED_ERRORS` 4 entries (after l. 342) | AC3 | no |
| `tests/test_audit_coverage.py` | modify: `AUDITED` 2 entries + scenarios, `len(AUDITED) == 29` (l. 508) | R4 coverage | no |
| `tests/test_rbac_schema.py` | modify: `ADMIN_LEAF_PERMISSIONS` (l. 48-62) adds the two keys | AC6 | no |

Not touched: `CLAUDE.md` (UUID metric ids keep R9 intact, no new exception), `app/core/**`, `app/modules/rbac/**`, `app/modules/admin_auth/**` (`require_permission` already accepts `admin:usage:*`, `admin_auth/deps.py:37`), `.env.example` (no config).

## Data model / migration
Migration `adm039_usage_metrics`, `down_revision = "c4e8a2f7b916"` (current head, ADM-029). Copy table style from `migrations/versions/20261119_b3f7a9c1d452_adm028_bug_reports.py` (names via `op.f`, naming convention from `app/db/base.py:27`) and the seed from `20261120_c4e8a2f7b916_adm029_bug_report_permissions.py:25-54`.

**Table `usage_metrics`**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | no | `UUIDPkMixin` | PK |
| `app_id` | uuid | no | — | FK `apps.id` `ON DELETE RESTRICT`, name `fk_usage_metrics_app_id_apps` |
| `key` | varchar(48) | no | — | immutable; CHECK `key_format`: `key ~ '^[a-z][a-z0-9-]{1,47}$'` (same alphabet as app/permission segments, e.g. `chat-turns`, `stt-seconds`) |
| `display_name` | varchar(100) | no | — | CHECK `display_name_length`: `char_length(display_name) BETWEEN 1 AND 100` |
| `unit` | varchar(32) | no | — | immutable; CHECK `unit_format`: `unit ~ '^[a-z][a-z0-9-]{0,31}$'` (free slug, e.g. `count`, `seconds`) |
| `default_limit` | bigint | no | — | CHECK `default_limit_range`: `default_limit BETWEEN 0 AND 9007199254740991` (0 blocks the metric, OQ62; max = 2^53−1 so JS clients never lose precision) |
| `period` | varchar(10) | no | `'week'` (server + ORM) | CHECK `period_valid`: `period IN ('day', 'week', 'month')` |
| `status` | varchar(20) | no | `'active'` (server + ORM) | CHECK `status_valid`: `status IN ('active', 'archived')` |
| `created_at`, `updated_at` | timestamptz | no | `now()` | `TimestampMixin` (`updated_at` drives the ETag) |

Constraints / indexes:
- `UNIQUE (app_id, key)` `uq_usage_metrics_app_id_key` — 409 source; also serves `WHERE app_id = ? ORDER BY key, id` lists and the FK. No extra index.
- `UNIQUE (id, app_id)` `uq_usage_metrics_id_app_id` — lets ADM-040/042 tables use a composite FK `(metric_id, app_id)` proving same-app (same pattern as `permissions`, `rbac/models.py:126`).
- No trigger for the `admin` refusal (cross-table); enforced in the service (R1).

The model `app/modules/usage/models.py` repeats the CHECK texts exactly (Alembic does not compare CHECKs; comment as in `bug_reports/models.py:1-4`).

**Seed:** `INSERT INTO permissions (id, app_id, key, description, is_admin) VALUES (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', :key, :description, true) ON CONFLICT (app_id, key) DO NOTHING` for
`admin:usage:read` ("Read usage metrics, defaults and periods of every app") and `admin:usage:write` ("Create usage metrics and change their name, default limit, period and status"). No `role_permissions` rows.

**downgrade():** delete the two permission keys (their `role_permissions` rows cascade; lossy for roles a super-admin added them to — documented in the docstring as in ADM-029), then `drop_table("usage_metrics")`. Docstring notes: later usage tables (ADM-040/042) must downgrade before this one.

## API contract
All routes: R3 class 1 (Bearer `aud=admin`), router `dependencies=[Depends(current_admin)]`, prefix `/apps/{key}/usage-metrics`, tag `usage`. `{key}` = app key (`APP_KEY_RE`), `{metric_id}` = UUID.
404 rule: unknown app, the `admin` app, or a metric id that does not belong to `{key}` → 404 `not_found`. Disabled apps are readable and writable (Q7).

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/usage-metrics` | query `limit`/`offset` (`PageDep`), `status` (`active`\|`archived`, optional; absent = all) | `Page[UsageMetricRead]`, ordered `key ASC, id ASC` | 200, 401, 403, 404, 422 | `admin:usage:read` |
| GET | `/api/v1/apps/{key}/usage-metrics/{metric_id}` | — | `UsageMetricRead` + `ETag` | 200, 401, 403, 404, 422 | `admin:usage:read` |
| POST | `/api/v1/apps/{key}/usage-metrics` | `UsageMetricCreate` | `UsageMetricRead` + `ETag`, 201 | 201, 401, 403, 404, 409 `usage_metric_exists`, 422 | `admin:usage:write` |
| PATCH | `/api/v1/apps/{key}/usage-metrics/{metric_id}` | `UsageMetricUpdate`, optional `If-Match` | `UsageMetricRead` + `ETag` | 200, 401, 403, 404, 412 `precondition_failed`, 422 | `admin:usage:write` |

No DELETE (archive = PATCH `status`; key stays reserved forever so history in ADM-042 is never orphaned).

Schemas (`app/modules/usage/schemas.py`, all request models `extra="forbid"`):
- `Reason` = `Annotated[str, BeforeValidator(nfc_if_str), strip, StringConstraints(min_length=1, max_length=500), AfterValidator(reject_disallowed_categories)]` — trimmed, not blank, NFC, no control/bidi chars (`app/core/text.py:12-21`, used as in `rbac/schemas.py:35-37`). Exported for ADM-040/044.
- `DisplayName` = same building blocks, 1–100.
- `UsageMetricCreate`: `key: str` (pattern `^[a-z][a-z0-9-]{1,47}$`), `display_name: DisplayName`, `unit: str` (pattern `^[a-z][a-z0-9-]{0,31}$`), `default_limit: int` (`ge=0, le=2**53-1`, `strict` so `true`/`"5"`/`1.5` are 422), `period: UsagePeriod = UsagePeriod.WEEK`, `reason: Reason` (required). `status` is not accepted (always `active`).
- `UsageMetricUpdate`: optional `display_name`, `default_limit`, `period`, `status` (`UsageMetricStatus`); required `reason`. Validator: at least one field besides `reason`; explicit `null` on any field → 422. `key` and `unit` are rejected as extra fields (422 `extra_forbidden`) — immutable.
- `UsageMetricRead`: `id`, `app_key`, `key`, `display_name`, `unit`, `default_limit`, `period` (always resolved, never null), `status`, `created_at`, `updated_at`. `reason` is never returned (it lives in the audit entry only).

OpenAPI: each route uses `**require_permission(...)` with `errors={404: ..., 409/412: ...}`; GET detail, POST 201 and PATCH 200 declare the `ETag` header (copy `_etag_200` from `bug_reports/router.py:40-45` and the 201 variant in `entitlements/router.py:28-34`); PATCH declares `If-Match` with `IF_MATCH_DESCRIPTION` (`app/core/etag.py:19`). The list route also declares 404 (`test_parameterized_admin_routes_declare_404`).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_usage_periods.py` › `test_week_sunday_235959_ict_belongs_to_previous_monday` | 2026-11-22T23:59:59.999999+07:00 → start 2026-11-16T00:00+07 (= 2026-11-15T17:00Z), end 2026-11-23T00:00+07 |
| › `test_week_monday_0000_ict_starts_new_week` | 2026-11-23T00:00+07 (= 2026-11-22T17:00Z) → start = itself |
| › `test_day_utc_1659_vs_1700_crossing` | 2026-11-20T16:59:59Z → 2026-11-20 ICT; 2026-11-20T17:00:00Z → 2026-11-21 ICT |
| › `test_month_last_second_and_first_instant` | 2026-11-30T23:59:59+07 → 2026-11-01; 2026-12-01T00:00+07 → 2026-12-01; 30 Apr / 1 May |
| › `test_month_end_year_rollover` | 2026-12-31T23:59:59+07: start 2026-12-01, end 2027-01-01 |
| › `test_february_non_leap_and_leap` | 2027-02-28 end = 2027-03-01; 2028-02-29 start 2028-02-01, end 2028-03-01 |
| › `test_week_spanning_month_and_year` | 2026-01-01 (Thu) → week start 2025-12-29 |
| › `test_naive_datetime_rejected` | `ValueError` for naive input |
| › `test_other_offset_same_result` | 2026-11-20T09:00+09:00 == 2026-11-20T00:00Z input |
| › `test_results_are_aware_utc` | `tzinfo is UTC` on start/end |
| › `test_bounds_contain_instant_and_chain` (parametrized over all periods + sample instants) | `start <= at < end`; `period_start(end) == end` |
| `tests/test_usage_schemas.py` › `test_reason_trimmed_and_required`, `test_reason_blank_or_501_chars_rejected`, `test_reason_control_char_rejected` | OQ67 contract |
| › `test_key_and_unit_patterns`, `test_default_limit_bounds_and_strict_int`, `test_period_defaults_to_week` | create validation |
| › `test_update_requires_a_field_besides_reason`, `test_update_rejects_null_and_key_unit` | PATCH validation, immutability |
| `tests/test_usage_metrics_api.py` › `test_routes_401_without_token` | 401 on all 4 routes |
| › `test_reads_403_without_read`, `test_writes_403_with_read_only` | 403 `permission_denied` (`details.required`) |
| › `test_admin_wildcard_allows_all` | `admin:*` covers both |
| › `test_create_happy_path_201_etag` | 201, body, `ETag`, `period` defaults to `week`, `status` `active` |
| › `test_create_explicit_period_month` | period stored |
| › `test_create_duplicate_key_409` | 409 `usage_metric_exists`; same key on another app is allowed |
| › `test_create_archived_key_still_409` | archived key stays reserved |
| › `test_create_concurrent_duplicate_409` | `IntegrityError` in savepoint mapped to 409 (pre-check bypassed via a row inserted between, as `entitlements/service.py:93-97`) |
| › `test_create_422_cases` | bad key/unit/limit/period, missing reason, extra `status` field |
| › `test_unknown_app_404_on_every_route` | 404 |
| › `test_admin_app_404_on_every_route` | ticket "refused for the admin app" |
| › `test_disabled_app_allowed` | Q7 default |
| › `test_list_pagination_order_and_bounds` | `Page[T]`, `key, id` order, `limit` > 100 → 422 |
| › `test_list_status_filter` | `status=active` / `archived` / absent |
| › `test_detail_other_app_metric_404`, `test_detail_unknown_metric_404` | 404 scoping |
| › `test_patch_display_name_limit_period` | 200, fields change, ETag changes |
| › `test_patch_key_or_unit_422` | immutability |
| › `test_patch_archive_and_restore` | `status` round-trip, archived metric still readable |
| › `test_patch_if_match_stale_412`, `test_patch_if_match_current_ok`, `test_patch_if_match_star_ok` | ADM-024 behaviour |
| › `test_noop_patch_writes_nothing` | same values → 200, no audit row, `updated_at` unchanged |
| › `test_create_writes_audit_row_with_reason` | `usage_metric.created`, target `usage_metric`, `app_key`, `changes` = diff of allowlist + `reason` string |
| › `test_patch_writes_updated_row` | `usage_metric.updated` with `{field: {from,to}}` + `reason` |
| › `test_archive_and_restore_rows` | `usage_metric.archived` / `.restored`; status + other field in one PATCH → two rows, both with reason |
| › `test_reason_not_in_logs` | `caplog` has no reason text |
| › `test_app_delete_with_metrics_is_409_not_500` | `DELETE /apps/{key}` with a metric → 409 (existing generic catch, `rbac/service.py:560-563`); see follow-up F1 |
| › `test_openapi_if_match_etag_declared` | PATCH 412 + `If-Match`, `ETag` on GET/POST/PATCH |
| `tests/test_usage_metrics_schema_db.py` › `test_check_constraints` (parametrized: key, unit, display_name length, default_limit < 0 and > 2^53−1, period, status), `test_unique_app_key`, `test_server_defaults`, `test_app_fk_restrict` | DB backs the rules |
| `tests/test_usage_metrics_migration.py` › `test_downgrade_removes_table_and_permissions` | downgrade to `c4e8a2f7b916`: no table, no `admin:usage:%`; upgrade head: both permissions `is_admin`, 0 role grants (copy `test_bug_reports_permissions_migration.py`) |
| `tests/test_audit_coverage.py` › existing parametrized tests | new `AUDITED` entries: POST (`_usage_create` / `_usage_create_dup` → 409) and PATCH (`_usage_update` changing `default_limit` only / `_usage_unknown` → 404), actions `{"usage_metric.updated","usage_metric.archived","usage_metric.restored"}` for PATCH |

## Approach
1. **Models** (`usage/models.py`): `UsagePeriod(StrEnum)` `day|week|month`, `UsageMetricStatus(StrEnum)` `active|archived`, `METRIC_KEY_RE`, `UNIT_RE`, `MAX_LIMIT = 2**53 - 1`; `UsageMetric(UUIDPkMixin, TimestampMixin, Base)` per the table above. Register in `app/db/models.py`.
2. **Migration**: `make migration m="adm039 usage metrics"`, then hand-edit to the exact table above plus the seed block; working `downgrade()`; `uv run alembic check` clean; round-trip locally.
3. **Period helper** (`usage/periods.py`, pure, no SQLAlchemy): `USAGE_TZ = ZoneInfo("Asia/Ho_Chi_Minh")`; `period_start(period, at)` rejects naive `at` (`ValueError`), converts to ICT, truncates to 00:00, back to Monday (`weekday()`) or day 1, returns `astimezone(UTC)`; `period_end` = exclusive next start (day +1, week +7, month → 1st of next month, December → January); `period_bounds` returns both. ICT has no DST, so wall-clock arithmetic is exact; the docstring says so. Callers (ADM-042+) pass the result as a bound parameter to SQL.
4. **Schemas** as in API contract.
5. **Repository** (`usage/repository.py`, SQLAlchemy only): `page(app_id, status, limit, offset) -> (list, total)`, `get(app_id, metric_id)`, `get_for_update(app_id, metric_id)` (`FOR UPDATE`), `find_by_key(app_id, key)`, `add(metric)` inside `session.begin_nested()`, `apply(metric, values)`. App lookup reuses `RbacRepository.get_app_by_key` (`rbac/repository.py:58`) — app lookup lives in `rbac`; no lock (`lock_app` is for RBAC writers only, ADR-0003 §9).
6. **Service** (`usage/service.py`, HTTP-free): `UsageMetricService(session)` with `list`, `get`, `create(app_key, data, *, actor: Actor, ctx)`, `update(app_key, metric_id, data, *, actor, ctx, precondition)`. `_app(app_key)`: `admin` → `NotFoundError` before any query; unknown → `NotFoundError`. Create: pre-check `find_by_key` → `UsageMetricExistsError`; insert in savepoint, `IntegrityError` → same error; audit `usage_metric.created`. Update: `get_for_update`, If-Match check (copy `bug_reports/staff_service.py:60-65`), compute changed fields; no change → return without write/audit; else `apply`, then audit: status change → one `archived`/`restored` row; other field changes → one `updated` row; each `changes = diff.diff(before, after) | {"reason": data.reason}`. `actor` is typed `Actor` (`rbac/actor.py:26`); the service never inspects `AdminPrincipal` fields and never checks permissions (the route dependency does) — this is the seam ADM-044 widens with an app-admin actor (ADR-0024). Log lines carry ids, app key, metric key and actor id only — never `reason` (R5/R11). R4: `AuditLogger.record` flushes in the request transaction, so change and row commit together.
7. **Router** (`usage/router.py`): HTTP only (R1); `AdminDep`, `AuditContextDep`, `PageDep`, `parse_if_match`, `version_etag`; sets `ETag`. Mount in `app/api/router.py`.
8. **Audit**: `AuditTarget.USAGE_METRIC = "usage_metric"`; actions `USAGE_METRIC_CREATED = "usage_metric.created"`, `USAGE_METRIC_UPDATED = "usage_metric.updated"`, `USAGE_METRIC_ARCHIVED = "usage_metric.archived"`, `USAGE_METRIC_RESTORED = "usage_metric.restored"`; `diff.USAGE_METRIC = ("key", "display_name", "unit", "default_limit", "period", "status")`. `reason` passes `_check_keys` (`audit/service.py:28`).
9. **ADR-0024 "Usage metrics: identity, periods and change rules"** (Proposed; accepted when Ming merges) records, for ADM-040/042–046: immutable `(app, key)` and `unit`, never deleted, `UNIQUE(id, app_id)` for composite FKs; `day|week|month` values (spec prose "daily/weekly/monthly" = these); calendar periods via `usage/periods.py` only; the change rules below; `reason` 1–500 required on every usage-config/limit write and stored as a flat `changes.reason` string next to the `{from,to}` diffs; actor-generic service, app-admin actor and its field allowlist (`default_limit`, `period` only) added by ADM-044 with an audit `actor_kind` extension; archived metric = consume refused (ADM-042). **D33** row in `docs/identity-architecture.md` points to it.
10. **Rules stated (ADR-0024):**
    - *Default-limit change* applies to the running period at once; it is read live, never copied into per-user rows (ADM-040 stores overrides only).
    - *Period change* applies at once: the running period becomes the calendar period of the new kind that contains `now`, and usage in it is what was consumed since that period's start (ADM-042 computes it; its totals are keyed by `(metric, user, period, period_start)` so old buckets are not reused).
    - *Archive* is reversible (`status` back to `active`); usage history and key are kept.
11. `make check` green; `uv run alembic check` clean.

Rules: R1 (layers above), R2 (step 2), R3 class 1 (step 7, test maps), R4 (step 6/8), R5 (no config; reason never logged), R6 (only `AppError` subclasses), R7 (follow-ups below), R8 (tests), R9 (plural noun, `Page[T]` ≤ 100, UUID ids, timestamptz, `PATCH`), R10 (no billing), R11 (no PII in logs/errors).

## Specialist input used
- api-platform-engineer: adopted — immutable key, immutable unit, 0 legal / no null limit, `day|week|month`, resolved period in response, archive via PATCH `status`, 404 for admin app, aware-datetime helper with UTC/ICT crossing tests, seed + CHECKs + unique, audit before/after; If-Match adopted because PATCH routes already use it (orgs, entitlements, bug reports). Period-change rule "applies immediately" adopted. List default: all statuses (it asked to choose).
- database-optimizer: adopted — table/columns/CHECKs (key alphabet changed to hyphen to match app/permission keys), bigint, `UNIQUE(id, app_id)`, server default `week`, no `apps` column (deferred, Q1), service-level admin refusal, never hard-delete, required `reason`, helper with exclusive end, no extra indexes. Rejected — addressing metrics by key in the path (would need a new R9 exception; UUID matches D18 "permissions, roles … keep UUID ids"); "period change from next boundary" (needs a pending-period column; immediate rule is simpler and still consistent with per-`period_start` totals).
- identity-access-engineer: adopted — one permission per route, no `requires_super`, 401/403/`admin:*` tests, required reason in `changes.reason`, never logged, 404 for admin, immutable key, generic `Actor` service seam, helper in a pure module (not repository, R1), If-Match. Its preferred nullable `apps.usage_period` column is put to Ming as Q1 with "no column" as default (ticket R7 scope; the metric already stores its own copy). Audit `actor_kind` for app keys deferred to ADM-044 (recorded in ADR-0024).

## Assumptions
- A1. Hotspots with open tickets in this batch (ADM-030, ADM-033, ADM-035 all PLANNING with migrations): **migration `down_revision`** (all currently chain from `c4e8a2f7b916`; whoever merges later rebases its revision), `app/db/models.py`, `app/api/router.py`, `app/modules/audit/actions.py`, `app/modules/audit/diff.py`, `tests/test_route_auth_classes.py`, `tests/test_openapi_contract.py`, `tests/test_audit_coverage.py` (the `len(AUDITED)` count), `tests/test_rbac_schema.py`, `docs/identity-architecture.md` (§4 table and D30–D32 rows), `docs/deploy.md` query 2a. Expect a rebase; conflicts are additive.
- A2. ADR number 0024 and decision D33 as assigned by the orchestrator (0021–0023 / D30–D32 reserved by other batch tickets).
- A3. The ticket says "repository helper" for the period start; it is pure calendar logic, so it goes in `usage/periods.py` (R1: repositories are SQLAlchemy only). Same single function, same tests.
- A4. `python:3.12-slim` ships tzdata, and CI runners do; `ZoneInfo("Asia/Ho_Chi_Minh")` loads (the period tests fail loudly otherwise). No new dependency.
- A5. `changes.reason` is a flat string next to `{from,to}` diffs (ADR-0009 shape for admin mutations is otherwise unchanged); admin-pan's audit view must render it — follow-up F2.
- A6. ADM-041 gates only app-credential usage routes; these Bearer routes may ship before it (spec l. 272 talks about the scoped-key routes).
- Follow-ups (not in this diff): F1 `DELETE /apps/{key}` with usage metrics returns the generic "App has role assignments" 409 (`rbac/service.py:560-563`); add an `app_has_usage_metrics` check with reason `app_has_usage_metrics` (same gap exists for bug reports). F2 admin-pan: usage-metric screens + render `changes.reason`. F3 optional `apps.usage_default_period` if Ming answers Q1 "yes, later".

## Blocking questions
None blocking — the defaults below are applied; Ming may override in the batch round.

1. **App-level period setting vs constant weekly.** Spec says "initialised from the app's period setting (default `weekly`)", but no such setting exists. *Default:* no `apps` column in v1; `period` on create is optional and defaults to the constant `week`; a later ticket may add `apps.usage_default_period` without breaking anything.
2. **Reason required on metric create/PATCH/archive?** *Default:* yes — `reason` 1–500 chars (trimmed, not blank) required on every write, stored in the audit entry only (OQ67 "every staff and app-admin adjustment").
3. **Unit mutability.** *Default:* `unit` and `key` are immutable (PATCH with them → 422). To change a unit, archive the metric and create a new key.
4. **Archive via PATCH `status`.** *Default:* yes — `status: archived|active` on PATCH, reversible, no DELETE, key reserved forever; list shows all statuses unless `?status=` is given.
5. **Mid-period change rule.** *Default:* `default_limit` and `period` changes apply immediately to the running period; for a period change the running period becomes the new kind's calendar period containing now, with usage counted since its start (alternative: apply from the next boundary of the new period).
6. **If-Match on PATCH.** *Default:* optional `If-Match` with 412 `precondition_failed`, `ETag` on reads/writes (ADM-024 precedent).
7. **Disabled apps and the `admin` app.** *Default:* `admin` → 404 on every route (like D17); disabled apps stay readable and writable (config is inert; ADM-042 consume refuses disabled apps).
8. **Metric addressing.** *Default:* `/usage-metrics/{metric_id}` (UUID), no new R9 exception; app backends use the metric `key` in ADM-042 bodies.

## Split proposal
None. Estimate ~450 product lines (module ~350, migration ~90, audit/registry ~15) plus ~550 test lines and the ADR; one migration, one module, one implement/review cycle. If the orchestrator wants a smaller High PR, the only clean slice is `usage/periods.py` + `tests/test_usage_periods.py` (pure, no High path) first — not recommended, the helper has no caller yet.
