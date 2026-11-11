# Implementation log — USR-02e

## Attempt 1 — 2026-11-11

### Files touched
| File | Required because |
|---|---|
| `app/core/etag.py` (new) | `strong_etag`, `if_none_match` helpers (AC4/AC5) |
| `app/modules/rbac/catalog_router.py` (new) | Public `GET /apps/{key}/roles`, rate limit `role_catalog`, ETag/304, no `x-permission` (AC1, R3 class 3) |
| `app/api/router.py` | Always mount `catalog_router` (AC1/AC7) |
| `app/modules/rbac/schemas.py` | Widened `_NO_CONTROL` moved above `AppCreate`; `AppName` on `AppCreate.name`/`AppUpdate.name`; `RoleCatalogEntry`, `RoleCatalog` (AC3, AC8) |
| `app/modules/rbac/repository.py` | `catalog_rows(app_id)` — one LEFT JOIN query, unordered (AC3, R1) |
| `app/modules/rbac/service.py` | `RoleCatalogService.read_catalog` — key regex before DB, one 404 for unknown/disabled/`admin`, Python sort, prefix filter (AC3, AC6) |
| `app/core/config.py` | `role_catalog_rate_limit=120`, `role_catalog_rate_window_seconds=60` (`ge=1`) (AC2, R5) |
| `app/core/rate_limit.py` | `role_catalog` scope in `_limit_for` (AC2) |
| `.env.example` | `ROLE_CATALOG_RATE_LIMIT=`, `ROLE_CATALOG_RATE_WINDOW_SECONDS=` (R5) |
| `docs/adr/0003-rbac-keys-and-matching.md` | Amended line + new §11 "Public role catalog" |
| `docs/identity-architecture.md` | §4 row for the catalog: public (D17), per-IP limit, owner USR-02e |
| `tests/test_etag.py` (new) | Helper unit tests |
| `tests/test_role_catalog_api.py` (new) | Catalog API tests (AC1–AC7) |
| `tests/test_rbac_apps_api.py` | Catalog entry (`None`) in the route map; app-name character tests |
| `tests/test_rbac_roles_api.py` | Role-name character tests; Vietnamese names accepted |
| `tests/test_rbac_permissions_api.py` | Description character tests |

`app/main.py` unchanged (flag still gates only the staff router).

### Tests added / changed
- `tests/test_etag.py › test_strong_etag_format`, `test_if_none_match_exact`, `test_if_none_match_weak_prefix`, `test_if_none_match_list` (4 cases, incl. several headers), `test_if_none_match_star` (2), `test_if_none_match_no_match` (4), `test_if_none_match_malformed` (7, incl. `*` inside a list and > 2048 chars)
- `tests/test_role_catalog_api.py › test_catalog_document_shape_and_order`, `test_catalog_exact_key_set`, `test_catalog_empty_app_and_role_without_permissions`, `test_catalog_etag_is_hash_of_body`, `test_catalog_etag_stable`, `test_catalog_etag_changes_after_change` (grant, revoke, rename, new role, role delete; also asserts the tag returns to the original after revoke — content-addressed), `test_catalog_304_variants` (exact, `W/`, list, `*`), `test_catalog_304_response_shape`, `test_catalog_if_none_match_no_match_200` (3), `test_catalog_404_cases_identical` (unknown, disabled, `admin`, `UPPER`, 40-char), `test_catalog_reachable_flag_off`, `test_catalog_reachable_flag_on_no_clash`, `test_post_roles_flag_off_is_405`, `test_only_catalog_route_listed_by_default`, `test_catalog_rate_limited_429`, `test_catalog_304_and_404_count_toward_limit`
- `tests/test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (updated: `("GET", "/api/v1/apps/{key}/roles"): None`), `test_app_name_control_chars_422` (7 chars × create + patch, no echo)
- `tests/test_rbac_roles_api.py › test_role_name_format_chars_422` (7 chars × create + patch, no echo), `test_vietnamese_names_accepted` (precomposed + combining marks)
- `tests/test_rbac_permissions_api.py › test_description_format_chars_422` (`‮`, `\x85`)

Note: `test_catalog_304_and_404_count_toward_limit` uses limit 3 (one 200 to learn the ETag, then one 404 and one 304, then 429) instead of the plan's limit 2 — the client needs a 200 first to obtain the tag; the assertion (304 and 404 count) is the same.

### Checks (actual output summary)
- Postgres: local cluster already running (`service postgresql status`), DBs `org_admin` / `org_admin_test` present.
- make check (APP_VERSION/DATABASE_URL/APP_ENV unset):
  - `ruff check .` → All checks passed!
  - `ruff format --check .` → 167 files already formatted
  - `mypy app` → Success: no issues found in 50 source files
  - `pytest -q` → 406 passed in 14.56s
- alembic check (no model change; run anyway): `No new upgrade operations detected.`

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- FU1–FU7 from plan.md stand (staff role listing for disabled/`admin` apps; limiter per-scope caps/eviction; refuse empty `TRUSTED_PROXY_CIDRS` in production; per-app role/permission caps; NFC normalization; refuse `*.api.mmlabs.online` origins; fix ticket title/Goal wording).
- Release prerequisite (plan A7): set `TRUSTED_PROXY_CIDRS` in the deployment repo before this ships, otherwise all callers behind cloudflared share one rate-limit bucket.

### Scope self-check
- Could the diff be smaller? No — every file is in the plan's list; production code ≈ 140 lines.
- Diff size: ≈ +555 / -7 lines (production + docs ≈ +230, tests ≈ +325).
