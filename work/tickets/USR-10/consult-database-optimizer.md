DB consult for USR-10 (read-only, no files changed).

## Findings
- Tables (migration /home/user/org-admin-api/migrations/versions/20261112_83936724f780_create_sessions.py, models in /home/user/org-admin-api/app/modules/sessions/models.py):
  - device_sessions: id, token_hash (unique), prev_*, created_at, last_seen_at, rotated_at, revoked_at, ip (INET), user_agent (varchar 512). Only a partial index on prev_token_hash besides the PK and the unique on token_hash.
  - account_sessions: id, device_session_id (FK cascade), user_id (FK cascade), created_at, last_used_at, revoked_at. Indexes: partial unique uq_account_sessions_active (device_session_id, user_id) WHERE revoked_at IS NULL, and ix_account_sessions_user_id (user_id).
- A user's sessions are reached via ix_account_sessions_user_id, and the join to device_sessions is a PK lookup. A user has a handful of sessions, so no new index is justified.
- Data caveats for "last-seen / IP / UA":
  - device_sessions.last_seen_at is refreshed at most once per hour (TOUCH_EVERY=1h), and on rotate.
  - ip and user_agent are written only in add_device (at device creation). They are not updated on rotation, so they mean "where the device first signed in", not the latest request. Label this in docs.
  - AccountSession.last_used_at is only bumped on upsert (login), so it is not the "last seen".
- Existing revoke code (/home/user/org-admin-api/app/modules/sessions/repository.py):
  - revoke_all_accounts_of_user(user_id) is already a set-based UPDATE ... WHERE user_id AND revoked_at IS NULL, returning the rowcount. Device sessions stay, so devices shared with other users' accounts are untouched. It is used by password reset and user disable, so USR-10 revoke-all can reuse it as is.
  - revoke_account(device_id, account_id, created_before) is device-scoped (cookie side). USR-10 needs a user-scoped variant: UPDATE ... WHERE id=:sid AND user_id=:uid AND revoked_at IS NULL RETURNING device_session_id (or id).
- Lock order in existing code:
  - UserService.set_status takes the admin app lock (FOR NO KEY UPDATE) first, then the user row (get_for_update, FOR NO KEY UPDATE), then runs revoke_all_accounts_of_user.
  - Password reset takes the user row (get_for_update), then revoke.
  - Login takes the user FOR SHARE (get_by_email_for_share), then the device row, then upserts the account.
  - users/repository.py documents that stronger user-row modes taken after the device row can deadlock with login/resend.
- Token path: after a revoke, /auth/token returns session_invalid because the account row has revoked_at set (same mechanism as reset/disable). The existing tests in tests/test_auth_token.py can be copied for the assertion.

## Recommendations
1. No migration and no new index. This satisfies R2 trivially (no schema change, alembic check stays clean). If a reviewer wants one, the only candidate is a partial index on account_sessions(user_id) WHERE revoked_at IS NULL, but it is not needed at this cardinality.
2. List query (new repo method, e.g. SessionRepository.list_for_user):
   - `SELECT a.id, a.created_at, d.last_seen_at, d.ip, d.user_agent FROM account_sessions a JOIN device_sessions d ON d.id = a.device_session_id WHERE a.user_id = :uid AND a.revoked_at IS NULL AND d.revoked_at IS NULL`.
   - Select explicit columns. Do not load tokens or hash columns (R11); never select token_hash or prev_token_hash.
   - Decide whether to also exclude idle- or max-age-expired devices (the `_usable` rule in the sessions service). The device is unusable after idle but not revoked, so the list would show a dead session. I recommend filtering with the same `idle` and `max_age` predicates on the DB clock, to match "active".
   - Order: `ORDER BY d.last_seen_at DESC, a.id DESC` (stable tiebreaker on the PK), with limit/offset and a separate `count(*)` with the same WHERE. `created_at DESC, id DESC` is also fine. Pick one and test the order.
   - Return the ip as str (INET cast). No extra index is needed.
