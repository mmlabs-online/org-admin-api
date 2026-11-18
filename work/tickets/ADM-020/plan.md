Status: READY

# Plan — ADM-020: Filters and sort for the staff organizations list

## Spec summary
- spec §5.1 Organizations: "CRUD for organizations (slug, name, description, status `active`/`suspended`). Paginated list."
- spec §5.9 (context only): "the admin UI **`admin-pan`** ... is a browser SPA that calls this API with Bearer `aud=admin` tokens." The Organizations screen (PAN-014) needs search, filter and sort done by the server.
- CLAUDE.md R9: "lists return `Page[T]` (`limit` ≤ 100, `offset`) ... No breaking changes inside v1."
- Ticket ADM-020 acceptance criteria (quoted in the table below). The ticket's Notes leave three optional items to PLAN. This plan decides all three as **follow-ups**: indexes, rejecting unknown query params, and `sort` on `GET /api/v1/users`.

This ticket builds the shared primitives in `app/core/listing.py` so that USR-18 (apps list) can reuse them unchanged. USR-18 is not planned here.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Optional `status` (`active` \| `suspended`) and `q`. `q` is trimmed, then must be 1..100 chars. It is a case-insensitive substring match on `name` OR `slug`, with LIKE wildcards escaped the same way as the users `email` filter. **Decision: an empty or whitespace-only `q` returns 422 `validation_error`.** An absent `q` means no filter. This is written in the param description. | `tests/test_organizations.py`: `test_list_filter_status`, `test_list_filter_q_name_or_slug_case_insensitive`, `test_list_filter_q_is_trimmed`, `test_list_filter_q_escapes_like_wildcards`, `test_list_filters_combined`, `test_list_rejects_bad_query[...]`. `tests/test_listing.py`: `test_search_term_*`. |
| 2 | Optional `sort` from the allow-list `created_at`, `-created_at` (default), `name`, `-name`, `slug`, `-slug`. `id` ascending is always the tie-breaker. An unknown value returns 422. | `test_list_sort_orders[...]` (all six values), `test_list_sort_tie_breaker_is_id_asc[...]`, `test_list_default_order_unchanged`, `test_list_rejects_bad_query[sort=...]`. `tests/test_listing.py`: `test_sort_clauses_*`. |
| 3 | The response stays `Page[OrganizationRead]`. `total` is the filtered count. No breaking change (R9). | `test_list_total_is_filtered_count` (`limit=1`, `total` = number of matches). The existing `test_list_paginates` passes unchanged. `test_list_default_order_unchanged` shows that no params gives the same order as today (`created_at DESC, id ASC`). |
| 4 | OpenAPI documents the new params. It declares the existing 404 (get/patch/delete) and 409 (duplicate slug, `code: conflict`) responses of the organizations routes. | `tests/test_openapi_contract.py`: `test_organizations_list_query_params`, `test_organizations_declared_error_responses`, `test_organizations_list_422_is_envelope`. The existing `test_every_error_response_references_envelope` and `test_schema_generation_does_not_mutate_shared_dicts` stay green. |
| 5 | Tests cover each filter alone and combined, `%`/`_` escaping, sort order and tie-breaker, 422 for bad `status`/`sort`/empty `q`, and 403 without `admin:organizations:read`. | All tests listed under "Tests to write", plus `test_list_without_permission_403`. `make check` is green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/listing.py` (new) | Shared, HTTP-free list primitives. `escape_like(term)` is moved here unchanged from users. Also adds `ilike_any(term, *columns) -> ColumnElement[bool]`, the `SearchTerm` Annotated type, and `sort_clauses(sort, columns, tiebreaker)`. | AC1, AC2. Reuse by USR-18. One LIKE-escaping implementation. |
| `app/modules/users/repository.py` | Delete the local `escape_like`. Add `from app.core.listing import escape_like`. No other change. | One implementation (consult consensus). `core` never imports `modules`, so there is no circular import. |
| `app/modules/organizations/schemas.py` | Add `OrganizationSort = Literal["created_at", "-created_at", "name", "-name", "slug", "-slug"]` and `DEFAULT_ORGANIZATION_SORT: OrganizationSort = "-created_at"`. | AC2. The allow-list type is shared by router, service and repository. |
| `app/modules/organizations/repository.py` | `list(*, status, q, sort, limit, offset)`. One `conditions` list feeds both count and page. A module-level `_SORT_COLUMNS` mapping and `sort_clauses(...)`. `list_for_user_app` is untouched. | AC1–AC3 (R1: SQLAlchemy only here). |
| `app/modules/organizations/service.py` | `list(*, status, q, sort, limit, offset)` passes the arguments through. | R1 layering. |
| `app/modules/organizations/router.py` | Add the `status`, `q` and `sort` query params with descriptions. Add a local `_route(key, errors)` helper that adds 404/409 to `require_permission(...)["responses"]`. Use it on get/patch/delete (404), post (409) and delete (409). | AC1, AC2, AC4. |
| `tests/test_listing.py` (new) | Unit tests for `escape_like`, `ilike_any`, `SearchTerm` and `sort_clauses`. | AC1, AC2. The shared helper is tested once, for USR-18 too. |
| `tests/test_organizations.py` | API tests for filters, sort, tie-breaker, filtered total, 422 and 403. | AC1–AC3, AC5, R8. |
| `tests/test_openapi_contract.py` | Three new contract tests (params, declared 404/409, GET list 422). | AC4. |
| `README.md` | Endpoints table row: `list (paginated; filters status, q; sort)`. | Docs match behaviour. |
| `docs/identity-architecture.md` | Row "Admin: `/api/v1/organizations` (CRUD)": add "list `?status=&q=&sort=` (ADM-020)". | Endpoint catalogue stays accurate. |
| `docs/spec-v1.md` | One dated note under §5.1 in the existing style: "> 2026-11-xx (ADM-020): staff list filters `status`, `q` (name or slug, trimmed 1..100, empty → 422) and `sort` (allow-list, default `-created_at`, `id` tie-breaker)." | §5.1 records shipped behaviour, as for USR-06a and ADM-006. |

No other files. No migration.

## Tests to write
All ordering tests use **ASCII, single-word names with the same capitalisation** (e.g. `Alpha`, `Bravo`, `Charlie`). They use **slugs without hyphens** (`alpha`, `bravo`, `charlie`), because `en_US` collation ignores `-` at the first level while `C` does not. Expected order is then the same under any collation.

The test transaction fixes `now()`, so every org created in one test has the **same `created_at`**. Tests that check `created_at` order must set `created_at` explicitly. Create the org via the API, then `update(Organization).where(id==...).values(created_at=T0 + n)` in `db_session`, or insert via a small `_org(db_session, slug, name, *, status="active", created_at=...)` helper. This follows the `_user(..., created_at=...)` pattern in `tests/test_users_admin.py`.

| Test (file › name) | Covers |
|---|---|
| `tests/test_listing.py › test_escape_like_escapes_backslash_first` | `\` → `\\`, `%` → `\%`, `_` → `\_`. `"\\%"` becomes `"\\\\\\%"`. Behaviour is identical to the old users helper. |
| `tests/test_listing.py › test_ilike_any_ors_columns_with_escape` | Compiles (postgresql dialect) to `col1 ILIKE :p ESCAPE '\' OR col2 ILIKE :p ESCAPE '\'`. The bound value is `%<escaped>%`. |
| `tests/test_listing.py › test_search_term_strips_and_normalizes` | Uses `TypeAdapter(SearchTerm)`. `"  acme "` gives `"acme"`. The NFD input `"é"` gives the NFC `"é"`. |
| `tests/test_listing.py › test_search_term_rejects_empty_whitespace_and_too_long` | `""`, `"   "` and `"x"*101` raise `ValidationError`. `"x"*100` and `"  " + "x"*100 + "  "` pass, because length is counted after the trim. |
| `tests/test_listing.py › test_sort_clauses_asc_desc_and_tiebreaker` | `"name"` gives `[name ASC, id ASC]`. `"-name"` gives `[name DESC, id ASC]`. Compare the compiled SQL strings. |
| `tests/test_listing.py › test_sort_clauses_unknown_key_raises` | `"bogus"` / `"-bogus"` raise `ValueError` (programming error; the router Literal already blocks it). |
| `tests/test_organizations.py › test_sort_allow_list_matches_repository_mapping` | Every value of `typing.get_args(OrganizationSort)` resolves through `sort_clauses` with the repository's `_SORT_COLUMNS` (no drift between the Literal and the mapping). |
| `tests/test_organizations.py › test_list_filter_status` | `status=suspended` returns only suspended orgs. `status=active` returns only active ones. `total` matches. |
| `tests/test_organizations.py › test_list_filter_q_name_or_slug_case_insensitive` | `q=ALP` matches name `Alpha`. `q=brav` matches slug `bravo` whose name has no `brav` (e.g. name `Other`). Non-matching orgs are excluded. |
| `tests/test_organizations.py › test_list_filter_q_is_trimmed` | `q=%20alpha%20` (i.e. `" alpha "`) matches `Alpha`. |
| `tests/test_organizations.py › test_list_filter_q_escapes_like_wildcards` (parametrized) | Names `100% Pure`, `100 Pure`, `a_b`, `axb`, `back\slash`. `q=%` returns only `100% Pure`. `q=a_b` returns only `a_b`. `q=_` returns only `a_b`. `q=\` returns only `back\slash`. Same shape as `test_list_email_filter_escapes_like_wildcards`. |
| `tests/test_organizations.py › test_list_filters_combined` | `status=suspended&q=alpha` returns only the suspended org matching `alpha`, not the active `alpha` one or the suspended non-matching one. |
| `tests/test_organizations.py › test_list_total_is_filtered_count` | Three matches and two non-matches, with `limit=1`. `total == 3`, `len(items) == 1`, `limit == 1`. |
| `tests/test_organizations.py › test_list_sort_orders` (parametrized over all six values) | Three orgs with distinct `created_at`, name and slug, chosen so that each sort gives a different order from the others. Asserts the exact id order. |
| `tests/test_organizations.py › test_list_sort_tie_breaker_is_id_asc` (parametrized: `name`, `-name`, `created_at`, `-created_at`) | Two orgs with the same name and same `created_at`, plus one other. The tied pair comes back in `id` ASC order for **both** directions. |
| `tests/test_organizations.py › test_list_default_order_unchanged` | No `sort` param. Order is `created_at DESC, id ASC` (explicit `created_at` values plus one tie) and equals the `sort=-created_at` response. |
| `tests/test_organizations.py › test_list_rejects_bad_query` (parametrized) | `status=deleted`, `sort=bogus`, `sort=id`, `sort=NAME`, `q=` (empty), `q=%20%20%20` (whitespace) and `q=` 101 × `x` each give 422 with `error.code == "validation_error"` (envelope). |
| `tests/test_organizations.py › test_list_without_permission_403` | `make_staff(db_session)` with no permission calls `GET /organizations?q=acme&sort=name` and gets 403 `permission_denied` with `details.required == "admin:organizations:read"`. With `sort=bogus` it also gets 403, not 422 (permission is checked before param validation). Uses the `client` fixture plus `bearer(mint(staff))`, as in `tests/test_admin_auth.py::test_missing_permission_403`. |
| `tests/test_openapi_contract.py › test_organizations_list_query_params` | The `GET /api/v1/organizations` parameters include `limit`, `offset`, `status` (enum `["active","suspended"]`, reached through `anyOf` if nullable), `q` (`maxLength == 100`, `minLength == 1`, reached through `anyOf`) and `sort` (enum of exactly the six values, `default == "-created_at"`). All are `in: query` and not required. Each new param has a non-empty `description`. The `q` description mentions that empty means 422. |
| `tests/test_openapi_contract.py › test_organizations_declared_error_responses` | `GET`, `PATCH` and `DELETE /api/v1/organizations/{org_id}` declare `404`. `POST /api/v1/organizations` declares `409`. `DELETE` declares `409`. Each references `#/components/schemas/ErrorEnvelope`. `PATCH` declares no `409`. |
| `tests/test_openapi_contract.py › test_organizations_list_422_is_envelope` | `GET /api/v1/organizations` has a `422` that references the envelope, and its description contains `validation_error`. |

