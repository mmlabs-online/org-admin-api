# Implementation log — USR-39

## Attempt 1
### Files touched
- `app/modules/tokens/service.py`: super-admin branch in `if not org_roles:`, per-mint TTL, audit row, `_denied()`, log field (required by plan).
- `app/modules/auth/router.py`: `ctx: AuditContextDep`, new constructor args, docstring.
- `app/core/config.py`: `super_admin_org_token_ttl_seconds`, `super_admin_org_token_ttl` property, validator, two rate-limit fields.
- `.env.example`: three new keys, no values.
- `app/modules/rbac/repository.py`: `org_of_app_clause(app_id)`, `RbacRepository.is_org_of_app`.
- `app/modules/rbac/authz.py`: `AuthzService.is_org_of_app`.
- `app/modules/audit/actions.py`: `TOKEN_SUPER_ADMIN_ORG_ISSUED` (with `noqa: S105`, like its neighbours).
- `app/modules/tokens/schemas.py`: `expires_in` description.
- Docs: `docs/consumers.md`, `docs/adr/0030-app-super-admin-role.md` (§7 + status line), `docs/identity-architecture.md`, `docs/spec-v1.md` (§5.16 bullets, OQ85-OQ89 ticked; OQ84 left open for USR-40, picker half).
- Tests: new `tests/test_auth_token_super_admin.py` (34 cases), `tests/test_rbac_org_of_app.py` (8); `tests/test_config.py` (+6 cases); `tests/test_tokens_service.py` (new kwargs, `ctx=`); `tests/test_audit_coverage.py` (comment only).

### USR-38 contract
Names matched the plan: `AuthzService.app_super_admin_access(user_id, app)`, `RbacRepository.is_active_staff_super_admin`, `RbacRepository.app_super_admin_access`, `is_designated_super_admin_role`. The mint calls only `app_super_admin_access`.

### Deviations
- Revocation / expired-holder tests (`designated_assignment_revoked`, `designated_assignment_expired`, `test_non_app_scope_or_expired_holder_is_non_holder`): the holder is given an extra app member role in the test. Without it, losing the designated role leaves no app role and the existing `not_member` check answers first (also 403, but a different code). The mint is still blocked; the byte-identical `org_access_denied` body is asserted with a remaining member role.
- `test_holder_non_member_org_claim_snapshot`: no `name` claim (holder has no display name); `roles == ["orgapp:super-admin"]`.
- Audit `expires_at` is ISO-8601 UTC of `exp`.
- Not changed: lag-note lines in `docs/consumers.md` (:172, :331, :647): they state upper bounds that still hold.

### Checks
- `ruff check app tests migrations`: All checks passed.
- `ruff format --check app tests migrations`: 443 files already formatted.
- `mypy app`: Success, no issues in 216 source files.
- `pytest -q` (full): 4369 passed, 2 warnings.
- `alembic check` (local dev DB): No new upgrade operations detected. No migration added.

### Out-of-plan files
None.

### Follow-ups
- `mmlabs-auth` `org_access` accessor and simap SIM-043 consumer changes (their repos).
- Recent re-auth / `authenticated_at` plus staff MFA (OQ3), per Q2.
- USR-40 reuses `org_of_app_clause`; it ticks OQ84 (picker half) and rebases `rbac/repository.py`, `authz.py`, ADR-0030 §8, `docs/consumers.md`.

### Scope self-check
Only plan files touched; no migration; route auth classes untouched (`test_route_auth_classes.py` green, no edit).
