Status: READY

# Plan — ADM-015: Dashboard stats endpoint for the admin UI

## Spec summary
`docs/spec-v1.md` §5.9 "Admin UI read models" (2026-11-16):

> **Dashboard stats** — one read-only document for the dashboard screen (a single document, not `Page[T]`: an R9 exception to record like D18). Every number comes from data that already exists; counts are returned as breakdowns so the UI, not this spec, chooses what a tile shows:
>
> | Total users | `users` — total, by `status` (`active`/`disabled`) |
> | Unverified users | `users.email_verified_at IS NULL` |
> | Active / suspended orgs | `organizations.status` (`active`/`suspended`, §5.1) |
> | Apps | `apps` — total, by `status` |
> | Sign-ins, last 24 h (succeeded / failed) | `audit_log` rows `auth.login_succeeded` / `auth.login_failed` in a rolling 24 h window (event counts, not distinct users) |
> | Sign-ups per month | `users.created_at` grouped by calendar month |
> | Members per app | same definition as the per-app member count below |
>
> Caveats to keep visible in the UI and API docs: failed sign-ins include unknown emails (anonymous actor) but **not** requests refused with 429 or by Turnstile (ADM-004b writes nothing for those); sign-up counts include self sign-ups and CLI-created accounts, and do not depend on the audit log (whose retention is ≥ 90 days, ADM-004c). Reads must meet §6 (p95 < 200 ms) — the planner checks that the audit-log count is index-backed.
>
> Permission: §8 OQ4. Read-only, so no audit entry (R4 covers mutations only).

Owner answers (ticket notes, 2026-11-17):
- **OQ4**: "new permission `admin:stats:read` for the dashboard stats document (counts only, no audit rows exposed); `member_count` uses the existing `admin:apps:read`."
- **OQ5**: "a member = distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff)."
- **OQ6**: "sign-ups per month: last 12 months, month boundaries in `Asia/Ho_Chi_Minh`; sign-ins last 24 h are totals (succeeded / failed), not split per app."

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | One Bearer admin endpoint returns one snake_case document with users by status + unverified; organizations by status (active, suspended); apps by status; sign-ins in the last 24 h (succeeded, failed) from `auth.login_succeeded` / `auth.login_failed` audit rows; sign-ups per month from `users.created_at`; members per app (OQ5 definition) | `tests/test_stats_api.py` › `test_dashboard_shape`, `test_user_counts`, `test_org_counts`, `test_app_counts`, `test_sign_ins_24h_counts`, `test_sign_ups_by_month_api`, `test_members_per_app_api`; `tests/test_stats_repository.py` › sign-up month tests; `tests/test_rbac_member_counts.py` (shared function) |
| 2 | Single document, not `Page[T]`; recorded as an R9 exception like D18 | Response has no `items`/`total`/`limit`/`offset` (`test_dashboard_shape`); diff shows the R9 line in `CLAUDE.md` and decision **D26** in `docs/identity-architecture.md` §8, plus the §4 API table row |
| 3 | Permission follows OQ4: `admin:stats:read` | `tests/test_route_auth_classes.py` BEARER map entry (checks the guard and `x-permission`); `test_stats_api.py` › `test_403_without_stats_permission`, `test_stats_permission_allows`, `test_wildcard_allows`; permission seeded by the new migration (`tests/test_stats_migration.py`, `tests/test_rbac_schema.py` › `test_admin_leaf_permissions_seeded`) |
| 4 | Read-only: no audit row, and `Cache-Control: no-store` | `test_stats_api.py` › `test_no_audit_row_written` (count `audit_log` before/after a 200), `test_cache_control_no_store`; route is `GET` only, so `tests/test_audit_coverage.py` › `test_every_mutating_bearer_route_is_mapped` stays green without a new entry |
| 5 | Tests cover each counter against seeded data, plus 401/403 and the 24 h window boundary | `test_stats_api.py` › `test_401_without_token` (plus the parametrized sweeps in `test_route_auth_classes.py`), `test_403_without_stats_permission`, `test_sign_ins_24h_boundary` |
| 6 | Query cost is bounded. **Precise wording adopted by this plan:** "the `audit_log` count uses `ix_audit_log_action` as a range scan over the 24 h window only (no full scan of `audit_log`); the sign-up series filters `users.created_at` with a sargable range; the other counts (users, organizations, apps, role assignments) are one aggregate query each over small tables, and no query runs per app, per org or per user (no N+1)" | `test_stats_repository.py` › `test_login_count_uses_action_index` (EXPLAIN with `enable_seqscan = off`, asserts `ix_audit_log_action`, as `tests/test_audit_schema.py` › `test_outcome_partial_index_used` does); `test_stats_api.py` › `test_dashboard_query_count` (a fixed number of statements, independent of the number of apps) |

