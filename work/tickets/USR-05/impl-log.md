# Implementation log: USR-05 (slice USR-05a)

## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| `app/modules/app_credentials/__init__.py` | new domain module |
| `app/modules/app_credentials/models.py` | `AppCredential` model (CHECKs match the migration; `__repr__` has no hash) |
| `app/modules/app_credentials/codec.py` | `new_credential`, `parse_credential`, `secret_hash` |
| `app/modules/app_credentials/schemas.py` | `AppCredentialRead`, `AppCredentialIssued` |
| `app/modules/app_credentials/repository.py` | add, count_active, page, get, revoke (SQLAlchemy only, R1) |
| `app/modules/app_credentials/service.py` | issue / list / revoke with app lock, `admin:*` on issue, audit (R4) |
| `app/modules/app_credentials/router.py` | 3 Bearer routes (R3 class 1), `no-store` on POST |
| `migrations/versions/20261117_f3a8d51c9e27_usr05a_app_credentials.py` | table, hand-written, working downgrade (R2) |
| `app/db/models.py` | import and export `AppCredential` |
| `app/main.py` | mount the router |
| `app/modules/audit/actions.py` | `APP_CREDENTIAL_ISSUED/REVOKED`, `AuditTarget.APP_CREDENTIAL` |
| `docs/adr/0016-app-credential-auth-class-and-manifest.md` | ADR, Proposed, pending owner |
| `CLAUDE.md` | R3 class (4) and R9 exception, wording as plan step 8 (owner-approval edit; edit succeeded, so no fallback text in the ADR) |
| `docs/identity-architecture.md` | §4 manifest row and credentials row; D25 |
| `docs/deploy.md` | "App credentials (USR-05a)" runbook |
| `tests/app_credential_factories.py` | `make_credential` |
| `tests/test_app_credentials_codec.py` | codec tests |
| `tests/test_app_credentials_api.py` | API tests |
| `tests/test_app_credentials_migration.py` | constraints, cascade, SET NULL, downgrade |
| `tests/test_route_auth_classes.py` | 3 `BEARER` entries, `credential_id` in `_fill` |
| `tests/test_audit_coverage.py` | 2 `AUDITED` routes with scenarios, count 16 -> 18 |

Index choice (plan left it open): both `ix_app_credentials_app_id_created_at` (paged list) and the
partial `ix_app_credentials_app_id_active` (active count).

### Tests added / changed
- `test_app_credentials_codec.py`: format and entropy, round trip, malformed (12 cases), sha256.
- `test_app_credentials_api.py`: issue stores hash and `no-store`; wildcard required (denied row);
  missing route permission; unknown app 404; admin app 409; disabled app 409; third active 409
  then rotation; list metadata only; list unknown 404; revoke audits; revoke twice no-op; unknown
  or foreign credential 404; revoke needs only `admin:apps:write`; audit rows never contain the
  credential; app delete cascades.
- `test_app_credentials_migration.py`: constraints / unique / partial index / SET NULL / cascade;
  downgrade and re-upgrade.
- `test_route_auth_classes.py`, `test_audit_coverage.py`: new entries picked up by the existing
  classification, 401 sweep, OpenAPI and audit tests.

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!";
  "272 files already formatted"; mypy "Success: no issues found in 123 source files";
  pytest "2042 passed, 1 warning in 163.01s". No flake seen in the final run.
  (Intermediate runs: 2 failures in my own new tests, fixed: principal without a real user broke
  the `created_by` FK; audit-row order within one transaction is not deterministic, so the test
  sorts the actions.)
- `alembic check`: "No new upgrade operations detected." Also `alembic downgrade -1` then
  `upgrade head` on the dev DB, then `alembic check` clean; the migration test does the same on the
  test DB.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- USR-05b: manifest route, `authenticate`, rate-limit scope, `docs/auth-integration.md`.
- Spec §5.3 one-sentence addition (product-manager); credential `expires_at` / `label`.

### Scope self-check
- Could the diff be smaller? No: every file is in the plan list.
- Diff size: not measured (no git commit requested; changes are in the working tree).
