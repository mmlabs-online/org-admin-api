# Consult — database-optimizer — ADM-033 (2026-11-20)

ADM-033 DB consult (read-only; based on the ticket, ADR-0018, spec §5.13, the ADM-028 migration as the style reference, and the goals of ADM-034/036/038).

FINDINGS
- The table design is clear. The only open DB questions are the index set, the CHECK shape, the "live" query, the FK behaviour and the admin collision.
- Follow the ADM-028 migration conventions: `op.f("ck_…")` names, an explicit FK name, `Uuid` id, `server_default now()` timestamps, and a `downgrade()` that drops indexes and then the table.
- ADM-038 adds `bug_reports.release_id` with `ON DELETE SET NULL`. It also needs the "latest deployed per (app, environment)" lookup and the (app, environment, version) lookup.
- ADM-034 and ADM-036 need an atomic upsert on the unique key. Design the constraints now so they need no ADM-033 changes.

RECOMMENDATIONS
1. Columns and types
   - `app_id` Uuid NOT NULL, FK `apps.id` ON DELETE RESTRICT (keep history, as in ADM-028).
   - `environment` String(10), `version` String(16), `status` String(10) with server_default 'pending', `git_sha` String(40) NULL.
   - `git_sha` is nullable because a record created from a report has none (ADR-0018 §6). Do not make it NOT NULL.
   - `images` and `changes` are `JSONB NOT NULL DEFAULT '[]'`.
   - `has_migrations` is boolean NULL. A record created from a report has no value, so NULL means "unknown".
   - `deployment_pr` String(255), `deploy_run_url` String(255), `created_via` String(10), plus the timestamps.
   - `created_via` values: `app_write` | `deploy_report`. ADM-033 has no write route, so seeds use either value. CHECK it as IN ('app','deployer') or similar, and decide the values now so ADM-034 and ADM-036 need no migration.
2. CHECKs (all named)
   - `environment IN ('production','staging')`; `status IN ('pending','deployed','failed')`.
   - Version per environment, with a single combined CHECK:
     `(environment='production' AND version ~ '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') OR (environment='staging' AND version ~ '^sha-[0-9a-f]{7}$')`
   - `git_sha IS NULL OR git_sha ~ '^[0-9a-f]{40}$'`.
   - Staging consistency: `environment <> 'staging' OR git_sha IS NULL OR version = 'sha-' || left(git_sha,7)`. The NULL branch is needed because of the report-created record.
   - `(status='deployed') = (deployed_at IS NOT NULL)`, which matches the ticket's "set iff deployed". It also means a `failed` record has no `deployed_at`.
   - `jsonb_typeof(images)='array' AND jsonb_array_length(images) <= 10`.
   - `jsonb_typeof(changes)='array' AND jsonb_array_length(changes) <= 200`.
   - Optionally a DB byte cap such as `pg_column_size(changes) < 262144`. The app layer enforces the real cap.
   - `deployment_pr` and `deploy_run_url` regexes are cheap to add as `IS NULL OR ~ …`, in the style of ADM-028 `page_url`. They are defence in depth.
3. Unique and indexes
   - `UNIQUE (app_id, environment, version)`, named, as a constraint. ADM-034/036 use `INSERT … ON CONFLICT (app_id, environment, version) DO UPDATE`, and this serves ADM-038 reported-version matching.
   - List, newest first: `(created_at DESC, id DESC)`. This gives a stable keyset or offset order with an id tie-break.
   - For `sort=deployed_at`: `(deployed_at DESC, id DESC) WHERE deployed_at IS NOT NULL`. In ORDER BY, state NULLS LAST explicitly, or filter to deployed.
   - App-filtered lists: `(app_id, created_at DESC, id DESC)`.
   - Live version: partial index `(app_id, environment, deployed_at DESC, id DESC) WHERE status='deployed'`. The query is `ORDER BY deployed_at DESC, id DESC LIMIT 1`, which gives a deterministic tie-break.
   - Do not add separate indexes for status or environment alone. Their cardinality is low and volume is small (~100k rows).
   - No extra `app_id` index is needed, because the unique and composite indexes lead with `app_id`.
   - `CREATE INDEX CONCURRENTLY` is not needed on a new empty table.