R8 notes: the endpoint has no path, query or body input, so "404" and "validation/conflict" cases do not apply. The plan records this; the 401/403 tests stand in for them.

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/stats/__init__.py` | new, empty | new domain module (shape of `app/modules/organizations/`) |
| `app/modules/stats/schemas.py` | new: `DashboardStats` and nested models (see API contract) | response contract, snake_case, fixed status keys |
| `app/modules/stats/repository.py` | new `StatsRepository`: `db_now()`, `user_counts()`, `organization_counts()`, `sign_ups_by_month(now, months, tz)` | R1: SQLAlchemy only; dashboard-only aggregates with no other caller |
| `app/modules/stats/service.py` | new `StatsService.dashboard() -> DashboardStatsView`: reads the DB clock once, calls the repositories one after another, zero-fills statuses, assembles the view; constants `SIGN_UP_MONTHS = 12`, `STATS_TIME_ZONE = "Asia/Ho_Chi_Minh"`, `SIGN_IN_WINDOW = timedelta(hours=24)` | R1 business assembly; OQ6 values defined in one place |
| `app/modules/stats/router.py` | new: `GET /stats/dashboard`, `dependencies=[Depends(current_admin)]`, `**require_permission("admin:stats:read")`, sets `Cache-Control: no-store`; OpenAPI description carries the §5.9 caveats | R3 class 1, R9 exception, AC 4 |
| `app/api/router.py` | include `stats_router` | mount under `/api/v1` (same place as `entitlements_router`) |
| `app/modules/rbac/repository.py` | add `member_counts(app_ids: Sequence[uuid.UUID] \| None = None) -> dict[uuid.UUID, int]` and `list_app_summaries() -> list[tuple[uuid.UUID, str, str, str]]` (id, key, name, status, ordered by key) | **The shared OQ5 member function ADM-016 reuses**; app list for members and apps by status in one query |
| `app/modules/audit/repository.py` | add `count_actions_since(actions: Sequence[str], since: datetime) -> dict[str, int]`; extend the module docstring ("`add` and `list` are the only row operations" → also the read-only `count_actions_since`) | `audit_log` access stays in the audit module, next to its indexes (DB consult 1) |
| `migrations/versions/20261117_<rev>_adm015_stats_permission.py` | new data-only migration, `down_revision = "c8a1f4e72d95"`: inserts `admin:stats:read` on the `admin` app, `is_admin = true`, granted to no role, `ON CONFLICT (app_id, key) DO NOTHING`; `downgrade()` deletes the key (its `role_permissions` rows cascade) | OQ4; R2 (working downgrade); ADM-006 pattern |
| `tests/test_stats_api.py` | new (see Tests) | R8, AC 1–6 |
| `tests/test_stats_repository.py` | new (see Tests) | sign-up month boundaries with a fixed clock; EXPLAIN check |
| `tests/test_rbac_member_counts.py` | new (see Tests) | the shared function, tested directly, so ADM-016 inherits the tests |
| `tests/test_stats_migration.py` | new: downgrade to `c8a1f4e72d95` removes the key, upgrade restores it (copy of `tests/test_entitlements_migration.py` › `test_downgrade_drops_table_and_permissions`, permissions part only) | R2 |
| `tests/test_route_auth_classes.py` | add `("GET", f"{V1}/stats/dashboard"): "admin:stats:read"` to `BEARER` (no `_fill` change: no placeholders) | `test_every_route_is_classified` / `test_bearer_route_permission_map` fail otherwise |
| `tests/test_rbac_schema.py` | add `"admin:stats:read"` to `ADMIN_LEAF_PERMISSIONS` | permission catalogue test (all leaf keys seeded, `is_admin`, super-admin holds only `admin:*`) |
| `CLAUDE.md` | R9 exceptions: append "; `GET /api/v1/stats/dashboard` (a single document, not `Page[T]`; D26)" | AC 2, same as D18/D23/D25 were recorded |
| `docs/identity-architecture.md` | §4 table: new row for `GET /api/v1/stats/dashboard`; §8: new decision **D26** after D25 | AC 2; API surface reference |
| `docs/spec-v1.md` | §8: tick OQ4, OQ5, OQ6 with the dated answers; §5.9: replace "Proposed definition (owner to confirm…)", "Permission: §8 OQ4" and the "Computable today?" cells for sign-ups/members with the decided values, and add a `> 2026-11-17 (ADM-015): …` note (as ADM-006 did in §5.6) | spec must agree with the answers; caller instruction |
| `docs/deploy.md` | pre-ADM-002b audit query 2a (line ~436): add `'admin:stats:read'` to the seeded-key list | same as ADM-006 did; otherwise the check flags the new seed as unexpected |

No other files. `app/db/models.py` needs no change (no new model). `ADM-016`'s `member_count` field, the apps router and the apps schemas are **not** touched (R7).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_stats_api.py` › `test_dashboard_shape` | 200; exact top-level keys; every status key present even when 0; `sign_ups.by_month` has exactly 12 entries, oldest first, last = current month in `Asia/Ho_Chi_Minh`; `sign_ups.time_zone == "Asia/Ho_Chi_Minh"`; no `Page` keys |
| `test_stats_api.py` › `test_user_counts` | baseline call, then seed active verified, active unverified, disabled unverified, disabled verified users; deltas: `total`, `by_status.active`, `by_status.disabled`, `unverified` (unverified counts all statuses) |
| `test_stats_api.py` › `test_org_counts` | seed active and suspended orgs; deltas by status and total |
| `test_stats_api.py` › `test_app_counts` | seed active and disabled apps; deltas; the migration-seeded `admin` app is counted |
| `test_stats_api.py` › `test_sign_ins_24h_counts` (`@pytest.mark.backdate_audit`) | rows of `auth.login_succeeded` (actor `user` + `actor_user_id`), `auth.login_failed` (actor `anonymous`, and with outcome `failure`), plus `auth.logout` and another action that must **not** count; `succeeded` / `failed` exact; `window_start == generated_at - 24h`, `window_end == generated_at` |
| `test_stats_api.py` › `test_sign_ins_24h_boundary` (`@pytest.mark.backdate_audit`) | rows at `now() - interval '24 hours'` (counted, inclusive), `now() - interval '23 hours 59 minutes'` (counted), `now() - interval '24 hours 1 second'` (not counted); `now()` is the transaction start and the client shares the test session, so this is deterministic |
| `test_stats_api.py` › `test_sign_ups_by_month_api` | users with `created_at` set (raw `UPDATE`) to now, to ~2 months ago and to 13 months ago; current-month and 2-month-ago buckets increase, the 13-month-old user is outside every bucket, a gap month shows 0 |
| `test_stats_api.py` › `test_members_per_app_api` | every app is listed (including `admin`, disabled apps, apps with 0 members), ordered by `app_key`; counts equal `RbacRepository.member_counts()` for the same data |
| `test_stats_api.py` › `test_401_without_token` | 401 `unauthorized`, `WWW-Authenticate: Bearer …` (real `client`, no override) |
| `test_stats_api.py` › `test_403_without_stats_permission` | `make_client(admin_permissions=["admin:users:read", "admin:apps:read", "admin:audit:read"])` → 403 `permission_denied`, `details.required == "admin:stats:read"` |
| `test_stats_api.py` › `test_stats_permission_allows` | real token: `make_staff(db_session, "admin:stats:read")` + `mint` → 200 (proves the key is seeded and enforced end to end) |
| `test_stats_api.py` › `test_wildcard_allows` | `admin:*` → 200 |
| `test_stats_api.py` › `test_cache_control_no_store` | 200 response has `Cache-Control: no-store` |
| `test_stats_api.py` › `test_no_audit_row_written` | `SELECT count(*) FROM audit_log` equal before and after a 200 |
| `test_stats_api.py` › `test_dashboard_query_count` | SQLAlchemy `before_cursor_execute` listener on the test session's engine/connection: the number of statements for one request is the same with 1 extra app and with 5 extra apps (no N+1); the exact number (expected 6: now, users, orgs, apps, audit, sign-ups, members → adjust if `db_now` is folded) is asserted |
| `test_stats_repository.py` › `test_sign_ups_month_boundary_in_hcm` | `sign_ups_by_month(now=2026-11-15T00:00Z, months=12, tz="Asia/Ho_Chi_Minh")`: user at `2026-10-31T16:59:59Z` → `2026-10`; user at `2026-10-31T17:00:00Z` → `2026-11` |
| `test_stats_repository.py` › `test_sign_ups_window_and_zero_fill` | same fixed `now`: exactly 12 entries `2025-12` … `2026-11`; user at `2025-11-30T16:59:59Z` (= 2025-11-30 local) excluded; user at `2025-11-30T17:00:00Z` (= 2025-12-01 local) in `2025-12`; empty months are 0; disabled users counted |
| `test_stats_repository.py` › `test_login_count_uses_action_index` | `SET LOCAL enable_seqscan = off`; EXPLAIN of the statement `count_actions_since` builds (or a text mirror of it, kept next to the builder) contains `ix_audit_log_action` and no `Seq Scan on audit_log` |
| `test_rbac_member_counts.py` › `test_app_and_org_scope_counted_once` | one user with two roles on the same app, and in two orgs + app scope → 1 |
| `test_rbac_member_counts.py` › `test_expired_assignment_excluded` | `expires_at` in the past → not counted; future `expires_at` → counted |
| `test_rbac_member_counts.py` › `test_disabled_user_excluded_unverified_counted` | disabled user not counted; active unverified user counted (OQ5) |
| `test_rbac_member_counts.py` › `test_admin_app_counts_staff` | `make_staff(..., "admin:stats:read")` → counted under the `admin` app id |
| `test_rbac_member_counts.py` › `test_app_ids_filter_and_missing_apps` | `app_ids=[a, b]` returns only those keys; an app with no members is absent from the dict (callers default to 0); `app_ids=[]` → `{}` without a query |
| `test_stats_migration.py` › `test_downgrade_removes_stats_permission` | downgrade to `c8a1f4e72d95` → key gone; upgrade head → key back, `is_admin = true`, held by no role |
| `test_route_auth_classes.py` (existing, one map entry) | classification, `x-permission`, 401/403 in OpenAPI, CORS matcher, 401 sweeps, cookie-only 401, app-credential 401 |
| `test_rbac_schema.py` › `test_admin_leaf_permissions_seeded` (existing, one set entry) | key seeded on `admin`, `is_admin`, super-admin still holds only `admin:*` |

