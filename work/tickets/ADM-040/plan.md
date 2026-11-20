Status: READY

> The plan below is complete, assuming every default in **Blocking questions** is accepted. If Ming answers "defaults" (or
> "đồng ý mặc định"), it becomes `READY` with no other change. Any other answer changes only the rows named in that question.
> The plan gate applies anyway: `migrations/**` and `app/db/models.py` are High paths.

# Plan: ADM-040: Per-user usage limit overrides (staff)

## Scope summary
- `docs/spec-v1.md:278` (OQ62): "**Grant shape (OQ62).** 'Grant extra' raises one user's per-period limit for one
  metric, optionally **until a date** (then the app default applies again). A user's limit may also be set below the
  default, including 0 (blocks that metric). One-off credit packs are later (§5.7)."
- `docs/spec-v1.md:283` (OQ67): "A reason (1–500 chars) is required on every staff and app-admin adjustment and stored in
  the audit entry (R4). No user notification in v1."
- `docs/spec-v1.md:299` (data table): "User grant | per user + app + metric | raised (or lowered) per-period limit,
  optionally until a date, set by staff or an app admin (OQ62)".
- `docs/adr/0024-usage-metrics.md:26-27` (point 4): "a default-limit change … is read live and never copied into
  per-user rows (ADM-040 stores overrides only)".
- `docs/adr/0024-usage-metrics.md:31-33` (point 5): reason "stored as a flat string `changes.reason` next to the
  `{from, to}` diffs of the audit entry, never returned by the API and never logged". Point 6: "A PATCH that changes
  nothing writes nothing." This plan applies the same rule to an unchanged PUT.
- `docs/adr/0024-usage-metrics.md` point 8: `admin:usage:read` / `admin:usage:write` already exist (seeded by ADM-039
  migration `8c2f6a1d4e93`). This ticket adds no new permission.
- Ticket scope: "Override table (user, metric, limit, optional `expires_at`, required reason, created_by) + migration;
  effective limit = unexpired override else metric default. Bearer routes (`admin:usage:write`) to set/remove an
  override for a user and metric; audited with the reason (no PII beyond ids)."
  - **Conflict flagged (spec/ADR wins):** the ticket lists "required reason" as a column of the table. ADR-0024 §5
    puts the reason in the audit entry only, so the table has **no reason column**. The reason is still required on
    every write (422 without it).
- `work/decisions.md`: no usage entries.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | Set an override | `tests/test_usage_overrides_api.py::test_put_creates_201_etag`: 201, body `limit`/`expires_at`/`active: true`, `ETag` header, one row in the DB |
| 1b | Replace an override | `::test_put_replaces_200_no_duplicate`: the second PUT returns 200 and the table still has one row for (user, metric), with the new values; `created_at`/`created_by` unchanged, `updated_at` bumped |
| 1c | Remove an override | `::test_delete_204_then_404`: DELETE with `{reason}` returns 204, the row is gone, and a second DELETE returns 404 |
| 1d | Expired overrides fall back to the default (DB clock) | `tests/test_usage_overrides_db.py::test_effective_limit_*` (four cases: none, active, expired, permanent; rows inserted directly with `expires_at = now() - interval`, no clock mocking) + `test_usage_overrides_api.py::test_get_expired_override_is_inactive` (`active: false`) |
| 1e | Limit below default incl. 0 (OQ62) | `::test_put_zero_and_below_default_allowed` + `test_effective_limit_zero_override` |
| 2a | Reason required (422 without) | `::test_put_422_cases` (missing / blank / 501 chars / bidi char) and `::test_delete_without_reason_422` (no body, `{}`, blank) |
| 2b | Audit rows | `::test_put_set_audit_row_with_reason`, `::test_put_replace_audit_row`, `::test_delete_audit_row`, `::test_noop_put_writes_nothing`; `tests/test_audit_coverage.py` parametrized cases for PUT and DELETE (success one row, failure no row, no email/secret) |
| 2c | 401/403/404 | `::test_routes_401_without_token`, `::test_writes_403_with_read_only`, `::test_reads_403_without_read`, `::test_404_matrix`; `tests/test_route_auth_classes.py` registry entries |
| 2d | Reason never returned, never logged (ADR-0024 §5) | `::test_reason_not_in_response_or_logs` (caplog DEBUG, sentinel string) |
| 2e | Past or naive `expires_at` refused | `::test_put_expires_at_past_or_naive_422` |
| 3 | Migration reversible, no drift | `tests/test_usage_overrides_migration.py::test_downgrade_removes_table`; `uv run alembic check` clean; `make check` green |

