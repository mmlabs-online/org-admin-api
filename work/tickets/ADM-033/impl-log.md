# Implementation log — ADM-033

## Attempt 1 — 2026-11-20

Environment: no Docker; local Postgres started with `service postgresql start` (org_admin, org_admin_test). Alembic head confirmed `c4e8a2f7b916` before the migration; now `89fd8d667a25` (single head).
Ming's defaults applied: live route `GET /api/v1/releases/live` returns `Page[ReleaseSummary]`; `created_via` is `app|deployer`; admin-pan collision deferred to ADM-034; unknown app filter gives 200 empty page. ADR is `docs/adr/0021-release-log-and-deployer-credential.md` (cited in comments).

### Files touched
| File | Required because |
|---|---|
| `app/modules/releases/{__init__,models,schemas,repository,service,router}.py` | new domain module: table, contract, 4 Bearer routes |
| `migrations/versions/20261120_89fd8d667a25_adm033_app_releases.py` | R2: table, CHECKs, unique, indexes, working `downgrade()` (hand-reviewed; texts generated from the model constants) |
| `app/db/models.py` | register `AppRelease` |
| `app/api/router.py` | mount the releases router |
| `app/modules/audit/actions.py` | `AuditTarget.RELEASE`, `AuditAction.RELEASE_DELETED` |
| `tests/release_factories.py` | `make_release`, seeds via `AppReleaseRepository.add` |
| `tests/test_releases_{schema_db,migration,api}.py` | AC 1-7 |
| `tests/test_route_auth_classes.py` | 4 `BEARER` entries; `release_id` added to the `_fill` path-param list (needed for the runtime sweep) |
| `tests/test_audit_coverage.py` | DELETE route in `AUDITED`, count 27 -> 28 |

### Tests added / changed
- `test_releases_schema_db.py`: all `test_rejects_*`, unique, accepts-boundaries, FK RESTRICT (16 cases).
- `test_releases_migration.py › test_app_releases_down_up`.
- `test_releases_api.py`: 401/403, OpenAPI, list (order, filters, sort, paging, 422, unknown app, no bodies), detail, live (rollback, failed/pending, env leak, tie, not shadowed), delete (audit ids only, 404), admin app, app delete 409.

### Checks (actual output summary)
- ruff check (`. --exclude .github/scripts`): All checks passed!
- ruff format --check app tests migrations: 310 files already formatted
- typecheck (`make typecheck`): Success: no issues found in 173 source files
- alembic check: No new upgrade operations detected.
- test (full `make test`): 3060 passed, 1 warning in 407.42s

### Out-of-plan files
None (the `_fill` edit is inside the planned `tests/test_route_auth_classes.py`).

### Follow-ups (noticed, not done)
- `rbac/service.py` app-delete 409 message says "App has role assignments" when releases block it (High path; plan follow-up).
- `ReleaseService.list` must be defined last in the class (it shadows the builtin in the class body); commented.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: see `git show --stat`.