Test notes for the implementer:
- `audit_log.occurred_at` is overwritten to `now()` by the `audit_log_stamp_occurred_at` trigger (ADR-0009 ADM-004d). Backdated rows **require** `@pytest.mark.backdate_audit` on the test (see `tests/test_audit_logs_api.py`). Do not mark tests that use a second connection or commit.
- `actor_consistent` CHECK: `actor_kind='user'` needs `actor_user_id`; `anonymous` needs none.
- Counter tests use a baseline call and assert deltas, so they do not depend on rows that migrations seed (the `admin` app, staff created by fixtures).

## Approach
1. **Migration** (R2): copy the ADM-006 permission block into a new revision on top of `c8a1f4e72d95`: `PERMISSIONS = [("admin:stats:read", "Read dashboard statistics (counts only)")]`, same `INSERT … ON CONFLICT (app_id, key) DO NOTHING` with `is_admin = true`, same `DELETE … WHERE app_id = … AND key IN :keys` downgrade. Docstring: "ADM-015: expand-only, data only; granted to no role; downgrade deletes the key (role_permissions cascade)". `alembic check` stays clean (no schema change).
2. **Shared member function** (`RbacRepository.member_counts`), the single source of the OQ5 definition:
   ```sql
   SELECT r.app_id, count(DISTINCT ra.user_id)
   FROM role_assignments ra
   JOIN roles r ON r.id = ra.role_id
   JOIN users u ON u.id = ra.user_id AND u.status = 'active'
   WHERE (ra.expires_at IS NULL OR ra.expires_at > now())   -- DB clock, as _assignment_rows
     [AND r.app_id = ANY(:app_ids)]
   GROUP BY r.app_id
   ```
   No filter on `org_id` (app scope and every org count), no email-verified filter, no org-status filter (OQ5 does not mention it). Docstring: "OQ5 member definition (owner, 2026-11-17); ADM-015 dashboard and ADM-016 `member_count` both call this — do not duplicate it." Use `UserStatus.ACTIVE.value`. Empty `app_ids` returns `{}` without a query.