## Ming verification after merge
None. Deploy runs the migration as usual (`docs/deploy.md`). Nothing needs a real device or hosted service.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/usage/models.py` | add `UsageLimitOverride` model (after `UsageMetric`, line 65) | table | no |
| `app/db/models.py` | import `UsageLimitOverride` (line 16) | R2 models registry | **yes** |
| `migrations/versions/20261120_<rev>_adm040_usage_limit_overrides.py` | new, `create_table` + downgrade | R2 | **yes** |
| `app/modules/usage/schemas.py` | add `UsageLimitOverrideSet`, `UsageLimitOverrideRemove`, `UsageLimitOverrideRead` (reuse `Reason`, `Limit`, lines 39-52) | API contract | no |
| `app/modules/usage/repository.py` | add `UsageLimitOverrideView`, `UsageLimitOverrideRepository` incl. `effective_limit()` and `db_now()` | R1 data access, ADM-042 reuse | no |
| `app/modules/usage/service.py` | move `_app` (lines 39-45) to module-level `resolve_app(rbac, app_key)` (behaviour unchanged, used by both services); add `UsageLimitOverrideService` | R1 business rules | no |
| `app/modules/usage/router.py` | 4 routes under the existing router (`/{metric_id}/overrides…`) | endpoints | no |
| `app/modules/audit/actions.py` | `AuditTarget.USAGE_LIMIT_OVERRIDE`; actions `USAGE_LIMIT_OVERRIDE_SET/UPDATED/REMOVED` (after line 95) | R4 | no |
| `app/modules/audit/diff.py` | `USAGE_LIMIT_OVERRIDE = ("user_id", "metric_id", "limit_value", "expires_at")` (after line 27) | allowlisted diff | no |
| `app/modules/usage/periods.py` | lines 20, 22, 28, 30: `is` → `==` (ADM-039 review nit, `work/tickets/ADM-039/review.md:49`) | known follow-up the orchestrator asked for. 4 lines, same module | no |
| `tests/test_usage_overrides_api.py` | new | R8 | no |
| `tests/test_usage_overrides_db.py` | new: constraints, FKs, `effective_limit` | DB clock AC, R2 | no |
| `tests/test_usage_overrides_migration.py` | new: downgrade/upgrade | R2 | no |
| `tests/test_usage_schemas.py` | add override schema cases | pure validation | no |
| `tests/test_usage_periods.py` | add plain-string period case | periods fix regression | no |
| `tests/test_route_auth_classes.py` | 4 entries after line 143 | R3 registry | no |
| `tests/test_audit_coverage.py` | fixtures after line 310; `AUDITED` entries after line 581; `_SUCCESS_STATUS_BY_ROUTE` PUT → 201; `len(AUDITED) == 34` (line 614) | R4 coverage | no |
| `docs/identity-architecture.md` | one endpoint row after line 162 | API catalogue | no |

## Data model / migration
Table **`usage_limit_overrides`**: one row per (user, metric). The current override is the row. History lives in the
audit log (`docs/adr/0024` §5-6), so there are no history rows.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | no | app `uuid4` | `UUIDPkMixin`, `pk_usage_limit_overrides` |
| `user_id` | uuid | no | | FK `users.id` **ON DELETE CASCADE**, name `fk_usage_limit_overrides_user_id_users` (personal data goes with the user, as `role_assignments.user_id`, `app/modules/rbac/models.py:211-213`) |
| `metric_id` | uuid | no | | FK `usage_metrics.id` **ON DELETE RESTRICT**, name `fk_usage_limit_overrides_metric_id_usage_metrics` (metrics are never deleted, ADR-0024 §1) |
| `limit_value` | bigint | no | | `limit` is a SQL reserved word. The API field is `limit` |
| `expires_at` | timestamptz | yes | NULL | NULL = permanent |
| `created_by` | uuid | yes | | FK `users.id` **ON DELETE SET NULL**, name `fk_usage_limit_overrides_created_by_users`. Holds the first setter and is not changed on replace. NULL for a future `SystemActor` (ADM-044) |
| `created_at` | timestamptz | no | `now()` | `TimestampMixin` |
| `updated_at` | timestamptz | no | `now()` | bumped by `bumped_updated_at()` (`app/db/base.py:17-23`) on replace; ETag source |

Constraints (model and migration texts identical, as `app/modules/usage/models.py:3` requires):
- `UniqueConstraint("user_id", "metric_id", name="uq_usage_limit_overrides_user_id_metric_id")`. It is the upsert
  target and also the lookup index for `effective_limit` (user first).
- `CheckConstraint(f"limit_value BETWEEN 0 AND {MAX_LIMIT}", name="limit_value_range")`, which becomes
  `ck_usage_limit_overrides_limit_value_range` with text `limit_value BETWEEN 0 AND 9007199254740991`.
- `Index("ix_usage_limit_overrides_metric_id", "metric_id")`. Covers the per-metric list and the RESTRICT check.
- **No** `CHECK (expires_at > created_at)` (Q7). **No** composite FK `(metric_id, app_id)` and no `app_id` column (Q8).

Migration: `revision` new 12-hex id; `down_revision = "d7a3f5b2e801"` (re-pointed at build time, see Assumptions).
`upgrade()`: one `op.create_table` with every named constraint above, then `op.create_index`. `downgrade()`:
`op.drop_index` then `op.drop_table` (this deletes the overrides, so the downgrade is lossy; say so in the docstring).
It is a new empty table with no backfill, so there is no lock risk. The ADM-039 migration test downgrades through this
revision to `5b7d1e9c3a42`, so it also exercises this downgrade.

## API contract
All routes: R3 class 1 (Bearer `aud=admin`, `current_admin` on the router, `app/modules/usage/router.py:19-21`).
`{key}` uses `APP_KEY_RE`. `metric_id` and `user_id` are UUIDs. 404 covers: unknown app, the `admin` app, a metric
not of the app, an unknown user, and (GET single / DELETE) no override. One shared 404 description is used, like
`_METRIC_404`. Disabled apps are allowed (ADR-0024 Consequences, "config is inert"). Archived metrics are allowed (Q3).
App membership is not required (Q4).

| Method | Path (`/api/v1` +) | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/apps/{key}/usage-metrics/{metric_id}/overrides` | `limit`, `offset` (`PageDep`) | `Page[UsageLimitOverrideRead]`, order `created_at, id` | 200, 401, 403, 404, 422 | `admin:usage:read` (Q1) |
| GET | `/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}` | | `UsageLimitOverrideRead` + `ETag` | 200, 401, 403, 404 | `admin:usage:read` (Q1) |
| PUT | `/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}` | `UsageLimitOverrideSet` | `UsageLimitOverrideRead` + `ETag` | **201** created, **200** replaced or unchanged, 401, 403, 404, 422 | `admin:usage:write` |
| DELETE | `/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}` | JSON body `UsageLimitOverrideRemove` | empty | 204, 401, 403, 404 (no override), 422 (no/blank reason) | `admin:usage:write` |