Existing tests that must stay green unchanged: `tests/test_users_admin.py` (regression for the `escape_like` move), `tests/test_organizations.py::test_list_paginates`, `tests/test_route_auth_classes.py`, `tests/test_cli_openapi.py`, and the whole of `tests/test_openapi_contract.py`.

## Approach
1. **`app/core/listing.py`** (pure Pydantic/SQLAlchemy, no FastAPI, no `app.modules` imports. R1, no cycles):
   - `escape_like(term: str) -> str`: moved verbatim from `users/repository.py`, docstring included.
   - `ilike_any(term: str, *columns) -> ColumnElement[bool]`: `pattern = f"%{escape_like(term)}%"`, then `or_(*(c.ilike(pattern, escape="\\") for c in columns))`. Requires at least one column (assert or `ValueError`).
   - `SEARCH_TERM_MAX_LENGTH = 100`.
   - `SearchTerm = Annotated[str, BeforeValidator(nfc_if_str), StringConstraints(strip_whitespace=True, min_length=1, max_length=SEARCH_TERM_MAX_LENGTH)]`. `nfc_if_str` comes from `app/core/text.py`. Validation order is NFC → strip → length, so `"  "` and `""` fail `min_length`. This is how FastAPI returns 422 `validation_error` through the existing envelope handler.
     - **Fallback (only if FastAPI does not apply the `Annotated` metadata to a query param, or does not render `minLength`/`maxLength`):** keep `Query(min_length=1, max_length=100)` for the docs, and add a `BeforeValidator` that does NFC + `strip()`. Then `min_length`/`max_length` apply to the stripped value. The result must be the same 422 `validation_error` and the same OpenAPI keys. Record in `impl-log.md` which variant was used.
   - `sort_clauses(sort: str, columns: Mapping[str, <column type>], tiebreaker: <column>) -> list[...]`:
     - `desc = sort.startswith("-")`, `key = sort.removeprefix("-")`.
     - `col = columns.get(key)`. If it is `None`, raise `ValueError` (no SQL is ever built from the string; the dict is the allow-list).
     - Return `[col.desc() if desc else col.asc(), tiebreaker.asc()]`.
     - Type the column params as whatever `mypy --strict` accepts for `InstrumentedAttribute` (e.g. `QueryableAttribute[Any]` or `ColumnElement[Any]`).
