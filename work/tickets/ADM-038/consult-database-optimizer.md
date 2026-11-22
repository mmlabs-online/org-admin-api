# Consult — database-optimizer — ADM-038 (2026-11-22)

**Findings**
- `bug_reports.app_version` already exists (String(64), nullable, no CHECK): app/modules/bug_reports/models.py:77. It is written at submit (service.py:141) and validated by the schema as 1..APP_VERSION_MAX printable ASCII (text.py:17, :47; schemas.py:35). The ticket's "store version string as sent" is already done, so no new version column is needed.
- Release grammar (ADM-033): production `^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`, staging `^sha-[0-9a-f]{7}$`, version column String(32) (releases/models.py:45-46, :116). The unique key is (app_id, environment, version) (:96-98).
- The live-release query is a DISTINCT ON (app_id, environment) over `status='deployed'`, ordered by `deployed_at DESC, id DESC` (repository.py:202-220). It is backed by the partial index `ix_app_releases_live` (models.py:101-108). A single-row lookup for one (app, env) can use that index.
- The current alembic head file is `20261121_a4c81e6d27f3_usr29_...` (down_revision 911eb99ddf8b). ADM-042 may add a head too.
- bug_reports has no release link, no CHECK on release_match, and no index that supports a release filter (models.py:50-59).

**Recommendations**
1. Add only two columns:
   - `release_id uuid NULL`, FK `fk_bug_reports_release_id_app_releases` to `app_releases.id` with `ON DELETE SET NULL`.
   - `release_match varchar(8) NOT NULL DEFAULT 'none'`, with `CHECK (release_match IN ('reported','inferred','none'))` and `CHECK ((release_match = 'none') = (release_id IS NULL))`. The default makes the migration safe for existing rows.
2. The second CHECK conflicts with SET NULL. When a release is deleted, `release_id` becomes NULL while `release_match` stays `reported` or `inferred`, and the constraint fails. Use `CHECK (release_id IS NULL OR release_match <> 'none')` instead. This still stops the nonsensical combination of a linked release with `none`. Keep the stale `reported`/`inferred` value after deletion, because it records how the link was made.
3. Store the link at submit time and do not compute it at read time. A read-time inferred link would drift as new releases deploy, and it would break the criterion that a delete keeps the report. Do the match in the same transaction as the insert, inside the service and repository layers.
   - Step (a): look up (app_id, env, version) by the unique index.
   - Step (b): look up the latest deployed row for (app_id, env) using ix_app_releases_live (`ORDER BY deployed_at DESC, id DESC LIMIT 1`).
   - Step (c): no row, so `release_match = 'none'`.
   - A `reported` match should only link if the release exists. A reported version that matches a release with status `pending` or `failed` is still linked as `reported`, because the user really ran it.
4. Make `app_version` as strict as ADM-033 without breaking old rows. The current grammar is any printable ASCII, so a strict CHECK would fail the migration if old rows hold values such as `1.2`. Keep the permissive column CHECK, since a free-form version is still stored "as sent". The strictness lives in the release link: `reported` matches only when the string equals a stored release version exactly, and the unique index guarantees that. Do not add a strict CHECK on `app_version`.
5. Indexes:
   - `ix_bug_reports_release_id_status` on `(release_id, status)` WHERE `release_id IS NOT NULL`. It serves the per-release open/done counts, the list filter by release, and the FK ON DELETE SET NULL scan.
   - Filtering by version: the existing `ix_bug_reports_app_id_status_created_at_id` (models.py:51) already covers the app-scoped case. Only if the filter is used without an app, add `(app_version)` partial WHERE `app_version IS NOT NULL`. Default: skip it.
6. Backfill: none. Old rows get `release_id NULL, release_match 'none'`. Inferring a release for past reports would be a guess, and the submit-time `deployed_at` is not recorded. Add the new columns in one `op.add_column` each. Use `CREATE INDEX CONCURRENTLY` outside a transaction only if bug_reports is large. At current volume a plain index is fine.
7. The migration's `downgrade()` drops the index, the CHECKs, the FK and both columns. Set `down_revision` to whichever head exists at build, and run `alembic check`.
8. Release-detail counts: use one `SELECT status, count(*) FROM bug_reports WHERE release_id = :id GROUP BY status`. Do not join per row.

**Risks**
- With SET NULL, the release delete (releases/repository.py:232) updates the linked bug_reports rows. This is cheap with the new partial index. Without it, the delete would seq-scan bug_reports.
- An FK to app_releases creates a dependency from bug_reports to releases. This is fine because there is no import cycle at the DB level, but keep the join code in repositories only (R1).
- The inferred link can be misleading when the user's build is stale, so label it clearly in the schema.
- Two heads if ADM-042 also adds a migration. Re-point `down_revision` at build.

**Open questions for Ming**
1. Should a `reported` match against a `pending` or `failed` release link? Default: yes.
2. Add an index on `app_version` for version filters without an app? Default: no.
3. Keep `release_match = 'reported'/'inferred'` on the report after the release is deleted? Default: yes (audit value).
