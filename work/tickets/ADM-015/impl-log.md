# Implementation log — ADM-015

## Attempt 1

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py` | new data-only migration on the single head `c8a1f4e72d95`; seeds `admin:stats:read` (`is_admin`, no role), downgrade deletes it |
| `app/modules/stats/__init__.py`, `schemas.py`, `repository.py`, `service.py`, `router.py` | new stats module (plan files) |
| `app/api/router.py` | mounts `stats_router` |
| `app/modules/rbac/repository.py` | `member_counts` (shared OQ5 function) and `list_app_summaries`; `Sequence` import |
| `app/modules/audit/repository.py` | `count_actions_since`; docstring and `Sequence` import |
| `tests/test_stats_api.py`, `tests/test_stats_repository.py`, `tests/test_rbac_member_counts.py`, `tests/test_stats_migration.py` | new tests listed in the plan |
| `tests/test_route_auth_classes.py` | BEARER map entry for `GET /stats/dashboard` |
| `tests/test_rbac_schema.py` | `admin:stats:read` in `ADMIN_LEAF_PERMISSIONS` |
| `CLAUDE.md` | R9 exception (D26) |
| `docs/identity-architecture.md` | §4 row and decision D26 |
| `docs/spec-v1.md` | OQ4-OQ6 ticked with answers; §5.9 updated; ADM-015 note |
| `docs/deploy.md` | `'admin:stats:read'` in pre-ADM-002b query 2a |

### Tests added
- `tests/test_stats_api.py` (16 tests: shape, user/org/app counts, sign-ins 24 h and boundary, sign-ups, members per app, 401, 403, permission, wildcard, no-store, no audit row, query count = 7 statements, independent of app count)
- `tests/test_stats_repository.py` (sign-up HCM month boundary, window and zero-fill, audit index use)
- `tests/test_rbac_member_counts.py` (5 tests)
- `tests/test_stats_migration.py` (downgrade/upgrade of the permission)

### Check results
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", "309 files already formatted", mypy "Success: no issues found in 144 source files", pytest "2270 passed, 1 warning in 198.96s". The flaky `test_purge_batches_share_cutoff` did not fail.
- `alembic heads`: single head `e2b7a94d1c58` (was `c8a1f4e72d95`). `alembic check`: "No new upgrade operations detected." Downgrade to `c8a1f4e72d95` and upgrade to head both ran cleanly.
- Note: the local dev DB was one revision behind before I started, so my first `alembic downgrade -1` rolled back ADM-006 there; I re-upgraded to head (the dev `org_entitlements` table was recreated empty). Test DB unaffected.

### Out-of-plan files
None.

### Follow-ups
- Tests use a text mirror of the audit count for the EXPLAIN check (as planned); it can drift from `count_actions_since` if that query changes.
- Only super-admins see the dashboard until `admin:stats:read` is added to a staff role (plan owner decision 2).

### Scope self-check
No ADM-016 field, no apps router/schema change, no new env keys, no model change.
