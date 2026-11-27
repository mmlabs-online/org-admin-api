Status: BLOCKED

> Every question below has a default. If Ming approves this plan as written in the batch 2026-11-27-1 approval round,
> the defaults apply and the plan is READY as is. It needs that approval anyway, because it edits `app/modules/rbac/**` (High).

# Plan: ADM-026, Organizations and apps lists: name sort collation and `member_count` sort

## Scope summary
Ticket goal (ticket.md): "`sort=name` orders Vietnamese and mixed-case names as staff expect (today it follows the
database collation: uppercase before lowercase, accented letters last), and apps can be sorted by `member_count`
(422 today)." Consumer: admin-pan PAN-029.

Scope sources:
- `docs/spec-v1.md:76`: "> 2026-11-18 (ADM-020): staff list filters `status`, `q` (name or slug, trimmed 1..100,
  empty → 422) and `sort` (allow-list, default `-created_at`, `id` tie-breaker)."
- `docs/spec-v1.md:53`: "> 2026-11-18 (USR-18): staff apps list filters `status`, `join_policy`, `org_mode`, `q`
  (key or name, trimmed 1..100, empty → 422) and `sort` (allow-list, default `key`, `id` tie-breaker)."
- `docs/spec-v1.md:118` (§5.9, OQ5): "**Per-app member count** ... a member is a distinct user with at least one
  non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified
  email still counts); the `admin` app shows its count (= staff)."
- CLAUDE.md R9: "lists return `Page[T]` (`limit` ≤ 100, `offset`) ... No breaking changes inside v1."
- `work/decisions.md`: nothing on sort collation or ADM-026 (grep: no match).

What changes: the order inside `name`/`-name` on two lists, plus two new allow-listed sort values on the apps list.
No new endpoint, no schema change, no change to the response shape.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `name`/`-name` on organizations and apps are case-insensitive and locale-aware, keep the `id` tie-break, and are documented. No migration, because no collation or index is created. | `tests/test_name_sort.py › test_orgs_name_sort_case_and_accents[name / -name]`, `› test_apps_name_sort_case_and_accents[name / -name]`, `› test_orgs_name_case_only_tie_broken_by_id[name / -name]`, `› test_apps_name_case_only_tie_broken_by_id[name / -name]`; existing `test_list_sort_tie_breaker_is_id_asc` (orgs) and `test_list_apps_sort_tie_breaker_is_id_asc` (apps) still green. Router `Query(description=…)` text in `/openapi.json` names the collation. `git diff --stat migrations/` is empty and `uv run alembic check` is clean. |
| 2 | Apps `sort` accepts `member_count`/`-member_count` without a per-row count. | `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count[member_count / -member_count]`, `› test_list_apps_sort_member_count_ties_by_id[...]`, `› test_list_apps_sort_member_count_excludes_expired_and_inactive`, `› test_list_apps_sort_member_count_query_count` (statement count does not grow with the number of apps). The reviewer reads the SQL: one grouped subquery with a LEFT JOIN, no correlated subquery. |
| 3 | Existing sort values and defaults are unchanged (R9). | `OrganizationSort` and `AppSort` keep every current value. Defaults stay `-created_at` and `key`. Existing `test_list_default_order_unchanged`, `test_list_apps_default_order_unchanged`, `test_list_sort_orders` and `test_list_apps_sort_orders` pass without edits. `sort=member_count` on **organizations** is still 422. |
| 4 | Tests cover mixed-case and accented names in the expected order, and the `member_count` sort with ties broken by `id`. | The tests in rows 1 and 2, plus `tests/test_name_sort.py › test_icu_collation_available`, which fails loudly when the test DB has no `und-x-icu`. |
| 5 | `make check` is green. | `make check` |

## Ming verification after merge
- **Before merge (read-only, on prod):** run `SELECT 1 FROM pg_collation WHERE collname = 'und-x-icu';` and
  `SHOW server_encoding;` against the production `org_admin` database. Both must succeed: one row, and `UTF8`. If
  the query returns no row, do not merge, because every `sort=name`/`-name` request would answer 500. Answer Q2b
  (fallback) instead. This is read-only and does not touch Alembic or the CLI (loop.config Guardrails).
