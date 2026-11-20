## Attempt 1 — 2026-11-20

Environment: no Docker; local Postgres started with `service postgresql start`. Alembic head was `89fd8d667a25` (ADM-033), confirmed with `uv run alembic heads`; the new migration `5b7d1e9c3a42` chains on it. Registry counts recounted: `len(AUDITED)` was 28 after ADM-033, now 30 (plan said 27 -> 29).

### Files touched
| File | Required because |
|---|---|
| `app/modules/deployer_credentials/{__init__,codec,models,schemas,repository,service,deps,router}.py` | new module (plan) |
| `app/api/router.py` | mount the router |
| `app/db/models.py` | register `DeployerCredential` (R2) |
| `migrations/versions/20261120_5b7d1e9c3a42_adm035_deployer_credentials.py` | table, reversible (R2) |
| `app/modules/audit/actions.py` | `DEPLOYER_CREDENTIAL` target, issued/revoked actions (R4) |
| `tests/deployer_credential_factories.py`, `tests/test_deployer_credentials_{codec,api,auth,concurrency,migration}.py` | R8 tests |
| `tests/test_route_auth_classes.py` | 3 BEARER entries, empty `DEPLOYER_CREDENTIAL`, `mmdk_` rejection tests |
| `tests/test_audit_coverage.py` | 2 AUDITED entries, count 30 |
| `tests/test_openapi_contract.py` | `_EXPECTED_ERRORS` for 3 routes |
| `docs/deploy.md`, `docs/identity-architecture.md` | runbook section, API table row |

### Tests added / changed
- tests/test_deployer_credentials_{codec,api,auth,concurrency,migration}.py (plan list), route-class, audit-coverage and openapi additions.
- Plan deviations in tests: raw value length is 65, not 64 (5+16+1+43); the non-`admin:*` 403 code is `admin_wildcard_required` (details `{"required": "admin:*"}`), not `permission_denied`; `SystemActor` field is `.name`; the log test attaches its own handler because Alembic's `fileConfig` leaves app loggers disabled; admin-issuing tests pass a real `admin_user` for the `created_by` FK.

### Checks (actual output summary)
- ruff check (`--exclude .github/scripts`): All checks passed
- format:check (app tests migrations): clean
- typecheck: Success: no issues found in 181 source files
- alembic check: No new upgrade operations detected
- test (full `make test`): 3185 passed, 1 warning in 442.16s

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- None.

### Scope self-check
- Could the diff be smaller? No; it mirrors app_credentials minus app binding.
- Diff size: see commit.
