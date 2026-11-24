# Security review — USR-31

## Attempt 1
Verdict: PASS

Scope: USR-31, attempt 1. I reviewed commit 0c8ebf1 against origin/main by reading the code and git output only. I did not run pytest or make check, as you asked, so the PASS covers static correctness only. Green `make check` (R8) still has to come from the implementer or reviewer run. impl-log.md:27 reports 4066 tests passing and the lint step failing only on the kit file `.github/scripts/risk_tier.py`, which this diff does not touch.

What I checked (no blockers found):
- **Scope and auth order.** `OrgInvitationsCredentialDep` (app/modules/app_credentials/deps.py:94-103) is a new `RequireAppCredential(ORG_MEMBERS_READ, ORGANIZATION_INVITATIONS_READ, lock=False)` instance. A denied request is therefore audited as `organization.invitations_read`, not `organization.members_read`. The constructor still refuses lock=True and mixed runtime scopes. The route (app/modules/organizations/app_router.py:71-99) runs in the same order as the member read: per-IP `rate_limit("app_org_members")` as a route dependency before auth, then the credential, then `enforce_subject_limit("app_org_members_key", caller.key_id)` after auth. Both buckets are shared with the member read, and tests/test_app_org_invitations.py:388-412 tests both.
- **Registry.** tests/test_route_auth_classes.py:67,76,325 adds the route with `{"org-members:read"}` and `lock is False`. The parametrized tests therefore cover the new route for: admin JWT → 401, deployer key → 401, no credential → 401, no CORS (including preflight), and the exact allow-list. CORS uses a path-prefix middleware (app/core/cors.py:80-82), so `/apps/...` stays outside it.
- **Uniform 404, no existence oracle.** The guard was extracted unchanged into `_require_visible_org` (app/modules/organizations/app_service.py:24-47) and both services use it (lines 66, 125). It runs before any invitation query. `org_related_to_app` (app/modules/rbac/repository.py:644-661) gives one answer for unknown, unrelated and suspended orgs. The test at tests/test_app_org_invitations.py:261 compares the three 404 bodies byte for byte, with pending invitations present on the suspended org.
- **Invitation query.** `page_pending_for_app` (app/modules/invitations/repository.py:181-206):
  - Filters on `app_id == caller.app_id` in addition to `org_id`.
  - The Role join is composite on `role_id` and `app_id`.
  - The pending predicates are explicit: `accepted_at IS NULL`, `revoked_at IS NULL`, `expires_at > func.now()` (DB clock, line 190). Count and rows use the same transaction time.
  - It selects only `id`, `Role.key`, `created_at`, `expires_at`. `email_hmac`, `token_hash`, `invited_by` and `accepted_by` are never loaded.
  - `org_id` is a typed UUID path parameter and there are no free-text filters, so there is no injection surface.
- **Response schema.** `AppOrgInvitationRead` (app/modules/organizations/schemas.py:81-85) is a separate class with exactly the 4 fields, not a subclass of the staff schema. tests/test_app_org_invitations.py:182 asserts the exact key set.
- **Audit.** The success row (app_service.py:131-139) holds only `returned`, `total`, `limit` and `offset`: no ids, role keys or emails. The denied row holds `{"reason": "org_access_denied"}`. Both are written in the request session.
- **Headers and 422.** `Cache-Control: no-store` is set (app_router.py:98) and tested. 422 uses the existing handler, which never echoes `input`/`ctx` (app/core/errors.py:215). The service adds no log lines.
- **Log-capture test is not vacuous.** tests/test_app_org_invitations.py:191-258 re-enables the loggers that Alembic's `fileConfig` disabled. It asserts that a line from `app.modules.app_credentials` carrying the wrong key's `key_id` was captured, and that `org_id` appears in the logs. impl-log.md:22 records that the test failed when a temporary `logger.info(email_hmac.hex())` was added.

Findings:

💭 tests/test_app_org_invitations.py:197-209: the `invitee` email sentinel is never written to the database. `email_hmac` is `os.urandom(32)` (line 198), not the HMAC of `invitee`, so nothing could ever emit that string and that one sentinel always passes. The other sentinels (HMAC hex, raw token, token hash, inviter email and id) do protect. Fix if you want the email sentinel to count: set `email_hmac` from the app's email-HMAC helper applied to `invitee`, or drop `invitee` from the list so it does not look like coverage. Not blocking.

💭 tests/test_app_org_invitations.py:252-253: the comment says the success audit row may be dropped when the later 404 rolls back the shared test session. In that case the check that no sentinel appears in audit `changes` may only look at denied rows. tests/test_app_org_invitations.py:354 (`test_success_writes_one_audit_row_counts_only`) asserts the exact `changes` keys of the success row, which covers that gap. Not blocking.

💭 app/modules/organizations/app_service.py:125-130: the 200 path runs one more query than the 404 path, so response timing can hint whether an org is related to the app. ADR-0025 and the plan already list this as accepted residual risk, and the member read has the same shape. No action.

Follow-ups (out of scope):
- None from security. The plan's own follow-ups (a `role` filter, admin-pan help text) are unaffected.