Schemas (`extra="forbid"` on request models):
- `UsageLimitOverrideSet`: `limit: Limit` (strict int, 0..2^53-1), `expires_at: AwareDatetime | None = None` (a naive
  value gives 422), `reason: Reason`.
- `UsageLimitOverrideRemove`: `reason: Reason`.
- `UsageLimitOverrideRead`: `app_key: str`, `metric_id: UUID`, `user_id: UUID`, `limit: int`,
  `expires_at: datetime | None`, `active: bool` (`expires_at IS NULL OR expires_at > now()`, computed in SQL),
  `created_by: UUID | None`, `created_at`, `updated_at`. **No `reason`.**
- Service error for a past `expires_at`: `UnprocessableError("expires_at must be in the future",
  details=[{"field": "expires_at", "reason": "not_in_future"}])`. The value is compared with the DB `now()`, not the
  app clock (Q5). No value is echoed in the error.
- PUT route: decorator `status_code=200`, `response.status_code = 201` when the service reports `created`. Document
  both 200 and 201 with the `ETag` header through `_with_etag` (`router.py:31-36`). No `If-Match` (Q6).

Audit (R4, same transaction, `app_key = app.key`, `org_id = None`, target `(usage_limit_override, override.id)`):
- `usage_limit_override.set` (insert): `diff(None, snapshot)` + `{"reason": …}`.
- `usage_limit_override.updated` (replace with a change): `diff(before, after)` (only the changed `limit_value` /
  `expires_at`; the target id identifies the row) + `reason`.
