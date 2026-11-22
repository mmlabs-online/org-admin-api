Status: READY

> BLOCKED on Q1 only (spec vs ticket conflict on how the environment is picked and what "inferred" means). The plan
> below is complete and written for the **default answer to Q1 (the spec wins)**. If Ming picks the ticket's rule
> instead, the delta is listed under Q1. Once Ming answers Q1, the orchestrator sets this to `READY`.
> High paths are touched (`migrations/**`), so the plan gate needs Ming's approval either way.

# Plan — ADM-038: Bug reports carry the release the user was running

## Scope summary
Spec `docs/spec-v1.md` §5.12 "Link to the release the user ran" (owner, 2026-11-19), lines 167-174:
> - **First, the report's app version** (the optional OQ26 context field): if it equals the `version` of a release
>   record of that app, that record is linked and **`release_match = reported`**.
> - **Else, the release live at report time:** the record of that app with the latest `deployed_at` ≤ the report's
>   `created_at` among `status = deployed` (the §5.13 "live now" rule, evaluated at `created_at`) →
>   **`release_match = inferred`**.
> - **Else** (no matching version and no deployed release before the report) → **`release_match = none`**, no linked release.
> - The response exposes `release_match` (`reported` | `inferred` | `none`) and the linked release's id and version
>   (or null). The link is informational; it never blocks submit or triage, and a missing release table row is not an error.
> - **Environment (open, not blocking — §8 OQ50):** a report carries no environment. Proposed default: a reported
>   version matches by grammar (`vX.Y.Z` → `production`, `sha-…` → `staging`); the `inferred` rule uses `production` only.
> - **Facts carried into PLAN:** (1) … **compute-at-read vs store-at-create is PLAN's choice**. (2) A staff-deleted
>   release (§5.13 OQ42) drops a `reported`/`inferred` link … (3) The app version is free text; whether a missing
>   leading `v` is normalized before matching is PLAN's.

§8 OQ50 (line 413): "Not blocking; PLAN of the owning ticket uses the default unless the owner answers."

§5.13 (line 231): "the §5.12 staff read links each bug report to the release its reporter ran … The release log adds
no route for it."

§5.12 audit (line 176): "`bug_report.created` … carrying ids and counts only (report id, app key, reporter id,
attachment count), never the description, context fields or attachment content."

Ticket ADM-038 scope (kept): store version as sent (already stored, `bug_reports.app_version`, models.py:77), nullable
`release_id` FK `ON DELETE SET NULL`, `release_match`, migration with working `downgrade()`; staff list/detail show the
linked release summary + `release_match`; list filters by release and version; release detail shows open/done counts
of linked reports; audit may add the release id.

**Conflict flagged (spec wins, CLAUDE.md):** the ticket (and the orchestrator note of 2026-11-19) says environment
from the validated `Origin` and inferred = latest deployed release; spec §5.12/OQ50 says environment from the version
grammar, inferred = production only, latest `deployed_at` ≤ `created_at`. This plan implements the spec (Q1 default).

