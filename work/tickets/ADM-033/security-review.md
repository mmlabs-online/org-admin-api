# Security review — ADM-033 (appsec-engineer)

## Attempt 1 (commit 901d91b)

ADM-033 security review, attempt 1. I read the diff, plan.md and ADR-0021. As asked, I did not run pytest or alembic, and I edited nothing.

**Verdict: PASS from the security side.** There are no blockers. There is one should-fix and four notes.

### 🟢 Confirmed OK
- **R3 Bearer `admin:*` on all four routes.** `app/modules/releases/router.py:450` puts `Depends(current_admin)` on the whole router, which verifies the JWT with `aud=admin`. Each route also declares `**require_permission("admin:*")` (lines 473, 511, 533, 543), and that call adds a `RequirePermission` dependency, so the permission is actually enforced (`app/modules/admin_auth/deps.py:146-150`). The registry has all four routes (`tests/test_route_auth_classes.py:124-127`). Tests cover 401 with no token and 403 for a non-wildcard staff user on all four routes, including `/live` (`tests/test_releases_api.py:52-70`). No route takes a machine credential.
- **`/live` vs `/{release_id}` ordering.** `/live` is declared first (`router.py:502-503`), and `release_id` is typed `uuid.UUID`, so even a wrong order would give a 422, not a data leak. `test_live_route_not_shadowed` (`tests/test_releases_api.py:295`) covers it.
- **Migration CHECKs.** Every regex and bound is a fixed module constant (`app/modules/releases/models.py:119-128`) put into DDL at definition time. No request value reaches DDL, and the constants contain no `'`. The migration has the same text as literals (`migrations/versions/20261120_89fd8d667a25_adm033_app_releases.py:779-818`).
  - The URL regexes are anchored with `^…$`, require `https://`, escape the dot in `github\.com`, and pin the path to `mmlabs-online/deployment/pull/N` or `/actions/runs/N(/attempts/N)?`. That blocks `javascript:`, other hosts and userinfo/`@` tricks.
  - The git sha regex is `^[0-9a-f]{40}$`, and the staging version is tied to the sha.
- **jsonb bounds.** Item counts are capped: images ≤ 10, changes ≤ 200. The CHECKs use `CASE` with `jsonb_typeof`, so a non-array fails the CHECK cleanly instead of raising a DataError (`models.py:152-161`). The byte cap (256 KiB) is left to the ADM-034 write path, as the plan records (plan.md:243).
- **List queries.** Both use an explicit column list that never selects `images` or `changes`; the change count comes from `jsonb_array_length` (`app/modules/releases/repository.py:272-289`).
  - `limit` is between 1 and 100 and `offset` ≥ 0 (`app/core/pagination.py:13-14`).
  - Filters are bound through the ORM (no raw SQL), `app` is pattern-checked against `APP_KEY_RE` (`router.py:454`), and `environment`, `status` and `sort` are enums.
  - `/live` uses DISTINCT ON in a subquery plus limit/offset (`repository.py:375-397`). Nothing loads an unbounded result set.
- **Delete audit.** The delete is a single atomic `DELETE … RETURNING` (`repository.py:399-414`). The audit row holds only environment, version, status and changes_count, with target `(release, id)` and the app key, written in the same session (`app/modules/releases/service.py:676-692`). No titles or URLs, as ADR-0021 §8 requires. The log line has only the release id and the actor's user id (`service.py:693`). A test checks that the stored row contains no title and no `https://` (`tests/test_releases_api.py:311`), and the unknown-id 404 writes no row (line 335).
- **PII and secrets.** The schema has no author, email or login fields (`app/modules/releases/schemas.py:590-627`). The 404 error says only "Release not found". `app_id` uses FK RESTRICT, so deleting an app cannot silently drop release history.

### 🟡 Should-fix
- **`app/modules/releases/schemas.py:596` (`ReleaseChange.pr_url`).** The database does not check the shape of `changes[*].pr_url`. The CHECKs bound only the array length, and ADM-033 has no write path. If ADM-034's validation ever misses a case, a `javascript:` or other non-GitHub URL would be returned as-is, and admin-pan would render it as a link: stored XSS in a staff session that holds `admin:*`.
  - **Fix:** give `pr_url` a field description like `bug_reports/schemas.py:16`. For example: `pr_url: str = Field(description="Untrusted URL supplied by CI. https://github.com/... only (enforced at write, ADM-034); re-check the scheme before rendering it as a link.")`.
  - This is a docs-only change, but OpenAPI generates admin-pan's types, so the warning reaches the UI developers.

### 💭 Notes / Follow-ups (out of scope, for ADM-034 / ADM-036)
1. **ADM-034 write validation** must be at least as strict as the read assumptions:
   - `pr_url`: the same anchored `https://github\.com/<org>/<repo>/pull/N` style regex.
   - `title`: a length cap, and rejection of control characters and bidi-override characters.
   - `ticket_id`: a pattern.
   - `images[*].digest`: `^sha256:[0-9a-f]{64}$`; `images[*].name`: a pattern.
   - A whole-body byte cap of 256 KiB, enforced before parsing.

   Consider a DB backstop such as `CHECK (pg_column_size(changes) <= 262144)` in ADM-034's migration.
2. **`router.py:464-465` (`_detail`)** builds `ReleaseImage(**i)` and `ReleaseChange(**c)` from stored jsonb. A row with a missing key or a wrong type would give a 500 rather than a clean error. This is not exploitable now, because only staff and the future validated writers can write rows, but ADM-034 should make sure the stored shape always matches these models.
3. **`app_key` filter on `/live` and the list.** An unknown key returns an empty page instead of a 404. That is fine for `admin:*` callers, since app keys are not secret from staff.
4. **The rate limit and CORS posture** for the future `mmdk_` deployer credential (ADM-036) and the app-credential `releases` scope (ADM-034) are not part of this diff. Make sure their registry entries in `tests/test_route_auth_classes.py` keep them off these four read and delete routes.
