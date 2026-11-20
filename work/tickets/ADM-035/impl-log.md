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

## Attempt 3 (REWORK 2) — 2026-11-20

Fixes for review.md attempt 1 only.

### Files touched
- `tests/test_deployer_credentials_api.py` — Issue 1: wrong secret is now `mmdk_<key_id>_` + 43 x `A` (guaranteed different from the real secret, no 1/16 collision); `pytest.raises(UnauthorizedError)`, `noqa: B017` dropped. Issue 2: service logger re-enabled in the test (as in `test_rejection_logs_reason_and_key_id_only`, restored in `finally`); positive assertion that exactly `deployer_credential_denied reason=secret key_id=...` was captured, then raw/secret not in the captured text.
- `app/modules/deployer_credentials/codec.py` — nit: comment "64 chars" -> "65 chars".

### Flake check
50 consecutive runs of `test_value_never_logged_or_audited`: 50 passed, 0 failed.

### Checks
- ruff check: All checks passed!
- ruff format --check app tests migrations: 350 files already formatted
- make typecheck: Success: no issues found in 181 source files
- make test: 3185 passed, 1 warning in 448.17s

### Out-of-plan files
None.

### Follow-ups
- Same "64 chars" comment in `app/modules/app_credentials/codec.py:11` (out of scope).
- appsec 🟡: `IssuedDeployerCredential.credential` could use `field(repr=False)`.
