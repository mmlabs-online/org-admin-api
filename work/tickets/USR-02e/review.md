# Review — USR-02e

## Attempt 1 — 2026-11-11
Verdict: PASS

### Checks (run by reviewer)
- make check (APP_VERSION/DATABASE_URL/APP_ENV unset; exit 0): `ruff check .` → All checks passed!; `ruff format --check .` → 168 files already formatted; `mypy app` → Success: no issues found in 50 source files; `pytest -q` → 406 passed in 14.37s. Matches impl-log (406 passed); the format count differs by one file (167 in impl-log vs 168 here) — the check is green either way, see Nits.
- alembic check (no model change; run anyway, `DATABASE_URL=…/org_admin_test`): `No new upgrade operations detected.`

### Acceptance criteria
Ticket ACs read with the orchestrator note (D17: public, always mounted; "flag off → 404" superseded by "reachable with flag off"). Verified against plan AC1–AC9.
- [x] AC1 own router, always mounted, no `x-permission` — evidence: `app/modules/rbac/catalog_router.py:14-35` (no `openapi_extra`); `app/api/router.py:5,11` (outside the flag branch; `app/main.py` unchanged, flag still gates only `rbac_router` at `main.py:42-43`); `test_only_catalog_route_listed_by_default`, `test_rbac_routes_declare_permissions` (entry `None`) pass.
- [x] AC2 per-IP limit `role_catalog`, configurable, 304/404 count, 429 + `Retry-After` — evidence: `catalog_router.py:20`; `app/core/config.py:43-44` (`ge=1`); `app/core/rate_limit.py:65-67`; `.env.example` two keys without values; `test_catalog_rate_limited_429`, `test_catalog_304_and_404_count_toward_limit` (limit 3 instead of plan's 2 — justified in impl-log: a 200 is needed to learn the tag; same assertion).
- [x] AC3 canonical document, sorted, nothing else — evidence: `app/modules/rbac/schemas.py` `RoleCatalogEntry`/`RoleCatalog` (dedicated models, not `RoleRead`); `service.py:89-109` sorts in Python, filters foreign-prefix permission keys; `repository.py:73-85` single LEFT JOIN query (no N+1); `test_catalog_document_shape_and_order` (out-of-order insert, non-ASCII name, compact bytes), `test_catalog_exact_key_set`, `test_catalog_empty_app_and_role_without_permissions`.
- [x] AC4 strong ETag = SHA-256/128 of exact bytes, `no-cache`, stable, changes on change — evidence: `app/core/etag.py:8-10`; `catalog_router.py:30-33` hashes the exact bytes sent; `test_catalog_etag_is_hash_of_body`, `test_catalog_etag_stable`, `test_catalog_etag_changes_after_change` (grant, revoke, rename, new role, delete; also checks content-addressing).
- [x] AC5 `If-None-Match` → 304 (exact, `W/`, list, `*`), empty body, same headers; no match/malformed → 200 — evidence: `etag.py:13-31`; `catalog_router.py:34`; `test_catalog_304_variants` ×4, `test_catalog_304_response_shape`, `test_catalog_if_none_match_no_match_200` ×3; `tests/test_etag.py` (7 test functions incl. 2048-char cap and `*` in a list).
- [x] AC6 one identical 404 for unknown, disabled, `admin`, malformed key (no DB query for malformed) — evidence: `service.py:91-95`; `admin` is seeded by migration `20261111_b638a7c84c31_create_rbac.py:193`, so the `admin` case exercises the real branch; `test_catalog_404_cases_identical` (byte-identical bodies, no `ETag`).
- [x] AC7 reachable flag off and on; flag on POST still hits staff route; flag off POST → 405 — evidence: `test_catalog_reachable_flag_off`, `test_catalog_reachable_flag_on_no_clash` (201 from staff POST), `test_post_roles_flag_off_is_405`.
- [x] AC8 carry-over character filter: C1, bidi, zero-width, LS/PS, BOM rejected; applied to `AppCreate.name`/`AppUpdate.name`, `RoleName`, `Description`; NUL in app name → 422; no echo — evidence: `schemas.py:17-21` (`_NO_CONTROL`, `AppName`), `AppCreate.name: AppName`, `AppUpdate.name: AppName | None`; `test_app_name_control_chars_422` (7 chars × create + patch, `"Marker" not in resp.text`), `test_role_name_format_chars_422`, `test_description_format_chars_422`, `test_vietnamese_names_accepted` (precomposed + combining).
- [x] AC9 `make check` green, `alembic check` clean — see Checks.

### Scope
Every changed file is in the plan's Files table (`app/core/etag.py`, `catalog_router.py`, `app/api/router.py`, `schemas.py`, `repository.py`, `service.py`, `config.py`, `rate_limit.py`, `.env.example`, ADR-0003 §11, identity-architecture §4 row, 5 test files). `work/` changes are orchestrator bookkeeping (ticket title/state, backlog row, consults, plan, impl-log). `app/main.py` untouched as planned. No out-of-plan code.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: router builds the HTTP response only (`catalog_router.py:28-35`); 404 decided in the service via `NotFoundError`; query in the repository.
- R2: no schema change; `alembic check` clean.
- R3: class 3 public, rate-limited, 404 for unknown/disabled/`admin`; no `x-permission`; staff routes still gated.
- R4: read-only, no audit needed.
- R5: two new env keys in `config.py` and `.env.example` without values; no secrets.
- R6: 404/429 use the `AppError` envelope; 304 has no body; 405 (flag off) is Starlette's shape — accepted in plan A6 and pinned by a test.
- R8: happy path, 404, 304, 429, validation (422) all tested.
- R9: D18 exception (key path, single document) applies.
- R11: response carries no tokens/emails/secrets; 422 bodies do not echo the rejected value.

### Nits (do not block)
- impl-log reports `167 files already formatted`; the reviewer's run shows 168. Harmless (green either way), but impl-log numbers should be pasted from the final run.
- `test_catalog_if_none_match_no_match_200` could include a `W/"stale"` case; `tests/test_etag.py` covers it at unit level.
- Character-filter tests do not exercise `U+061C`, `U+200F`, `U+2060`; the regex covers them and the class is documented in ADR-0003 §11.
- No `HEAD` support on the catalog (FastAPI does not add it for `GET`); fine for API clients, note only.

### Follow-ups (out of scope)
- Plan FU1–FU7 stand (staff role listing for disabled/`admin` apps; limiter per-scope caps/eviction; refuse empty `TRUSTED_PROXY_CIDRS` in production; per-app role/permission caps; NFC normalization; refuse `*.api.mmlabs.online` origins; ticket wording — the title is already fixed in this branch, the Goal and AC4 text still say "without making it public" / "flag off → 404").
- **Release prerequisite (plan A7):** set `TRUSTED_PROXY_CIDRS` in the deployment repo before this ships; otherwise every caller behind cloudflared shares one `role_catalog` bucket (120/min). State it in the PR body.