3. Revoke-all, set-based, one transaction:
   - Check the actor is not the target (403, OQ7 self rule) before taking any lock.
   - Take the user row with UserRepository.get_for_update (FOR NO KEY UPDATE). 404 if the user is missing.
   - Call revoke_all_accounts_of_user(user_id) and use the returned count.
   - Audit in the same transaction with ids and the count only. Do not put ip or user_agent in the audit. For revoke-all, the revoked session ids would need `RETURNING id` (the current method returns only a count). The ticket says "session ids and counts", so add a RETURNING variant, or a sibling method, rather than change the password-reset caller.
   - Why FOR NO KEY UPDATE: it conflicts with login's FOR SHARE, so a login in flight either commits first, so its new account session is revoked too, or waits until the revoke commits. Without it a login racing the revoke could leave a fresh session alive.
4. Revoke-one: same lock, then UPDATE ... WHERE id AND user_id AND revoked_at IS NULL RETURNING id, device_session_id. No row returns 404, and the same 404 covers another user's session, an unknown id and an already revoked one (no oracle, and the 404 test in the ticket). Audit with the account session id, plus the device id if wanted.
5. Do not take the admin app lock for the revoke endpoints. Reason: the lock exists in set_status to serialize the last-super-admin guard. Revoke makes no such decision, and the endpoint already requires `admin:*` (owner, 2026-11-16), so the OQ7 rule "admin-role holders need admin:*" is automatically satisfied for the revoke. If you do add the privileged-assignment check, copy the order exactly from set_status: admin app lock, then user row, then check (`require_super`, denied audit via record_denied inside the DENIED_ERRORS handler). This is the ADM-004b pattern the ticket points to.
6. The list endpoint is the one place the OQ7 admin-role rule still matters, if it uses a weaker permission than `admin:*`. The ticket does not state the list permission, so confirm it (see questions). A read-only check needs no lock.
7. Do not revoke the device session itself, because it may carry other users' accounts (explicit AC). The device row stays open even if it ends with no active accounts. That is harmless, because start_for_login already revokes an empty device on the next login (fixation guard) and the idle and max-age limits apply.
8. Use database time (`func.now()`) for revoked_at and any age predicates, per the repo convention. The commit of the revoke and the audit row must be the same transaction. A denied attempt is audited under the existing denied-audit pattern (record_denied), which is committed despite the raised error as in set_status.
9. Tests to add beyond the AC list: concurrent login while revoke-all runs (see tests/test_login_concurrency.py for the pattern); a shared device carrying two users keeps the other user's account alive after revoke-all; revoke-all on a user with zero sessions returns a count of 0 and writes one audit row (decide this and document it); a session of a disabled user is still listable and revocable.

## Risks
- Lock-mode drift: using FOR UPDATE or an admin-lock-after-user ordering could deadlock with login (user FOR SHARE then device) or with set_status. Use get_for_update on the user and keep the admin app lock first whenever it is taken.
- If the list is not filtered on idle and max-age, support staff see sessions that are already unusable and may think a revoke is needed.
- Audit content: revoke-all returning a count without ids would not meet "session ids and counts"; do not log IP or user agent anywhere in the audit or logs (R5/R11).
- last_seen_at has 1 h granularity, and ip and UA are creation-time values. The staff UI must not imply real-time presence.
- Old access tokens stay valid until their TTL. USR-13 raised it up to 12 h (ADR-0005 amendment), so the docs wording for the last AC must not say ~15 min. Admin tokens reload permissions per request, but app tokens do not.

## Open questions
- What permission does the list endpoint need (the ticket says only the revokes need `admin:*`)? If it is weaker, the OQ7 rule for admin-role holders is enforced on the list, and it needs the user_has_privileged_assignment check plus a denied audit (and the choice of whether denied reads are audited at all, per ADM-004b).
- Should the list filter out idle or max-age expired devices (recommended), or only revoked ones?
- Should revoke-one of an already revoked session be 404 (recommended, no oracle) or an idempotent 204?
- Should revoke-all with zero active sessions write an audit row, or be a silent 204 as logout is for a no-op?
- Is the self-target 403 checked before the 404 for a missing user (it would be, if the actor id equals the path id), and is that audited as a denied action?
