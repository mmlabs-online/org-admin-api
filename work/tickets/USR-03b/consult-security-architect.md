# consult-security-architect — USR-03b

**Findings**
1. **Cross-app enrollment.** `AppContextDep` (`auth/deps.py:63`) ties `X-MM-App` to a registered Origin. Join must also require path `{key}` == `X-MM-App` (403 `app_mismatch`, same idea as `audience != app.key` in `tokens/service.py:56`). Without that check, app A could enroll users into app B. Origin exclusivity is only enforced in `register` (`rbac/service.py:362`). Admin `PATCH /apps` and loopback origins in dev can let two apps share an origin, and then the binding fails.
2. **Admin escalation.** Refuse `key == admin` with 403 before the policy is read, even though D9 sets `admin` to `closed`. Policy is data: one PATCH to `open` must never grant admin roles.
3. **Default-role race (USR-02d F1, USR-02c F4).** Read `default_role_id` under `lock_app` (or `FOR KEY SHARE`) before inserting into `role_assignments`. A NULL default under `open` → 409 `no_default_role`, never a silent 200. Re-check `role_has_admin_permission` under the lock as defence in depth.
4. **USR-07 S1.** A CLI permission add can flip a NULL default back to `<key>:member`. Once join exists, that silently widens what joiners get. Fix it before or inside this ticket.
5. **`accounts_created_before` (USR-03 N1).** Not applied anywhere yet for reads. Both `/accounts` and join's `account_id` lookup must go through `get_active_account(..., created_before=...)`.
6. **Exposure in `/accounts`.** Any registered origin on the device sees every account's email, user id and verification state. That includes accounts the user never used with that app. All apps are same-site mmlabs properties and D4 intends this "Continue as" UX, so it is acceptable. Still, it is a cross-app linkage leak if one app is compromised (XSS in any app → all emails on the device).

**Recommendations**
- Join is cookie class: `DeviceDep` + `AppContextDep` + path/header match. POST only, `extra="forbid"`, only `account_id` in the body. Consent = that explicit POST, never on GET or automatic.
- Idempotency: `INSERT … ON CONFLICT DO NOTHING` on `uq_role_assignments_user_role_org`. Decide what happens to an *expired* existing assignment (currently it is left expired, so the user stays not-a-member).
- `/accounts`: return `account_id`, masked email (`m***@domain`), `display_name`, `email_verified`; full email only for the account the app already holds a token for, or behind an owner decision. Never return other devices' sessions or revoked ones. Add `Cache-Control: no-store`.
- Rate limits: add `rate_limit("join")` per IP and per account. `approval`: one pending request per `(app_id, user_id)` (partial unique index), 202 idempotent, a cooldown after rejection (e.g. 7 days) and a TTL purge.
- Staff approve/reject is a Bearer/admin endpoint (R3 class 1): note `admin:apps:members:manage`, lock the app, re-read the default role at approval time, and keep it out of `/api/v1/auth/*`.
- Audit interface: actor = user, app key, policy outcome, role key. Log `user_id`, never the email.

**Risks**
- Open join plus open sign-up (§7) lets anyone with a verified email become a member. Removing a member is not a ban: they can re-join under `open`.
- Shared loopback origins in dev/staging data weaken binding 1.

**Open questions**
- Mask emails in `/accounts` (owner)?
- Does a staff-removed user get blocked from re-joining (a denial record)?
- Should approval of a rejected user be retryable, and after how long?
- Enforce origin exclusivity on admin `PATCH /apps` too (follow-up or this ticket)?
