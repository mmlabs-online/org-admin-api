# consult-identity-access-engineer — ADM-004b

**Findings**

1. **Login** (`LoginService.login`, app/modules/auth/service.py). Three kinds of refusal never reach the credential check and should not be audited: `_raise_if_blocked` (429) in phase 1, `TurnstileRequired` and `TurnstileFailed`, and `reserve()` returning `None` (which ends in 429 or Turnstile). Three branches are credential-checked: unknown email (after `verify_dummy`), wrong password, and a disabled user with a correct password. All three end at the single `raise InvalidCredentialsError`. The reservation `commit()` happens **before** Argon2, so the outcome is not known yet. The ticket's "add the row before that commit" therefore can't apply to login: the failure row needs its own second commit after the check. Success: after `add_account`, inside the request transaction.
2. **Reuse revocation** (`DeviceSessionService.resolve`). `revoke_device` is followed by `commit()` when `commit_on_reuse=True`. Every cookie route reaches it through `get_device` (`DeviceDep`), so there is one hook point. Inside `start_for_login` (`commit_on_reuse=False`), the revoke is caught as `SessionInvalidError` and commits with the successful login. `DeviceSessionService` currently has no `AuditLogger` and no context.
3. **Logout** (`LogoutService.logout`). `revoke_account` returns nothing, so "nothing revoked" (an unknown or foreign id, the N1 rule) looks the same as a real revoke. `revoke_device` does not return counts either.
4. **Reset** (`PasswordResetService.reset`). This is the success path only. It already marks the email verified as a side effect. **Verify** (`EmailVerificationService.verify`) has an idempotent early `return` that changes nothing.
5. **AuditLogger.record.** It has no anonymous path: anything that isn't a `SystemActor` becomes `USER`. `ActorKind.ANONYMOUS` exists but is unused. `AuditTarget` has no `device_session`. `_FORBIDDEN_KEY` rejects keys containing "password", so `password_ok` is out but `reason` is allowed.
6. **Context.** `AuditContextDep` is used only by `join`. The login, logout, verify-email and reset routes do not take it.

**Recommendations**

- **Actor.** Add `AnonymousActor` (a frozen sentinel) to `record()`, giving `kind=ANONYMOUS`, `user_id=None`, `label=None` (AC4).
- **Per-event actor, target and changes:**
  - `auth.login_succeeded`: actor `EndUser(user.id)`, target `(user, id)`, `app_key=_app.key`, changes `{"account_session_id", "device_session_id", "new_device": bool}`.
  - `auth.login_failed`, unknown email: actor anonymous, target `None`, changes `{"reason": "unknown_account"}`. No email and no HMAC.
  - `auth.login_failed`, known user: actor anonymous (they proved nothing), target `(user, id)`, changes `{"reason": "bad_password" | "inactive", "failures": n}`.
  - `auth.logout`: write it only when a row was actually revoked; change `revoke_account` to `RETURNING user_id`. Actor `EndUser(user_id)`, target `(user, id)`, changes `{"account_session_id"}`.
  - `auth.logout_all`: actor anonymous (the device holder), target `(device_session, id)`, changes `{"accounts_revoked": n}` (`RETURNING` count).
  - `device_session.reuse_revoked`: actor anonymous, target `(device_session, id)`, changes `{"accounts_revoked": n, "during_login": bool}`.
  - `auth.password_reset_completed`: actor `EndUser(user.id)` (the token is bound to the user), target user, changes `{"account_sessions_revoked": n, "email_verified": bool}`. Do not write a second `email_verified` row.
  - `auth.email_verified`: only when `mark_email_verified` actually ran. Skip the idempotent return.
- **Failure-path ordering for login.** After the check fails, call `record_failure`: rollback (this only drops the `FOR SHARE` read; the reservation is already committed), add the row, commit, then `raise InvalidCredentialsError`. The error is unchanged.
- **Failure-path ordering for reuse.** Do **not** use the rollback-first variant here: it would undo `revoke_device`. Add the row inside `session.begin_nested()` and catch and log `audit_write_failed` on error, then do the existing commit and the existing raise. Without the savepoint, a failed flush poisons the session, the revoke commit fails and the revocation is lost. That is a security regression, not just a missing row.
- **Reuse inside `start_for_login`.** It is part of the success path, so it is written atomically like other success rows (consistent with ADM-004). A failure there gives a 500, which is acceptable. Add one test to pin this behaviour.
- **Context delivery.** Inject `AuditContext` into `DeviceSessionService` in `get_device_sessions` (which depends on `audit_context`), so `resolve` and `start_for_login` share it. Login, logout, verify-email and password reset take `ctx: AuditContextDep` and pass it to the service (R1: services stay HTTP-free). Pass `app_key` where an app context exists; verify-email and reset have none, so it is NULL there.
- **Signup:** out of the ticket's list (R7). Offer `auth.signed_up` (actor `EndUser`, target user, no email) as a follow-up for the owner.
- **Token issuance:** do not audit it. It changes no state, is high-volume, and the logs already cover it.
- **Tests.** Assert that no row is written for 429, `TurnstileRequired`, `TurnstileFailed` or reserve-`None`. Force `AuditRepository.add` to raise on the login-failure and reuse paths: the response must still be 401, and on reuse the device must still be revoked. Search for the test email and the cookie value in `changes`, `actor_label` and `user_agent` and assert they never appear.

**Risks**

- `record_failure` applied the same way to every path would silently undo the reuse revocation (see above).
- The extra commit on a failed login runs under the `create_savepoint` harness. That works, because the reservation commit already does the same, but it doubles commit latency on an attacker-driven path. Per-IP 20/min bounds the number of rows, and retention (Q6) has to keep up with that volume.
- Recording `reason=bad_password` against a known user's id is only an enumeration oracle for audit readers. Keep `admin:audit:read` narrow until Q6 is decided.
- Concurrent reuse detection can make two requests both revoke the same device. The repository only updates rows where `revoked_at IS NULL`, so write the row only when the UPDATE matched, or duplicates appear.
- The `X-Request-ID` and user agent on anonymous rows are controlled by the attacker (already carried from ADM-004).

**Open questions**

1. For a known user, should a failed-login actor be anonymous (my recommendation) or the user?
2. `logout_all` and reuse: one device-level row (recommended) or one row per revoked account?
3. Should signup get an audit row in this ticket?
4. Should a `login_failed` for an unknown email still record the attempt count, with no target? I recommend no, because the count is keyed by email HMAC.
