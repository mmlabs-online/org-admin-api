USR-10 REST contract consult (read-only; nothing edited)

FINDINGS
- Users router (/home/user/org-admin-api/app/modules/users/router.py): prefix /users, router-level current_admin. GET list uses Page[UserRead], PageDep, require_permission("admin:users:read"). PATCH /{user_id} uses a `_WRITE` copy that adds a 409 response. Role assignments already live at /users/{user_id}/role-assignments, so /users/{user_id}/sessions fits the existing sub-resource style (R9).
- Session model (/home/user/org-admin-api/app/modules/sessions/models.py):
  - AccountSession has id (this is the `account_id` apps use, ADR-0004), device_session_id, user_id, created_at, last_used_at and revoked_at.
  - DeviceSession has token_hash, prev_token_hash, last_seen_at, ip (INET) and user_agent (String 512).
  - Only the id is safe to expose. The id of the device session is not needed.
- Existing guard errors (/home/user/org-admin-api/app/core/errors.py): AdminWildcardRequiredError (403 `admin_wildcard_required`), SelfAssignmentForbiddenError (403 `self_assignment_forbidden`), SelfStatusChangeForbiddenError (403 `self_status_change_forbidden`). These are in DENIED_ERRORS and are audited as outcome=denied (ADM-004b). SessionInvalidError (code `session_invalid`) is in app/modules/sessions/errors.py.
- tests/test_route_auth_classes.py:
  - The BEARER map (lines 63-104) must list every new route with its single permission. test_bearer_route_permission_map also checks `x-permission` in OpenAPI.
  - `_fill` (line 295) only substitutes a fixed name list: org_id, user_id, assignment_id, permission_id, role_id, credential_id, request_id, entitlement_id. A new `{session_id}` placeholder must be added to that list, or `.format_map` raises KeyError in the runtime sweep.
- tests/test_audit_coverage.py:
  - `test_every_mutating_bearer_route_is_mapped` asserts set(AUDITED) == non-GET BEARER routes and `len(AUDITED) == 23`. Adding 2 mutating routes means 25.
  - Each AUDITED entry needs an allowed action set, a success scenario, a failure scenario and a failure status.
  - `_SUCCESS_STATUS` maps DELETE to 204, POST to 201 and PATCH to 200. `_SUCCESS_STATUS_BY_ROUTE` overrides single routes.
  - The success test expects exactly one row, and test_noop_writes_no_row exists. Revoke-all with zero sessions should therefore be a no-op that writes no row (see Q2).
  - The denied-guard test (line 470) and the no-secret/no-email test (line 570) apply to the new routes too.

RECOMMENDATIONS
1. Routes, all under /api/v1/users/{user_id}:
   - GET /sessions
     - Permission: admin:users:read (consistent with GET role-assignments). Escalate to admin:* if the target holds any admin role (OQ7).
     - Returns Page[AccountSessionRead], active sessions only (revoked_at IS NULL, and device session not revoked), sorted by last_seen desc, id as tiebreaker.
     - A 404 for an unknown user.
   - DELETE /sessions/{session_id}
     - Permission: admin:* (see 2).
     - Returns 204 with no body. A session that is unknown, already revoked, or belongs to another user gives 404.
     - The 404 must come from a `WHERE id=:sid AND user_id=:uid` lookup, so the response does not reveal that the session belongs to someone else.
   - DELETE /sessions
     - Permission: admin:*.
     - Returns 204, and 204 even when there is nothing to revoke (idempotent, no audit row).
     - I recommend this over POST .../revoke-all. It is a collection delete, matches R6 "204 delete", and R9 has no verb-style paths except the documented exceptions. Revoke-all is a different, higher-impact operation from deleting one session, so it needs its own audited route and its own audit action. DELETE on the collection with no body is idempotent and has no body-handling ambiguity. A response with the revoked count would need 200 instead of 204. The count goes in the audit row instead.
2. Permission mechanics for revoke: require_permission has one key per route (the test asserts keys == [expected]). The `x-permission` in BEARER should be "admin:*". Check that require_permission accepts the wildcard key and that the `admin:*` holder satisfies it. If it does not, use `admin:users:write` as the route key plus the in-service AdminWildcardRequiredError guard (the existing PATCH pattern). The ticket says revoke requires admin:*, so a plain route-level `admin:*` is the simplest. Confirm which pattern the planner prefers.
3. Guards (OQ7), in the service layer (R1) and raising errors that are in DENIED_ERRORS so they are audited as denied:
   - Self: if user_id == actor id, raise 403 for both list and revoke. Add SelfSessionManagementForbiddenError(PermissionDeniedError), code `self_session_forbidden`, to DENIED_ERRORS. The self-check precedes the target-lookup 404.
   - Self-check on list: it is a read, so it writes no denied audit row. Decide whether list also counts as "denied audited". I suggest a plain 403 for list with no audit, since ADM-004b is for privileged writes.
   - Privileged target on list or revoke: if the target holds any admin role and the actor lacks `admin:*`, raise AdminWildcardRequiredError. On revoke this is moot because revoke already needs admin:*. It only matters for list, so the list guard needs a repository check "user has any admin role assignment" (reuse the ADM-002b helper used by set_status).