2. **`users/repository.py`**: replace the local definition with `from app.core.listing import escape_like`. Nothing else imports `users.repository.escape_like` (grepped), so no re-export shim is needed. Do not switch users to `ilike_any`, so that the users diff stays at one import (R7).
3. **`organizations/schemas.py`**: add the `OrganizationSort` Literal and the default constant.
4. **`organizations/repository.py`**:
   - `_SORT_COLUMNS = {"created_at": Organization.created_at, "name": Organization.name, "slug": Organization.slug}`.
   - `list(self, *, status: OrganizationStatus | None, q: str | None, sort: OrganizationSort, limit, offset)`:
     - `conditions: list[ColumnElement[bool]] = []`.
     - If `status` is set, append `Organization.status == status.value`.
     - If `q` is set, append `ilike_any(q, Organization.name, Organization.slug)`.
     - `total = count(*) ... .where(*conditions)`.
     - The page query uses `.where(*conditions).order_by(*sort_clauses(sort, _SORT_COLUMNS, Organization.id))`.
   - The default `-created_at` gives `created_at DESC, id ASC`, the same as today.
   - `name` sorts on the plain column (DB collation). See "Needs owner's decision" below.
5. **`organizations/service.py`**: `list(*, status, q, sort, limit, offset)` passes the arguments through. No business rule.
6. **`organizations/router.py`**:
   - The list route signature:
     ```python
     status: Annotated[OrganizationStatus | None, Query(description="Only orgs with this status.")] = None,
     q: Annotated[SearchTerm | None, Query(description="Case-insensitive substring of name or slug; trimmed, 1..100 chars after trimming; empty or whitespace-only is 422 (omit the parameter instead).")] = None,
     sort: Annotated[OrganizationSort, Query(description="One of created_at, -created_at, name, -name, slug, -slug ('-' = descending); id ascending breaks ties. name follows the database collation.")] = DEFAULT_ORGANIZATION_SORT,
     ```
   - Rename nothing in the response. `Page(...)` is built as today.
   - Local helper:
     ```python
     def _route(key: str, errors: dict[int, str]) -> dict[str, Any]:
         kwargs = require_permission(key)
         for code, description in errors.items():
             kwargs["responses"][code] = {"description": description}
         return kwargs
     ```
     `require_permission` returns a fresh `dict(_ERROR_RESPONSES)`, so shared dicts are not mutated (existing test).
   - Declarations:
     - get: `{404: "Organization not found"}`
     - patch: `{404: "Organization not found"}`
     - delete: `{404: "Organization not found", 409: "Organization still has role assignments (code org_has_assignments) or entitlements (code org_has_entitlements)"}`
     - post: `{409: "Slug already taken (code conflict)"}`
   - Do **not** touch the `_with_404`/`_with_409` copies in rbac, entitlements, join_requests or app_credentials (R7). They go to follow-ups.
