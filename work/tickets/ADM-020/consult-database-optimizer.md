**Findings**

- **Model (`app/modules/organizations/models.py`).** `slug` is `String(63)` with a unique constraint, which gives an implicit unique btree. `name` is `String(255) NOT NULL`. `status` is `String(20) NOT NULL` with a CHECK constraint. `created_at` comes from `TimestampMixin`. The only extra index is the partial `ix_organizations_created_by`. There are no indexes on `name`, `status` or `created_at`.
- **NULLs.** `name`, `slug`, `status` and `created_at` are all NOT NULL, so NULL ordering (NULLS FIRST/LAST) is irrelevant for every allowed sort key. No special handling is needed.
- **Existing list (`app/modules/organizations/repository.py:23`).** The count is an unfiltered `count(*)`. The page query orders by `created_at DESC, id ASC` with limit and offset. `list_for_user_app` already orders by `name, id`, which is a precedent for a plain `name` sort without `lower()`.
- **Users filter (`app/modules/users/repository.py:9,76` and `users/router.py:30`).**
  - `escape_like` escapes `\`, `%` and `_`, backslash first, which is correct.
  - It is used as `col.ilike(f"%{escape_like(t)}%", escape="\\")`, and `escape_like` is only used there.
  - `email` is CITEXT, and the route uses `Query(min_length=1, max_length=254)` with **no trimming**.
  - The users list builds a `conditions` list and runs the same `where(*conditions)` for the count and the page query. That is the pattern to reuse.
- **Case-insensitivity.** `ILIKE` and `lower() LIKE` give the same results on a UTF-8 database with a normal libc or ICU locale. Under the `C` locale, both fold only ASCII.
  - The dev compose image defaults to `en_US.utf8`, but the shared production Postgres collation is unverified.
  - This matters for Vietnamese names such as "Đ" and "đ".
  - Slugs are ASCII, so slug matching is safe either way.
- **Pagination.** `PageDep` is `page_params`, with limit ≤ 100. The routers build `Page(items, total, limit, offset)` by hand.

**Recommendations**

1. **Match.** Use `or_(Organization.name.ilike(pat, escape="\\"), Organization.slug.ilike(pat, escape="\\"))`, with `pat = f"%{escape_like(q)}%"`.
   - Do not use `lower(col) LIKE lower(:p)`. It gains nothing and defeats any future plain index.
   - Move `escape_like` to a shared module, for example `app/core/query.py`, and re-import it in the users repository so there is one implementation. A regression test is enough, since behaviour is unchanged.
2. **`q` validation.** Trim first, then enforce 1..100 characters.
   - `Query(min_length=…)` does not strip, so `"  "` would pass and become an empty pattern that matches everything.
   - Use `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]`, either through a Pydantic query-params model or a `BeforeValidator`.
   - Decision for PLAN: an empty or whitespace-only `q` returns **422**. That matches the AC wording "1..100 chars", is unambiguous, and `q=` is a client bug. Absent `q` means no filter.
   - FastAPI's `Query()` model with `extra="forbid"` would also settle the "reject unknown params" option. I would put that in a follow-up and apply it to all list routes at once, because doing it here only would be inconsistent.
3. **Sort.** Use a `StrEnum` (`SortKey`) or a `Literal` for the six values.
   - Keep a mapping of `{key: (column, desc)}` in the repository, never f-string or `getattr` on user input.
   - Always append `Organization.id` ascending as the tie-breaker, even for descending sorts. This matches the current `created_at.desc(), id` behaviour.
   - Use a plain `name` sort, not `lower(name)`. It is collation-ordered, as in `list_for_user_app`. Document that ordering follows the database collation.
   - The default stays `-created_at`, so existing clients see no change.
4. **Count.** Build the `conditions` list once. Run `select(func.count()).select_from(Organization).where(*conditions)` and the page query with the same `where(*conditions)`. Do not repeat the filters in two places.
5. **Indexes.** **No migration now.**
   - The table is small, and a seq scan plus sort is cheaper than any index here. An unanchored `%q%` cannot use a btree at all.
   - `lower(name)` would not help the `%q%` pattern or the OR with `slug`.
   - `pg_trgm` is the only index that serves `%q%`. It is a new extension on the shared Postgres, and the migration would need `CREATE INDEX CONCURRENTLY` outside a transaction, so it should be a deliberate decision.
   - Record a follow-up: add GIN `gin_trgm_ops` indexes on `name` and `slug`, and a btree on `(created_at DESC, id)`, once `organizations` exceeds roughly 10k rows or the list p95 exceeds about 100 ms. Check with `EXPLAIN ANALYZE` first.
   - `status` has two values, so an index on it is not worthwhile unless it is partial.
6. **Reusable helper for USR-18.** Put a small module in `app/core/listing.py` (name to be agreed). It needs no ORM base-class magic.
   - `escape_like(term)` and `ilike_any(term, *columns) -> ColumnElement[bool]` (the OR of `ilike` with `escape="\\"`).
   - `SortSpec`: `parse(value, allowed: Mapping[str, ColumnElement]) -> list[UnaryExpression]`, which returns `[col.asc()/desc(), tie_breaker]`.
   - A shared `Q = Annotated[str, StringConstraints(...)]` type.
   - Each repository still owns its own `conditions`, the count and the page query.
   - USR-18 supplies its own allow-list and columns. The apps list addresses apps by `key`, so its tie-break column may be `id` or `key`; check that it is unique.
7. **OpenAPI.** Add `responses={404:…, 409:…}` to the organizations routes through the existing `require_permission` dict pattern. `users/router.py` uses `_WRITE["responses"][409]`, so follow that.

**Risks**

- Under a `C`-collation production database, `ILIKE` on "Đ"/"đ" would not match case-insensitively, and `name` sort order would be byte order. This is cosmetic, but the test should not assert non-ASCII ordering.
- The tests run on the compose Postgres (`en_US.utf8`), which may differ from production. Keep the sort tests ASCII and make them explicit about the case of the names.
- Offset pagination with the `id` tie-breaker is stable only if rows are not inserted mid-paging. That is acceptable for staff tooling.
- Moving `escape_like` risks a circular import if `core` imports from modules. Put it in `core` and have `users` import it, not the reverse.
- `q` that is 100 characters of `%` becomes 200 characters after escaping. That is fine, since there is no length limit on the LIKE pattern.

**Open questions**

- What is the production database collation and encoding? If it is not UTF-8 with a locale, the Vietnamese case-folding point above becomes a real issue. This is cheap to check with `SHOW lc_ctype`.
- Should `q` be applied to `description`? The ticket says no. I assume no.
- Should the users list get `sort` and trim of `email` in the same PR? The ticket marks it optional, so I would make it a follow-up. It reuses the helper, so the cost is small.
- Do you want 422 or ignore for an empty `q`? I recommend 422.

Files read:
- `/home/user/org-admin-api/work/tickets/ADM-020/ticket.md`
- `/home/user/org-admin-api/app/modules/organizations/repository.py`
- `/home/user/org-admin-api/app/modules/organizations/models.py`
- `/home/user/org-admin-api/app/modules/organizations/router.py`
- `/home/user/org-admin-api/app/modules/users/repository.py`
- `/home/user/org-admin-api/app/modules/users/router.py`
- `/home/user/org-admin-api/app/api/deps.py`