- After deploy: in admin-pan, open the Organizations and Apps screens, sort by name, and check that a lowercase
  name and an accented name (e.g. "Ánh…") sort among their letters. Then sort Apps by Members in both directions.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/repository.py` | Add `NAME_SORT_COLLATION = "und-x-icu"` and `name_sort_key(column)`. Add a module-level `member_count_select()` that holds the current grouped query from `member_counts` (:1039-1048, unchanged logic). `member_counts` calls it and adds its `in_` filter. `_APP_SORT_COLUMNS` (:79) becomes `_APP_SORT_EXPRESSIONS` (`name` → `name_sort_key(App.name)`). `list_apps` (:150-178) builds its own order clauses and adds the LEFT JOIN for `member_count`. | AC1, AC2 | **High** (`app/modules/rbac/**`) |
| `app/modules/rbac/schemas.py` | `AppSort` (:30) adds `"member_count", "-member_count"`. `DEFAULT_APP_SORT` stays unchanged. | AC2, AC3 | **High** |
| `app/modules/rbac/router.py` | Update the `sort` `Query` description (:89-93). | AC1, AC2 (documented) | **High** |
| `app/modules/organizations/repository.py` | `_SORT_COLUMNS` (:16-20) becomes `_SORT_EXPRESSIONS` (`name` → `name_sort_key(Organization.name)`, imported from `rbac.repository`, which this file already imports at :14). Add a local `_order_by(sort)`. `list` (:54) uses it. Drop the `sort_clauses` import. | AC1 | no |
| `app/modules/organizations/router.py` | Update the `sort` `Query` description (:76-80). | AC1 (documented) | no |
| `tests/test_name_sort.py` (new) | ICU guard, accent/case order and case-only ties for orgs and apps, plus pure SQL-compile checks. | AC1, AC4 | no |
| `tests/test_rbac_apps_api.py` | Remove `"sort=member_count"` from `test_list_apps_rejects_bad_query` (:1196). Rewrite `test_apps_sort_allow_list_matches_repository_mapping` (:1016-1018) for the new mapping. Add the `member_count` sort tests. Update imports (:12, `_APP_SORT_COLUMNS`). | AC2, AC3 | no |
| `tests/test_organizations.py` | Rewrite `test_sort_allow_list_matches_repository_mapping` (:153-155) against `_SORT_EXPRESSIONS` / `_order_by`. Drop the `sort_clauses` import (:11). Add `"sort=member_count"` → 422 to the existing bad-query test, or as a one-line test if there is none. | AC3 | no |
| `tests/test_rbac_member_counts.py` | No edit expected. It must stay green after `member_counts` is refactored. | AC2 regression | no |
| `docs/spec-v1.md` | Add a dated note under the lines at :53 and :76, for example: "> 2026-11-27 (ADM-026): `name` sorts by `lower(name)` in the ICU root collation (`und-x-icu`), case-insensitive, accents next to their base letter, `id` tie-break; apps also sort by `member_count`/`-member_count` (§5.9 definition, `id` tie-break)." | AC1 "documented"; spec is the scope source | no |

`app/core/listing.py` is **not** touched (default of Q3). After this change `sort_clauses` has no callers in `app/`.
It stays, along with its tests in `tests/test_listing.py`, and goes to follow-ups (remove it or generalise it to
`ColumnElement`, which is a High change).

## Data model / migration
None. `und-x-icu` is a predefined collation that initdb creates when Postgres is built with ICU, so no
`CREATE COLLATION` is needed. No index, because both tables are small and the sort is an in-memory top-N (consult
database-optimizer, rec. 2). `alembic check` must stay clean.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/organizations` | `sort` ∈ unchanged set {`created_at`, `-created_at`, `name`, `-name`, `slug`, `-slug`}, default `-created_at` | `Page[OrganizationRead]`, unchanged | 200, 401, 403, 422 | unchanged (existing `require_permission`) |
| GET | `/api/v1/apps` | `sort` ∈ {`key`, `-key`, `name`, `-name`, `created_at`, `-created_at`, **`member_count`, `-member_count`**}, default `key` | `Page[AppRead]`, unchanged (`member_count` already present) | 200, 401, 403, 422 | `admin:apps:read` (unchanged) |

Semantics:
- `name`: `ORDER BY lower(name COLLATE "und-x-icu") ASC|DESC, id ASC`.
- `member_count`: `ORDER BY coalesce(mc.member_count, 0) ASC|DESC, apps.id ASC`, where `mc` is the OQ5 grouped
  count, LEFT JOINed on `mc.app_id = apps.id`. `total` is counted without the join.

New description texts (router):
- Orgs: "One of created_at, -created_at, name, -name, slug, -slug ('-' = descending); id ascending breaks ties.
  name is case-insensitive and locale-aware (ICU root collation): accented letters sort next to their base letter."
- Apps: "One of key, -key, name, -name, created_at, -created_at, member_count, -member_count ('-' = descending); id
  ascending breaks ties. name is case-insensitive and locale-aware (ICU root collation). member_count uses the
  same member definition as the member_count field."

Breaking change? No new or removed values. Only the order inside `name` changes: a fix for the requested behaviour
(Q1), noted in the PR description and the spec note. Pagination across the deploy moment may shift once, which is
cosmetic.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_name_sort.py › test_icu_collation_available` | `SELECT 1 FROM pg_collation WHERE collname = 'und-x-icu'` returns a row, and `SHOW server_encoding` = `UTF8`. Assertion message: "Postgres test DB lacks ICU collation und-x-icu (or is not UTF8): sort=name would 500 — see ADM-026 plan". Loud, not skipped. |
| `tests/test_name_sort.py › test_name_sort_key_compiles_to_icu_lower` | Pure: compile `name_sort_key(Organization.name)` with the postgresql dialect. The SQL contains `lower(organizations.name COLLATE "und-x-icu")`. The collation is a literal constant, never input. |
| `tests/test_name_sort.py › test_orgs_name_sort_case_and_accents[name]` / `[-name]` | Seed names `Beta`, `alpha`, `Ánh`, `Zed`, `đức`, `Dung`. Expected asc: `alpha, Ánh, Beta, Dung, đức, Zed` (`-name` is the exact reverse). Under today's byte order this fails (`Beta, Dung, Zed, alpha, Ánh, đức`), so it is a real regression test. |
| `tests/test_name_sort.py › test_apps_name_sort_case_and_accents[name]` / `[-name]` | Same names on apps. Filter out `admin` the way `test_list_apps_sort_orders` does (:1129). |
| `tests/test_name_sort.py › test_orgs_name_case_only_tie_broken_by_id[name]` / `[-name]` | `Same` and `same` (and `ân`/`Ân`) are ordered by `id` ascending in both directions, which shows that `lower()` folds non-ASCII case under ICU. |
| `tests/test_name_sort.py › test_apps_name_case_only_tie_broken_by_id[name]` / `[-name]` | The same for apps. |
| `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count[member_count]` / `[-member_count]` | Apps with 0, 1 and 3 members (plus `admin`, whose count is the seeded staff). Ascending puts zero-member apps first. Descending puts them last. Each item's `member_count` field matches its position. |
| `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count_ties_by_id[member_count]` / `[-member_count]` | Two apps with equal counts (both 0, and both 2) are ordered by `id` ascending in both directions. |
| `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count_excludes_expired_and_inactive` | An expired assignment and a `disabled` user do not raise an app's rank. Uses the existing helpers `make_role`, `assign` and `make_login_user`, as at :1161-1164. |
| `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count_with_filters_and_paging` | `status=active&q=…&sort=-member_count&limit=1&offset=1`: `total` matches the filtered count (it ignores the join), and the page is the second app by count. |
| `tests/test_rbac_apps_api.py › test_list_apps_sort_member_count_query_count` | Copy of `test_list_apps_query_count_with_filters` (:1174-1184) with `sort=-member_count`. The statement count is equal for 1 and 5 apps (3 statements). `role_assignments` appears in exactly 2 statements: the sorted list and the page counts. No N+1. |
| `tests/test_rbac_apps_api.py › test_apps_sort_allow_list_matches_repository_mapping` (rewritten) | `set(get_args(AppSort)) == {k, f"-{k}" for k in _APP_SORT_EXPRESSIONS} ∪ {"member_count", "-member_count"}`. |
| `tests/test_organizations.py › test_sort_allow_list_matches_repository_mapping` (rewritten) | `set(get_args(OrganizationSort)) == {k, f"-{k}" for k in _SORT_EXPRESSIONS}`, and `_order_by(v)` returns 2 clauses for each value. |
| `tests/test_organizations.py` bad-query test, extended | `sort=member_count` on orgs → 422 `validation_error`. |
| existing `test_list_apps_rejects_bad_query` | Still 422 for `bogus`, `id`, `KEY`. `member_count` is removed from its list. |

## Approach
0. **Implementer environment check first** (you cannot skip this). After `make db-up`, run
   `docker compose exec <db service> psql -U postgres -d org_admin_test -c "SELECT collname FROM pg_collation WHERE collname IN ('und-x-icu','vi-x-icu')"` and `-c "SHOW server_encoding"`. Record the output in
   `impl-log.md`. Without Docker (loop.config "Prepare": `service postgresql start`, local PG 16), run the same
   queries with `psql`. If `und-x-icu` is missing locally, stop and record it in the impl-log. Do not switch to a
   different collation silently: that is Q2b, Ming's call. CI uses `postgres:17-alpine` (`.github/workflows/ci.yml:13`),
   which is normally built with ICU. `test_icu_collation_available` proves it in CI.
1. `rbac/repository.py`:
   - `NAME_SORT_COLLATION: Final = "und-x-icu"`.
   - `def name_sort_key(column: QueryableAttribute[str]) -> ColumnElement[str]: return func.lower(column.collate(NAME_SORT_COLLATION))`.
     Collate **inside** `lower()`, so both the case mapping and the ordering use ICU. The result of `lower()`
     inherits the input's collation. This is more robust than `lower(name) COLLATE …` when the database default
     collation is `C`/musl, where `lower()` would fold ASCII only (`Đ` would stay `Đ`). The case-only-tie test with
     `Ân`/`ân` checks this. Docstring: "Literal constant; never build a collation from input."
   - `def member_count_select() -> Select[tuple[uuid.UUID, int]]`: the body of `member_counts` (:1039-1048) with
     labelled columns `app_id` and `member_count`. Docstring: the OQ5 definition, "the single definition; ADM-015,
     ADM-016 and ADM-026 use it; do not duplicate".
   - `member_counts` becomes `stmt = member_count_select()` plus the existing `in_` filter. Behaviour is unchanged
     (`tests/test_rbac_member_counts.py` guards it).
   - `_APP_SORT_EXPRESSIONS: dict[str, ColumnElement[Any]] = {"key": App.key, "name": name_sort_key(App.name), "created_at": App.created_at}`.
   - In `list_apps`, keep the `conditions` and `total` query as they are. Then:
     `key = sort.removeprefix("-")`. If `key == "member_count"`:
     `mc = member_count_select().subquery("mc")`, `stmt = stmt.outerjoin(mc, mc.c.app_id == App.id)`,
     `primary = func.coalesce(mc.c.member_count, 0)`. Otherwise use `_APP_SORT_EXPRESSIONS[key]`. Then
     `.order_by(primary.desc() if sort.startswith("-") else primary.asc(), App.id.asc())`. An unknown key cannot
     reach this point because the `Literal` gives a 422. A `KeyError` would be a bug, so keep the code simple and
     add no fallback.
   - Leave the router's per-page `member_counts` call (:107) as it is. Re-selecting the count from the join would
     change the router/service contract for no gain (R7). It is 3 statements total and stays constant.
2. `rbac/schemas.py`: extend `AppSort`. `service.py:358` only passes the value through, so it needs no change.
3. `organizations/repository.py`: `_SORT_EXPRESSIONS` with `name_sort_key(Organization.name)`, and
   `_order_by(sort: OrganizationSort) -> list[ColumnElement[Any]]` (primary asc/desc, then `Organization.id.asc()`).
4. Update both router descriptions as in "API contract". Add the spec notes.
5. Write and update the tests above, then run `make check` and `uv run alembic check`.

Rules: R1 (ordering stays in repositories, routers only change descriptions), R2 (no schema change → no migration,
`alembic check` clean), R3/R8 (no new endpoint and no auth change; the 403 test on apps already exists at :1218), R7
(`sort_clauses` cleanup → follow-up, not in this diff), R9 (values and defaults unchanged; the new values are
additive; `Page[T]` unchanged). mypy `--strict`: annotate the mappings as `dict[str, ColumnElement[Any]]`.
`QueryableAttribute` and `ColumnElement` both have `.asc()`/`.desc()`.

Size: about 60 changed lines in app code and about 200 in tests and docs. That is one cycle, so no split is needed
for size.

**Tier:** **High**. `app/modules/rbac/{repository,schemas,router}.py` match `app/modules/rbac/**`. On its own, the
orgs half (`app/modules/organizations/**`, tests, docs) would be Medium. With one PR the whole PR is High: it stops
at PLAN for Ming's approval, and `--auto-merge` never merges it. `app/core/` is untouched by design (Q3), so the
reviewer and appsec trigger fire only because of `**/rbac*`.

## Specialist input used
- api-platform-engineer rec. 1 (build clauses locally, keep `app/core` untouched): **adopted**. One deviation:
  `name_sort_key` is shared from `rbac.repository` instead of written twice, because `organizations/repository.py`
  already imports from there (:14) and the reverse import would be circular.
- api-platform-engineer rec. 1 ("then raw `col`" as a second key): **rejected** in favour of the database-optimizer
  Q3 default (`lower(name)` then `id`). It keeps the `id` tie-break the ticket and spec name. Q3 asks Ming.
- api rec. 2 / db rec. 6 (R9 as a fix, plus documented descriptions): **adopted**. The repo has no changelog file,
  so the spec note and PR description serve as the changelog.
- Both consults on `und-x-icu`, no `CREATE COLLATION`, no nondeterministic collation, no index: **adopted**.
- database-optimizer rec. 1 (`lower(name) COLLATE "und-x-icu"`): **adopted with a change**, writing
  `lower(name COLLATE "und-x-icu")` so `lower()` uses ICU case mapping too (see Approach 1).
- database-optimizer rec. 3 (extend `sort_clauses` in `app/core`): **rejected** (Q3 default). It would add a second
  High area for no functional gain.
- Both consults on `member_count` through a shared grouped subquery, LEFT JOIN with `coalesce(…, 0)`, `total`
  without the join, and a "do not duplicate" docstring: **adopted** (`member_count_select()`).
- Test sets from both consults: **adopted**, merged into one name set that also fails under today's byte order.
- database-optimizer risk (ICU missing in prod → 500): **adopted** as the pre-merge Ming check and the loud test.
- database-optimizer risk (PG16 vs PG17 CLDR differences): accepted. The test names use only common letters
  (a/á, d/đ, case), which are stable across ICU versions.

## Split proposal
None for size. One optional split is offered in Q4: (a) ADM-026a, orgs `name` collation, **Medium**, which Ming
could "cho merge" in this batch; (b) ADM-026b, apps `name` collation and `member_count` sort, **High**. Default: one
PR, High. Splitting makes 2 review cycles and 2 PRs for about 60 lines of app code. It also puts the shared
`name_sort_key` in a High file that the Medium half would import, so 026a would have to carry its own copy until
026b lands. That is not worth it for a P3 ticket.

## Blocking questions for Ming
1. **R9: is the `name` order change acceptable as a fix inside v1** (same values and defaults, only the order inside
   `name`/`-name` changes; there is no version bump)? Default: **yes**, as a fix with a spec note and a PR note.
2. **Collation: `und-x-icu` (CLDR root) or `vi-x-icu` (Vietnamese tailoring)?** Default: **`und-x-icu`**. Root
   already places á/ă/â next to a and đ after d, and it is neutral for non-Vietnamese names. `vi-x-icu` mainly
   changes the order of tone marks.
   2b. If the prod check (see "Ming verification") shows that ICU is missing, the fallback is `lower(name)` in the
   default collation (case-insensitive, but accents still sort last). Default: **do not merge**, and come back for a
   decision.
3. **Stable order for case-only ties** (`alice` vs `Alice`): order them by `id` only, or add a case key before
   `id`? Default: **`lower(name)` then `id`**, which matches the documented `id` tie-break.
   3b. Keep `app/core/listing.py` untouched and build clauses locally? Default: **yes** (consult api Q3).
4. **One PR (High) or split into orgs (Medium) + apps (High)?** Default: **one PR, High** (see Split proposal).
5. **Ship the `member_count` sort now?** (The ticket allows leaving it out and documenting that.) Default: **yes,
   now**. It is done with one grouped subquery and no per-row count.

## Assumptions
- Prod Postgres is built with ICU and uses UTF8 encoding (to be confirmed by Ming's read-only check before merge).
  `postgres:17-alpine` in docker-compose and CI normally includes ICU, and `test_icu_collation_available` proves it
  in CI.
- `sort=name` is used only by admin-pan (PAN-029 asked for this change), so the order change does not surprise
  other consumers.
- Apps and organizations stay small (well under 10^5 rows), so there is no functional index on `lower(name …)` and
  no index on the subquery.
- The `member_count` value shown on each item and the sort key come from the same `member_count_select()` and the
  same `now()` within one request transaction, so they agree.
- **Hotspots (expect a rebase):** batch peers USR-26 (role definitions: assignment count), USR-27 (role assignments:
  names) and USR-25 are `PLANNING` and are likely to edit `app/modules/rbac/repository.py`, `schemas.py` and
  `router.py`, as well as `tests/test_rbac_apps_api.py`. USR-26's "assignment count" may want to reuse
  `member_count_select()` or touch the area around `member_counts` (:1030). This plan keeps its edits in
  `list_apps` (:150-178), `_APP_SORT_COLUMNS` (:79) and `member_counts` (:1030-1051).
- Follow-ups (not in this diff): remove `app/core/listing.py::sort_clauses` or generalise it to `ColumnElement`
  (High, it has no callers after this ticket); consider whether other name-sorted lists (roles, users) should use
  `name_sort_key`.
