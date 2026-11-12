# Review — USR-02c

## Attempt 1 — 2026-11-12
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (exit 0): ruff "All checks passed!"; format "188 files already formatted"; mypy "Success: no issues found in 52 source files"; pytest "499 passed in 18.83s". Matches impl-log (499 passed); impl-log says 187 formatted files vs 188 here, a count difference only (no failure), not a blocker.
- alembic check: not required (no migration, no model change; `git diff c2a3a0f...HEAD` touches no `migrations/` or `models.py`).

### Acceptance criteria
- [x] AC1 flag gating — `app/main.py:43-45` mounts `assignments_router` only inside `if settings.admin_rbac_api_enabled`; `test_flag_off_404` (GET/POST/DELETE → 404) passes; all other tests use `admin_rbac_api_enabled=True`.
- [x] AC2 permissions — `assignments_router.py` `openapi_extra=permission("admin:users:read" | "admin:roles:assign")`; `test_route_permission_map` asserts the exact map.
- [x] AC3 list — `repository.py:324` `page_assignments` (joins Role/App, `active` computed with `func.now()`, order `created_at desc, id`); `test_list_happy_path`, `test_list_order_created_at_desc`, `test_list_filter_by_app`, `test_list_unknown_app_empty_page`, `test_list_bad_app_key_422`, `test_list_active_flag`, `test_list_unknown_user_404`, `test_list_pagination_limit_over_100_422`.
- [x] AC4 assign 201 app/org scope — `test_assign_app_scope_201`, `test_assign_org_scope_201` (incl. `org_id: null` on a `required` app, A4).
- [x] AC5 422s — `service.py:510/514` unknown_role, `:534` org_id_not_allowed, `:537` unknown_org, `:545` expires_in_past (DB clock), `schemas.py:199-203` `extra="forbid"` + `AwareDatetime`; tests `test_assign_unknown_role_422`, `test_assign_naive_expires_at_422`, `test_assign_past_expires_at_422`, `test_assign_org_id_on_none_app_422`, `test_assign_unknown_org_422`, `test_assign_extra_field_422`.
- [x] AC6 duplicates — `service.py:551-562` active → 409, expired → replaced in place (same id/created_at); `test_assign_active_duplicate_409[False/True]` (NULLS NOT DISTINCT at app scope), `test_assign_expired_duplicate_replaced`.
- [x] AC7 super-admin expiry → 422 — `service.py:539-541`; `test_assign_super_admin_with_expiry_422`.
- [x] AC8 revoke — `service.py:580-603`, row scoped to `user_id` (`repository.py:346`); `test_revoke_204`, `test_revoke_twice_404`, `test_revoke_unknown_404` (incl. unknown user), `test_revoke_other_users_assignment_404`.
- [x] AC9 last-super-admin guard — `service.py:591-597` with `repository.py:385-409` (app scope, not expired by DB clock, user `active`, excluding target); `test_revoke_last_super_admin_409`, `test_revoke_one_of_two_super_admins_204`, `test_revoke_super_admin_other_holder_disabled_409`, `test_revoke_expired_super_admin_204`.
- [x] AC10 locking — `service.py:511` and `:586` lock the role's app before any guard/write; re-read under the lock with `populate_existing`; `test_rbac_locking.py › test_assignment_writes_lock_role_app_first` (POST, DELETE stoktok, DELETE super-admin incl. guard count after the lock), `› test_super_admin_revoke_holds_admin_lock` (55P03 on NOWAIT).
- [x] AC11 error envelope — every 4xx test asserts `error.code`; `_assert_unprocessable` asserts `details == [{field, reason}]` exactly; no values echoed (messages are static strings).
- [x] AC12 `make check` green — above.
- [x] Ticket AC4 (tests listed in the USR-02 plan for 02c, CLI moved to USR-02f) — every test in plan.md "Tests to write" exists and passes, plus two extras.

### Issues
None blocking.

### Scope
Changed files = plan's list (`assignments_router.py`, `schemas.py`, `service.py`, `repository.py`, `main.py`, `tests/test_rbac_assignments_api.py`, `tests/test_rbac_locking.py`, `tests/rbac_factories.py`) plus workflow bookkeeping (`work/backlog.md`, ticket state, impl-log). Deviation (reused existing `assign` factory instead of a new `make_assignment`) is justified in impl-log and reduces the diff. No out-of-plan code.

### Rules R1–R11
No violations found.
- R1: router only calls `AssignmentService`; service raises `NotFoundError`/`ConflictError`/`UnprocessableError`; SQLAlchemy only in `repository.py`.
- R2: no schema change. R3: class 1 (Bearer) with declared `x-permission`, unauthenticated until ADM-002 behind the existing flag; `_admin_apis_gate` untouched. R4: `# R4` markers on insert, replacement and revoke. R5: no config. R6: envelope + `{field, reason}` details. R8: tests cover happy path, 404, 409, 422 (no auth yet). R9: `/api/v1/users/{user_id}/role-assignments`, `Page[T]`, UUIDs, timestamptz. R11: no email/token in responses or errors.

### Nits (do not block)
- 💭 `service.py:511` — if the role's app is deleted between the unlocked `role_with_app` read and `_locked_app`, the client gets 404 "App not found" instead of the 422 `unknown_role` used for the deleted-role case at `:514`. Extremely narrow race; consider catching it and mapping to `unknown_role` when ADM-002 touches this code.
- 💭 `service.py:517-519` — the "unreachable" re-read raises `ConflictError`; fine, but an assertion-style comment is all it needs.
- 💭 `test_rbac_locking.py` `_is_guard_count` relies on lowercase `count(` in compiled SQL; robust today, brittle if the guard query changes shape.

### Follow-ups (out of scope)
- FU1–FU5 from plan.md stand (notably FU1: guard bypass via user disable/delete in ADM-002; FU2: `org_id` FK in USR-06).