7. Update `README.md`, `docs/identity-architecture.md` and `docs/spec-v1.md` as listed.
8. Run `make check` (ruff, format, mypy --strict, pytest). `alembic check` must report no drift (no model change).

Rules:
- **R1**: the router only declares params, the service passes them through, and the repository holds all SQL. `core/listing.py` has no HTTP.
- **R2**: no schema change, no migration.
- **R3**: same auth class (Bearer + `admin:organizations:read`/`write`), with permissions unchanged.
- **R4**: no new mutation, so there is no audit change.
- **R5**: no config.
- **R6**: 422 and 403 go through the existing envelope. The 404/409 declarations use the envelope via `_postprocess`.
- **R7**: the rbac helper refactor, users sort and unknown-param rejection are follow-ups.
- **R8**: tests as listed.
- **R9**: all params are optional and additive. The default order is unchanged. `Page[T]` is kept.
- **R10**: not affected.
- **R11**: `q` is not logged. The validation error details follow the existing handler.

## Specialist input used
- **api-platform-engineer**
  - #1 trim before validation, NFC, shared `SearchTerm`: adopted.
  - #2 empty `q` → 422: adopted. It matches the users `email` `min_length=1` and the AC's "1..100".
  - #3 `Literal` over StrEnum with a repository mapping: adopted.
  - #4 `lower(name)`: rejected in favour of DB collation (see decision below). The `id` ASC tie-breaker in both directions is adopted.
  - #5 `app/core/listing.py` with `escape_like`, `ilike_any`, `SearchTerm` and an order helper, with module-owned Literals: adopted.
  - #6 one `conditions` list: adopted.
  - #7 declare 404 on get/patch/delete, 409 on post **and** delete: adopted, through a **local** helper in organizations. A shared helper in `admin_auth/deps.py` is deferred to a follow-up together with migrating the four copies, so that only one helper style exists in the codebase meanwhile.
  - #8 unknown params: follow-up.
  - #9 `/users` sort: follow-up.
  - #10 no index: adopted.
  - #11 tests: adopted, including the contract tests.
  - Risk "users `email` does not trim": noted. `email` is left unchanged (R9).
