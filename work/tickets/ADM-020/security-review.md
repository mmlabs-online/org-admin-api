## Attempt 1

## Security review: ADM-020 (filters and sort on GET /api/v1/organizations, new app/core/listing.py)

I read the code and ran git only, as asked. I did not run `make check` or `pytest`.

### Findings

No 🔴 blockers and no 🟡 should-fix findings.

- 💭 **Leading-wildcard ILIKE is a full table scan** (`app/core/listing.py:43`; called at `app/modules/organizations/repository.py:44`). Each request with `q` runs a `%term%` ILIKE on `name` and `slug`, twice: once for the count and once for the page.
  - **Risk:** small. The endpoint needs `admin:organizations:read`, `q` is at most 100 characters, and the org table is small.
  - **Fix:** none needed now. Add a `pg_trgm` GIN index only if org counts grow.
- 💭 **`sort_clauses` raises `ValueError` on an unknown key** (`app/core/listing.py:56-58`). A plain `ValueError` would become a 500 rather than a 422.
  - It cannot be reached today: FastAPI checks `OrganizationSort` (a `Literal`, `app/modules/organizations/schemas.py:11`) and returns 422 first. `test_sort_allow_list_matches_repository_mapping` keeps that type and `_SORT_COLUMNS` in sync.
  - **Fix:** none now. A future module that passes a plain `str` into `sort_clauses` must type its query parameter as a `Literal` the same way.

### Checks that passed

- **LIKE escaping:** `escape_like` escapes the backslash first, then `%` and `_` (`app/core/listing.py:32`). `ilike_any` passes `escape="\\"`, so the escape character matches. Values are bound as parameters. `tests/test_listing.py:13-17` and `test_list_filter_q_escapes_like_wildcards` cover this.
- **Sort is allow-listed:** no SQL is built from user strings. The `Literal` validates the value, it maps to ORM columns through `_SORT_COLUMNS` (`repository.py:13-17`), and `id ASC` is always added as the tie-breaker.
- **Limits on `q`:** `SearchTerm` applies NFC, then strip, then 1..100 characters. Empty or whitespace-only input returns 422. `status` is an enum.
- **Authorization unchanged:** the list route still declares `require_permission("admin:organizations:read")`, covered by `test_list_without_permission_403`. The write routes keep `admin:organizations:write`. `_route` adds its error entries to a fresh copy that `require_permission` returns on each call (`deps.py:119`), so the shared `_ERROR_RESPONSES` is not mutated.
- **Users email filter unchanged:** `app/modules/users/repository.py:72` still uses the same `escape_like` body with `escape="\\"`. It was moved without changes, and its input validation was not altered.
- **OpenAPI and error output:** the new descriptions only list allowed values and error codes. 422 responses drop `input`/`ctx` (`app/core/errors.py:168-170`), so `q` is never echoed back.
- **Out of scope:** no secrets or config changes, and nothing in migrations, dependencies, Docker or CI is touched.

### Follow-ups (out of scope)
- If organization search becomes heavily used, consider a `pg_trgm` index (owned by `database-optimizer`).

**Verdict: PASS.** No security blockers. A final PASS still needs a green `make check` (R8).
