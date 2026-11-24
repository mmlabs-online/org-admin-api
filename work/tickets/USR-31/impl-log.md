## Attempt 1 — 2026-11-24

### Files touched
| File | Required because |
|---|---|
| app/modules/app_credentials/deps.py | `OrgInvitationsCredentialDep` (denied audit action bound to the dependency) |
| app/modules/audit/actions.py | `ORGANIZATION_INVITATIONS_READ` |
| app/modules/invitations/repository.py | `page_pending_for_app` (explicit columns, DB clock) |
| app/modules/organizations/schemas.py | `AppOrgInvitationRead` (4 fields) |
| app/modules/organizations/app_service.py | shared `_require_visible_org` guard; `AppOrgInvitationService` |
| app/modules/organizations/app_router.py | `GET /{key}/orgs/{org_id}/invitations` |
| tests/test_app_org_invitations.py | new API tests (18) |
| tests/test_route_auth_classes.py | registry entry, `lock is False` |
| tests/test_audit_coverage.py | audit registry entry and route test |
| docs/consumers.md, docs/adr/0025-org-members-read-scope.md, docs/identity-architecture.md (D38), docs/spec-v1.md, CLAUDE.md | per plan docs list |

### Tests added
`tests/test_app_org_invitations.py` (18, all plan names), `test_org_invitations_read_route_is_audited`.
Log test: loggers re-enabled; captured lines include `app.modules.app_credentials.service`
(`app_credential_denied reason=scope key_id=...`, from an extra wrong-scope call) and httpx request
lines carrying the org id. The invitations route itself logs nothing. A temporary
`logger.info(email_hmac.hex())` in the service made the test fail; it was removed.

### Check results
- ruff format --check / ruff check: clean. mypy app: Success, 209 files.
- Targeted run (invitations, members, route classes, audit coverage): all pass.
- Full pytest: 4066 passed, 2 warnings (one SAWarning from a test subquery, harmless). `make check` lint step fails only on the known kit file risk_tier.py (untouched).

### Out-of-plan files
None.

### Deviations
- `_invite` test helper inserts with a valid past window then updates times on the DB clock (the
  `expires_at > created_at` CHECK rejects a past expiry at insert).
- The no-secret test asserts audit rows exist, not that both exist: the shared test session drops
  the success row when the later 404 rolls back (harness artifact; success row is covered by
  `test_success_writes_one_audit_row_counts_only`).

### Follow-ups
- `role` filter if simap asks; admin-pan help text for the new route.

### Scope self-check
Only plan files touched; no migration.
