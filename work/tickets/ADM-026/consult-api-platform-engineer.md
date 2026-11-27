# Consult — api-platform-engineer — ADM-026 (2026-11-27)

**Findings**
- `app/core/listing.py:45-59` `sort_clauses(sort, columns, tiebreaker)` takes `QueryableAttribute` only, builds `asc()/desc()` + ascending tiebreaker — can't take `lower(name)` or a collated expression without editing app/core (High).
- Orgs: `organizations/repository.py:16` `_SORT_COLUMNS`, :54 `sort_clauses(..., Organization.id)`; sort Literal `schemas.py:12` (default `-created_at`); `name` plain `String(255)` (models.py:33); :140 is a separate non-list ordering.
- Apps: `rbac/repository.py:79` `_APP_SORT_COLUMNS`, :174; `AppSort` `rbac/schemas.py:30` (default `key`); App.name `rbac/models.py:113`.
- `member_count` isn't a column: `member_counts` (repository.py:1030-1051) is a grouped aggregate (distinct active users, non-expired assignments, Role → User); apps without members absent (= 0); router (rbac/router.py:107) attaches counts after paging.
- A correlated subquery per row is what the ticket forbids; an outer join to the grouped subquery with `coalesce(cnt, 0)` is the clean way (or defer, as the ticket allows).

**Recommendations**
1. Keep out of app/core: build order clauses locally in each module repository; for `name` use `func.lower(col)` or `col.collate(...)` primary, then raw `col`, then `id` asc. Touch listing.py only if Ming wants one shared helper (High plan).
2. R9: sort values and defaults unchanged; only the order within `name` changes (requested fix) — changelog + OpenAPI/field description; Ming confirms.
3. Collation: built-in `COLLATE "und-x-icu"` (no `CREATE COLLATION`, no migration); verify ICU in prod and test images; `vi-x-icu` optional. No custom nondeterministic collation (needs a migration, R2). No index (small lists).
4. `member_count` / `-member_count` in `AppSort`: outer-join a shared `_member_count_subq()` (also used by `member_counts`); order by `coalesce(cnt,0)` then `App.id` asc; `total` counted before the join.
5. Update the Literal enums (OpenAPI regenerates) and router Query descriptions.
6. Tests: "alpha", "Beta", "Álpha", "Zed", "đức" asc/desc; name tie by `id`; `member_count` ties by `id` both directions; defaults unchanged; expired assignments and inactive users excluded from the count.

**Risks**
- ICU availability differs between CI/prod and local images — verify `SELECT collname FROM pg_collation`.
- `-name` order changes for admin-pan and pagination across deploys — cosmetic.
- Grouped join on every `member_count` list call — fine for few apps.

**Open questions for Ming (defaults)**
1. `name` order change acceptable under R9 as a fix, no version bump — yes, changelog note.
2. `und-x-icu` or `vi-x-icu` — `und-x-icu`.
3. Avoid app/core, duplicate clause building in two repositories — yes.