3. **App summaries** (`RbacRepository.list_app_summaries`): `SELECT id, key, name, status FROM apps ORDER BY key`. The service derives `apps.by_status` / `apps.total` from it and joins it with `member_counts()` (default 0) for `members_per_app`. One query for both tiles.
4. **Audit count** (`AuditRepository.count_actions_since`): `SELECT action, count(*) FROM audit_log WHERE action IN (:actions) AND occurred_at >= :since GROUP BY action`; returns a dict, missing actions default to 0 in the service. Actions come from `AuditAction.AUTH_LOGIN_SUCCEEDED` / `AUTH_LOGIN_FAILED`, never literals. No upper bound (rows committed after the transaction started may be included; harmless, documented).
5. **Stats repository**:
   - `db_now()` → `SELECT now()` (transaction time).
   - `user_counts()` → `SELECT status, count(*), count(*) FILTER (WHERE email_verified_at IS NULL) FROM users GROUP BY status`.
   - `organization_counts()` → `SELECT status, count(*) FROM organizations GROUP BY status`.
   - `sign_ups_by_month(now, months, tz)` → months generated and zero-filled in SQL, sargable on `created_at`:
     ```sql
     WITH cur AS (SELECT date_trunc('month', CAST(:now AS timestamptz) AT TIME ZONE :tz) AS m),
          months AS (SELECT generate_series(cur.m - make_interval(months => :months - 1), cur.m,
                                            interval '1 month') AS m FROM cur)
     SELECT to_char(months.m, 'YYYY-MM') AS month, count(u.id) AS count
     FROM months LEFT JOIN users u
       ON u.created_at >= (months.m AT TIME ZONE :tz)
      AND u.created_at <  ((months.m + interval '1 month') AT TIME ZONE :tz)
     GROUP BY months.m ORDER BY months.m
     ```
     Never `date_trunc('month', created_at)` without `AT TIME ZONE` (the session zone is UTC: off by 7 h near month ends). Taking `now` as a parameter makes the boundary tests deterministic.
