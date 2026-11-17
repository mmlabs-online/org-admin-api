## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| `app/modules/app_credentials/errors.py` (new) | 403 `app_credential_mismatch` / `app_disabled` (AC1, R6) |
| `app/modules/app_credentials/deps.py` (new) | `current_app_credential` dependency, R3 class 4 |
| `app/modules/app_credentials/service.py` | `AuthenticatedApp`, `authenticate` (compare_digest, single 401, denied row, `last_used_at`) |
| `app/modules/app_credentials/repository.py` | `get_with_app`, `touch_last_used` |
| `app/modules/rbac/manifest.py` (new) | `ManifestService.sync` |
| `app/modules/rbac/manifest_router.py` (new) | `PUT /apps/{key}/manifest`, rate-limited, no `current_admin` |
| `app/modules/rbac/schemas.py` | Manifest request and result models |
| `app/modules/rbac/service.py` | `_build_for_app(only=)`, `PermissionService._create(admin_create_allowed=)` |
| `app/modules/rbac/repository.py` | `roles_holding` |
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | `app_manifest` scope and two optional env keys (R5) |
| `app/modules/audit/actions.py` | `APP_MANIFEST_SYNCED` |
| `app/main.py` | mount the manifest router |
| `docs/auth-integration.md` (new), `docs/consumers.md`, `docs/identity-architecture.md`, `docs/adr/0016-...md` (status line), `docs/deploy.md` (hardened issue snippet, orphan key_id line, rate-limit keys) | AC6, AC9, plan doc list |
| `tests/test_app_credential_auth.py` (new), `tests/test_app_manifest_api.py` (new), `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py` | AC1-AC8 tests |

### Tests added / changed
- `test_app_credential_auth.py`: valid 200, 401 matrix (11 cases, identical bodies), wrong app 403 + denied row, unknown path app 403, disabled 403, disabled under lock, compare_digest spy, last_used_at, rate limit before auth, invalid body without credential 401, no secret in logs.
- `test_app_manifest_api.py` (37 tests): namespace 422 (permissions/roles/member), parametrized validation reasons, extra field, scope_required, limits, member reach, is_admin excluded, idempotency, org app twice, requires_staff, flips (is_admin, scope), override, rebuild re-adds, extra roles additive, role_not_manifest_managed (system/default/owner), member_is_org_owner, not_in_manifest, description, app fields untouched, 3 ETag tests, one audit row, no-op no row.
- `test_route_auth_classes.py`: `APP_CREDENTIAL` allow-list exactness, classification/permission-map/stale/no-bearer-guard updates, OpenAPI `appCredential`, CORS matcher, `test_manifest_no_cors`, 401 without credential, admin JWT on manifest 401, `mmak_` on Bearer routes 401.
- `test_audit_coverage.py`: `APP_CREDENTIAL_AUDITED`, classification test, `test_app_credential_routes_are_audited`.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff clean, 279 files formatted, mypy no issues, pytest `2125 passed, 1 warning in 182.27s` (the warning is the existing forkpty one). The known flake `test_purge_batches_share_cutoff` did not fail.
- `alembic upgrade head && alembic check` (test DB): "No new upgrade operations detected." No migration added.

### Out-of-plan files
None beyond the plan's list (`errors.py` and the ADR-0016 status line are the plan's marked deviations).

### Follow-ups (noticed, not done)
- As in the plan: `mmlabs-auth` `push_manifest` helper, credential `expires_at`/`label`, global body-size limit, per-credential rate bucket, deployment-repo workflow step, `actor_kind` filter for machine rows, 05a `assert` tidy-up and 2-active concurrency test, spec §5.3 one-liner.
- `tests/test_app_credential_auth.py::test_no_secret_in_logs` checks the raw value and secret only; it does not assert on the `key_id` log content.

### Scope self-check
- Could the diff be smaller? No; every file is in the plan.
- Diff size: about 450 lines of production code, 220 lines of docs, 800 lines of tests.
