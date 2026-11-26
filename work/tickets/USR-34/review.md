# Review — USR-34

## Attempt 1 — 2026-11-26
Verdict: PASS

Reviewed: implementation commit `fcb55ea` (diff vs `236cd35`), branch `claude/festive-volta-rikup7`.
Risk tier (`python3 -I .github/scripts/risk_tier.py --base origin/main`): **medium**. Ming approved all plan defaults
and gave merge approval for this Medium ticket (2026-11-26, batch 2026-11-26-2).
Review triggers: none apply — areas api, db; changed code only under `app/modules/users/**` (no `app/core/`,
`app/api/deps.py`, auth, rbac, permissions, tokens, sessions, migrations, `.env.example`, `Dockerfile`, `.github/`).
No `security-review.md` needed.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed.
- `uv run ruff format --check app tests migrations` — 445 files already formatted.
- `uv run mypy app` — Success: no issues found in 216 source files.
- `uv run pytest -q` (full, local Postgres `org_admin_test`) — 4416 passed, 2 warnings (pre-existing forkpty
  DeprecationWarning), 862 s. Matches impl-log.
- `uv run alembic check` (local dev DB, `localhost:5432/org_admin`) — No new upgrade operations detected. (No model
  change in this diff.)
- `risk_tier.py --base origin/main` — `medium`.

### Acceptance criteria
- [x] 1a `include=access` shape — `tests/test_users_access.py:68` `test_include_access_shape` (exact key sets at
  every level, nullable keys present); schemas `app/modules/users/schemas.py:31-51`.
- [x] 1b No-include response unchanged — router keeps today's path (`app/modules/users/router.py:52-58`, plain
  `UserRead.model_validate`); `test_without_include_is_unchanged` (`tests/test_users_access.py:93`) asserts body ==
  `Page[UserRead](…).model_dump(mode="json")`, item key order == `UserRead.model_fields`, no `access` in text, no
  `role_assignments` statement. `UserRead` is untouched in the diff; `test_get_and_patch_never_carry_access` (:121).
- [x] 1c User with none → `access: []`, `access_truncated: false` — `test_user_without_access_gets_empty_list` (:134);
  service seeds every requested id (`app/modules/users/service.py:51`).
- [x] 2a Expired excluded — `active_assignment_clause()` in the WHERE (`app/modules/users/repository.py:135`);
  `test_expired_assignments_excluded` (:153); also the 3 expired rows in `test_cap_truncates_at_20_in_order`.
- [x] 2b App- and org-scope rows — `test_app_and_org_scope_rows` (:170).
- [x] 2c Disabled app with `app_status` — no app-status filter in `list_access`; `test_disabled_app_listed_with_status`
  (:191).
- [x] 3a-3c Cap 20 + `access_truncated`, boundary 20/21, per user — cap+1 probe in SQL (`repository.py:149`
  `rn <= per_user`, `service.py:52` `ACCESS_CAP + 1`), truncation `service.py:56,71`; tests :212, :240 (param 20/21),
  :250.
- [x] 3d Order: app key, role key (byte order), app scope first, org id, assignment id — window `order_by`
  `repository.py:122-130` (`collate("C")` on both keys, `org_id.is_not(None)` false first); `test_access_order` (:264)
  incl. `a-c` before `ab`.
- [x] 4 Constant query count — one windowed statement for the whole page (`repository.py:102-152`);
  `test_query_count_constant` (:305, `limit=1` vs `limit=50`, equal counts, exactly one `FROM role_assignments`),
  `test_query_count_without_include` (:330). EXPLAIN in impl-log: index scan on `uq_role_assignments_user_role_org`.
- [x] 5a `include` repeatable enum list; unknown/empty/case/comma-joined → 422 `validation_error` — `router.py`
  `include: list[UserInclude] | None` ; `test_include_rejects_bad_values` (:339, `bogus`, ``, `ACCESS`,
  `access,bogus`), `test_include_repeated_is_idempotent` (:347).
- [x] 5b 401 — `test_include_access_requires_token` (:365).
- [x] 5c 403 / `admin:users:read` alone suffices — `test_include_access_requires_users_read` (:354).
- [x] 5d Pagination unchanged; empty page issues no access query — `test_include_access_pagination` (:371); repository
  returns `[]` without a query for empty ids (`repository.py:108-109`).
- [x] 6 OpenAPI — `tests/test_openapi_contract.py:237` `test_users_list_operation` (query param, enum `["access"]`,
  not required; 200 `anyOf` of both page types; `x-permission`; `UserRead.required` 7 keys; `AccessRole`/`AppAccess`
  required sets). Existing `test_every_error_response_references_envelope` green.
- [x] Separate `UserWithAccessRead(UserRead)` — `schemas.py:45`; `UserRead` unchanged.
- [x] `active_assignment_clause()` exactly per the plan contract — `app/modules/users/repository.py:15`, module level,
  `() -> ColumnElement[bool]`, body `or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())`,
  uncorrelated. USR-35 can import it as named.
- [x] No High path touched — changed app files are `app/modules/users/{repository,router,schemas,service}.py` only;
  `rbac` models imported, not edited.
- [x] `make check` components green (above).

Ming verification after merge (not verified here; carry into the PR):
- admin-pan PAN-032 shows access chips for a 100-user page, "+N more" for a user with > 20 active assignments, "app
  disabled" for a disabled app; optional p95 of `GET /api/v1/users?include=access` < 200 ms (§6).

### Scope
Every changed code/test/doc file is in the plan's file list. The other files in `origin/main...HEAD`
(`work/backlog.md`, `work/batches/*`, `work/tickets/USR-3x/*`, `USR-40/ticket.md`) are workflow state from the batch
commits, not from the implementation commit. No out-of-plan changes.

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: SQL only in the repository, cap/grouping in the service, router HTTP only. R3: class 1, same
`admin:users:read`, no new route. R4: read-only, no audit. R6: 422 through the standard envelope. R8: happy path,
401, 403, 422, pagination tested; 404/409 not applicable (list route, read-only). R9: additive, opt-in, `Page[T]`,
`limit` ≤ 100 unchanged. R11: nothing logged.

### Blockers from review agents
None (no review trigger applies).

### Nits (do not block)
- 💭 `tests/test_users_access.py:64` — `_iso()` is an unused identity helper; delete it.
- 💭 `tests/test_users_access.py:365-368` — the 401 test checks only `"error" in body`; asserting
  `error["code"]` would match the style of the 422 test.
- 💭 `AccessRow.app_status` is typed `str` (`repository.py`) and converted in the service with `AppStatus(...)`;
  fine, but typing it as the model's column type would drop the conversion.

### Follow-ups (out of scope)
- Move rbac's inline active-assignment predicates (`RbacRepository._is_active`, `rbac/repository.py:597-599`, and
  copies) onto one shared definition — High path, separate ticket.
- admin-pan (PAN-032): the users list 200 response is now `anyOf` two page types in OpenAPI; generated clients need
  to narrow on `include=access`.
