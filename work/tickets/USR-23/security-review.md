# Security review — USR-23 (appsec-engineer)

## Attempt 1
**Verdict: PASS. No 🔴 blockers and no 🟡 should-fix findings.** I only read the code. I did not run `make check` or the tests (no Postgres in this review), so the orchestrator or reviewer still has to confirm a green `make check` (R8).

### Findings

**Authorization (R3 class 1)**
- 🟢 All three routes are on the `/organizations` router, which has `dependencies=[Depends(current_admin)]` (`app/modules/organizations/router.py:28-30`). Each route also declares a permission through `_route` → `require_permission`:
  - members: `admin:users:read` (`router.py:180`). This matches the existing email-bearing holder and user lists (`rbac/router.py:300`, `users/router.py:47`).
  - invitations: `READ` = `admin:organizations:read` (`router.py:206`).
  - revoke: `WRITE` = `admin:organizations:write` (`router.py:228-235`).
- 🟢 Dependencies run before the handler, so 401/403 always come before the org-existence 404. A caller without the permission cannot probe which orgs exist.
- 🟢 The route-class registry test covers the new routes (`tests/test_route_auth_classes.py`). `test_members_403_with_organizations_read_only` shows that `organizations:read` alone does not expose member emails.

**Cross-org IDOR on revoke**
- 🟢 Both statements are keyed on `(id, org_id)`:
  - `revoke_for_org`: `OrgInvitation.id == invitation_id AND OrgInvitation.org_id == org_id` (`app/modules/invitations/repository.py`, the new method after `status_expr`).
  - `get_state` uses the same key, so an invitation from another org returns `None` → 404, with no write and no audit row.
  - The 404 message "Invitation not found" says nothing about the other org.
  - Covered by `test_revoke_other_org_404` (`tests/test_org_invitations_admin_api.py:328`).
- 🟢 Both list queries filter on `org_id` first (`page_for_org`; `page_org_members` with `RoleAssignment.org_id == org_id`). Only org-scoped assignments can appear (`test_members_only_this_org_scoped_rows`).

**No leak of secrets or PII**
- 🟢 The invitations query selects explicit columns (`page_for_org`). `token_hash`, `email_hmac` and the ORM entity are never loaded. `OrgInvitationRead` has no email, token or hash fields (`app/modules/invitations/schemas.py`). Tests: `test_invitations_item_shape_exact_keys`, `test_invitations_body_has_no_secrets`.
- 🟢 The members query adds only `User.email, User.display_name, User.status`, not the `User` entity, so `password_hash` cannot reach the result (`app/modules/rbac/repository.py`, `page_org_members`).
- 🟢 The audit `changes` is just `{"reason": "staff"}` (`app/modules/invitations/admin_service.py`, `revoke`). No hash, HMAC or email goes into the audit row, and nothing new is logged.
- 🟢 The invitations list shows `invited_by` and `accepted_by` user UUIDs to `admin:organizations:read` holders who may lack `admin:users:read`. These are opaque IDs with no PII, and the ticket requires them. Acceptable.

**Revoke racing accept**
- 🟢 This is safe under Postgres READ COMMITTED.
  - Accept takes `SELECT … FOR UPDATE` in `get_pending_for_update` (`repository.py:~105-118`), then runs a conditional `consume` (`accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`).
  - Revoke is a single conditional `UPDATE … WHERE accepted_at IS NULL AND revoked_at IS NULL RETURNING app_id`.
  - Whichever commits first wins. The other blocks on the row lock, re-checks its WHERE against the new row, and matches 0 rows. Accept then gets "not found or consumed"; revoke falls through to `get_state` → 409 (accepted) or 204 no-op (already revoked).
  - The `not_accepted_and_revoked` CHECK can never be hit, so this never becomes a 500. Two staff revoking at once produce exactly one audit row.

**Audit in the same transaction (R4)**
- 🟢 `AuditLogger.record` adds and flushes in the request session and never commits itself (`app/modules/audit/service.py:~141`). `get_session` commits once on success and rolls back on any exception (`app/db/session.py:24-35`). The revoke and its audit row therefore commit or roll back together.
- 🟢 Audit is written only when this call actually revoked the row (`app_id is not None`). Tested: `test_revoke_pending_204_and_audit`, `test_revoke_already_revoked_204_no_second_audit`, and no audit on 404/409.

**Cache-Control**
- 🟢 `Cache-Control: no-store` is set on the 200 members response (it carries emails) and on the invitations response (`router.py`, both list handlers). Tested by `test_members_cache_control_no_store` and `test_invitations_cache_control_no_store`.
- 🟢 Error responses (404/422) don't get the header, but they carry no PII. The 204 revoke has no body.

**R11 / R5 / input handling**
- 🟢 This diff does not touch credentials, cookies or tokens. The `app` filter is limited by `APP_KEY_RE = ^[a-z][a-z0-9-]{1,31}$`: anchored and bounded, so no ReDoS risk. Pydantic validates `status` as an enum and the path ids as UUIDs. All values reach SQLAlchemy as bound parameters; there is no SQL built from strings.
- 🟢 R1 holds: the router only calls the services, the services raise `NotFoundError`/`ConflictError`, and the repositories are SQLAlchemy-only. R6: the 409 carries `details=[{"field":"invitation_id","reason":"invitation_accepted"}]` and reveals nothing sensitive.

### Follow-ups (out of scope)
1. Add a real two-connection concurrency test for revoke vs accept (`asyncio.gather` on separate sessions). The current tests only check the order "revoke, then accept". The guarantee above rests on the `FOR UPDATE` plus conditional `UPDATE` design, and no test proves it under true concurrency.
2. The existing role-holders list (`rbac/router.py:~302`) returns emails but, unlike the new members route and `users/router.py:116`, doesn't appear to set `Cache-Control: no-store`. This was not introduced here; I'd file it as a small bug to make the email-bearing admin lists consistent.
3. `page_for_org` inner-joins `Role` and `App` but counts `total` without those joins. Today the cascading `fk_org_invitations_role_same_app` foreign key keeps the two consistent, so this only matters if that FK changes. It's a data-integrity note, not a security issue.

## Attempt 2
USR-23 security re-review, attempt 2: f7ab3fb does not change the security posture. No blockers, no should-fix items.

I read the full diff with `git show f7ab3fb`. It touches two files and I did not run the tests myself.

**🟢 Findings**
- tests/test_audit_coverage.py: the only code change. It adds an audit-coverage registry entry for `DELETE /organizations/{org_id}/invitations/{invitation_id}` (expected event `organization.invitation_revoked`).
  - It adds two test scenarios: `_inv_revoke` for success and `_inv_revoke_unknown` for the 404 case.
  - It changes the `AUDITED` count assertion from 25 to 26.
  - The registry test `test_every_mutating_bearer_route_is_mapped` asserts that the registry matches the mutating Bearer routes. That makes it a guard on R4 (every mutating admin action writes an audit entry).
  - The change closes the gap the earlier review found, where this route was missing from the registry. It strengthens audit assurance and does not weaken it.
- No `app/` code, migration, model, config or `app/core/` file changed. Auth classes (R3), token handling, rate limits and CORS are untouched.
- The test imports `tests.invitation_factories.make_invitation` and builds fixtures with random UUIDs and hours-scale timestamps. It contains no secrets, no hardcoded credentials and no network access.
- work/tickets/USR-23/impl-log.md: only an "Attempt 3 (REWORK 2)" section was added. It reports `make test` at 2714 passed, plus typecheck and ruff clean. It also states "no app code touched", which matches the diff.

**Follow-ups:** none from this commit. The attempt 1 verdict (PASS) stands.