6. **Service** (`StatsService.dashboard`): `now = await stats_repo.db_now()`; then, **sequentially on the one session** (no `asyncio.gather`: one asyncpg connection), users, orgs, app summaries, member counts, audit counts (`since = now - SIGN_IN_WINDOW`), sign-ups (`now`, 12, tz). Zero-fill status keys from `UserStatus`, `OrganizationStatus`, `AppStatus`. Returns a frozen dataclass view; raises nothing (no `AppError` path). The document is "approximate, not a snapshot" (no REPEATABLE READ), stated in the OpenAPI description.
7. **Router**: `APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(current_admin)])`; `@router.get("/dashboard", response_model=DashboardStats, summary=…, description=<§5.9 caveats + window/zone/partial-month notes>, **require_permission("admin:stats:read"))`; inject `response: Response`, set `Cache-Control: no-store` (as `me_router.py`). No `AuditContextDep` (read-only, R4 n/a). Mount in `app/api/router.py`. Admin CORS follows the BEARER map automatically (`BearerRouteMatcher`), no change.
8. **Docs**:
   - `CLAUDE.md` R9: append "; `GET /api/v1/stats/dashboard` (a single document, not `Page[T]`; D26)". R3 is unchanged: this is an ordinary class-1 Bearer route with a permission, not self-scoped.
   - `docs/identity-architecture.md` §4 row: "| Admin: `GET /api/v1/stats/dashboard` | Bearer `aud=admin` + `require_permission` (`admin:stats:read`, `is_admin`, granted to no role; `admin:*` covers it) | dashboard counters (single document, `Cache-Control: no-store`, no audit row) | ADM-015 |". §8 after D25: "| D26 | **Proposed in ADM-015, accepted when the owner merges it (answers OQ4–OQ6, 2026-11-17):** R9 exception — `GET /api/v1/stats/dashboard` is a single, bounded document (fixed counters, 12 months, one entry per app), not `Page[T]`. Permission `admin:stats:read` (`is_admin`), counts only, no audit rows exposed. Members = OQ5 definition, one shared repository function (`RbacRepository.member_counts`) also used by ADM-016. |"
   - `docs/spec-v1.md`: tick OQ4/OQ5/OQ6 with "— **answered (owner, 2026-11-17):** …" text copied from the ticket notes; update §5.9 as listed in Files; add the ADM-015 note.
   - `docs/deploy.md`: add `'admin:stats:read'` to query 2a.
