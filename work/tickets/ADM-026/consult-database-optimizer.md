# Consult — database-optimizer — ADM-026 (2026-11-27)

**Findings**
- `app/core/listing.py:45-59` `sort_clauses` maps key → column, asc/desc, appends `id` ASC; takes `QueryableAttribute`s only (a collated / `member_count` expression needs `ColumnElement` support).
- Orgs `organizations/repository.py:16-20`, `:54`; apps `rbac/repository.py:79`, `:174`; literals `organizations/schemas.py:12`, `rbac/schemas.py:30` (`AppSort`, no `member_count`). Router docs say "name follows the database collation" (`organizations/router.py:79`, `rbac/router.py:92`) — must change.
- `member_counts` (`rbac/repository.py:1030-1051`): one grouped query; apps with no members absent → `COALESCE(...,0)` for a sort; `rbac/router.py:107` calls it per page after the list.
- No collation/ICU precedent in `migrations/` (only citext, `20261111_7b847bdc5230_create_users.py:25`); no name index; in-memory top-N sort over a tiny table.
- docker-compose and CI use `postgres:17-alpine` (prod build not checked; local dev 16). Alpine/musl libc collations ≈ byte order (today's "uppercase first, accents last"); Docker Alpine builds link ICU, so `und-x-icu` is normally present — not confirmed.

**Recommendations**
1. Inline, no migration: prefer `lower(name) COLLATE "und-x-icu"` (deterministic, case-insensitive); `und-x-icu` alone is case-insensitive only at tertiary level. CLDR root order sorts Vietnamese acceptably (a < ă < â, d < đ); `vi-x-icu` only if present and wanted.
2. No nondeterministic collation (High migration, breaks `LIKE`/`ilike` and equality); no functional index until ~10^5 rows.
3. Extend `sort_clauses` to `Mapping[str, ColumnElement | QueryableAttribute]` (app/core — High) with `func.lower(Org.name).collate("und-x-icu")` for `name`; keep `id` ASC tie-break both directions; `key`/`slug` unchanged.
4. Apps `member_count`: subquery of the `member_counts` logic grouped by `Role.app_id`, `LEFT JOIN`, sort on `COALESCE(cnt, 0)`; one shared `_member_count_subquery()` for both (docstring: "do not duplicate it"); join only for this sort; `total` query join-free.
5. Tests: `["bob","Alice","alice","Đào","Dao","Ân","an"]` asc/desc; `member_count` ties by `id`; zero-member apps last in `-member_count`, first in `member_count`; expired / inactive excluded; a test that fails loudly if `und-x-icu` is missing.
6. Update both router descriptions and `docs/`.

**Risks**
- ICU absent in prod Postgres → every name sort 500s — run `SELECT 1 FROM pg_collation WHERE collname='und-x-icu'` on prod before merge.
- PG16 vs PG17 ICU/CLDR versions may shift rare characters (ordering only).
- Inline `COLLATE` is a code literal — never from input.
- `lower()` Unicode-aware only under UTF-8 — confirm encoding.

**Open questions for Ming (defaults)**
1. Prod Postgres built with ICU — assume yes; run the check, fall back to `lower(name)` in default collation if not.
2. `vi-x-icu` — no, `und-x-icu`.
3. Stable order for case-only ties — yes, `lower(name)` then `id`.
4. Ship `member_count` sort now — yes.