4. Response schema AccountSessionRead (snake_case, ISO 8601 / timestamptz as elsewhere):
   - `id` (UUID, the account_session id)
   - `created_at`
   - `last_seen_at` (device_sessions.last_seen_at per the ticket; the account session's own last_used_at is the more accurate "this account was used" time, so I suggest exposing both as `last_seen_at` and `last_used_at`, or only last_used_at if the owner prefers)
   - `ip` (string or null)
   - `user_agent` (string or null)
   Never expose token_hash, prev_token_hash or device_session_id. The ids are random, so exposing device_session_id is not a secret leak, but it is an internal handle and not needed. Keep it out.
   - Do not add a `current` flag (staff cannot manage their own sessions).
   - The Page[T] envelope needs `total` of active sessions.
5. Audit:
   - Actions `user_session.revoked` (one row, details `{session_id}`) and `user_session.revoked_all` (one row, details `{session_ids: [...], count: n}`). The audit target is the user id.
   - No ip, user agent, token or email. test_no_secret_or_email_in_any_row will cover this.
   - Revoke-all is one row, not N. Cap or omit session_ids if a user could have very many sessions (unlikely, but bound it).
   - Denied attempts (self or wildcard guard) write outcome=denied rows per ADM-004b. Add the new routes to AUDITED with failure scenario = unknown session (404), and to the denied-guard tests.
6. Revoke semantics:
   - Set revoked_at on the AccountSession rows only. A DeviceSession that carries other users' accounts is untouched (acceptance criterion).
   - Revoke the device session only if its last remaining active account is revoked, otherwise leave it. This is optional (the ticket does not ask for it); I mark it for the planner, not as a requirement.
   - /auth/token must return 401 `session_invalid` once the account session is revoked. Verify the existing token path already checks AccountSession.revoked_at (it is the account session id that is the `account_id`).
   - Use a single UPDATE ... RETURNING id for revoke-all, in the same transaction as the audit row.
7. Test plumbing to schedule: add `session_id` to `_fill`; add the three new routes to BEARER; bump the 23 count in test_audit_coverage to 25; add scenarios (success, 404 failure) and success status 204 for both DELETEs.
8. Docs and OpenAPI: describe the access-token tail. OQ8 was answered by USR-13 (TTL up to 12 h, admin requests reload permissions per request), so the docs line should say issued access tokens stay valid until their TTL, up to 12 h, not ~15 min as the ticket text says. Update the ticket's last acceptance criterion accordingly. Declare 401, 403 and 404 responses (see `_WRITE["responses"]` for the pattern).

RISKS
- Staff 12 h token tail: a revoke does not invalidate issued access tokens, so "sign out everywhere" is not immediate for 12 h. Document it clearly. Do not claim a hard logout. Apps that verify only via JWKS never learn about the revoke before the token expires.
- Information leak via 404 vs 403: the self-check must come before the lookup, and a session of a different user must return the same 404 as an unknown one.
- Privilege escalation via list: the list exposes ip and user agent of admins. The wildcard guard on a privileged target is required.
- A route-level `admin:*` permission only works if the wildcard satisfies require_permission. If the guard compares keys literally, the BEARER map and `x-permission` will not match. Check app/modules/admin_auth before the plan.
- The audit count assertion (23) and `_fill` are brittle: forgetting either fails the suite, which is intended but should be in the plan.
- ip is INET, so schemas must serialize it as str (use `str(ip)`), or Pydantic will fail or emit an ipaddress object.

OPEN QUESTIONS
- Q1: DELETE /users/{id}/sessions (my recommendation) or POST .../revoke-all? Owner or planner decides. If the owner wants the count in the response, use POST with 200.
- Q2: Should revoke-all with zero active sessions return 204 without an audit row (my recommendation) or 404?
- Q3: Does the list guard for a privileged target return the existing `admin_wildcard_required`, and is a self-list 403 audited? Recommendation above.
- Q4: Should list expose `last_seen_at` of the device session, `last_used_at` of the account session, or both? The ticket says the device's last-seen time.
- Q5: Does revoking the last active account on a device also revoke the device session? Not required by the ticket.
- Q6: R9 and R3 list explicit exceptions for non-plural paths. These routes are plural and nested, so no R9 exception or ADR amendment is needed. Only the auth-class table needs no change, since these are ordinary Bearer admin routes with a declared permission.

Files read: /home/user/org-admin-api/work/tickets/USR-10/ticket.md, /home/user/org-admin-api/app/modules/users/router.py, /home/user/org-admin-api/app/modules/sessions/models.py, /home/user/org-admin-api/app/core/errors.py, /home/user/org-admin-api/tests/test_route_auth_classes.py, /home/user/org-admin-api/tests/test_audit_coverage.py.
