# Review — ADM-020

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (ruff, format check, mypy --strict, pytest): green — `2495 passed, 1 warning in 347.57s` (warning is the pre-existing forkpty DeprecationWarning in test_cli_create_account). The known-flaky `tests/test_stats_repository.py::test_login_count_uses_action_index` passed on the first run; no rerun needed. Matches impl-log (2495 passed).
- `alembic upgrade head && alembic check`: "No new upgrade operations detected." (no model change in the diff).

### Acceptance criteria
- [x] AC1 `status` + `q` (trimmed, 1..100, ILIKE on name OR slug, wildcards escaped; empty → 422, documented) — `app/core/listing.py` (`SearchTerm`, `escape_like`, `ilike_any` with `ESCAPE '\'`), `app/modules/organizations/repository.py` list(); router `q` description states empty/whitespace → 422. Tests: `test_list_filter_status`, `test_list_filter_q_name_or_slug_case_insensitive`, `test_list_filter_q_is_trimmed`, `test_list_filter_q_escapes_like_wildcards[%|a_b|_|\]`, `test_list_filters_combined`, `test_list_rejects_bad_query[q=''|'   '|101x]`, `tests/test_listing.py::test_search_term_*` — all pass.
- [x] AC2 `sort` allow-list, default `-created_at`, `id` ASC tie-breaker, unknown → 422 — `OrganizationSort` Literal (schemas.py), `_SORT_COLUMNS` + `sort_clauses`. Tests: `test_list_sort_orders[x6]`, `test_list_sort_tie_breaker_is_id_asc[x4]`, `test_list_default_order_unchanged`, `test_list_rejects_bad_query[sort=bogus|id|NAME]`, `test_sort_allow_list_matches_repository_mapping`, `test_sort_clauses_*` — pass.
- [x] AC3 `Page[OrganizationRead]`, filtered `total`, no breaking change — one `conditions` list feeds count and page; `test_list_total_is_filtered_count`, existing `test_list_paginates` unchanged and green; default order identical (`created_at DESC, id ASC`).
- [x] AC4 OpenAPI params + declared 404 (get/patch/delete) and 409 (post, delete) — `_route` helper in router.py; `test_organizations_list_query_params`, `test_organizations_declared_error_responses`, `test_organizations_list_422_is_envelope`; existing contract tests (envelope refs, no shared-dict mutation) green. `require_permission` returns a fresh `responses` dict, so `_route` does not mutate shared state.
- [x] AC5 tests incl. 403 without `admin:organizations:read` — `test_list_without_permission_403` (also checks 403 precedes 422 for `sort=bogus`).

### Issues
None.

### Scope
Changed files match plan.md's file list (plus workflow files: backlog row, ticket state, consult notes, plan, impl-log). `users/repository.py` change is limited to the `escape_like` import move, as planned. No out-of-plan code changes.

### Rules R1–R11
No violations found. R1: router declares params only, service passes through, SQL only in repository; `app/core/listing.py` has no FastAPI/modules imports. R2: no schema change, `alembic check` clean. R3: auth class/permissions unchanged. R6: 422/403 via existing envelope. R9: params optional and additive, default order unchanged. R11: `q` not logged.

### Nits (do not block)
- impl-log says "alembic check: no model change, none needed" rather than reporting a run; reviewer ran it (clean). Next time record the actual command output.
- Implementer used the plan's documented fallback for `SearchTerm` (strip in a BeforeValidator) — correctly recorded in impl-log.
- `test_list_default_order_unchanged` mixes id and slug assertions; readable enough, could assert the full expected list in one line.

### Follow-ups (out of scope)
- As listed in plan.md: reject unknown query params (reconcile with USR-18 AC), `sort` on `GET /api/v1/users`, hoist `_route`/`_with_404`/`_with_409` into one shared helper, trigram indexes, verify production `lc_collate` for `name` sort, NFC-normalize org names on write.
