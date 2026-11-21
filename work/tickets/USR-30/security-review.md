# Security review — USR-30

## Attempt 1
Verdict: PASS

Static review of fdfe1b9 against origin/main (git, grep and reads only; no pytest or make check run). I found no blockers and nothing that must be fixed before merge. The items below are notes.

**What holds**

- **CSRF and app context:** the route uses `AppContextDep`, which checks `X-MM-App` and that the `Origin` is registered for that app (`app/modules/auth/deps.py:67-78`). It also uses `DeviceDep` and the cookie-class registry entry (`tests/test_route_auth_classes.py:61`). CORS comes from the `/api/v1/auth/` prefix policy. `<app>` is taken only from the locked app row (`member_service.py:99`). Every query is filtered by `app.id`/`app.key`, and `delete_org_assignments` limits itself to `Role.app_id == app_id` (`app/modules/rbac/repository.py:866-886`). Cross-app removal is not possible: the other-app target gets 404 (`tests/test_org_member_remove.py:340`), and other-app roles stay untouched (`:88`).
- **Who is the caller:** the caller comes only from `body.account_id`, resolved through `get_active_account(device.device_id, …)`. The target comes only from the path. The swap test sends the target's account_id and gets 401 `session_invalid` (`tests/test_org_member_remove.py:182`).
- **Org-exact permission:** `org_role_keys` and `org_permission_keys` look only at that exact org, ignore expired assignments, and require the org to be active (`app/modules/rbac/repository.py:463-506`). A role held at app scope or in another org gives no privilege (`tests/test_org_member_remove.py:226`). `<app>:*` works through `permits` and `is_subset`. A target holding `<app>:*` can only be removed by a caller who also holds `<app>:*` (`tests/test_org_member_rules.py`).
- **Check order and no membership oracle:** unknown, suspended and foreign orgs all return the same 403 `org_access_denied` body (`tests/test_org_member_remove.py:198`). The 404 for a missing target, the rank rule and the last-owner rule are reached only after the permission check. Self-removal is checked before the target lookup, so a sole owner removing themselves gets 403, not 409.
- **Last-owner guard and concurrency:** `lock_app` is the first statement. All role_assignments deleters take it, including staff `revoke` (`app/modules/rbac/service.py:1631`). Under READ COMMITTED, the queued request re-reads after the first one commits. In the two-owners race, the loser's caller no longer has access, so it gets 403, and exactly one owner row remains (`tests/test_org_member_remove_concurrency.py:59`). A remaining owner counts only if the assignment is permanent and the user is active and verified.
- **Deletion scope:** only non-expired org assignments of this app in this org are deleted. App-scope roles and other orgs stay (`tests/test_org_member_remove.py:88`).
- **Pending invitations:** they are revoked using the same `hmac(normalize_email(...))` scheme the invitation service uses, scoped to org and app, and each one gets its own audit row. Join requests have no org dimension, so a removed member cannot rejoin the org that way.
- **Reserved scope:** `org:remove-member` is reserved at org scope (`app/modules/rbac/scopes.py:10-12`), and the CLI message is updated.
- **Rate limits:** per IP through the `rate_limit("org_member_remove")` dependency, and per user after session resolution (`member_service.py:106`). Both have 429 tests.
- **Audit and logs:** the audit row holds role_keys, count and was_owner, with no email. Refusals write no audit row, which matches the end-user policy for invitations. Logs contain ids only. The caplog test is not vacuous: it re-enables loggers disabled by Alembic and asserts that an `org_member_remove_denied` line was captured (`tests/test_org_member_remove.py:481-494`).
- **Cookie rotation:** `_finish` sets the rotated cookie and `Cache-Control: no-store` on 204. Error paths match the existing routes: the transaction rolls back.

**Findings**

💭 `app/modules/organizations/member_service.py:99` — `lock_app` is taken before the account, email and per-user checks. Anyone with a valid device cookie and a registered Origin can briefly serialize all RBAC writes for one app. The per-IP limit of 30/hour bounds this. The order is deliberate: the account must be re-read after the lock for the race outcome to be correct. Acceptable as is; just be aware of it.

💭 `docs/adr/0026-org-member-removal.md:39` and `member_service.py:96` — "every role_assignments writer takes it" is true, but the owner count also depends on `users.status` and `email_verified_at`. Staff disabling a user does not take this app's lock. So if staff disable owner A while A removes owner B, the org can be left with zero counting owners. This is a staff action and fits the ADR's "Staff revoking the last owner stays unguarded". Suggest one sentence in the ADR's Consequences section.

💭 `tests/test_org_member_remove.py` — two HTTP-level tests are missing. The code paths are structurally correct, so these would only lock the behaviour in:
- (a) A caller whose org role holds `<app>:*` but not the literal `<app>:org:remove-member` gets 204. Today this is covered only by the unit test.
- (b) A caller who holds `other:org:remove-member` in the same org, sending `X-MM-App: other` against a target holding only the primary app's roles, gets 404 and nothing is deleted. This pins "no cross-app removal" from the caller side; the current test only covers the target side.

💭 `member_service.py:133-142` — `role_has_admin_permission` runs once per role held (N+1). The number of roles is tiny, so there is no security impact. It is only a performance note.

**Follow-ups (out of scope)**
- Guard staff disable or revoke against leaving an org with no owner (already listed in ADR-0026).
- Push notification or webhook on removal, to shorten the up-to-12 h access-token lag (ADR-0026 §8).
