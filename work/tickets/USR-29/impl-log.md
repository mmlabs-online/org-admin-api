
## Attempt 1

Branch `claude/festive-volta-rikup7`. Alembic head after: `a4c81e6d27f3` (down_revision `911eb99ddf8b`).

### Files touched (each required by plan.md)
- `migrations/versions/20261121_a4c81e6d27f3_usr29_org_members_read_scope.py` (new): CHECK widened; downgrade deletes the keys first (R2).
- `app/modules/app_credentials/models.py`: `ORG_MEMBERS_READ`, `RUNTIME_SCOPES`, CHECK text (byte-identical to migration).
- `app/modules/app_credentials/repository.py`: `touch_last_used_throttled`.
- `app/modules/app_credentials/service.py`: runtime path in `authenticate`, `RUNTIME_TOUCH_INTERVAL_SECONDS = 60`.
- `app/modules/app_credentials/deps.py`: refuse `lock=True` for runtime scopes; `OrgMembersCredentialDep`.
- `app/core/rate_limit.py`, `app/core/config.py`, `.env.example`: IP scope `app_org_members`, `enforce_subject_limit`, four settings.
- `app/modules/rbac/repository.py`: `AppOrgMemberRow`, `org_related_to_app`, `page_app_org_members`.
- `app/modules/audit/actions.py`: `ORGANIZATION_MEMBERS_READ`.
- `app/modules/organizations/errors.py`, `schemas.py`, `app_service.py` (new), `app_router.py` (new); `app/api/router.py` (mount).
- Tests: `tests/test_app_org_members.py` (new, 23), `test_app_credential_auth.py`, `test_app_credentials_api.py`, `test_app_credential_scope_migration.py`, `test_route_auth_classes.py`, `test_audit_coverage.py`, `test_rate_limit.py`.
- Docs: `docs/adr/0025-org-members-read-scope.md` (new), pointer lines in ADR-0016/0021/0022, `docs/identity-architecture.md` (D34), `CLAUDE.md` (R3), `docs/consumers.md`, `docs/spec-v1.md` (dated lines in §5.8, §5.7, §8 Q6).

### Checks
- `ruff check app tests migrations`: All checks passed. `ruff format --check .`: only `.github/scripts/risk_tier.py` (known kit failure, untouched).
- `mypy app`: Success, no issues (201 source files before the last edits; re-run clean on the final tree below).
- `alembic heads`: a4c81e6d27f3 (head); `alembic check`: No new upgrade operations detected.
- Full `pytest`: 3811 passed, 1 warning.

### Out-of-plan files
None.

### Deviations
- `test_runtime_scope_takes_no_app_lock` monkeypatches `RbacRepository.lock_app` to fail instead of holding `SELECT ... FOR UPDATE` in a second connection: test rows are uncommitted in the per-test transaction and invisible to another connection.
- `AppOrgMemberService` imports `_org_not_supported` (private helper) from `auth_service` instead of duplicating it.

### Follow-ups
- Invitations in the member read; `name` token claim; alert on many distinct `org_id` per key; admin-pan offering the scope; tombstones if removal sync is wanted (all from plan).
- Expect rebase hotspots with USR-30 (consumers.md, D-log, actions.py, rbac repository, errors.py).

### Scope self-check
Only plan files touched; no refactors.
