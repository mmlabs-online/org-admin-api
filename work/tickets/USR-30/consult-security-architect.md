# Consult — security-architect — USR-30 (2026-11-21)

### Findings
1. **CSRF/CORS are already covered by the existing pieces.** `get_app_context` (`app/modules/auth/deps.py:67-78`) → `AppContextService.check` (`app/modules/auth/service.py:83-97`) requires `X-MM-App` to name an active app and `Origin` to be one of *that* app's origins. Origins are unique across apps (`app/modules/rbac/service.py:147`), and loopback origins are dropped in staging/production (`app/modules/rbac/origins.py:70-74`). The credentialed CORS policy allows only GET/POST with `X-MM-App` (`app/core/cors.py:34-38`). So app A's page cannot send `X-MM-App: B`. The remaining cross-app risk sits in the service: it must take `<app>` **only** from the `App` that `AppContextDep` returns. Do not add an app key to the path or body.
2. **The org-exact check is reusable.** `AuthzService.org_permissions` (`app/modules/rbac/authz.py:51-57`, `repository.py:469+`) reads only the roles held in that one org, only while the org is active, and only under the app's own prefix. That rules out privilege from app-scope roles, other apps or staff `admin` roles. Note that `permits` also accepts `<app>:*` (`authz.py:23`), so a wildcard role in the org grants removal too.
3. **Lock.** `lock_app` (`rbac/repository.py:81-93`) already serializes every RBAC writer of the app, including staff revokes. If the service takes it first (as `create`/`accept` do, `invitations/service.py:160,296`) and counts owners **after** the lock, the two-owners race is closed with no extra lock. The cost is that removals across the whole app run one at a time, which is acceptable.
4. **Hostile takeover is not guarded.** The ticket's guards are only self-removal and last-owner. Any holder of `remove-member` (for example a future `simap:manager`) could remove every owner except one, or remove owners outright. Invitations limit the grant side with `is_subset` plus "no `is_admin` role" (`invitations/service.py:178-182`). Removal has no matching rule.
5. **Denied attempts are not audited for end users.** `record_denied` writes only for `AdminPrincipal` (`audit/service.py:210-212`), and invitations write no denied rows. So "follow the existing pattern" means **no** denied row.
6. **The status code for an unrelated org conflicts with the ticket.** Invitations answer an unknown, suspended or non-member org with **403** `org_access_denied` (`tokens/errors.py:27-30`, `invitations/service.py:166-167`). The ticket says 404 "as USR-29".
7. **There is no per-account rate limit.** `rate_limit()` only counts per IP (`core/rate_limit.py:142-148`).
8. **A removal can be undone.** If the target still has a pending invitation to that org, they can accept it and rejoin. Invitations are keyed by `email_hmac`, so the server can find them.
9. **Inviters lose their invitations correctly.** Invitations the target sent become unusable after removal, because `_recheck` re-checks the inviter's `org:invite` (`invitations/service.py:378-381`).

### Recommendations
- **Check order** (no oracle): lock app → `org_mode` (422) → caller `_user` (401 on a bad session, 403 if unverified) → caller has org roles (same answer as invitations, see Q1) → `permits(..., "<app>:org:remove-member")` (403) → `caller == target` (403 `self_removal_forbidden`) → target's org roles (404 if none) → rank rule → last-owner (409). A target lookup only after the permission check means non-permitted members cannot probe membership.
- **Path `user_id` is the target and body `account_id` is the caller's device account.** Never resolve the target from `account_id`. Add a test where the two are swapped.
- **Rank rule (AC "authorization")**: refuse with 403 `member_remove_forbidden` when any of the target's org roles has an `is_admin` permission (staff granted it, so staff remove it), or when the target's org permissions are not a subset of the caller's (`is_subset`, `rules.py:14-16`). A manager then cannot remove an owner, while co-owners can still remove each other.
- **Last-owner count**: count only owner-role holders in that org with `expires_at IS NULL` other than the target, counted under the app lock. An owner whose assignment is about to expire should not count as the remaining owner.
- **Effect**: also revoke the target's pending invitations in that org/app. Write one `organization.invitation_revoked` row per invitation with reason `member_removed`.
- **Audit row**: actor `EndUser(caller)`, target `(USER, target_id)`, `app_key`, `org_id`, `changes={"role_keys": [...], "count": n, "was_owner": bool}`. No email. Add the route to `IDENTITY_MULTI_ROW_AUDITED` (`tests/test_audit_coverage.py:933`) if invitation-revoke rows are added; it must also pass `test_no_secret_or_email_in_any_row` (`:840`).
- **Rate limit**: add a new scope `org_member_remove` per IP. Per account, do a `limiter.hit` keyed by the caller's user id after `_user`, or a DB count as invitations do. Suggested ~10 per 10 min.
- **Response**: 204 with `Cache-Control: no-store` and cookie rotation via `_finish` (`invitations/router.py:36-40`).

### Risks
- **Revocation lag of up to 12 h**: a removed hostile member keeps the org in live tokens. That is acceptable only if documented and if apps re-check membership for sensitive or destructive actions (via the USR-29 read or by forcing a token refresh). Medium risk.
- **Co-owner takeover** stays possible by design. The only recovery path is staff through the admin portal, and the audit row is the only evidence.
- The app-wide lock turns a burst of removals into a queue. It is bounded by the rate limit.

### Open questions for Ming (defaults in brackets)
1. Unrelated or suspended org: 403 `org_access_denied` like invitations, or 404 like USR-29? [403, consistent with the existing cookie-class org write; fix the ticket wording]
2. Rank rule as above (no `is_admin` targets, and target permissions must be a subset of the caller's)? [yes]
3. Co-owners may remove each other? [yes, audited; no extra guard]
4. Audit denied attempts by end users (403/409) with `record_failure` + `EndUser`? [no, follow the existing pattern: log a warning line without email; reconsider if simap asks]
5. Live-token lag: accept 12 h and document it, or lower `ACCESS_TOKEN_TTL_SECONDS`? [accept and document, and advise apps to re-check on destructive actions]
6. Revoke the target's pending invitations to that org on removal? [yes]