- `usage_limit_override.removed`: `diff(snapshot, None)` (all four fields from→null, so the user and metric ids stay
  in the trail after the row is gone) + `reason`.
- An unchanged PUT (same limit and same `expires_at`) writes nothing, returns 200 and leaves `updated_at` as it was.
- Log lines: `usage_override_{set,updated,removed} app=%s metric=%s user=%s actor=%s` (ids/keys only, never the reason).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_usage_periods.py › test_plain_string_period_matches_enum` (parametrized day/week/month; `cast(UsagePeriod, "week")`) | periods `==` fix: plain stored string gives the same bounds as the enum |
| `test_usage_schemas.py › test_override_set_valid` / `test_override_set_422_cases` (bool/float/str limit, -1, 2^53, naive `expires_at`, extra field, missing/blank reason) / `test_override_remove_requires_reason` | pure validation |
| `test_usage_overrides_db.py › test_limit_value_check` (−1 and 2^53 rejected, 0 and MAX ok) | CHECK |
| `› test_unique_user_metric` | one row per (user, metric) |
| `› test_user_delete_cascades`, `› test_creator_delete_sets_null`, `› test_metric_delete_restricted` | FKs |
| `› test_effective_limit_default_without_override`, `› test_effective_limit_active_override`, `› test_effective_limit_expired_override_falls_back` (row inserted with `expires_at = now() - interval '1 minute'`), `› test_effective_limit_permanent_override`, `› test_effective_limit_zero_override`, `› test_effective_limit_unknown_metric_none` | AC 1d/1e, DB clock, ADM-042 function |
| `test_usage_overrides_migration.py › test_downgrade_removes_table` (downgrade to `PREVIOUS` = this migration's `down_revision`, then upgrade head; `finally` upgrade head) | R2 |
| `test_usage_overrides_api.py › test_routes_401_without_token` | all 4 routes |
| `› test_reads_403_without_read`, `› test_writes_403_with_read_only`, `› test_admin_wildcard_allows_all` | 403 / permission map |
| `› test_put_creates_201_etag` | AC 1a |
| `› test_put_replaces_200_no_duplicate` | AC 1b |
| `› test_put_zero_and_below_default_allowed` | OQ62 |
| `› test_put_permanent_then_dated` (null → date → null) | `expires_at` optional |
| `› test_put_422_cases` (missing/blank/long reason, negative/too large/non-int limit, extra field) | AC 2a |
| `› test_put_expires_at_past_or_naive_422` (past by 1 min → `not_in_future`, naive → 422) | Q5 |
| `› test_put_archived_metric_allowed`, `› test_put_disabled_app_allowed`, `› test_put_user_not_member_allowed` | Q3, Q4, ADR Consequences |
| `› test_404_matrix` (unknown app, `admin` app, metric of other app, unknown metric, unknown user; on GET list, GET one, PUT, DELETE) | AC 2c |
| `› test_get_single_and_list` (pagination bounds `limit=101` → 422, order, `total`) | reads |
| `› test_get_single_no_override_404` | reads |
| `› test_get_expired_override_is_inactive` (row inserted directly with past `expires_at`; `active: false`; still listed) | AC 1d |
| `› test_delete_204_then_404`, `› test_delete_expired_override_allowed` | AC 1c |
| `› test_delete_without_reason_422` (no body, `{}`, blank) | AC 2a |
| `› test_put_set_audit_row_with_reason`, `› test_put_replace_audit_row` (only changed fields + reason), `› test_delete_audit_row` (all fields to null + reason) | AC 2b |
| `› test_noop_put_writes_nothing` (200, same `ETag`, no audit row) | ADR-0024 §6 rule |
| `› test_reason_not_in_response_or_logs` (caplog DEBUG, sentinel reason, PUT and DELETE) | ADR-0024 §5, R5/R11 |
| `› test_openapi_put_declares_200_201_etag_and_delete_body` | contract |
| `test_route_auth_classes.py` registry: 4 entries (GET×2 read, PUT/DELETE write) | R3 |
| `test_audit_coverage.py` `AUDITED`: PUT (`{set, updated}`, success fixture = new override → 201, failure = unknown metric → 404), DELETE (`{removed}`, success = existing override with `{"reason": "cleanup"}`, failure = no override → 404); count 34 | R4 |

API tests that PUT must pass `admin_user=<real user>` to `make_client`: `created_by` is an FK, and `conftest.py:158`
otherwise uses a random id.

## Approach
1. `periods.py`: replace `period is UsagePeriod.X` with `==` (lines 20, 22, 28, 30). `UsagePeriod` is a `StrEnum`, so
   a stored plain string then matches. Add the test.
2. Model `UsageLimitOverride(UUIDPkMixin, TimestampMixin, Base)` in `usage/models.py`, with the constraints above.
   Register it in `app/db/models.py`. Run `make migration m="adm040 usage limit overrides"`, then hand-edit: named
   constraints, CHECK text, downgrade, and a docstring in the style of `20261120_8c2f6a1d4e93_adm039_usage_metrics.py:1-14`.
   `uv run alembic check` must be clean.
3. Audit catalogue: add the target and 3 actions, and the `diff.USAGE_LIMIT_OVERRIDE` allowlist.
4. Schemas (step "Pure logic first"): `UsageLimitOverrideSet`/`Remove`/`Read` reuse `Reason`/`Limit`.
5. Repository `UsageLimitOverrideRepository(session)` (SQLAlchemy only, R1):
   - `db_now() -> datetime` (`SELECT now()`).
   - `page(metric_id, limit, offset)` and `get_view(user_id, metric_id)`. Both select the row plus
     `(expires_at IS NULL OR expires_at > func.now()).label("active")` and map to the `UsageLimitOverrideView`
     dataclass (plain values, router never sees ORM).
   - `get_for_update(user_id, metric_id)` (`FOR UPDATE`, `populate_existing`).
   - `insert(...) -> UsageLimitOverride | None`: `pg_insert(...).on_conflict_do_nothing(constraint=uq…)
     .returning(...)`. It returns None when a concurrent insert won.
   - `apply(id, values)`: `UPDATE … updated_at = bumped_updated_at(...)`.
   - `delete(user_id, metric_id)`: `DELETE … RETURNING` the full row, which gives the old values atomically for audit.
   - **`effective_limit(user_id, metric_id) -> int | None`**: `SELECT COALESCE(o.limit_value, m.default_limit) FROM
     usage_metrics m LEFT JOIN usage_limit_overrides o ON o.metric_id = m.id AND o.user_id = :u AND (o.expires_at IS
     NULL OR o.expires_at > now()) WHERE m.id = :m`. The expiry test goes in the `ON` clause, uses the DB clock, and
     ignores metric status. The docstring states that ADM-042 calls it inside its consume transaction and owns the
     archived-metric refusal (ADR-0024 §1). Not exposed over HTTP here (ADM-043 owns staff "current limit" reads).
6. Service `UsageLimitOverrideService(session)`. It is actor-generic like `UsageMetricService` (takes `Actor`, never
   checks permissions, ADR-0024 §7, so ADM-044 reuses it with an app-admin actor). `created_by = actor_user_id(actor)`.
   - Shared `_resolve(app_key, metric_id, user_id)`: `resolve_app` (404 for `admin`/unknown), then
     `UsageMetricRepository.get(app.id, metric_id)` (404), then `UserRepository.get(user_id)` (404). The metric status
     is not checked.
   - `set(...) -> tuple[UsageLimitOverrideView, bool]`: resolve. Then, if `expires_at` is set and `<= db_now()`,
     raise 422. Then `get_for_update`:
     - None: `insert`. If that returns None (race), `get_for_update` again and continue as a replace. Otherwise audit
       `.set` and return `created=True`.
     - Existing: compare `(limit_value, expires_at)`. If equal, return without a write or audit. Otherwise `apply`,
       audit `.updated` with the diff and reason.
     - In all cases, re-read with `get_view`.
   - `remove(...)`: resolve, `delete` (None gives 404 "Usage limit override not found"), audit `.removed`.
   - `list(...)` / `get(...)`: resolve (for `get`, the user check), then repository reads.
   - Logging: ids only (R5/R11).
7. Router: 4 routes in `usage/router.py` using the existing `READ`/`WRITE`, `_with_etag`, and a shared `_OVERRIDE_404`
   text. The DELETE takes `body: UsageLimitOverrideRemove` and returns `Response(status_code=204)` (as
   `releases/router.py:120-124`). Description strings state: "replaces the default for this user (not added to it);
   expired overrides fall back to the metric default; the reason is stored in the audit entry only".
8. Registries (`test_route_auth_classes.py`, `test_audit_coverage.py`), the docs row, the tests, then `make check` and
   `uv run alembic check`.

Rules: R1 (router → service → repository; service raises `NotFoundError`/`UnprocessableError` only). R2 (migration +
downgrade + registry + `alembic check`). R3 class 1 with declared permissions. R4 (audit in the request transaction, no
row on no-op or failure). R5/R11 (reason never logged or returned). R6 (envelope only; codes 201/200/204/404/422).
R7 (only the periods nit beyond the ticket, as instructed). R8 (tests above). R9 (plural `overrides`, `Page[T]`, UUIDs,
timestamptz; apps by `key`, the existing exception).

## Specialist input used
- database-optimizer:
  - **Adopted:** one row per (user, metric) with unique key + upsert; `limit_value`; hard DELETE; FKs CASCADE / RESTRICT /
    SET NULL; `ix_…_metric_id`; CHECK on range; no `expires_at > created_at` CHECK (rec. 3b); `effective_limit` with
    the expiry in the `ON` clause and `func.now()`; `DELETE … RETURNING` for audit; lock before diff; tests insert
    past rows instead of mocking the clock; no reason column.
  - **Changed:** concurrency uses `get_for_update` + `INSERT … ON CONFLICT DO NOTHING` + re-lock instead of
    `ON CONFLICT DO UPDATE … xmax = 0`. This gives an exact "from" for audit and detects no-ops before writing.
    `created_by` is not overwritten on replace (the audit log keeps who replaced).
  - **Rejected:** 409 on archived metric (open question 2; see Q3).
- api-platform-engineer:
  - **Adopted:** PUT/DELETE on `/usage-metrics/{metric_id}/overrides/{user_id}`; 201/200; DELETE 404 when absent;
    DELETE JSON body `{reason}` (not a query param); list + single reads with `active` flag; aware and future
    `expires_at` vs DB clock; user must exist, membership not required; archived metric allowed; audit action
    triple; no-op PUT writes nothing; registry updates.
  - **Rejected by default:** composite FK `(metric_id, app_id)`. `usage_metrics.app_id` is immutable and the metric is
    already resolved via `(app_id, metric_id)`, so the extra column proves nothing new (Q8). Also optional `If-Match`:
    ADR-0024 §8 requires it only on metric PATCH; a PUT here is a full idempotent replacement, races are serialised by
    the row lock and both writes are audited, and `PreconditionFailedError` (`app/core/errors.py:161`) needs a current
    `updated_at`, which a missing row has none of (Q6). ETags are still emitted so `If-Match` can be added later
    without a breaking change.

## Assumptions
- **Migration chain:** ADM-034 (same batch) also adds a migration on `d7a3f5b2e801` and merges first. At build time
  the implementer sets this migration's `down_revision` (and `PREVIOUS` in the migration test) to ADM-034's revision,
  so there is one head. `alembic heads` must show one head.
- **Hotspots** (ADM-034 likely edits them too; expect a rebase): `app/db/models.py`, `app/modules/audit/actions.py`,
  `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py` (`AUDITED` count line 614 becomes ADM-034's count
  + 2), `docs/identity-architecture.md`.
- No new ADR or D-number. OQ62/OQ67 and ADR-0024 §4-6 already decide the behaviour. The route-level choices below
  (archived metric, membership, past expiry) are recorded in OpenAPI descriptions and the identity-architecture row.
  If Ming changes Q3 or Q4 toward a rule ADM-042/044 must follow, add it as a point in ADR-0024.
- `created_by` holds the first setter. `created_at` is the first set. The audit log is the full history.
- Expired rows are never deleted on read. They are listed with `active: false` and are removable. A purge job is a
  follow-up.
- Disabled users may receive overrides. Overrides are inert until they can use the app.
- `admin:usage:write` description in the ADM-039 seed ("Create usage metrics and change…") is not updated. That is a
  cosmetic follow-up, because editing it would mean a data migration.
- Follow-ups (not in this diff): admin-pan override screen; index on `created_by` if user deletes get slow;
  `usage_limit_override` expired-row purge; reverse read `GET /users/{user_id}/usage-overrides` (if wanted, ADM-043).

## Blocking questions
Defaults are what this plan implements.
1. **Reads in scope?** Default: **yes**, `GET …/overrides` (`Page[T]`) and `GET …/overrides/{user_id}`, with
   `admin:usage:read`. The ticket lists only writes, but staff must see what they set. Saying "no" removes 2 routes and
   about 80 lines. AC 1d is then verified through `effective_limit` only.
2. **Route shape and codes?** Default: `PUT /api/v1/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}` (201
   created / 200 replaced or unchanged). `DELETE` on the same path with JSON body `{reason}` → 204, 404 when no
   override exists. The alternative to a DELETE body is `POST …/overrides/{user_id}/remove`.
3. **Archived metric?** Default: **allow** PUT and DELETE (archive is reversible, and ADM-042 refuses consume on
   archived metrics anyway). The alternative is 409 `usage_metric_archived` on PUT, with DELETE still allowed.
4. **App membership required?** Default: **no**, the user only has to exist (404 otherwise). Staff can pre-stage a
   limit before the user joins.
5. **Past `expires_at`?** Default: **422** (`details[0] = {field: expires_at, reason: not_in_future}`), compared with
   DB `now()`. Naive datetimes give 422.
6. **`If-Match`?** Default: **not supported** in ADM-040 (last writer wins, both audited). ETag is still returned.
   Choosing "yes" adds optional `If-Match` on PUT and DELETE, and 412 on a missing row via a usage-local 412 error,
   about 40 lines.
7. **`CHECK (expires_at > created_at)`?** Default: **no CHECK**. The service checks against DB `now()` instead,
   because `created_at` stays at the first set on replace and the CHECK would be wrong.
8. **Composite FK `(metric_id, app_id)`?** Default: **no**, `metric_id` FK only, no `app_id` column.
9. **Column name?** Default: `limit_value` in the DB, `limit` in the API.
10. **`effective_limit(user_id, metric_id)` repository function for ADM-042?** Default: **yes**, included and tested
    here, not exposed over HTTP.

## Split proposal
None. Estimate: about 430 product lines (models 35, migration 75, schemas 35, repository 120, service 120, router 95,
audit/diff/periods/docs 15), plus about 450 test lines. It fits one cycle. With Q1 = "no", it drops by about 80 lines.
