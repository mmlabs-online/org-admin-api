# Implementation log — USR-02c

## Attempt 1 — 2026-11-12

### Files touched
| File | Required because |
|---|---|
| `app/modules/rbac/assignments_router.py` (new) | GET/POST/DELETE `/api/v1/users/{user_id}/role-assignments[/{assignment_id}]`, `x-permission` `admin:users:read` / `admin:roles:assign`; `app` query validated with `APP_KEY_RE` |
| `app/modules/rbac/schemas.py` | `AssignmentCreate` (`extra="forbid"`, `AwareDatetime`), `AssignmentRead` |
| `app/modules/rbac/service.py` | `AssignmentService` (`list`, `assign`, `_grant_locked`, `revoke` + last-super-admin guard), `_assignment_unprocessable`, `_assignment_read`, `# ADM-002 guard` / `# R4` markers |
| `app/modules/rbac/repository.py` | `user_exists`, `role_with_app`, `org_exists_key_share` (`FOR KEY SHARE`), `db_now`, `page_assignments`, `get_assignment_row`, `find_assignment` (`IS NOT DISTINCT FROM`), `add_assignment`/`delete_assignment` (savepoints), `count_other_active_super_admins`, `is_counting_super_admin` (DB clock) |
| `app/main.py` | mount `assignments_router` inside the existing `admin_rbac_api_enabled` block |
| `tests/rbac_factories.py` | `super_admin_role(session)` |
| `tests/test_rbac_assignments_api.py` (new) | API tests (AC1–AC9, AC11) |
| `tests/test_rbac_locking.py` | AC10 locking tests |

### Tests added / changed
- `test_rbac_assignments_api.py` › `test_flag_off_404`, `test_route_permission_map`, `test_list_happy_path`, `test_list_order_created_at_desc`, `test_list_filter_by_app`, `test_list_unknown_app_empty_page`, `test_list_bad_app_key_422`, `test_list_active_flag`, `test_list_unknown_user_404`, `test_list_pagination_limit_over_100_422`, `test_assign_app_scope_201`, `test_assign_org_scope_201`, `test_assign_unknown_user_404`, `test_assign_unknown_role_422`, `test_assign_naive_expires_at_422`, `test_assign_past_expires_at_422`, `test_assign_future_expires_at_201`, `test_assign_org_id_on_none_app_422`, `test_assign_unknown_org_422`, `test_assign_extra_field_422`, `test_assign_active_duplicate_409[app/org scope]`, `test_assign_expired_duplicate_replaced`, `test_assign_super_admin_with_expiry_422`, `test_assign_disabled_user_and_disabled_app_allowed`, `test_revoke_204`, `test_revoke_twice_404`, `test_revoke_unknown_404`, `test_revoke_other_users_assignment_404`, `test_revoke_last_super_admin_409`, `test_revoke_one_of_two_super_admins_204`, `test_revoke_super_admin_other_holder_disabled_409`, `test_revoke_expired_super_admin_204`, `test_role_delete_with_assignment_409`
- `test_rbac_locking.py` › `test_assignment_writes_lock_role_app_first` (POST stoktok, DELETE stoktok, DELETE admin super-admin with guard count), `test_super_admin_revoke_holds_admin_lock`

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!"; format "187 files already formatted"; mypy "Success: no issues found in 52 source files"; pytest "499 passed in 17.59s".
- `alembic check` (against the migrated `org_admin_test` DB): "No new upgrade operations detected." (no migration in this ticket).
- Local Postgres was already running (`pg_isready`: accepting connections); nothing started or created.

### Deviations from plan
- `tests/rbac_factories.py`: did not add `make_assignment`; the existing `assign(session, user, role, *, org_id, expires_at, granted_by)` factory already does exactly that. Only `super_admin_role` added.
- `is_counting_super_admin(role_id, assignment_id)` takes the role id too (the predicate is shared with the count).
- Added two small tests beyond the plan list: `test_list_order_created_at_desc` (rows in one test transaction share `created_at`, so the `desc` part needed its own test) and `test_assign_future_expires_at_201`.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- None beyond the plan's FU1–FU5.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +400 / -2 in modified files, plus 2 new files (56 + 491 lines).
