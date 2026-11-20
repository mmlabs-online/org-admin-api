## Attempt 1 — 2026-11-20

### Files touched
| File | Required because |
|---|---|
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | `release_deployment` bucket and 4 settings (OQ46, R5) |
| `app/modules/audit/actions.py` | `RELEASE_DEPLOYMENT_REPORTED` |
| `app/modules/releases/write_schemas.py` | `ReleaseDeploymentReport`, `DEPLOY_RUN_URL_FULL` |
| `app/modules/releases/rules.py` | `decide_report`, `deployed_at_window_error`, `Deployment`, `ReportDecision` |
| `app/modules/releases/errors.py` | `ReleaseAlreadyDeployedError` |
| `app/modules/releases/repository.py` | `app_id_by_key`, `db_now`; `insert_if_absent` status now overridable |
| `app/modules/releases/deployment.py`, `deployment_router.py` | service and route |
| `app/api/router.py` | mount |
| `app/modules/deployer_credentials/deps.py` | docstring |
| `CLAUDE.md`, `docs/identity-architecture.md`, `docs/adr/0021-...md`, `docs/deploy.md` | docs per plan |
| `tests/test_release_deployment_schemas.py`, `tests/test_releases_deployment_api.py`, `tests/test_releases_deployment_concurrency.py` | new tests |
| `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py` | registry, OpenAPI, no-CORS, 401 and audit coverage |

### Tests added / changed
- test_release_deployment_schemas.py (schema and pure rules in one file; 40 tests)
- test_releases_deployment_api.py (49), test_releases_deployment_concurrency.py (3)
- test_route_auth_classes.py: exact set, `deployerCredential` scheme, no-CORS, admin JWT and app key 401
- test_audit_coverage.py: classified + audited
- Report-first then app PUT enriches (200, no spurious 409; thin record has `images=[]`, `changes=[]`).

### Deviations from the plan (ADM-034 actual code)
- Schema model is `ReleasePut` in `write_schemas.py`; the app write lives in `app_write.py` (`ReleaseAppWriteService`), not `service.py`.
- `insert_if_absent` hard-coded `status=pending` plus `**fields`, so passing `status` raised a duplicate keyword; changed to `{"status": pending, **fields}` (behaviour unchanged for ADM-034).
- `rules.py` uses `StoredDoc`/`Decision`; new names `Deployment`/`ReportDecision`/`ReportKind` avoid clashes.
- Pure rules tests merged into `test_release_deployment_schemas.py` instead of a separate rules file.
- Strict mode: tests that build the report from dicts need `model_validate_json` or typed constructor.
- Window check runs only when `deployed_at` is present (failed reports have none).

### Checks (actual output summary)
- ruff check / format --check on app and tests: clean. `make check` stops at the known `.github/scripts/risk_tier.py` lint failure (untouched), so remaining steps were run directly.
- mypy app: Success, no issues in 199 source files (`make check` mypy scope is `app`; `mypy tests` has pre-existing errors, not gated).
- alembic check: run, no drift reported (last line below).
- pytest: 3688 passed, 1 warning in 598.95s.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- `mypy tests` has ~140 pre-existing errors; a few new ones in the new tests (Optional `changes` indexing) are of the same kind.
- Cross-repo: deployment repo step that sends the report; Ming ops step: issue first deployer key.

### Scope self-check
- Could the diff be smaller? No.