`work/decisions.md`: nothing specific to ADM-038.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | Reported path | `tests/test_bug_report_release_link.py::test_submit_reported_production_version_links_release`, `::test_submit_reported_staging_version_links_release` |
| 1b | Reported links pending/failed releases too | `::test_submit_reported_matches_pending_and_failed_release` |
| 1c | Inferred path (production only, latest `deployed_at` ≤ `created_at`) | `::test_submit_without_version_infers_latest_deployed_production`, `::test_inferred_ignores_future_deployed_at_and_non_deployed`, `::test_inferred_ignores_staging_releases` |
| 1d | Unknown version falls back to inferred | `::test_submit_unknown_version_falls_back_to_inferred`, `::test_submit_version_match_is_exact_and_case_sensitive` (`1.2.3` vs `v1.2.3`, `V1.2.3`) |
| 1e | None path; never rejects | `::test_submit_no_release_gives_none`, `::test_submit_other_apps_release_never_links` (201 in every case) |
| 1f | Staging vs production (spec: by version grammar; see Q1) | pure unit `tests/test_bug_report_release_link_rules.py::test_environment_for_version` + 1a staging/production API tests |
| 2 | Deleting a release keeps the report, nulls `release_id`, keeps the version string | `tests/test_bug_report_release_link.py::test_release_delete_keeps_report_nulls_link_keeps_version` (via `DELETE /api/v1/releases/{id}`, then staff detail shows `release: null`, `release_match` unchanged, `app_version` unchanged) |
| 3a | Report list filter by release / version (+ `release_match`) | `tests/test_bug_reports_staff_api.py::test_list_filter_release_id`, `::test_list_filter_app_version_exact`, `::test_list_filter_release_match`, `::test_list_release_filters_validation_422` |
| 3b | Staff list/detail show `release_match` + release summary | `tests/test_bug_reports_staff_api.py::test_list_and_detail_show_release_link`, `::test_list_release_link_no_n_plus_one` (query count fixed regardless of row count, if the suite has a query counter; else single-query assertion by inspection) |
| 3c | Release detail report counts (open / done) | `tests/test_releases_api.py::test_release_detail_bug_report_counts`, `::test_release_detail_counts_zero_when_no_reports` |
| 4 | `make check` and `alembic check` green; migration round-trip | `make check`; `uv run alembic check`; `tests/test_bug_report_release_link_migration.py::test_upgrade_downgrade_roundtrip` |