9. Run `make check` (ruff, format, mypy --strict, pytest) and `alembic check`.

Rules: R1 (router → service → repositories; no ORM in router; no `HTTPException`), R2 (data migration with downgrade; no model change), R3 (class 1 Bearer + one permission, in the BEARER map), R4 (read-only: no audit, tested), R5 (no new env keys; the time zone and month count are owner product rules, kept as named constants in the service), R6 (errors come from the existing guards), R7 (no ADM-016 field), R8 (tests above), R9 (exception recorded as D26), R10/R11 n/a (no credentials touched).

## Specialist input used
- **api-platform-engineer**: adopted 2 (the response shape: zero-filled status keys, `generated_at`, window start/end, `YYYY-MM` months, time zone in the document), 3 (`is_admin = true`, ADM-006 migration pattern), 4 (aggregate counts with only `admin:stats:read`, documented), 5 (`no-store` via `Response`), 6 (audit-count test before/after), 7 (one shared member function), 8 (tick OQ4–OQ6 in this ticket), 9 (test list). **Path: rejected `/api/v1/stats`; chose `/api/v1/stats/dashboard`.** Reason: the document is shaped for one screen (tiles), so the path should name it. That keeps `/stats` free and the R9 exception narrow (one route) rather than a generic `/stats` document that would need reshaping if a second one is ever added. I did not adopt the "compare with ADM-016 `member_count`" test now: ADM-016 does not exist yet. The equivalent is that both call the same function, which is tested directly.
- **database-optimizer**: adopted 1 (one grouped audit query, in the audit repository, `>=` inclusive boundary, enum values), 2 (one users query with `FILTER`), 3, 4 (the `AT TIME ZONE` bucketing with a SQL-computed, sargable window and zero-fill. The plan passes `now` in as a parameter fetched from the DB clock, which gives deterministic tests as the consult suggested), 5 (`member_counts(app_ids)` in `RbacRepository`, returning a dict, with absent apps as 0 at the caller), 6 (sequential queries, no gather, "approximate, not a snapshot"), 7 (seeding notes). **Where the consults differ:**
  - Signature: the API consult proposed `count_members_by_app(app_ids=None)` and the DB consult `member_counts(app_ids) -> dict`. I chose the DB consult's `member_counts(app_ids: Sequence[UUID] | None = None) -> dict[UUID, int]`. Its batched-ids shape is exactly what ADM-016's list page needs (no N+1).
  - Apps by status: the plan derives it from the app list query (DB consult 5/6) instead of a separate `GROUP BY`, which saves a query.
  - "Indexed queries" AC: both consults agree that a literal reading cannot be met on users/orgs without useless indexes. I took the API consult's reading ("no audit_log scan outside the indexed range") and made it more precise with the sargable sign-up range and the no-N+1 rule (AC 6 above).
  - The DB consult missed the `backdate_audit` trigger: `occurred_at` is stamped to `now()` on insert, so backdated test rows need the marker. Added to the test notes.
- Neither consult listed `tests/test_rbac_schema.py` `ADMIN_LEAF_PERMISSIONS` or `docs/deploy.md` query 2a. Both are added, following ADM-006.

