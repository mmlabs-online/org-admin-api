# Review — USR-28

## Attempt 1 — 2026-11-27
Verdict: PASS

Implementation commit d820596 (diff vs efe6ddb). Branch `claude/festive-volta-rikup7`.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed.
- `uv run ruff format --check app tests migrations` — 450 files already formatted.
- `uv run mypy app` (strict) — Success: no issues found in 216 source files.
- `uv run pytest -q` (local Postgres, org_admin_test) — **4569 passed, 2 warnings in 841.18s**, exit 0 (matches impl-log: 4569 passed).
- `uv run alembic check` (local dev DB `org_admin`, default DSN, no `.env`) — No new upgrade operations detected.
- `python3 -I .github/scripts/risk_tier.py --base origin/main` — **medium** (matches the plan's prediction).
- Scope: `git diff --name-only efe6ddb d820596` = the 8 plan files + `tests/test_audit_coverage.py` (justified under Out-of-plan files) + impl-log. Other files in `origin/main...HEAD` (`work/backlog.md`, `work/batches/2026-11-27-1.md`, `work/tickets/USR-27/ticket.md`, USR-28 `ticket.md`) are orchestrator bookkeeping from efe6ddb / 967f25b. No High path changed (no migration, nothing under `app/core/`, `app/modules/auth/`; `normalize_email` and `read_json_body` are imported only).

### Acceptance criteria
- [x] 1. Exact, case-insensitive match after sign-up normalisation via `email_match=exact` — `app/modules/users/service.py:96-98` (`normalize_email` = `strip().lower()`, `app/modules/auth/service.py:39-40`); `app/modules/users/repository.py:166-167` `User.email == email` on CITEXT. Tests `test_get_exact_hit`, `test_get_exact_case_insensitive_and_trimmed`, `test_post_exact_hit`, `test_post_exact_case_insensitive_and_trimmed` (tests/test_users_search.py:64-129) — pass.
- [x] 1a. No partial hit in exact mode — `test_get_exact_no_partial_hit` (:79), `test_post_exact_no_partial_hit` (:132): `an@` does not return `joan@`, `n@<tag>` → total 0.
- [x] 1b. Default stays `contains`, behaviour unchanged — repository.py:168-169 keeps the `ilike` branch byte-for-byte; no normalisation in contains mode (service.py:97 only when exact). `test_get_default_is_contains` (:89), `test_get_explicit_contains_matches_default` (:94), `test_post_default_is_contains` (:141); existing `tests/test_users_admin.py`, `tests/test_users_filters.py` unchanged and green.
- [x] 1c. `email_match` without `email` → 422 `unprocessable` / `email_required` — service.py:92-95 (raised before the app/role lookups, so GET and POST answer identically); `test_email_match_without_email_422` (GET+POST, :109). Bad enum → 422 `validation_error` loc `["query","email_match"]`: `test_get_bad_email_match_422` (:102).
- [x] 2. `POST /api/v1/users/search`, same filters/pagination/include, 200 — router.py:180-210 (declared before `/{user_id}` at :213), `status_code=200`; body model `UserSearch` schemas.py:36-50 (`extra="forbid"`, limit 1..100 = 20, offset ≥ 0). Identical page shape through the shared helper `_users_page` (router.py:38-67), used by GET (:152) and POST (:208). `test_post_parity_with_get` (:148) asserts `post.json() == get.json()` with email+status+app+role_id+include=access+limit/offset; `test_post_include_access_shape`, `test_post_empty_body_lists_all`, `test_post_pagination_bounds`, `test_post_unknown_app_422`, `test_post_unknown_role_422`.
- [x] 2a. Email not in URL / app logs — `test_post_email_not_logged` (:275): request URL has no query string, captured DEBUG records do not contain the email. The service logs nothing for `list`.
- [x] 2b. Body cap and hardening — `read_json_body(..., cap=4 KiB, timeout 10 s)` router.py:34-35, 195-197; 411/413/415/422 tests (:226-272); 422 responses never echo the submitted value (`"secret-" not in resp.text`).
- [x] 2c. No audit — `test_post_writes_no_audit` (:288), audit_log row count unchanged.
- [x] 3. Permission `admin:users:read`, 401/403 — `_search_kwargs` → `require_permission("admin:users:read", …)` router.py:157-177, router-level `Depends(current_admin)`; `test_search_requires_token` (401, GET exact + POST), `test_search_requires_users_read` (`admin:orgs:read` → 403 `permission_denied`; `admin:users:read` → 200).
- [x] 3a. R3 route registry — `tests/test_route_auth_classes.py:116` `("POST", f"{V1}/users/search"): "admin:users:read"`; registry tests (`test_bearer_route_permission_map`, 401 without token / cookie only, machine credential → 401, CORS matcher agreement) are parametrised over `BEARER` and pass.
- [x] 3b. OpenAPI contract — `test_openapi_search_contract` (:320): GET `email_match` enum `["exact","contains"]`, POST requestBody fields, `x-permission`, 200 and no 201.
- [x] Docs — CLAUDE.md R9 exception `POST /api/v1/users/search (… 200 Page[T], not 201; D43)`; `docs/identity-architecture.md:341` row D43 (D43 was free; D42 was the last row).
- [x] `make check` equivalent green (above).

Ming verification after merge (not reviewer's to verify; carry to the PR):
- After admin-pan PAN-015c switches to `POST /api/v1/users/search` with `email_match: "exact"`: on `api.mmlabs.online`, assign a role by email and confirm in `docker compose logs` (docs/deploy.md:179) that the request line is `POST /api/v1/users/search` with no email in it.

### Deviation — `READ_ONLY_POST` in tests/test_audit_coverage.py
Sound and narrow. `test_every_mutating_bearer_route_is_mapped` (tests/test_audit_coverage.py:689-691) equates "non-GET" with "mutating"; the new route is the first Bearer POST that is a pure read (D43, recorded R9 exception), so it must be excluded or the guard would demand an audit row for a read, contradicting R4 and the plan (AC 2c). The exclusion is a single explicit `(method, path)` tuple with a D43 comment (line 686), not a pattern; `len(AUDITED) == 35` is unchanged, so any other new non-GET route still fails the guard; and the "no audit" claim is itself asserted by `test_post_writes_no_audit`. It is test-only and recorded under Out-of-plan files in impl-log. Accepted.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: rule (`email_required`, normalisation) in the service; ORM equality in the repository; router only parses and builds the response.
- R3: class (1) Bearer + `admin:users:read`, registry row added.
- R4: read, no audit (asserted).
- R5/R11: no email logged; static error message `"email_match requires email"`; validation errors do not echo input.
- R6: 411/413/415/422 through the existing `AppError` path, standard envelope.
- R8: happy, 401/403, 422/411/413/415 covered; no 404 case exists for a search (empty page).
- R9: additive change, exception recorded in R9 and D43; `Page[T]`, limit ≤ 100.

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡.

### Nits (do not block)
- tests/test_audit_coverage.py:686 — could add `assert READ_ONLY_POST <= set(BEARER)` so a renamed/removed search route cannot leave a stale exclusion behind.
- tests/test_users_search.py:275 — `test_post_email_not_logged` cannot see the uvicorn access log (ASGI transport); the URL-has-no-query assertion is the real guarantee. Fine as is; Ming's post-merge check covers production.
- docs/identity-architecture.md:183 §4 table row "Admin: `/api/v1/users` (list, get, status, display name …)" does not mention `POST /users/search`; D43 covers it.

### Follow-ups (out of scope)
- Access-log query-string stripping for `GET /api/v1/users?email=` (High, `app/core/`), separate ticket (plan Blocking question 1, default).
- admin-pan PAN-015c: switch the role-assign picker to `POST /api/v1/users/search` with `email_match: "exact"`; then revisit deprecating GET `email`.
- Spec §5.15 (docs/spec-v1.md:336) "current contract" snapshot and identity-architecture §4 row 183: mention `email_match` and `POST /users/search` at the next docs refresh.