- **database-optimizer**
  - #1 `ILIKE` with `ESCAPE '\'`, not `lower() LIKE`: adopted.
  - #2 trim then 1..100, 422 on empty: adopted.
  - #3 plain `name` sort under DB collation, as in `list_for_user_app`: **adopted**.
  - #4 one `conditions` for count and page: adopted.
  - #5 no migration, with trigram and btree indexes as a follow-up: adopted.
  - #6 helper shape: adopted (`sort_clauses` replaces "SortSpec.parse").
  - Risks "ASCII-only ordering tests" and "no core→modules import": adopted.
  - Open question "production collation": carried to the owner decision below (non-blocking).

## Assumptions
- The test DB runs one transaction per test, so `now()` (and therefore `created_at`) is constant within a test. Ordering tests set `created_at` explicitly.
- Staff-created org names are not NFC-normalized at write time (`OrganizationCreate` has no `nfc_if_str`), while `q` is NFC-normalized. A decomposed stored name could miss an NFC query. This is accepted as rare. Normalizing org names on write is out of scope.
- `q` matches `name` and `slug` only, not `description` (per the AC).
- Offset pagination over a mutable sort key (`name`) can show a row twice or skip one if data changes between pages. This is acceptable for staff tooling.
- Estimated diff: about 330 lines including tests. One cycle is enough.

