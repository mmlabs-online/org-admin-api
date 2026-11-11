# Review — USR-01b

## Attempt 1 — 2026-11-11
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest), run with `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` against local Postgres 16 (already online):
  - `ruff check .` → `All checks passed!`
  - `ruff format --check .` → `111 files already formatted` (impl-log says 110; ruff's count of formatted files, not a result mismatch — every check is green in both)
  - `mypy app` → `Success: no issues found in 40 source files`
  - `pytest -q` → `79 passed in 2.45s` (matches impl-log: 79 passed)
- alembic check (no model change; run anyway): `DATABASE_URL=…/org_admin_test uv run alembic check` → `No new upgrade operations detected.`

### Acceptance criteria
- [x] AC1 `GET /api/v1/users?email=&status=&limit=&offset=` → `Page[UserRead]`, case-insensitive contains with LIKE escaping, order `created_at desc, id` — evidence: `app/modules/users/repository.py:9-11` (`escape_like`, `\` first), `:21-37` (`ilike(..., escape="\\")`, filtered `total`, `order_by(User.created_at.desc(), User.id)`); `app/modules/users/router.py:21-36`; tests `test_list_orders_by_created_at_desc_then_id` (incl. tie on `created_at`), `test_list_paginates_and_total_respects_filters`, `test_list_email_filter_is_case_insensitive_contains`, `test_list_email_filter_escapes_like_wildcards` (`%`, `_`, `\`, `a_b` vs `axb`), `test_list_status_filter`, `test_list_rejects_bad_query` (6 cases) — all pass.
- [x] AC2 `GET /api/v1/users/{id}` → `UserRead`/404; `PATCH {status}` with extra fields / explicit null → 422 — evidence: `app/modules/users/schemas.py:23-31` (`extra="forbid"`, required `status: UserStatus`), `app/modules/users/service.py:14-34`; tests `test_get_user`, `test_get_missing_user_returns_404` (id not echoed), `test_get_bad_uuid_returns_422`, `test_patch_disables_and_reenables` (DB row checked), `test_patch_same_status_is_noop` (`updated_at` unchanged, then changed), `test_patch_missing_user_returns_404`, `test_patch_rejects_invalid_bodies` (`{}`, null, `banned`, extra `email`, extra `password_hash`; row unchanged) — all pass.
- [x] AC3 `UserRead` exact keys, never `password_hash` — evidence: `app/modules/users/schemas.py:9-20`; `test_user_read_has_exact_keys` asserts the key set on list item, GET and PATCH bodies and that neither `password_hash` nor the hash value appears in the response text.
- [x] AC4 Permissions declared per route (`admin:users:read` list/get, `admin:users:write` PATCH) — evidence: `app/core/permissions.py`, `app/modules/users/router.py:21,40,45`; `test_users_routes_declare_permissions` asserts the exact (method, path) → permission map via the OpenAPI schema (deviation from `app.routes` justified in impl-log: FastAPI 0.162 lazy included routers; same assertion strength).
- [x] AC5 Exposure gated: router mounted only when `ADMIN_USERS_API_ENABLED=true` (default false), refused in staging/production — evidence: `app/core/config.py:50-51,69-75`, `app/main.py:37-38`, `.env.example:17`; tests `test_users_api_not_mounted_by_default` (404 ×3), `test_users_api_flag_refused_in_staging_and_production[staging|production]`, `test_users_api_flag_allowed_in_local_and_test[local|test]`.
- [x] AC6 Tests: filters, pagination, 404, status change, 422 cases, key set — all present in `tests/test_users_admin.py` and green. 401/403 not possible before ADM-002 (no auth exists) — recorded as FU1.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: router maps HTTP only; `UserService` raises `NotFoundError`; SQL in `UserRepository`. `self.repo.session.flush()/refresh()` in `set_status` mirrors the reference `OrganizationService.update`.
- R2: no schema change; `alembic check` clean.
- R3: class 1 (Bearer) declared in router docstring; permissions declared; exposure fail-closed via flag + validator until ADM-002.
- R4: no audit infra yet; `# R4` marker at `service.py:29` (FU3).
- R5: flag via `Settings`, key in `.env.example` without value; no logging added.
- R6/R9: existing envelope; 200/404/422; `/api/v1/users`, `PATCH`, `Page[T]` with `limit` ≤ 100, UUID ids, `timestamptz`.
- R7: every changed app/test file is in the plan's list; `work/` files are workflow bookkeeping. `app/api/router.py` unchanged as required.
- R11: no `password_hash` in any schema; 404 message has no id; no emails/filters logged.

### Nits (do not block)
- `ruff format --check` file count in impl-log (110) differs from the reviewer run (111); results identical. Record exact counts from the final run next time.
- `tests/test_users_admin.py:283-285` builds a `User` inline instead of using `_user(...)` (needed for a custom hash value; a `password_hash` kwarg on `_user` would keep one helper).

### Follow-ups (out of scope)
- FU1 (ADM-002): remove `admin_users_api_enabled` + validator, mount behind Bearer `aud=admin` + `require_permission` from the `x-permission` declarations, add 401/403 tests, self-disable / last-admin guards; reconcile ADR-0001's `admin:users:disable` example with `admin:users:write`. Route-enumeration tooling must handle FastAPI's lazy `_IncludedRouter` (use OpenAPI or the source routers).
- FU2 (USR-03/03b): enforce `disabled` (reject login, revoke device sessions, refuse tokens).
- FU3 (ADM-004): audit status changes in the same transaction; decide on auditing PII reads.
- FU4 (owner): confirm user PII and the disable action are outside the §6.1 accepted risk until ADM-002; confirm whether a public staging exists.
- FU5: `pg_trgm` index on `users.email` if contains-search gets slow.
- FU6: disable `/openapi.json` in production.
