Status: READY

# Plan — ADM-033: Release log — data model and staff read (list, detail, live version, delete)

Scope reference: `docs/spec-v1.md` §5.13 and the release-log ADR (`docs/adr/0018-release-log-and-deployer-credential.md`,
to be renumbered 0021 by a separate solo change that Ming approves; its D-log row becomes D30). In this plan it is
called "the release-log ADR". This ticket adds no ADR. It does not edit `CLAUDE.md`: there are no machine routes here,
so R3 stays as it is, and the default live-read shape needs no R9 exception (see Q1).

## Scope summary
Spec §5.13, decided by the owner on 2026-11-19:
- *"Data model `app_releases`: app (FK, addressed by key), `environment` (`production` | `staging`), `version`, `status` (`pending` | `deployed` | `failed`), `git_sha`, `images` `[{name, digest "sha256:…"}]`, `deployment_pr` (URL on `github.com/mmlabs-online/deployment/pull/N`), `changes` `[{pr_url, title, ticket_id, risk}]` (bounded: ≤ 200 items, …), `has_migrations`, `created_at`, `updated_at`, `deployed_at`, `deploy_run_url`. **Unique (app, environment, version).** **No commit author emails or other PII.**"*
- *"Readers/managers: `admin:*` only (Bearer, `aud=admin`). **No new leaf permission** (no `admin:releases:*` …)."*
- *"Staff reads: list across all apps and per app (`Page[T]`; filters app, environment, status, date range; newest first) and release detail, plus the live-version read (OQ40). Staff delete of a wrong record (audited, OQ42). Documented in OpenAPI so admin-pan generates types (as ADM-013)."*
- OQ36: *"`production`: strict `vMAJOR.MINOR.PATCH`, no leading zeros, no pre-release or build suffix … `staging`: `sha-` + exactly 7 lowercase hex … `git_sha` = 40 lowercase hex … for `staging` the version must equal `sha-` + the first 7 chars of `git_sha`."*
- OQ38: *"`git_sha`, `deployment_pr`, `has_migrations` null; `images`, `changes` empty"* for a record created from a report. These columns must therefore be nullable or allowed to be empty now.
- OQ40: *"'live now' per app + environment = the record with the latest `deployed_at` among `status = deployed` … the exact shape (list `sort=deployed_at` + a filter, or a small dedicated endpoint) is PLAN's. The redeploy overwrites `deployed_at` … naming it `last_deployed_at` instead is a naming alternative left to PLAN."*
- OQ42: *"`DELETE /api/v1/releases/{id}`, `admin:*`, 204, audited. A later retried CI write may recreate a deleted record; accepted."*
- OQ45: *"`images`: 1–10 items on the app write … `deployment_pr`: optional, exactly `https://github.com/mmlabs-online/deployment/pull/<N>`. `deploy_run_url`: … exactly `https://github.com/mmlabs-online/deployment/actions/runs/<N>` with an optional `/attempts/<N>`."*
- OQ47 and ADR §8: *"ids and status only (app key, environment, version, release id, status, change count) — never titles or URLs: … `release.deleted`"*.
- OQ49: *"Staff: `GET /api/v1/releases` (`Page[T]`, filters `app`, `environment`, `status`, `created_from`/`created_to`, `sort=created_at|deployed_at`, default `created_at` desc), `GET /api/v1/releases/{id}`, `DELETE /api/v1/releases/{id}`, plus the live-version read of OQ40 (shape PLAN's)."*
- OQ48 and ADR §11: *"org-admin-api and admin-pan both map to the `admin` app and could collide on `(app, environment, version)`; proposed resolution — give admin-pan its own app row or key — decided at PLAN of the releases ticket."* See Q3.
- The ticket also lists `created_via` as a column. The spec's data model does not name it; the value set is Q2.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Migration up/down is clean and `alembic check` reports no drift | `tests/test_releases_migration.py › test_app_releases_down_up`; `uv run alembic check` prints no new upgrade operations |
| 2 | DB CHECKs reject bad version/environment/status combinations | `tests/test_releases_schema_db.py` (every `test_rejects_*` case, plus the `test_accepts_*` boundary cases) |
| 3 | List, detail, live and delete require `admin:*`: 401 and 403 tests, `x-permission` declared, route-class map updated | `test_releases_api.py › test_routes_401_without_token`, `test_routes_403_without_wildcard`, `test_openapi_declares_admin_wildcard`; `tests/test_route_auth_classes.py` passes with the 4 new `BEARER` entries |
| 4 | Filters, paging (`limit` ≤ 100) and newest-first order are covered; detail returns 404 for an unknown id | `test_list_*` and `test_detail_*` in `test_releases_api.py` |
| 5 | Live version = latest `deployed_at` among deployed records per app + environment, with a rollback case in the seeded rows | `test_live_rollback_returns_redeployed_older_version`, `test_live_ignores_failed_and_pending`, `test_live_environments_do_not_leak`, `test_live_tie_breaks_on_id` |
| 6 | DELETE writes an audit row in the same transaction, ids only | `test_delete_writes_one_audit_row_ids_only`, `test_delete_unknown_404_writes_no_row`; `tests/test_audit_coverage.py` (the route is in `AUDITED`; count is 28) |
| 7 | OpenAPI schemas are present for admin-pan type generation | `test_openapi_release_schemas`: `ReleaseSummary`, `ReleaseDetail`, `ReleaseChange`, `ReleaseImage`, enums, nullable fields |
| 8 | `make check` is green | `make check` |

