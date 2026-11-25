# Consult — identity-access-engineer — USR-33 (2026-11-25)

**Findings**
- Nothing writes `users.display_name` (String(100), `app/modules/users/models.py:31`). It is read live by the USR-29 member read (`app/modules/organizations/app_service.py:101`, which already nulls it for disabled accounts), by staff views (`rbac/repository.py:598,634,703`, `join_requests/service.py:44`, `bug_reports/repository.py:224,231`) and by `/auth/accounts` (`auth/router.py:239`). Tokens only pick it up at the next issue, through the guard at `tokens/service.py:54,144`.
- The cookie class has no ambient "current account". The device holds several accounts, so a route must take `account_id` in the body and resolve it against the device. `resolve_reporter` (`bug_reports/service.py:67-82`) already does this: active account on this device, active user, verified email.
- `PATCH /users/{id}` already means "set status" under `admin:users:write`, with a G5 check that needs `admin:*` when the target holds a privileged role (`users/router.py:53-71`, `users/service.py:61-62`).
- Audit diffs record `{from,to}` values (`audit/diff.py:52`), and `USER = ("status",)` (`diff.py:25`). Adding `display_name` to `USER` would write the name into the audit row.

**Recommendations**
1. Self-service (cookie class, R3(2)): `PATCH /api/v1/auth/profile` (or `/auth/accounts/{account_id}`) with `{account_id, display_name}`. Use Origin + `X-MM-App` + DeviceDep, resolve the user with `resolve_reporter` (extract it into `auth/`), and set the rotated cookie the way `list_accounts` does (`router.py:230`). Require a verified email, as R11 does for token use and as bug reports do. Account-scoped errors (`session_invalid`) give no hint whether another user exists.
2. Staff: a separate `PATCH /api/v1/users/{id}/profile` under `admin:users:write`. If the target holds a privileged assignment, require `admin:*` (reuse G5). Otherwise a users:write holder could rename a super admin and pass as them in staff UIs. Staff may rename themselves.
3. Audit: action `user.display_name_changed`, target user, actor (the user for self-service, staff for staff). Write `changes={"display_name": {"set": bool}}` or no changes at all. Never put the field in `USER`.
4. Validation: reuse `reject_disallowed_categories` (`core/text.py:17`), NFC, trim, 1–100. `null` or `""` clears it. Write the 422 detail without `input`.
5. Rate limits: a new `rate_limit("profile")` bucket per IP, plus about 10 changes per account per hour.
6. Record this as D40 only, with an ADR-0005 amendment note. No new ADR: no new auth class or credential.

**Risks**
- Impersonation: a name like "mmlabs Support" or a staff member's name. Staff UIs must always show the email next to the name.
- `name` stays frozen in live tokens until `exp`, which is up to 12 h (ADR-0005:131-133).

**Open questions for Ming**
- Should sign-up accept an optional name? Default: no, to keep sign-up minimal.
- May staff rename privileged users with only `admin:users:write`? Default: no, `admin:*` required.
- Add a DB CHECK on length 1–100? Default: yes, with a migration (High path).