## Ming verification after merge
None (all behaviour is visible in local API tests). Optional: after deploy, staff detail of a new report from an app
that sends `app_version` shows `release_match: reported`.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261122_<rev>_adm038_bug_report_release_link.py` | create (`<rev>` from `make migration`), `down_revision = "a4c81e6d27f3"` | R2: two columns, FK, CHECKs, partial index | **High** |
| `app/modules/bug_reports/models.py` | modify (BugReport columns + `__table_args__`, new `BugReportReleaseMatch` StrEnum) | ORM mirrors migration (`alembic check`) | no |
| `app/modules/bug_reports/release_link.py` | create — pure: `environment_for_version(version) -> ReleaseEnvironment \| None` | pure logic first (rule 6) | no |
| `app/modules/bug_reports/service.py` | modify `BugReportSubmitService.submit` (lines 134-155): match, store, audit | link stored at submit | no |
| `app/modules/releases/repository.py` | modify: add `find_for_report_link`, `live_for_report_link`, `bug_report_counts` | release lookups and counts in the repository (R1) | no |
| `app/modules/bug_reports/repository.py` | modify `ListFilter`, `BugReportView`, `_select`, `_where`, `_view` (lines 154-234) | release summary + filters on staff reads | no |
| `app/modules/bug_reports/schemas.py` | modify `BugReportListItem`, `BugReportRead` (add `release_match`, `release`) | staff read contract | no |
| `app/modules/bug_reports/router.py` | modify `_item`, `_read`, `list_bug_reports` (new query params) | staff read contract + filters | no |
| `app/modules/releases/schemas.py` | modify: add `ReleaseBugReportCounts`, `StaffReleaseDetail(ReleaseDetail)`, `staff_release_detail()` | release detail counts, staff route only | no |
| `app/modules/releases/service.py` | modify: add `get_with_report_counts` (or `bug_report_counts`) | R1: router never queries | no |
| `app/modules/releases/router.py` | modify `get_release` (lines 93-99) to `StaffReleaseDetail` | release detail counts | no |
| `tests/test_bug_report_release_link_rules.py` | create | unit tests of pure rule | no |
| `tests/test_bug_report_release_link.py` | create | submit matching + delete behaviour (AC1, AC2) | no |
| `tests/test_bug_report_release_link_migration.py` | create | round-trip + CHECK/FK behaviour | no |
| `tests/test_bug_reports_staff_api.py` | modify | list/detail fields + filters (AC3) | no |
| `tests/test_releases_api.py` | modify | release detail counts (AC3) | no |
| `tests/test_bug_reports_submit.py` | modify line 484 only | audit `changes` key set grows (`release_id`, `release_match`) | no |
| `docs/adr/0028-bug-report-release-link.md` | create | decision outlives the ticket: store-at-submit, env rule, kept `release_match` after delete | no |
| `docs/identity-architecture.md` | modify: add D37 row after D35 (line 332) | decision log entry for ADR-0028 | no |
| `docs/adr/0020-bug-report-triage.md` | modify: one "Amended by ADR-0028" line under Consequences | list/detail contract extended | no |

Not touched: `app/db/models.py` (no new model; `BugReport`/`AppRelease` already registered), `app/core/**`,
`tests/test_route_auth_classes.py` (no new route), `docs/spec-v1.md` (only under Q1 alternative), `CLAUDE.md`.

## Data model / migration
Table `bug_reports` (existing; `app_version varchar(64) NULL` already stores the version as sent — no new column, no
stricter CHECK, legacy rows keep free-form values):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `release_id` | `uuid` | NULL | — | FK `fk_bug_reports_release_id_app_releases` → `app_releases.id` `ON DELETE SET NULL` |
| `release_match` | `varchar(8)` | NOT NULL | `server_default 'none'`; ORM `default=BugReportReleaseMatch.NONE.value` | how the link was made |

Constraints (names via the naming convention `ck_%(table_name)s_%(constraint_name)s`; CHECK texts identical in model
and migration — Alembic does not compare CHECKs, see models.py:3-4):
- `ck_bug_reports_release_match_valid`: `release_match IN ('reported', 'inferred', 'none')`
- `ck_bug_reports_release_link_consistent`: `release_id IS NULL OR release_match <> 'none'`
  (not `(release_match = 'none') = (release_id IS NULL)`: that would break `ON DELETE SET NULL`; consult-db #2).

Index: `ix_bug_reports_release_id_status` on `(release_id, status)` `WHERE release_id IS NOT NULL` (serves release
filter, per-release counts, and the FK SET NULL scan on release delete). No index on `app_version` (Q7).

Migration `upgrade()`: `op.add_column` × 2 (release_match with `server_default=sa.text("'none'")`), `op.create_foreign_key`
(`ondelete="SET NULL"`), `op.create_check_constraint` × 2, `op.create_index(..., postgresql_where=sa.text("release_id IS NOT NULL"))`.
Plain (non-concurrent) index: table is small. **No backfill**: existing rows get `release_id NULL, release_match 'none'`.
`downgrade()`: drop index, both CHECKs, FK, then both columns (reverse order). Header docstring in the style of
`20261121_a4c81e6d27f3_usr29_org_members_read_scope.py`.

Model: in `BugReport.__table_args__` add the two `CheckConstraint`s (names `release_match_valid`,
`release_link_consistent`) and `Index("ix_bug_reports_release_id_status", "release_id", "status",
postgresql_where=text("release_id IS NOT NULL"))`; columns `release_id: Mapped[uuid.UUID | None] =
mapped_column(ForeignKey("app_releases.id", ondelete="SET NULL", name="fk_bug_reports_release_id_app_releases"))`
(string reference, no import of the releases module), `release_match: Mapped[str] = mapped_column(String(8),
nullable=False, server_default=text("'none'"), default=BugReportReleaseMatch.NONE.value)`.
`class BugReportReleaseMatch(StrEnum): REPORTED="reported"; INFERRED="inferred"; NONE="none"`.

## API contract
No new route; auth classes unchanged (R3); no new error codes (R6). All changes additive inside v1 (R9).

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/bug-reports` (cookie, unchanged) | unchanged (`app_version?` optional, free text 1..64) | unchanged `{id, status, created_at}` | unchanged; never rejects for release matching | cookie class |
| GET | `/api/v1/bug-reports` | new query: `release_id: UUID?` (exact, regardless of `release_match`), `app_version: str?` (`min_length=1, max_length=64`, exact case-sensitive match on the stored string), `release_match: reported\|inferred\|none?`; AND with existing filters | `Page[BugReportListItem]`; items gain `release_match: BugReportReleaseMatch`, `release: ReleaseSummary \| null`, `app_version: str \| null` (UNTRUSTED description) | 200, 401, 403, 422 (bad UUID / enum / length) | `admin:bug-reports:read` |
| GET | `/api/v1/bug-reports/{id}` | — | `BugReportRead` gains `release_match`, `release: ReleaseSummary \| null` | 200, 401, 403, 404 | `admin:bug-reports:read` |
| PATCH | `/api/v1/bug-reports/{id}` | unchanged | same `BugReportRead` additions | unchanged | `admin:bug-reports:write` |
| GET | `/api/v1/releases/{id}` | — | `StaffReleaseDetail` = `ReleaseDetail` + `bug_reports: {open: int, done: int}` (all linked reports, reported + inferred) | 200, 401, 403, 404 | `admin:*` |

Machine routes (`PUT …/releases/…` → `ReleaseDetail`, `…/deployment` → `ReleaseSummary`) are **unchanged**: counts
are only on the staff detail, so app/deployer credentials never see bug-report data.

Field docs (OpenAPI): `release_match` — "How `release` was linked at submit: `reported` = the app's version equals a
release version of that app; `inferred` = no/unknown version, the production release live at submit time; `none` = no
link. `reported`/`inferred` with `release: null` means the linked release was deleted. Clients should tolerate unknown
values." `release` — "Release summary, linked at submit; null for `none` or a deleted release."

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_bug_report_release_link_rules.py › test_environment_for_version` (parametrized: `v1.2.3`→production, `v0.0.0`→production, `sha-abc1234`→staging, `1.2.3`/`V1.2.3`/`v01.2.3`/`sha-ABC1234`/`sha-abc123`/`v1.2.3 `/`""`→None) | grammar rule, exact + case-sensitive, reuses `PRODUCTION_VERSION_RE`/`STAGING_VERSION_RE` |
| `tests/test_bug_report_release_link.py › test_submit_reported_production_version_links_release` | AC1a; DB row `release_id`, `release_match='reported'` |
| `… › test_submit_reported_staging_version_links_release` | AC1a/1f staging |
| `… › test_submit_reported_matches_pending_and_failed_release` | Q4 default |
| `… › test_submit_reported_wins_over_newer_deployed_release` | order of rules |
| `… › test_submit_without_version_infers_latest_deployed_production` | AC1c (two deployed rows, picks greatest `deployed_at`, tie → greatest id) |
| `… › test_inferred_ignores_future_deployed_at_and_non_deployed` | `deployed_at > now()` and pending/failed are skipped (factory timestamps must be explicitly `now ± 1h`: the test transaction shares one `now()`) |
| `… › test_inferred_ignores_staging_releases` | spec: inferred uses production only |
| `… › test_submit_unknown_version_falls_back_to_inferred` | AC1d (`v9.9.9` not stored; and a non-grammar `1.2`) |
| `… › test_submit_version_match_is_exact_and_case_sensitive` | no normalization of missing `v` |
| `… › test_submit_no_release_gives_none` | AC1e; 201, `release_id NULL`, `'none'` |
| `… › test_submit_other_apps_release_never_links` | release of app B with same version not linked for app A |
| `… › test_submit_audit_has_release_id_and_match_only` | audit `changes == {"attachment_count": 0, "release_id": "<uuid>"\|None, "release_match": "..."}`; version string absent |
| `… › test_release_delete_keeps_report_nulls_link_keeps_version` | AC2 via `DELETE /api/v1/releases/{id}` |
| `tests/test_bug_report_release_link_migration.py › test_upgrade_downgrade_roundtrip` | R2 (pattern of `test_bug_report_attachments_migration.py`, `_admin`/`_cfg` from `test_bug_reports_migration`) |
| `… › test_checks_reject_bad_release_match_and_linked_none` | CHECKs: `release_match='x'` and (`release_id` set, `'none'`) → IntegrityError |
| `… › test_existing_rows_default_none` | default for legacy rows / insert without the column |
| `tests/test_bug_reports_staff_api.py › test_list_and_detail_show_release_link` | AC3b: `release` summary (version, environment, status, `deployed_at`, `deployment_pr`) + `release_match` on list and detail |
| `… › test_list_filter_release_id` | AC3a (includes reported + inferred) |
| `… › test_list_filter_app_version_exact` | AC3a; `V1.2.3` does not match `v1.2.3` |
| `… › test_list_filter_release_match` | Q5 default |
| `… › test_list_release_filters_validation_422` | bad UUID, bad enum, `app_version` of 65 chars |
| `tests/test_releases_api.py › test_release_detail_bug_report_counts` | AC3c: 2 open + 1 done linked, 1 unlinked → `{open: 2, done: 1}` |
| `… › test_release_detail_counts_zero_when_no_reports` | `{open: 0, done: 0}` |
| `tests/test_bug_reports_submit.py › test_submit_audit_row_ids_only` (line 484 edit) | key set `{"attachment_count", "release_id", "release_match"}` |

## Approach
1. **Migration + model** (R2). `make migration m="adm038 bug report release link"`, hand-edit per Data model, set
   `down_revision = "a4c81e6d27f3"`. Run upgrade/downgrade/upgrade and `uv run alembic check`.
2. **Pure rule** `app/modules/bug_reports/release_link.py`: `environment_for_version(v)` →
   `ReleaseEnvironment.PRODUCTION` if `re.fullmatch(PRODUCTION_VERSION_RE)`, `STAGING` if `STAGING_VERSION_RE`, else
   `None` (import the regex constants from `app.modules.releases.models`). `INFERRED_ENVIRONMENT = PRODUCTION`.
3. **Release repository** (`app/modules/releases/repository.py`, R1):
   - `find_for_report_link(app_id, environment, version) -> uuid.UUID | None`: `select(AppRelease.id)` on the unique
     key, any status, `.with_for_update(read=True, key_share=True)` (FOR KEY SHARE pins the row so a concurrent release
     delete cannot turn the insert into an FK error → 500).
   - `live_for_report_link(app_id, environment) -> uuid.UUID | None`: `status='deployed' AND deployed_at <= func.now()`,
     `ORDER BY deployed_at DESC, id DESC LIMIT 1`, same lock (uses `ix_app_releases_live`). `func.now()` is the
     transaction timestamp, identical to the report's `created_at` server default, so this is exactly
     "`deployed_at` ≤ `created_at`".
   - `bug_report_counts(release_id) -> tuple[int, int]`: one `SELECT status, count(*) FROM bug_reports WHERE release_id
     = :id GROUP BY status` (imports `BugReport` model only; no cycle: `bug_reports.models` references
     `app_releases.id` by string).
4. **Submit** (`service.py` `submit`, after the caps check at line 132, before `repo.add`): compute
   `(release_id, match)`: if `data.app_version` and `environment_for_version` gives an env and
   `find_for_report_link` finds a row → `reported`; else `live_for_report_link(app.id, PRODUCTION)` → `inferred`; else
   `(None, none)`. Wrap nothing in try/except beyond normal flow: lookups cannot fail on user input (version never
   reaches SQL unless it matches the grammar; ≤64 chars anyway). Pass `release_id`, `release_match` to `BugReport(...)`.
   Audit `changes={"attachment_count": 0, "release_id": str(id) | None, "release_match": match}` (R4, ids only; version
   string never in audit or log). Add `release_match` to the existing `bug_report_created` log line (no version).
5. **Staff read** (`bug_reports/repository.py`): `ListFilter` gains `release_id`, `app_version`, `release_match`;
   `_where` adds equality conds. `_select` adds `BugReport.release_match` and `outerjoin(AppRelease, AppRelease.id ==
   BugReport.release_id)` selecting the release summary columns (id, environment, version, status, git_sha,
   deployment_pr, has_migrations, deploy_run_url, created_via, `jsonb_array_length(changes)`, created_at, updated_at,
   deployed_at) — one query, no N+1. `BugReportView` gains `release_match: str` and `release: ReleaseView | None`;
   `_view` builds `ReleaseView(app_key=<report app_key>, …)` when the release id is not null (release belongs to the
   report's app by construction). Stop relying on `BugReportView(*row)` positional for the new tail; build explicitly.
   The count query in `page()` needs no join (filters are on `bug_reports` columns).
6. **Schemas/router**: `BugReportListItem` + `BugReportRead` gain `release_match: BugReportReleaseMatch` and
   `release: ReleaseSummary | None` (import from `app.modules.releases.schemas`); list item also gains `app_version`.
   `_item`/`_read` map via `release_summary(v.release)`. `list_bug_reports` adds the 3 query params.
7. **Release detail counts**: `ReleaseBugReportCounts(open: int, done: int)`, `StaffReleaseDetail(ReleaseDetail)` with
   `bug_reports: ReleaseBugReportCounts`; `ReleaseService.get_detail(release_id) -> tuple[ReleaseView, tuple[int,int]]`
   (404 as `get`); `get_release` returns `staff_release_detail(view, counts)`. `ReleaseDetail` itself unchanged
   (machine responses unchanged).
8. **Docs**: ADR-0028 (store-at-submit; env by grammar per OQ50 default; inferred = production, `deployed_at` ≤
   `created_at`, at submit; exact case-sensitive match; reported links any status; SET NULL keeps `release_match`;
   counts on staff detail only; audit ids only). D37 row. ADR-0020 consequence line.
9. `make check`, `uv run alembic check`.

Rules: R1 (repos only touch ORM; services raise; router maps), R2 (step 1), R3 (no route change), R4 (audit
unchanged action, same transaction), R5 n/a, R6 (no new codes), R7 (only listed files), R8 (tests above), R9
(additive fields, `Page[T]`), R11 (no PII/version string in audit or logs).

## Specialist input used
- consult-database-optimizer #1 (two columns, FK SET NULL, default `'none'`): **adopted**.
- #2 CHECK `release_id IS NULL OR release_match <> 'none'`: **adopted** (strict equivalence breaks SET NULL).
- #3 store at submit, same transaction; reported links pending/failed: **adopted** (Q2, Q4 defaults).
- #4 no strict CHECK on `app_version`, exact match: **adopted**.
- #5 partial index `(release_id, status)`; skip `app_version` index: **adopted** (Q7).
- #6 no backfill: **adopted**. #7 downgrade order: **adopted**. #8 grouped count: **adopted**.
- consult-api #1 body unchanged, version free text: **adopted**. #2 exact, case-sensitive: **adopted**.
- #3 `release_match` StrEnum + `release: ReleaseSummary | None` on list and detail, tolerate unknown values: **adopted**.
- #4 filters `release_id`, `app_version` (≤64): **adopted**; plus `release_match` filter (Q5).
- #5 store at submit: **adopted**. #6 environment from Origin: **not adopted** — conflicts with spec §5.12/OQ50;
  raised as Q1 (blocking), plan follows the spec default.
- #7 counts in releases repository behind a service; not on `ReleaseSummary`: **adopted**, and also **not** on
  `ReleaseDetail` (machine PUT returns it) — new `StaffReleaseDetail` instead.
- #8 no new routes/codes: **adopted**. #9 audit adds `release_id` + `release_match`: **adopted** (Q8).
- consult-api "docs need a note in identity-architecture.md:179 / ADR-0019 about build version + Origin rule":
  **not adopted** — app-facing guidance belongs to ADM-032/ADM-037 (ticket Out of scope); follow-up.

## Assumptions
- **Release belongs to the report's app** is guaranteed by the service (lookups filter on `app.id`), not by a
  composite FK. Non-blocking.
- **Inferred at submit** means "live at `created_at`"; a later redeploy/rollback does not change a stored link (spec
  fact 1 is the reason for storing).
- Matching runs only for the grammar-valid version; a free-form version (e.g. `1.2`) is stored as sent and gives
  `inferred`/`none`.
- After a release delete, `release_match` keeps `reported`/`inferred` with `release: null` (Q3). Spec fact (2) "drops
  a link (falls back to the next rule, or none)" describes compute-at-read; with store-at-submit we keep the stored
  value as an audit fact and document the combination.
- `GET /api/v1/releases/{id}` response schema name changes to `StaffReleaseDetail` in OpenAPI (fields additive);
  admin-pan regenerates types — follow-up for admin-pan.
- Release delete (`releases/repository.py:232`) now also nulls linked reports (DB `SET NULL`, cheap with the new
  index); release delete audit unchanged. `bug_reports.updated_at`/ETag are **not** bumped by the SET NULL.
- **Hotspots:** ADM-042 (same batch, builds after this) adds a migration → it must set its `down_revision` to this
  ticket's revision; it also appends D36/ADR-0027 to `docs/identity-architecture.md` (D37 row here, rebase likely).
  ADM-037 (PLANNING, release-log guide) may touch release docs; no shared code file expected.
- Size: ~300 product lines + ~400 test lines; one cycle (ticket Size S). No split.

## Blocking questions
**Questions for Ming** (Q1 blocking; Q2-Q8 non-blocking, the plan uses the default unless Ming says otherwise):

1. **(Blocking — spec vs ticket.)** Which rule picks the environment and defines "inferred"?
   **Default: the spec (§5.12 + OQ50 default)** — a reported version picks its environment by grammar (`vX.Y.Z` →
   production, `sha-<7 hex>` → staging); `inferred` = the production release with the latest `deployed_at` ≤ the
   report's `created_at`; no spec edit, OQ50 stays answered-by-default.
   Alternative (ticket): environment from the validated `Origin` (`staging-<app>.mmlabs.online` → staging, else
   production) for both reported and inferred, inferred = latest deployed. Delta if chosen: `docs/spec-v1.md`
   §5.12/OQ50 amended by product-manager in the same PR; `environment_for_origin(origin, app_key)` replaces
   `environment_for_version` in `release_link.py`; `auth_router.py` passes the validated Origin to `submit`; tests
   switch to Origin-based staging/production cases; risk: preview/localhost origins in `apps.origins` count as
   production.
2. Store the link at submit (not compute at read)? **Default: store at submit.**
3. Keep `release_match = reported|inferred` after the linked release is deleted (`release: null`)? **Default: yes.**
4. Should a reported version that matches a `pending`/`failed` release link it? **Default: yes** (spec: "a release
   record of that app").
5. Add a `release_match` filter on the staff list? **Default: yes.**
6. Release detail counts: all linked reports (reported + inferred) together? **Default: yes**, split later if wanted.
7. Index on `app_version` for version filters without an app? **Default: no.**
8. Audit `bug_report.created` adds `release_id` and `release_match` (ids/enum only)? **Default: yes.**

## Split proposal
None.

## Follow-ups (not in this diff, R7)
- ADM-032/ADM-037 guide: apps send exactly the release version string (`vX.Y.Z` / `sha-xxxxxxx`) as `app_version`.
- admin-pan: show `release_match`/`release` on reports, counts on release detail, new filters; regenerate types.