## Ming verification after merge
- After the production deploy has run `alembic upgrade head` (a separate deploy step, `docs/deploy.md`), `GET https://api.mmlabs.online/api/v1/releases` with an `admin:*` token returns `{"items": [], "total": 0, …}`, and `/api/v1/releases/live` returns an empty page.
- Otherwise none. No external service is involved.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/releases/__init__.py` | create (empty) | new domain module (Domain focus: module shape) | no |
| `app/modules/releases/models.py` | create: `AppRelease` model; StrEnums `ReleaseEnvironment`, `ReleaseStatus`, `ReleaseCreatedVia`; regex constants | table `app_releases` (R2) | no |
| `app/modules/releases/schemas.py` | create: `ChangeRisk`, `ReleaseImage`, `ReleaseChange`, `ReleaseSummary`, `ReleaseDetail`, `ReleaseSort` | OpenAPI contract for admin-pan; frozen for ADM-034/036/038 | no |
| `app/modules/releases/repository.py` | create: `ListFilter`, `ReleaseView`, `AppReleaseRepository` (`add`, `page`, `get_view`, `live_page`, `delete_returning`, `app_key`) | R1: SQLAlchemy only here; `add` is the seed path for tests (ticket) and later for ADM-034 | no |
| `app/modules/releases/service.py` | create: `ReleaseService` (`list`, `get`, `live`, `delete`) | R1 business rules; R4 audit | no |
| `app/modules/releases/router.py` | create: the 4 Bearer routes | R1 HTTP only; R3 class 1 | no |
| `migrations/versions/20261120_<rev>_adm033_app_releases.py` | create: table, CHECKs, unique, indexes; `downgrade()` | R2 | **yes** (`migrations/**`) |
| `app/db/models.py` | modify: import and export `AppRelease` | R2 (Alembic sees the model) | **yes** (`app/db/**`) |
| `app/api/router.py` | modify: `include_router(releases_router)` | mount the routes (Domain focus) | no |
| `app/modules/audit/actions.py` | modify: `AuditTarget.RELEASE = "release"`, `AuditAction.RELEASE_DELETED = "release.deleted"` | R4 / ADR §8 catalogue | no |
| `tests/release_factories.py` | create: `make_release(...)`, which seeds through `AppReleaseRepository.add` | ticket: "Tests seed rows through the repository"; reused by ADM-034/036/038 | no |
| `tests/test_releases_schema_db.py` | create | AC 2 | no |
| `tests/test_releases_migration.py` | create | AC 1 | no |
| `tests/test_releases_api.py` | create | AC 3–7 | no |
| `tests/test_route_auth_classes.py` | modify: add the 4 routes to `BEARER` (lines 64–124) as `"admin:*"` | AC 3, R3 | no |
| `tests/test_audit_coverage.py` | modify: add `("DELETE", f"{V1}/releases/{{release_id}}")` to `AUDITED` (lines 378–476) with success and unknown-id scenarios; change `len(AUDITED) == 27` (line 508) to 28 | AC 6; `test_every_mutating_bearer_route_is_mapped` fails without it | no |

Not touched: `CLAUDE.md`, `docs/**`, `app/main.py`, `app/core/**`, `app/modules/rbac/**`.

## Data model / migration
Table **`app_releases`**. The model uses `UUIDPkMixin, TimestampMixin, Base`, like `BugReport` in `app/modules/bug_reports/models.py:22`.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | no | (mixin) | PK `pk_app_releases` |
| `app_id` | uuid | no | — | FK `apps.id` **ON DELETE RESTRICT**, explicit name `fk_app_releases_app_id_apps` (as ADM-028), so history is kept |
| `environment` | varchar(10) | no | — | |
| `version` | varchar(32) | no | — | 32 leaves headroom for large semver numbers; ADM-034 must cap at ≤ 32 too |
| `status` | varchar(10) | no | server `'pending'`, ORM default `pending` | |
| `git_sha` | varchar(40) | **yes** | — | NULL for a record created from a report (OQ38) |
| `images` | jsonb | no | server `'[]'::jsonb` | `[{name, digest}]` |
| `changes` | jsonb | no | server `'[]'::jsonb` | `[{pr_url, title, ticket_id, risk}]` |
| `deployment_pr` | varchar(255) | yes | — | |
| `has_migrations` | boolean | **yes** | — | NULL means "unknown" (report-created record) |
| `deploy_run_url` | varchar(255) | yes | — | |
| `created_via` | varchar(16) | no | **none** | `app` \| `deployer` (Q2). No default, so each writer must state it |
| `created_at`, `updated_at` | timestamptz | no | `now()` (mixin) | |
| `deployed_at` | timestamptz | yes | — | set iff `status = 'deployed'`. Holds the **latest** deploy and is overwritten on redeploy or rollback. The name `deployed_at` is kept as in the spec, not `last_deployed_at` |

**CHECK constraints.** Name them via the convention `ck_app_releases_<name>`, using `op.f(...)` in the migration. The SQL text must be **identical** in the model and the migration (Alembic does not compare CHECKs; see the `bug_reports/models.py` docstring). Write regexes as Python raw strings.
- `environment_valid`: `environment IN ('production', 'staging')`
- `status_valid`: `status IN ('pending', 'deployed', 'failed')`
- `created_via_valid`: `created_via IN ('app', 'deployer')`
- `version_matches_environment`:
  `(environment = 'production' AND version ~ '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') OR (environment = 'staging' AND version ~ '^sha-[0-9a-f]{7}$')`
- `git_sha_format`: `git_sha IS NULL OR git_sha ~ '^[0-9a-f]{40}$'`
- `staging_version_matches_sha`: `environment <> 'staging' OR git_sha IS NULL OR version = ('sha-' || left(git_sha, 7))`
- `deployed_iff_deployed_at`: `(status = 'deployed') = (deployed_at IS NOT NULL)`
- `images_bounded`: `CASE WHEN jsonb_typeof(images) = 'array' THEN jsonb_array_length(images) <= 10 ELSE false END`
- `changes_bounded`: `CASE WHEN jsonb_typeof(changes) = 'array' THEN jsonb_array_length(changes) <= 200 ELSE false END`
  Use `CASE`, not `AND`. PostgreSQL does not guarantee short-circuit evaluation, and `jsonb_array_length` on a non-array raises a `DataError` instead of failing the CHECK.
- `deployment_pr_format`: `deployment_pr IS NULL OR deployment_pr ~ '^https://github\.com/mmlabs-online/deployment/pull/[0-9]+$'`
- `deploy_run_url_format`: `deploy_run_url IS NULL OR deploy_run_url ~ '^https://github\.com/mmlabs-online/deployment/actions/runs/[0-9]+(/attempts/[0-9]+)?$'`

The DB CHECKs are deliberately a little more lenient than OQ45: digits may have a leading zero, and the images minimum of 1 is not enforced because report-created records have no images. **ADM-034/036 Pydantic validation must be at least as strict as every CHECK**, so that a CHECK can never turn a CI write into a 500.

**Unique constraint.** `UniqueConstraint("app_id", "environment", "version", name="uq_app_releases_app_id_environment_version")`. Use an explicit name, because the convention would otherwise produce `uq_app_releases_app_id`. ADM-034/036 use it for `ON CONFLICT (app_id, environment, version)`.

**Indexes.** Name them explicitly, as `bug_reports` does. Columns are ascending, and Postgres scans them backward for `DESC`.
- `ix_app_releases_created_at_id (created_at, id)`: default list order.
- `ix_app_releases_app_id_created_at_id (app_id, created_at, id)`: list filtered by app.
- `ix_app_releases_live (app_id, environment, deployed_at, id) WHERE status = 'deployed'`: live read. It also serves ADM-038's "latest `deployed_at` ≤ T" lookup.
- No single-column status/environment index (low cardinality, ~100k rows max, OQ41), and no extra `app_id` index (the unique and composite indexes lead with it).
- No `deployed_at` list index: `ORDER BY deployed_at DESC NULLS LAST` over all rows cannot use a partial index, and a plain ascending index scanned backward gives NULLS FIRST. A top-N sort at this volume is cheap. A follow-up applies if the table grows.

**Migration.** `make migration m="adm033 app releases"`, then hand-edit. Set `down_revision = "c4e8a2f7b916"` (the current head, `20261120_c4e8a2f7b916_adm029_bug_report_permissions.py:20`). Follow the style of `20261119_b3f7a9c1d452_adm028_bug_reports.py`. A new empty table needs no `CONCURRENTLY`. `downgrade()` drops the three indexes and then the table.

## API contract
All routes are R3 class 1 (Bearer, `aud=admin`) with `**require_permission("admin:*", errors={…})` (`app/modules/admin_auth/deps.py:116`). They go under router prefix `/releases`, tag `releases`, `dependencies=[Depends(current_admin)]`, mounted in `app/api/router.py`. **`/live` must be declared before `/{release_id}`**, and `release_id` is typed `uuid.UUID`.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/releases` | query: `limit` (1–100, default 20), `offset`; `app` (pattern `APP_KEY_RE`); `environment` (`production`\|`staging`); `status` (`pending`\|`deployed`\|`failed`); `created_from` (AwareDatetime, inclusive), `created_to` (AwareDatetime, exclusive); `sort` (`created_at`\|`deployed_at`, default `created_at`) | `Page[ReleaseSummary]` | 200; 401; 403; 422 (bad enum/pattern/limit, naive datetime, `created_from` > `created_to`) | `admin:*` |
| GET | `/api/v1/releases/live` | query: `limit`, `offset`; `app`; `environment` | `Page[ReleaseSummary]`: one item per (app, environment) that has at least one `deployed` record, namely the one with the latest `deployed_at` (tie → greatest `id`). Ordered by `app_key` asc, then `environment` asc | 200; 401; 403; 422 | `admin:*` |
| GET | `/api/v1/releases/{release_id}` | — | `ReleaseDetail` | 200; 401; 403; 404 `not_found`; 422 (non-UUID) | `admin:*` |
| DELETE | `/api/v1/releases/{release_id}` | — | empty | 204; 401; 403; 404 `not_found`; 422 | `admin:*` |

Ordering:
- `sort=created_at`: `ORDER BY created_at DESC, id DESC`.
- `sort=deployed_at`: `ORDER BY deployed_at DESC NULLS LAST, created_at DESC, id DESC`.
- There is no `order=asc`: the spec says "newest first" and nothing more (rule 1).

Unknown `app` on either list gives **an empty page with 200**, the same behaviour as the bug-reports list (`bug_reports/repository.py:132`; Q4). Disabled apps' releases are included.

Schemas (`app/modules/releases/schemas.py`), all response-only in this ticket:
- `ReleaseEnvironment`, `ReleaseStatus`, `ReleaseCreatedVia` (StrEnums from `models.py`), `ChangeRisk` (`low`\|`medium`\|`high`), `ReleaseSort` (`created_at`\|`deployed_at`).
- `ReleaseImage {name: str, digest: str}`.
- `ReleaseChange {pr_url: str, title: str, ticket_id: str | None, risk: ChangeRisk | None}`. The `title` field description is the "untrusted plain text, render as text" note, like `bug_reports/schemas.py:10`.
- `ReleaseSummary {id: UUID, app_key: str, environment, version: str, status, git_sha: str | None, deployment_pr: str | None, has_migrations: bool | None, deploy_run_url: str | None, created_via, changes_count: int, created_at, updated_at, deployed_at: datetime | None}`. It has **no `changes` or `images` bodies**.
- `ReleaseDetail(ReleaseSummary) + {images: list[ReleaseImage], changes: list[ReleaseChange]}`.

Field descriptions to include:
- `deployed_at`: "Latest deploy of this version; overwritten on redeploy or rollback; null unless `status = deployed`."
- `git_sha` / `has_migrations` / `deployment_pr`: "null for a record created from a deploy report".
- `created_from` / `created_to`: inclusive / exclusive on `created_at`.
- Live route: the "latest `deployed_at` among deployed" rule.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_releases_migration.py › test_app_releases_down_up` | In a throwaway DB (pattern `tests/test_bug_reports_migration.py:45`, `PREVIOUS = "c4e8a2f7b916"`): upgrade head → table exists with the named constraints; downgrade to previous → table gone; upgrade again → back (AC 1) |
| `tests/test_releases_schema_db.py › test_rejects_bad_production_version` | `v01.2.3`, `v1.2`, `v1.2.3-rc1`, `1.2.3`, `sha-abcdef0` in production → `IntegrityError` (each insert in `begin_nested()`) |
| `… › test_rejects_bad_staging_version` | `v1.2.3`, `sha-ABCDEF0`, `sha-abcdef` (6 hex) in staging |
| `… › test_rejects_bad_environment_status_created_via` | `prod`, `rolled_back`, `staff` |
| `… › test_rejects_bad_git_sha_and_staging_mismatch` | uppercase or 39-char sha; staging `sha-1111111` with `git_sha` starting `2222222` |
| `… › test_rejects_deployed_at_mismatch` | deployed without `deployed_at`; pending with it; failed with it |
| `… › test_rejects_oversized_or_non_array_json` | 11 images; 201 changes; `changes = {}`. The `{}` case must raise `IntegrityError` (CheckViolation), **not** `DataError` |
| `… › test_rejects_bad_urls` | `deployment_pr` on another repo; `deploy_run_url` with `/jobs/1` |
| `… › test_unique_app_environment_version` | duplicate → `IntegrityError`; the same version in the other environment or another app is accepted |
| `… › test_accepts_report_created_and_boundaries` | thin record (`git_sha`/`has_migrations`/`deployment_pr` NULL, `[]` arrays, `created_via='deployer'`); staging with matching sha; 10 images / 200 changes; `v0.0.0`; `…/runs/1/attempts/2` |
| `… › test_app_with_release_cannot_be_deleted_at_db` | FK RESTRICT: deleting the `App` row → `IntegrityError` |
| `tests/test_releases_api.py › test_routes_401_without_token` | 4 routes → 401 (AC 3) |
| `… › test_routes_403_without_wildcard` | `admin_permissions=["admin:apps:read", "admin:bug-reports:read"]` → 403 on all 4; the DELETE leaves the row |
| `… › test_openapi_declares_admin_wildcard` | `x-permission == "admin:*"` on the 4 operations |
| `… › test_openapi_release_schemas` | components exist; `changes`/`images` absent from `ReleaseSummary`; nullable fields are `anyOf … null` (AC 7) |
| `… › test_list_newest_first_with_id_tiebreak` | default order; two rows with the same `created_at` ordered by `id` desc |
| `… › test_list_filters` | `app`, `environment`, `status`, `created_from` inclusive / `created_to` exclusive (boundary rows), combined |
| `… › test_list_sort_deployed_at_nulls_last` | deployed rows by `deployed_at` desc, then pending/failed |
| `… › test_list_paging_and_bounds` | `limit`/`offset`/`total`; `limit=101` and `limit=0` → 422 |
| `… › test_list_validation_422` | `environment=prod`, `status=x`, `sort=x`, `app=Bad_Key`, naive `created_from`, `created_from > created_to` |
| `… › test_list_unknown_app_is_empty_page` | 200, `total == 0` (Q4) |
| `… › test_list_items_have_no_bodies` | no `changes`/`images` keys; `changes_count` correct |
| `… › test_detail_full_and_thin_record` | all fields, including `images`/`changes`; thin record shows nulls and `[]` |
| `… › test_detail_unknown_404_and_bad_id_422` | 404 `not_found`; `/releases/not-a-uuid` → 422 |
| `… › test_live_rollback_returns_redeployed_older_version` | production v1.2.0 `deployed_at` t3 (redeploy), v1.3.0 t2 → live = v1.2.0 (AC 5) |
| `… › test_live_ignores_failed_and_pending` | a newer failed v1.4.0 and a pending v1.5.0 do not appear |
| `… › test_live_environments_do_not_leak` | staging and production each return their own row; the same version string in both environments; a second app; filters `app`, `environment`; an app with only pending rows is absent; unknown app → empty page |
| `… › test_live_tie_breaks_on_id` | equal `deployed_at` → greatest `id` |
| `… › test_live_route_not_shadowed` | `GET /releases/live` → 200, not 422 |
| `… › test_delete_writes_one_audit_row_ids_only` | 204 with an empty body; row gone; exactly one `release.deleted` row: `target_type='release'`, `target_id`, `app_key`, actor user, `changes` keys exactly `{environment, version, status, changes_count}` (`to: null`); the serialized row contains no change title or `https://` (AC 6) |
| `… › test_delete_unknown_404_writes_no_row` | 404, no audit row; a second delete of the same id → 404 |
| `… › test_admin_app_releases_listed` | a release under the seeded `admin` app is listed (OQ48) |
| `… › test_app_delete_with_release_is_409` | `DELETE /api/v1/apps/{key}` for an app that has a release → 409, not 500 (existing `IntegrityError` handler, `rbac/service.py:560-563`) |
| `tests/test_route_auth_classes.py` (existing tests) | the 4 new `BEARER` entries |
| `tests/test_audit_coverage.py` (existing parametrized tests) | the DELETE route: success writes one row, unknown id writes none |

## Approach
1. **Model** (`releases/models.py`): StrEnums, regex constants, and `AppRelease` with the CHECKs, unique constraint and indexes above (exact SQL text). Register it in `app/db/models.py` (import and `__all__`).
2. **Migration**: generate, hand-edit to the exact names and texts, add a working `downgrade()`, then run `uv run alembic check` (R2).
3. **Audit catalogue**: add `AuditTarget.RELEASE` and `AuditAction.RELEASE_DELETED`.
4. **Repository** (R1, SQLAlchemy only). Pattern: `bug_reports/repository.py:66-203`.
   - `add(release)`: add, flush, refresh. Used by the tests now and by ADM-034 later.
   - `page(f, sort, limit, offset)`: explicit column list joined to `App` for `App.key`, `func.jsonb_array_length(AppRelease.changes)` as `changes_count`. It **never selects `changes`/`images`**. The count uses the same `where`.
   - `get_view(id)`: all columns plus `App.key`.
   - `live_page(app_key, environment, limit, offset)`: inner `select(...).where(status == 'deployed', …).distinct(AppRelease.app_id, AppRelease.environment).order_by(app_id, environment, deployed_at.desc(), id.desc())` as a subquery; the outer query orders by `app_key, environment` with limit/offset; total = `count()` over the subquery.
   - `delete_returning(id)`: one `DELETE … WHERE id = :id RETURNING app_id, environment, version, status, jsonb_array_length(changes)`. This is atomic, needs no separate lock and returns None if the row is missing.
   - `app_key(app_id)`.
   - Views are frozen dataclasses (`ReleaseView`) with plain values, so routers never see the ORM (R1).
5. **Service** (`ReleaseService(session)`, raises only `AppError` subclasses):
   - `list`: `created_from > created_to` raises `UnprocessableError` with details `[{"field": "created_from", "reason": "after_to"}]`, as `bug_reports/staff_service.py:32-39` does.
   - `get` raises `NotFoundError("Release not found")`.
   - `live`.
   - `delete(id, actor, ctx)`: `delete_returning`, `NotFoundError` if None, then `AuditLogger.record(RELEASE_DELETED, target=(RELEASE, id), app_key=…, changes=diff.diff({"environment", "version", "status", "changes_count"}, None))` in the same session, before the commit (R4). Log line: ids only (R5/R11).
6. **Router**: the 4 routes with `**require_permission("admin:*", errors={404: "Unknown release"})` on detail and delete. Declare `/live` before `/{release_id}`. DELETE uses `status_code=204` and returns `Response(status_code=204)`. Build the schemas from views in small `_summary` / `_detail` helpers. Mount the router in `app/api/router.py`.
7. **Tests**: `tests/release_factories.py › make_release(session, app, *, environment="production", version=None, status="pending", deployed_at=None, created_at=None, created_via="app", git_sha=<random 40 hex>, images=(), changes=(), **kw)`. It derives a staging version from `git_sha` when `version` is None and always inserts through `AppReleaseRepository.add`. Then write the test files above, update `test_route_auth_classes.py` and `test_audit_coverage.py`, and run `make check`.

Rules that apply:
- R1: layering as above.
- R2: migration with downgrade; model registered.
- R3: class 1, `admin:*`, `x-permission`.
- R4: same-transaction audit on DELETE.
- R5: nothing new in config.
- R6: `NotFoundError` / `UnprocessableError` use the envelope; 204 on delete.
- R8: 401/403/404/422 plus happy paths.
- R9: plural `/releases`, `Page[T]`, UUID ids, timestamptz. `/releases/live` is a sub-collection that returns `Page[T]` (like `/audit-logs/export` is a sub-path of its collection), so it needs no R9 exception under the default answer to Q1.
- R10: no non-goal touched.
- R11: no secrets. The audit holds no titles or URLs.

## Specialist input used
- **api-platform-engineer.** Adopted:
  - A dedicated live route, declared before `/{id}` (rec. 1).
  - Explicit `sort`, `id` tie-break and NULLS LAST (rec. 2).
  - The `ReleaseSummary`/`ReleaseDetail`/`ReleaseChange`/`ReleaseImage` schemas, enums, `app_key`, `changes_count`, explicit nullables, and keeping the name `deployed_at` with the overwrite documented (rec. 3).
  - Delete with 204/404 and an ids-only audit row, without If-Match (rec. 4).
  - The auth map and the 401/403 tests (rec. 5).
  - Text + named CHECKs (rec. 6).
  - The admin-pan app row as the default (rec. 7).
  - The live index serving ADM-038 (rec. 8).

  Adapted:
  - The live route returns `Page[ReleaseSummary]` of all live releases, filterable by `app`/`environment`, instead of a single document. This avoids an R9 exception and covers the dashboard use the consult wanted as a follow-up.
  - Unknown app gives an empty page, not 404 (consistent with bug-reports).

  Rejected:
  - `order=asc|desc`: not in the spec (rule 1).
  - `release_not_found` code: the existing modules use generic `not_found`.
- **database-optimizer.** Adopted:
  - Types and nullability (`git_sha`, `has_migrations` NULL; jsonb `'[]'` NOT NULL), FK RESTRICT.
  - All the named CHECKs, with `(status='deployed') = (deployed_at IS NOT NULL)`.
  - The named unique constraint for future `ON CONFLICT`.
  - `(created_at, id)`, `(app_id, created_at, id)` and the partial live index with the `id` tie-break.
  - No low-cardinality indexes; no `is_live` flag.
  - A summary-only select with a jsonb length; hard delete; the rollback/leak seeding; CHECK-violation tests; seeding via the repository.

  Changed:
  - `CASE` instead of `AND` in the jsonb CHECKs (the short-circuit hazard).
  - `version` String(32) instead of 16, and `created_via` String(16) instead of 10, for headroom.

  Rejected:
  - `pg_column_size` byte cap: the app layer enforces 256 KiB (OQ46, ADM-034).
  - The partial `deployed_at` list index: it cannot serve NULLS LAST over all rows, and volume is small.
- **Disagreement on `created_via` values** (`app_write`/`deploy_report` from the API consult vs `app`/`deployer` from the DB consult). Chose `app`/`deployer`: they match the audit actors `app:{key}/{key_id}` and `deployer:{key_id}` (OQ47) and fit `String(16)`. This is Q2.

## Assumptions
- **Query parameter names follow the spec** (`app`, `created_from`, `created_to`; OQ49). The bug-reports list uses `app_key`, `from` and `to`. The spec wins, and the difference is noted for admin-pan.
- **The date range filters `created_at` only**, also when `sort=deployed_at`, since the spec names the parameters `created_*`.
- The `admin` app's releases appear in all reads (OQ48). Disabled apps' releases appear too.
- Response models do not re-validate jsonb items beyond their shape. Only ADM-034/036 write them, with validation at least as strict as the CHECKs and OQ45.
- Reads are not PII-bearing, so no `Cache-Control: no-store` is needed (titles are PR titles only, per §5.13 PII). No ETag.
- **Hotspots** with other open tickets in batch 2026-11-20-1 (all PLANNING; whoever merges second rebases):
  - `migrations/versions` head `c4e8a2f7b916`: ADM-030, ADM-035 and ADM-039 may also chain on it, so `down_revision` must be re-pointed on rebase and `alembic heads` must show one head.
  - `app/db/models.py`, `app/api/router.py`, `app/modules/audit/actions.py` (ADM-030/035 add actions too).
  - `tests/test_route_auth_classes.py`; `tests/test_audit_coverage.py` (the `len(AUDITED)` count changes in several tickets).
- Deleting an app that has releases already yields 409 through the existing `IntegrityError` handler in `rbac/service.py:560-563`, but with the message "App has role assignments; disable it instead", which is misleading. That is a follow-up for rbac (a High path), not fixed here. This ticket only adds a test that it is a 409, not a 500.
- No new ADR. The decisions taken here (live shape, `created_via` values, CHECK set) are recorded in this plan and in the batch answers; ADM-034/036/038 consume them.
- Size: roughly 450 product lines (migration ~120, model ~90, schemas ~80, repository ~120, service ~60, router ~90) plus ~550 test lines. That is at the upper edge of one cycle; see Split proposal.

## Blocking questions
None that block. Each question below has a default, and the plan is written to the defaults. Ming may override them in batch 2026-11-20-1.
1. **Live route shape.** Default: `GET /api/v1/releases/live?app=&environment=` returns `Page[ReleaseSummary]`, one item per (app, environment) with something deployed. Unknown app or nothing deployed gives an empty page. No R9 exception and no `CLAUDE.md` change. Alternatives:
   - (a) A single document `GET /api/v1/releases/live?app=<key>&environment=<env>` (required params, 404 when nothing is deployed). This **needs an R9 exception** in `CLAUDE.md` (High path; this ticket would then add it, and it would need an ADR-level note, numbering 0025 / D34).
   - (b) No new route: clients call `GET /releases?app=…&environment=…&status=deployed&sort=deployed_at&limit=1`.
2. **`created_via` values.** Default: `app` (written by the app's release workflow, ADM-034) | `deployer` (record created from a deploy report, ADM-036). Fixed in the CHECK now, so later tickets need no migration.
3. **admin-pan / org-admin-api collision on the `admin` app.** Default: admin-pan gets its own app row (for example key `admin-pan`) when ADM-034 issues its `releases` key. Releases key off `apps.id`, so **this ticket's schema is unaffected**. The final details (enabled state, role catalog visibility, who registers it) are settled at the PLAN of ADM-034 at the latest. Alternative: a `component` column in the unique key, which would require an ADM-034 migration and is not recommended.
4. **Unknown `app` filter value.** Default: an empty page with 200 on both lists, matching `GET /api/v1/bug-reports?app_key=`. Alternative: 404.

## Split proposal
None. The data model, repository and routes are tightly coupled, and the route tests are the main proof that the indexes and CHECKs fit. If the implementation passes ~450 product lines, the fallback is:
- slice A: model, migration, repository, `release_factories.py`, schema and migration tests;
- slice B: service, router, audit action and the API, auth-map and audit-coverage tests.