## Assumptions
- "Unverified" counts users of every status with `email_verified_at IS NULL` (spec §5.9 has no status filter).
- Sign-ups count every user row (active and disabled), per spec "`users.created_at`".
- Members ignore org status: a member through a suspended org's assignment still counts (OQ5 does not mention org status). ADM-016 inherits the same function.
- `apps.by_status` and `members_per_app` include the `admin` app (OQ5: "the `admin` app shows its count").
- `members_per_app` entries carry `app_key`, `name` and `member_count`. The name saves the SPA a second call, and the field is additive.
- 24 h window: `[now − 24 h, ∞)` with `now` = DB transaction time, inclusive at the start.
- The production Postgres 17 image has tzdata, so `Asia/Ho_Chi_Minh` resolves. Viet Nam has no DST.
- Time zone and month count are product rules (OQ6), not deployment config. They are named constants, not env keys (R5 covers config/secrets).
- Unknown future status values: the schema has fixed keys today. New statuses would be added as fields (additive). The OpenAPI description tells clients to ignore unknown keys.
- No caching, ETag or rate limit beyond the existing Bearer guard. At the expected admin load, p95 < 200 ms holds (small tables; the audit count is index-bounded).

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **Path**: `GET /api/v1/stats/dashboard` (default) vs `GET /api/v1/stats` / `/api/v1/dashboard-stats`. Changing it later inside v1 would be a breaking change.
2. **`admin:stats:read` is `is_admin = true`** (default, same as every other admin key): only an `admin:*` holder can put it into a role. The OQ4 text in the spec floated "non-`is_admin`". Note that the key is granted to no role, so only super-admins see the dashboard until a super-admin adds it to a staff role (tell the admin-pan side).
3. **12 entries including the current, partial month** (default; documented as partial) vs the 12 complete months before it.
4. **All apps in `members_per_app`**, including disabled apps, apps with 0 members and `admin` (default).
5. **Wording of the "indexed queries" AC**, as in AC 6 above (no table indexes added; the audit count is index-backed and window-bounded; no N+1).
6. **Recording the R9 exception** in `CLAUDE.md` R9 and as D26 in `docs/identity-architecture.md` (same treatment as D18/D25). Merging the PR accepts both.
7. Members through suspended orgs still count (see Assumptions). If not, the shared function gains an org-status join before ADM-016.

## Blocking questions
None.

## Split proposal
None. This is one spec feature (§5.9 Dashboard stats). Production code is about 250 lines (module, three repository additions, a one-key migration); tests and docs make up the rest. ADM-016 (`member_count` on the apps API) stays a separate ticket and reuses `RbacRepository.member_counts`.

## Data model / migration
No schema change and no new index (both consults: `ix_audit_log_action (action, occurred_at, id)` already serves the 24 h count; the other tables are small).

Data-only migration `migrations/versions/20261117_<rev>_adm015_stats_permission.py` (`revision` = new 12-hex id, `down_revision = "c8a1f4e72d95"`):
- `upgrade()`: `INSERT INTO permissions (id, app_id, key, description, is_admin) VALUES (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 'admin:stats:read', 'Read dashboard statistics (counts only)', true) ON CONFLICT (app_id, key) DO NOTHING`. Granted to no role.
- `downgrade()`: `DELETE FROM permissions WHERE app_id = '…a1' AND key IN ('admin:stats:read')`. Its `role_permissions` rows cascade, which is lossy for roles a super-admin added it to; say so in the docstring.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/stats/dashboard` | none (no path, query or body) | `DashboardStats` (below), header `Cache-Control: no-store` | 200, 401 `unauthorized`, 403 `permission_denied` | `admin:stats:read` (Bearer `aud=admin`; `admin:*` covers it) |

`DashboardStats` (snake_case; timestamps ISO 8601 UTC; fields additive-only from here on):
```json
{
  "generated_at": "2026-11-17T09:00:00Z",
  "users": {"total": 0, "by_status": {"active": 0, "disabled": 0}, "unverified": 0},
  "organizations": {"total": 0, "by_status": {"active": 0, "suspended": 0}},
  "apps": {"total": 0, "by_status": {"active": 0, "disabled": 0}},
  "sign_ins_24h": {"window_start": "2026-11-16T09:00:00Z", "window_end": "2026-11-17T09:00:00Z",
                    "succeeded": 0, "failed": 0},
  "sign_ups": {"time_zone": "Asia/Ho_Chi_Minh",
               "by_month": [{"month": "2025-12", "count": 0}, "… 12 entries, oldest first, last = current (partial) month"]},
  "members_per_app": [{"app_key": "admin", "name": "Admin", "member_count": 0}]
}
```
`generated_at` = `window_end` = DB transaction time. The OpenAPI description states: failed sign-ins include unknown emails but not 429/Turnstile refusals; sign-ups include CLI-created accounts and do not depend on audit retention; the last month is partial; members = OQ5 definition; the counts are approximate (not one snapshot); clients ignore unknown keys.
