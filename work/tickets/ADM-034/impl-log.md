## Attempt 1 — 2026-11-20

Two commits: A = scope column, issuance, scope-aware `authenticate`/`confirm`, registry, docs; B = release PUT.

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261120_58a494f0449e_adm034_app_credential_scope.py` | scope column, CHECK `ck_app_credentials_scope_valid`, index swap; downgrade deletes non-manifest keys first (R2) |
| `app/modules/app_credentials/models.py` | `AppCredentialScope`, `scope` column, CHECK, new partial index |
| `app/modules/app_credentials/repository.py` | `count_active(app_id, scope)` |
| `app/modules/app_credentials/service.py` | `issue(scope)` with `admin` allow-list, `authenticate(scope, denied_action, lock)` in the planned order, `confirm()`, `scope` in view/audit, `repr=False` |
| `app/modules/app_credentials/errors.py` | `AppCredentialScopeError` (`app_credential_scope`) |
| `app/modules/app_credentials/deps.py` | `RequireAppCredential`, `AppCredentialDep` (manifest), `ReleasesCredentialDep` (`lock=False`) |
| `app/modules/app_credentials/schemas.py` | `scope` on read, `AppCredentialIssue`, `repr=False` |
| `app/modules/app_credentials/router.py` | optional issuance body |
| `app/modules/rbac/manifest_router.py` | 403 description only |
| `app/modules/deployer_credentials/service.py` | Q5: `field(repr=False)` on `credential` (incidental, one line) |
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | `app_release` bucket, 4 settings, keys without values |
| `app/modules/audit/actions.py` | `release.created`, `release.updated` |
| `app/modules/releases/write_schemas.py` | strict input models, `check_release_path`, `check_staging_sha` |
| `app/modules/releases/rules.py` | pure decision table |
| `app/modules/releases/body.py` | raw capped JSON body reader, `inline_schema` |
| `app/modules/releases/errors.py` | `ReleaseNotPendingError` |
| `app/modules/releases/repository.py` | `insert_if_absent`, `get_for_update` |
| `app/modules/releases/app_write.py` | `ReleaseAppWriteService.put` |
| `app/modules/releases/app_router.py`, `app/api/router.py` | the route and its mount |
| `app/modules/releases/schemas.py`, `router.py` | `release_summary`/`release_detail` moved to schemas (shared); `pr_url` "untrusted" description |
| `CLAUDE.md`, `docs/identity-architecture.md`, `docs/adr/0016`, `0017`, `0021` | R3 class 4 / R9 text, D30 row, pointers, line-6 fix (original number ADR-0018 / D28 confirmed from git history), §11 decision |
| tests (see below) | AC1-5 |

### Tests added / changed
- New: `test_app_credential_scope_migration.py` (round trip, downgrade deletes releases keys, CHECK), `test_release_rules.py`, `test_release_write_schemas.py`, `test_releases_write_api.py` (62 cases), `test_releases_write_concurrency.py` (2 races).
- Extended: `test_app_credentials_api.py` (11 scope tests), `test_app_credential_auth.py` (6: scope denied row, order, 401 first, repr, confirm re-check), `test_route_auth_classes.py` (route to scope map, `lock` flags, 401 / admin JWT / mmdk / no-CORS parametrised), `test_audit_coverage.py` (release route), `app_credential_factories.py` (`scope`).
- Changed mechanically: `test_app_credential_auth.py` call sites of `authenticate(...)` and `AuthenticatedApp(...)` (new `scope` arg; plan listed only line 244, line 142 needed it too), `test_app_credentials_migration.py` index name.

### Checks (actual output summary)
- typecheck: `mypy app` -> Success: no issues found in 197 source files
- lint: `ruff check`/`ruff format --check` clean for everything I touched; `make lint` fails only on `.github/scripts/risk_tier.py` (kit-managed, pre-existing, 13 errors, untouched by me)
- alembic check: No new upgrade operations detected
- test: `uv run pytest -q` -> 3591 passed, 1 warning in 606s

### Out-of-plan files
None. (`tests/test_audit_coverage.py` and `tests/app_credential_factories.py` are in the plan's list.)

### Follow-ups (noticed, not done)
- `.github/scripts/risk_tier.py` fails `ruff check` / `ruff format --check` (kit-managed: fix in spaceM project-kit); it makes `make check` exit non-zero on lint.
- Alembic `fileConfig` disables module loggers in tests: existing `assert x not in caplog.text` checks in `test_app_credential_auth.py` are vacuous (my release conflict-log test re-enables its logger).
- Orchestrator: Ming ops todo (admin-pan app row, `releases` keys) per plan; per-key rate bucket (Q2) not done.
- Chunked (no Content-Length) bodies get 411 by design; `test_body_cap_413` covers declared-over-cap and over-declared bodies.

### Scope self-check
- Could the diff be smaller? No: every file is in the plan list.
- Diff size: about +2900 / -90 lines including ~1700 test lines.
