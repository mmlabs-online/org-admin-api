## Attempt 1 — 2026-11-21

### Files touched
| File | Required because |
|---|---|
| app/modules/organizations/member_service.py (new) | `OrgMemberService.remove`: check order, guards, delete, invitation revoke, audit (plan §4) |
| app/modules/organizations/member_rules.py (new) | pure `outranks`, `leaves_no_owner` |
| app/modules/organizations/auth_router.py | route `POST /{org_id}/members/{user_id}/remove`, 204 |
| app/modules/organizations/schemas.py | `OrgMemberRemove` |
| app/modules/organizations/errors.py | six module-local error codes |
| app/modules/rbac/repository.py | `org_roles_held`, `count_other_org_owners`, `delete_org_assignments` |
| app/modules/rbac/scopes.py | reserved scope `org` for `<app>:org:remove-member` |
| app/cli.py | `MSG_RESERVED_SCOPE` names the new key |
| app/modules/audit/actions.py | `ORGANIZATION_MEMBER_REMOVED` |
| app/core/rate_limit.py, app/core/config.py, .env.example | scope `org_member_remove` + four settings (R5) |
| docs/adr/0026-org-member-removal.md (new), docs/adr/0014-org-invitations.md, docs/identity-architecture.md (§4 row, D35), docs/spec-v1.md, docs/consumers.md, CLAUDE.md (R3), work/ming-todo.md (M-5) | docs per plan |
| tests/org_member_factories.py (new), tests/test_org_member_remove.py, tests/test_org_member_remove_concurrency.py, tests/test_org_member_rules.py (new) | tests |
| tests/test_rbac_scopes.py, tests/test_route_auth_classes.py, tests/test_audit_coverage.py | scope case, COOKIE registry, IDENTITY_AUDITED entry |
| tests/test_cli_register_app.py | one extra parametrized case for the new reserved scope (CLI message) |

### Tests added / changed
- tests/test_org_member_rules.py (6), tests/test_org_member_remove.py (36 cases incl. caplog no-email test that re-enables loggers and asserts an `org_member_remove_denied` line from an app module was captured), tests/test_org_member_remove_concurrency.py, test_rbac_scopes (remove-member), test_cli_register_app (extra param), registry entries in test_route_auth_classes and test_audit_coverage.

### Checks (actual output summary)
- ruff check: only the known pre-existing `.github/scripts/risk_tier.py` errors (13); none in my files.
- ruff format --check: only `.github/scripts/risk_tier.py` would be reformatted.
- mypy app: Success: no issues found in 203 source files.
- pytest (full, once after the last code change): 3857 passed, 1 warning.
- build: n/a.

### Deviations from the plan (adapted to merged USR-29 code)
- Concurrency test: two owners removing each other gives `[204, 403]` (loser: 403 `org_access_denied`, its caller lost the owner role while queued on `lock_app`), not `[204, 409]`. At least one owner remains; asserted by row count. 409 `last_org_owner` is covered by `test_last_owner_409` (non-owner caller). ADR-0026 §5 states this.
- Last-owner test caller is a non-owner "deputy" role holding all owner permissions (a `calechip:*` permission must be `is_admin`, so a wildcard caller would hit `member_outranks_caller`).
- `rate_limit.py` has USR-29's per-key hook; USR-30 only appended a scope literal + `_LIMITS` entry. Per-user limit uses the limiter directly as bug reports do.
- Plan listed no `tests/test_cli_register_app.py` change; added one parametrized case.
- USR-29 org-read 404 `org_access_denied` vs this route's 403: kept 403 (ADR-0014), documented in consumers.md.
- No migration.

### Out-of-plan files
- tests/test_cli_register_app.py — extra reserved-scope case (see above).

### Follow-ups (noticed, not done)
- De-duplicate `_user` (now 5 copies); self-service "leave org"; cookie-side member list; guard staff revoke of last org owner; webhook on removal.

### Scope self-check
- Could the diff be smaller? No.

## Attempt 2 (REWORK)

### Files touched
- `tests/test_org_member_remove.py`: `_rows` now takes `org_id` and filters audit rows by `AuditLog.org_id` (required because the concurrency test commits audit rows; audit_log is append-only so they cannot be cleaned up). All call sites pass `renv.org.id`.

### Checks
- `pytest tests/test_org_member_remove_concurrency.py tests/test_org_member_remove.py`: 37 passed
- reverse order: 37 passed
- full `pytest`: 3857 passed, 1 warning
- `ruff check app tests`: clean; `mypy app`: no issues in 203 files

### Out-of-plan files / follow-ups
- None.