4. Live read shape
   - Prefer a dedicated `GET /api/v1/releases/live?app=<key>&environment=…`, returning the release summary or 404.
   - A second option is `?app=` only, which returns both environments as a small document, one entry per environment (null if none).
   - Register `/releases/live` before `/{id}`, or the path-parameter route will shadow it.
   - It is a single document, not `Page[T]`, so R9 needs an exception entry.
   - A cheaper option that needs no R9 change is `list ?status=deployed&sort=deployed_at&limit=1`. This works, but it is clumsy for clients.
   - The `/api/v1/stats/dashboard` precedent supports the dedicated route.
   - Do not store a `is_live` flag. It would drift on rollback, and ADR-0018 §5 deliberately derives it.
5. List query
   - Summary columns only. Never load `changes` or `images`.
   - Add `jsonb_array_length(changes)` as `change_count`, which is cheap and useful for the UI.
   - Use `defer()` or an explicit column list in the repository, not SELECT *.
   - `Page[T]` total: a count(*) with the same filters is fine at this scale.
   - Add an `app` filter by key via a join on `apps.key`. Resolve an unknown key to 404 or an empty page, and decide which at PLAN. I suggest an empty page for consistency with other lists; check what the existing modules do.
   - Date range filters `created_from` / `created_to` are inclusive/exclusive as decided at PLAN. Use `>=` and `<`.
6. DELETE
   - Hard delete with RESTRICT-safe semantics. ADM-038's `release_id` is `SET NULL`, so deleting is safe later.
   - Audit `release.deleted` in the same transaction with ids only (app key, environment, version, release id, status).
   - No cascade is needed from this table.
7. Admin app collision (needs a decision at PLAN)
   - Recommended: record both products under `admin` with a version prefix is not possible, because the grammar is strict.
   - Cleanest option: give admin-pan its own app row (key e.g. `admin-pan`). Releases then key off `apps.id` unchanged, with no schema change here.
   - Alternative: add a nullable `component` column and put it in the unique key. Rejected: it is more complex, and the nullable unique is awkward.
   - ADM-033 itself is unaffected, but the decision must be recorded before ADM-034. Registering an `admin-pan` app also needs a check that no seed or list code treats a second app row as an `admin` app (the `admin` app is special in R3, role catalog and manifest).
8. Tests
   - Migration round-trip: upgrade, downgrade, upgrade.
   - Direct inserts that violate each CHECK, asserting IntegrityError for: bad version per environment, deployed without `deployed_at`, failed with `deployed_at`, a staging mismatch with `git_sha`, and oversized arrays.
   - Live seeding with a rollback case: v1.2.0 deployed at t1, v1.3.0 at t2, then v1.2.0 redeployed at t3, so live = v1.2.0. Also a failed record with a later `created_at`, and the same version in staging and production, each of which must not leak into the other's live answer.
   - Seeds go through the repository, per the ticket.

RISKS
- Models with CHECKs that use regexes and `||` can show `alembic check` drift if the SQL text differs from the model. Write them identically in the model and the migration.
- A JSONB `jsonb_array_length` CHECK on a 256 KiB column costs a little on every write. This is acceptable.
- If `created_via` values or the NULL semantics of `git_sha` and `has_migrations` are not settled now, ADM-034/036 will need a migration.
- Route order `/releases/live` vs `/{id}` is a common 422 trap if declared after.
- The R9 exception list needs the live read. `CLAUDE.md` is High path (docs), so flag it for Ming.

OPEN QUESTIONS
1. `created_via` values: confirm names (suggest `app` / `deployer`).
2. Admin-pan collision: separate app row (recommended) vs another approach.
3. Live read shape: dedicated route (recommended), returning both environments or one per call?
4. Unknown `app` filter key: 404 or empty page?
5. Whether `has_migrations` NULL (unknown) is acceptable in the API schema, or the API should expose it as an explicit nullable boolean (suggested).
