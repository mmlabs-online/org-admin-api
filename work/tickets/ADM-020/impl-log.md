## Attempt 1 — 2026-11-18

### Files touched
| File | Required because |
|---|---|
| app/core/listing.py (new) | shared `escape_like`, `ilike_any`, `SearchTerm`, `sort_clauses` (AC1, AC2) |
| app/modules/users/repository.py | `escape_like` moved to core; one import |
| app/modules/organizations/schemas.py | `OrganizationSort`, default constant |
| app/modules/organizations/repository.py | filters, sort, filtered total |
| app/modules/organizations/service.py | pass-through of new args |
| app/modules/organizations/router.py | `status`/`q`/`sort` params; `_route` declares 404/409 |
| tests/test_listing.py (new) | helper unit tests |
| tests/test_organizations.py | filter/sort/422/403 API tests |
| tests/test_openapi_contract.py | 3 contract tests |
| README.md, docs/identity-architecture.md, docs/spec-v1.md | docs per plan |

### Tests added / changed
- tests/test_listing.py: escape_like, ilike_any (+ no-column), SearchTerm (normalize, reject), sort_clauses (asc/desc/tiebreaker, unknown)
- tests/test_organizations.py: allow-list vs mapping, filter status, q name/slug, q trimmed, q wildcards (x4), combined, filtered total, sort orders (x6), tie-breaker (x4), default order, bad query (x7), 403 without permission
- tests/test_openapi_contract.py: list query params, declared 404/409, list 422 envelope

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): all green on final tree; pytest `2495 passed, 1 warning in 344.02s`
- alembic check: no model change, none needed

### Notes
- SearchTerm variant: the plan's primary form (`StringConstraints(strip_whitespace=True, min_length=1)`) did not reject `"   "` (length checked before strip). Used the fallback: a before-validator does NFC + `strip()`, then `min_length=1`/`max_length=100` apply to the stripped value. Same 422 `validation_error`; OpenAPI shows minLength/maxLength.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- As listed in plan.md (unknown-param rejection, users sort, shared 404/409 helper, trigram indexes, collation check, NFC on org names).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +480 / -25 lines including tests and docs.
