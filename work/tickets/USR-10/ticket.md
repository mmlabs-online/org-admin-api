# USR-10: Staff session management for a user (list, revoke one, revoke all)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/53 |

## Goal
Support staff can sign a user out on one device or everywhere without disabling the account.

## Spec reference
§5.10 Staff session management (2026-11-16); §5.8 Admin bullet.

## Acceptance criteria
- [ ] A Bearer admin endpoint lists a user's active account sessions. Each entry has the session id, the device's last-seen time, IP and user agent. The response is `Page[T]`.
- [ ] Two revoke actions: revoke one account session, and revoke all of the user's account sessions on every device. Device sessions that also carry other users' accounts are left alone.
- [ ] Both revoke actions require permission `admin:*` (owner, 2026-11-16).
- [ ] Each revoke writes an audit row in the same transaction, holding session ids and counts only (no IP, user agent or token). Denied attempts follow the ADM-004b pattern.
- [ ] Tests:
  - list, revoke one and revoke all;
  - another user's session gives 404;
  - 401/403;
  - the audit rows;
  - after a revoke, the user's `/auth/token` returns 401 `session_invalid`.
- [ ] Docs state that access tokens already issued stay valid until their TTL (~15 min), per OQ8.

## Dependencies
USR-03 (sessions), ADM-002b, ADM-004b, ADM-012.

## Notes / answers from owner
- Needs answers before PLAN: **OQ7** (can staff use this on their own sessions? any extra guard for users holding admin roles?), **OQ8** (is the ~15-minute token tail acceptable?). See `docs/spec-v1.md` §8.
- 2026-11-17 (owner) **OQ7**: staff may NOT list/revoke their own sessions through the admin API (403, like other admin self-changes); listing/revoking sessions of a user who holds any admin role requires `admin:*`.
- 2026-11-17 (orchestrator): OQ8 was answered by USR-13 (owner, 2026-11-17: access-token TTL up to 12 h, ADR-0005 amendment; admin requests reload permissions per request).
- 2026-11-17 (owner): list AND both revokes require `admin:*` (spec §5.10 wins over the earlier OQ7 reading). The list shows the full IP and user agent (labelled "recorded when this browser first signed in"), `Cache-Control: no-store`, never logged.
- 2026-11-17 (orchestrator): the docs AC "~15 min" is stale; use the USR-13 TTL (up to `ACCESS_TOKEN_TTL_SECONDS`, 12 h in production, + leeway) and state that revoking sessions does not end already-issued tokens, including admin tokens.