### Needs owner's decision (non-blocking)
- **`name` sort: DB collation (chosen) vs `lower(name)`.** The plan sorts on plain `Organization.name`. Reasons:
  1. Under a linguistic collation (`en_US.utf8`, used in dev/CI, or ICU), case is already only a tie-level difference, so `lower()` changes nothing visible.
  2. Under the `C` collation, `lower()` folds only ASCII, so it would not fix Vietnamese names either. The real fix there is a collation choice, not `lower()`.
  3. It matches the existing `list_for_user_app` order (`name, id`), so both org lists order alike.
  4. It keeps a future plain btree on `name` usable.

  Risk: if the production database `org_admin` uses `C`/`POSIX` collation, `Zeta` sorts before `acme`. Owner action: run `SHOW lc_collate;` on the shared Postgres. If it is `C`, choose either `lower(name)` (an ASCII-only fix, a one-line repository change) or `COLLATE "und-x-icu"` on the sort expression. Either is a follow-up with no API change.
- Empty `q` → 422 (chosen, per both consults). The alternative was "treat as absent". admin-pan must omit `q` when the search box is blank.

## Blocking questions
None.

## Split proposal
None.

## Data model / migration
None. No schema change and no index. Trigram GIN indexes on `name`/`slug` are a follow-up, if the table grows past about 10k rows or list p95 exceeds about 100 ms (`EXPLAIN ANALYZE` first).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/organizations` | Query: `limit` (1..100, default 20), `offset` (≥0, default 0); **new**: `status` (`active`\|`suspended`, optional), `q` (string, trimmed + NFC, 1..100 after trim, optional; empty/whitespace → 422), `sort` (`created_at`\|`-created_at`\|`name`\|`-name`\|`slug`\|`-slug`, default `-created_at`; tie-breaker `id` ASC). Unknown query params are ignored (unchanged). | `Page[OrganizationRead]`; `total` = filtered count | 200, 401, 403, 422 | `admin:organizations:read` |
| POST | `/api/v1/organizations` | unchanged | unchanged | 201, 401, 403, **409 declared** (`conflict`), 422 | `admin:organizations:write` |
| GET | `/api/v1/organizations/{org_id}` | unchanged | unchanged | 200, 401, 403, **404 declared**, 422 | `admin:organizations:read` |
| PATCH | `/api/v1/organizations/{org_id}` | unchanged | unchanged | 200, 401, 403, **404 declared**, 422 | `admin:organizations:write` |
| DELETE | `/api/v1/organizations/{org_id}` | unchanged | unchanged | 204, 401, 403, **404 declared**, **409 declared** (`org_has_assignments`, `org_has_entitlements`), 422 | `admin:organizations:write` |

Runtime behaviour of POST/GET-one/PATCH/DELETE is unchanged. Only the OpenAPI declarations are added.

## Follow-ups (not in this diff, R7)
1. Reject unknown query params on all list routes. Note that USR-18's AC says "unknown or invalid values → 422", so its planner must reconcile that with this deferral.
2. `sort` on `GET /api/v1/users` (PAN-013), reusing `sort_clauses`. Also consider trimming `email` there, which is a behaviour change to weigh against R9.
3. Hoist `_with_404`/`_with_409`/`_route` into one helper next to `require_permission`. Migrate rbac, entitlements, join_requests, app_credentials and organizations to it.
4. Trigram GIN indexes on `organizations.name`/`slug` once size or latency warrants it.
5. Check the production collation (`SHOW lc_collate`) and revisit the `name` sort if it is `C`.
6. Optionally NFC-normalize org names on write (`OrganizationCreate`/`OrganizationUpdate`/`AuthOrgCreate`).
