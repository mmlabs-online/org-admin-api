# Consult — api-platform-engineer — ADM-033 (2026-11-20)

**ADM-033 API consult (api-platform-engineer, read-only)**

Sources read: ticket, ADR-0018, spec §5.13. I did not inspect the code beyond the `bug_reports` router; plan details such as `require_permission` signatures need checking at PLAN.

**Findings**
- The staff routes are specified: `GET /releases`, `GET /releases/{id}`, `DELETE /releases/{id}`, and a live-version read whose shape is left to PLAN. The spec lists filters `app`, `environment`, `status`, `created_from`/`created_to` and `sort=created_at|deployed_at`. The ticket says "date range" without saying which column.
- R9 allows `Page[T]`, UUID ids and plural nouns here. The only exception is the machine write path, which is ADM-034/036 and out of scope.
- The `bug_reports` router already shows the pattern: `require_permission(READ, errors={...})` spread into the route decorator.
- ADM-034, ADM-036 and ADM-038 will reuse the `app_releases` schema, so the contract frozen here binds them.

**Recommendations**
1. **Live version** (AC 4). Prefer a dedicated endpoint over a `sort`+filter on the list. Use `GET /api/v1/apps/{key}/releases/live?environment=production|staging`, or the flatter `GET /api/v1/releases/live?app=<key>&environment=...`.
   - Beware that `/releases/live` collides with `/releases/{id}` unless `live` is declared first or `{id}` is typed as UUID. A UUID-typed `{id}` gives 422 for "live", so declare the literal route first.
   - The flatter route follows the existing `/stats/dashboard` precedent and avoids a new R9 exception. The `/apps/{key}/…` form is allowed, since apps are addressed by key.
   - Return a single document, the same detail or summary schema. Return 404 `release_not_found` when nothing is deployed yet, and 404 for an unknown app. Make `app` and `environment` required and return 422 otherwise.
   - If `app` is omitted, a Page of the live release for every app and environment would be useful for the admin-pan dashboard. That is optional and should be a follow-up.
2. **Sort and order.** Make the Page filters explicit:
   - `sort=created_at|deployed_at` and `order=desc|asc`, default `created_at` desc.
   - Add a secondary sort on `id` for a stable total order, since `deployed_at` is NULL for pending and failed rows. Place NULLs last for desc.
   - Add a `deployed_at` index that supports the live query: partial on `status='deployed'`, columns `(app_id, environment, deployed_at DESC)`.
   - Filter `created_from`/`created_to` are inclusive/exclusive timestamptz. State the semantics in the OpenAPI description.
3. **Schemas** (OpenAPI for admin-pan type generation):
   - `ReleaseSummary` is the list item. It has id, app_key, environment, version, status, git_sha, deployment_pr, has_migrations, deploy_run_url, `changes_count`, and all timestamps. It has no `changes` or `images` bodies. Adding `changes_count` costs one `jsonb_array_length` and helps the UI, so include it.
   - `ReleaseDetail` extends the summary with `images` and `changes`.
   - Use enums `Environment`, `ReleaseStatus` and `ChangeRisk`. Define `ReleaseChange` as `{pr_url, title, ticket_id|null, risk|null}` and `ReleaseImage` as `{name, digest}` now, even though ADM-034 does the validation. That avoids a later breaking rename.
   - Expose `app_key`, not just `app_id`, to match R9's key addressing.
   - Mark nullable fields explicitly, because records created from a report (ADM-036) have null `git_sha`, `deployment_pr` and `has_migrations`.
   - Name the timestamp `deployed_at`, as in the spec. Do not rename it to `last_deployed_at`. It is overwritten on redeploy, so document that in the field description.
4. **Delete.**
   - Return 204 with an empty body, 404 for an unknown id, and write the audit row `release.deleted` in the same transaction. The audit details hold ids only: release id, app key, environment, version, status. No titles or URLs.
   - DELETE is already idempotent from the client's perspective, so no If-Match is needed. The ADM-024 If-Match/ETag pattern is optional and not required here.
5. **Auth.** All four routes declare `require_permission("admin:*")` or the equivalent. Add them to the route-class map in `tests/test_route_auth_classes.py` as Bearer, and add 401 and 403 tests per route.
6. **DB CHECKs for acceptance.** Cover:
   - production version matches `vX.Y.Z` with no leading zeros;
   - staging version is `sha-[0-9a-f]{7}`;
   - `git_sha` is 40 lowercase hex or NULL, and for staging `version = 'sha-' || left(git_sha,7)`;
   - `deployed_at IS NOT NULL` iff `status='deployed'`;
   - `jsonb_typeof = 'array'` for `images` and `changes`, with `jsonb_array_length(changes) <= 200` and `images` ≤ 10;
   - `created_via` has a fixed value set, with names agreed now so ADM-034/036 do not need an enum migration.

   Prefer text + CHECK over a PG enum, so a later value does not require `ALTER TYPE`. `alembic check` can drift on CHECK expressions, so use named constraints written to match the model.
7. **admin-app collision.** Both org-admin-api and admin-pan map to the `admin` app, so `(app, environment, version)` can collide, because the two repos version independently. Recommended resolution: give admin-pan its own app row, for example key `admin-pan`, so the unique key stays intact. The alternative is to add a `component` column to the unique key, but that complicates every route and ADM-036's path. Decide at this PLAN, or no later than ADM-034, since it affects the natural-key route. It is not blocking for ADM-033, which makes only a data-model choice.
8. **ADM-038 forward-compat.** Bug reports link to a release either by reported version (matching `(app, version)`) or by "live at report time". For the second case, index `(app_id, status, deployed_at)` to support "latest deployed_at ≤ T". Because the redeploy overwrites `deployed_at`, an inferred link can drift. Compute at read time and document the drift, per ADR-0018.

**Risks**
- Because `deployed_at` is overwritten on redeploy, history of earlier deploys is lost. This is accepted in ADR-0018, so state it in the field description.
- If a CHECK is more strict than the writer's validation, a 500 may reach CI from ADM-034. The Pydantic validators in ADM-034 must be at least as strict as the CHECKs, and ADM-033 should state this.
- The list at ~100k rows is fine with the proposed indexes. Without them, `ORDER BY deployed_at` with filters will scan.
- A route-order collision for `/releases/live` versus `/releases/{id}` (see recommendation 1).
- A migration is a High path (the ticket is already gated at plan-approval).

**Open questions**
1. Live-version route shape: dedicated endpoint (recommended, flat `/releases/live`) versus list filter?
2. Does `created_from`/`created_to` filter on `created_at` only, or also on `deployed_at` when it is the sort key?
3. The `admin`/admin-pan collision: separate app row (recommended) or another scheme?
4. Final `created_via` values (for example `app_write`, `deploy_report`) so later tickets do not need a migration.
5. Should list items expose `changes_count`?
