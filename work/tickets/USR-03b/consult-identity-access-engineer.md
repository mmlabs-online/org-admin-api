# consult-identity-access-engineer — USR-03b

**Findings**
- The building blocks already exist. `RbacRepository.lock_app` (`FOR NO KEY UPDATE`, `populate_existing`), `AssignmentService._grant_locked` (replaces an expired row in place, raises 409 on an active one), `SessionRepository.get_active_account(created_before=…)` and `TokenService.issue` together give the order join needs: account → user status → verified email. `AppContextDep` already rejects inactive apps and unregistered Origins.
- `join_requests` does not exist yet: no model, no migration. §3 has no `decided_at` column.
- USR-07 S1 is still in the code. `register` → `_build_for_app` → `set_default_role_if_null` can reset a NULL `default_role_id` that staff set on purpose, but `docs/deploy.md:383` says the default role never changes.
- `TokenService` computes `join_available = policy != closed`. It ignores a NULL default role and pending requests.

**Recommendations**
1. **Join (AC2).** Require `X-MM-App == {key}` or return 403 `app_mismatch`, like `AudienceMismatchError`: only an app can join users into itself. Steps in order:
   - `lock_app(key)` first, then read `default_role_id` from that locked row. This covers both USR-02c F4 and USR-02d.
   - Resolve the account with the N1 cutoff; a missing account or disabled user → 401 `SessionInvalid`, same as `/token`.
   - Unverified email → 403 `email_unverified`. `closed` (including `admin`) → 403 `join_closed`.
   - `open` with NULL default role → 403 `join_unavailable` (deliberately disabled).
   - Already a member (any active app-scope role, the same test as `/token`) → 200 with no write.
   - Otherwise `_grant_locked(default_role, org_id=None, granted_by=None)`; an expired assignment is replaced in place.
   - One response shape `{status: "member" | "pending"}`. Add `rate_limit("join")`, `no-store`, and set the rotated cookie.
2. **Fix S1 first** (either in this ticket's plan or as a small direct change ahead of it). Keep NULL on register: give `_build_for_app` a `set_default` parameter, and make `register` pass it only when it creates the app. Update the docstring. Also make `join_available` false when the default role is NULL.
3. **Accounts list (AC1).** Cookie class: `AppContextDep` + `DeviceDep`. Return active account sessions on this device only, with the `accounts_created_before` cutoff, hiding disabled users. Fields: `account_id`, `email` (not masked: it is the caller's own device), `email_verified`, `display_name`. Never return `user_id`. Order by `last_used_at` desc. Plain list with `no-store`.
4. **Audit.** Add a small `AuditRecorder` protocol with a logging no-op, called inside the transaction. Log ids only, never emails.
5. **Scope.** Split approval out to a follow-up ticket (USR-03g): `join_requests` with `decided_at`, a partial unique index on `(app_id, user_id) WHERE status = 'pending'`, and staff approve/reject behind `ADMIN_RBAC_API_ENABLED`. Approve must take `lock_app` and recheck verified email, active user and default role. Until then `approval` → 403 `join_approval_unavailable`. With approval out, the rest fits in about 400 lines.

**Risks**
- `lock_app` makes joins to one app run one at a time. That is fine at current volume.
- Under `open`, a user whose role staff revoked can simply join again. There is no ban; that needs `closed` or a later deny-list.
- Granting through the expired-row path resets a staff-set expiry on the default role to permanent.
- Leaving the default-role invariant to runtime alone is risky. Assert `role_has_admin_permission` is false under the lock as a cheap safety check.

**Open questions**
1. Can approval move to USR-03g? This changes AC2/AC4, so the owner needs to agree.
2. Which code for a new membership: 201 or 200? R6 says 201 for create; I suggest 200 for both new and existing, since the call is idempotent.
3. Does `GET /accounts` need an exception from R9's `Page[T]` (a short list bounded per device)?
4. After a rejection, can the user ask again right away, or after a cooldown?
