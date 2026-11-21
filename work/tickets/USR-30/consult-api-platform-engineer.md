# Consult — api-platform-engineer — USR-30 (2026-11-21)

**Findings**
- Cookie POSTs identify the actor with `account_id` in the body. Invitation accept does this (app/modules/invitations/schemas.py:11,28; router.py:111). The service resolves it with `_user(device, account_id)` (service.py:103-114). That checks the account belongs to the signed-in device session and the email is verified, and raises `SessionInvalidError` or `EmailUnverifiedError`. The `GET /auth/orgs` handler also takes `account_id` (auth_router.py:76), as a query parameter. So `account_id` is the actor (the caller, a device can hold several accounts), not the target. The target is the `{user_id}` path segment.
- CORS allows only GET and POST for cookie routes (app/core/cors.py:36). DELETE and PATCH are admin-only (cors.py:44), so a `DELETE` route could never be called from a browser app. POST with a `/remove` suffix is forced. Naming it an action on the member is consistent with the existing `/invitations/accept`.
- Invitation checks run in this order (service.py:109-199): session invalid (401), email unverified, origin, org_mode (422 `_org_not_supported`, line 162), org access (`OrgAccessDeniedError`, 167), then the org-exact permission (170). Rate limits are named keys in app/core/rate_limit.py:78-112 and config.py:72-94, applied through `dependencies=[rate_limit("...")]` (router.py:59).
- Route registry: COOKIE set in tests/test_route_auth_classes.py:55-60. Add the new tuple there.
- Existing mutating cookie routes return a body (201 or 200). Nothing else on the cookie side returns 204.

**Recommendations**
1. **Route:** `POST /api/v1/auth/orgs/{org_id}/members/{user_id}/remove`, body `{account_id}`. Use the same `_cookie: DeviceCookieDoc`, `AppContextDep` and `DeviceDep` stack as the invitation routes. Set `response_model` explicitly. Reuse `_finish` (cookie rotation) as the invitations router does.
2. **Status: 200 with a summary** `{org_id, user_id, removed_roles: [role keys], removed_count}`. Reasons:
   - It stays consistent with the other cookie POSTs.
   - The app UI can confirm what was removed.
   - R6 lists 204 for delete, but this is a POST action, not a delete.
   - Role keys are not sensitive, and the caller already holds a permission in that org.
   - Retries get 404, since the target no longer has roles. Document this: the second call 404s, so treat 404 after a timeout as success.
3. **Error order** (cheap and non-leaking first):
   - 401 no cookie or invalid session.
   - 403 origin or `X-MM-App`. The existing deps already run this before the handler.
   - 403 email unverified (same as invitations).
   - 422 `org_not_supported` (app with `org_mode = none`).
   - 404 for an org the caller cannot see, or a suspended org (`OrgAccessDeniedError`, as at service.py:167).
   - 403 permission (`<app>:org:remove-member`, org-exact).
   - 403 `self_removal_forbidden`. Check it AFTER the permission, so non-holders learn nothing. It needs no DB lookup, though.
   - 404 target has no `<app>` org roles in that org.
   - 409 `last_org_owner`, with `details: {"reason": "last_org_owner"}` (R6 shape). Hold the lock before this check. Order the target 404 before the 409, so the 409 only fires for a real holder.
   - Do not put the target's email or account fields in any error.
4. **Rate limit:** add key `org_member_remove`, with settings in config.py and an entry in rate_limit.py (at the list around lines 78-112), around 30 per hour per IP and per account. The cap should be low because this is destructive. Note that the existing keys appear to be per IP, so confirm a per-account bucket is supported (see Risks).
5. **Audit:** add action `ORGANIZATION_MEMBER_REMOVED = "organization.member_removed"` next to actions.py:41. Metadata: org_id, app key, role keys, count. No email.
6. **Docs contract** in consumers.md "Organizations" (line 155): the permission name, the 200 body, the error table, that a retry gets 404, and the token lag. Already-issued app tokens keep the roles until `exp` (up to 12 h in prod), so the app must also check its own member list (the USR-29 read) for authorization-critical actions. New `/auth/token` calls for that org omit the roles immediately.
7. **Tests:** register the route in COOKIE; add the 422 and 401/403 cases (R8); add a regression asserting `/auth/token` no longer carries the roles. For concurrency, use two sessions and `SELECT ... FOR UPDATE` on the owner-role rows, in a fixed order (sorted by user id) to avoid deadlock.

**Risks**
- Self-removal vs the last owner: the 403 `self_removal_forbidden` fires before the 409. A sole owner who tries to remove themselves gets 403, not 409. That is fine, but document it.
- A target holding only non-owner roles that can still be removed is fine. If the target holds the owner role AND others, remove all of them, but only if another owner remains.
- The "per-account" limit may not exist as a bucket type today. Verify in app/core/rate_limit.py. If it does not, per-IP alone will do.
- `app/core/` edits (rate_limit.py, config.py) are High-path, and the plan gate already covers this.
- The permission string `<app>:org:remove-member` must pass the manifest validator (ADR-0003, three segments, hyphen allowed). Verify hyphens are accepted.
- Revocation lag of up to 12 h for live tokens is a real authorization gap. State it plainly in the ADR.

**Open questions for Ming (defaults)**
1. 200 with a summary vs 204? Default: 200.
2. Show removed role keys in the response? Default: yes, keys plus count.
3. Rate limit? Default: 30 per hour per IP (and per account if supported).
4. Amend ADR-0014 or write a new ADR? Default: a new ADR (member management is a different action from invitations), cross-linked from ADR-0014, plus a dated spec line.
5. Should a retry return 404 (default) or an idempotent 200 with `removed_count: 0`? Default: 404, per the ticket's no-existence-leak rule.
