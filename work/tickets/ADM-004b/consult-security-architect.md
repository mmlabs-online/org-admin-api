# consult-security-architect — ADM-004b

## Findings

1. **No guard site writes anything before it runs (verified).** Each site below takes only a lock or does reads before its guard. The rollback in `record_failure` would still discard any pending write, so it is the real guarantee.
   - `AppService.update` (admin app, before `setattr`).
   - Permission `_create`, update and delete.
   - Role create, rename and delete.
   - `replace_permissions` (before `_set_role_permissions`).
   - `_guard_privileged` in assign and revoke (before the insert or delete).
   - `revoke` last-super 409.
   - `users.set_status`: self-status, G5 and last-super, all before `user.status =`.
2. **Rollback expires ORM objects.** `expire_on_commit=False` does not cover `rollback()`. If the row reads `app.key`, `role.id` or `user.id` after the rollback, an async lazy load raises `MissingGreenlet`. The broad `except` swallows it, so the row is lost silently on every denial.
3. **The ticket note "add the row before the reservation commit" is wrong for login.** At `auth/service.py:159` the result of the password check is not known yet. The failure is decided after Argon2, when nothing is pending except the `FOR SHARE` read. `record_failure` (rollback → add → commit) fits that point. The note does apply to `commit_on_reuse=True` (`sessions/service.py:91`). With `commit_on_reuse=False` (login), the reuse row must be a normal `record` in the login transaction.
4. **Join reaches `_guard_privileged` through `_grant_locked` with `SYSTEM_JOIN`.** It is unreachable today. If it ever fired, a rollback would discard the device-cookie rotation on a cookie-authenticated path.
5. **An attacker can suppress their own row.** Any client-controlled field that makes the INSERT fail turns into `audit_write_failed` on failure paths and a 500 on success paths.
   - A NUL in the user agent fails Postgres `text`.
   - A scoped IPv6 address passes `ipaddress.ip_address()` (`str` keeps `%zone`) and fails `INET`.
   - The request id is safe (regex).
6. **`X-Request-ID` comes from the client** (`core/request_id.py:27`). Any caller can make it collide with another request's id.
7. **Admin endpoints have no rate limiter.** The limiter runs in-process, so its limits multiply per worker.

## Recommendations

- **Denied attempts to record (exact list, AC2).** `outcome=denied`, `action` = the attempted action. Record these and nothing else:
  - `app.updated`: G1 `require_super`.
  - `permission.created`, `.updated`, `.deleted`: `require_super`.
  - `role.created`, `.updated` (rename), `.deleted`: `require_super`.
  - `role.permissions_replaced`: `require_super`.
  - `role_assignment.granted`: `require_super` and `self_assignment_forbidden`.
  - `role_assignment.revoked`: `require_super`, `self_assignment_forbidden` and `last_super_admin` 409.
  - `user.disabled`/`user.enabled`: `self_status_change_forbidden`, G5 `require_super` and `last_super_admin` 409.
  - Never record: plain `require_permission`, 404, 422, other 409s, or any `SystemActor`. Record only when the actor is an `AdminPrincipal`.
- **What a denied row may contain:**
  - The actor's user id, plus `target_type`/`target_id`, `app_key` and `org_id`. Take them only from server-resolved objects, and copy them to primitives before the rollback (Finding 2).
  - `changes = {"reason": <error.code>}`, plus catalog keys only (`role_key`, sorted `permission_keys` added/removed).
  - Never the request body, the error message, an email or a free-text field.
  - Add a test that reads the row back after a denial.
- **Where to call it.** Call it explicitly from the public mutators, not from `_guard_privileged` or `_grant_locked` (ADR-0009 §3), and not from a global exception handler. A handler runs after `get_session` has rolled back, lacks the target, and would catch plain 403s. Pattern: `try: require_super(actor) except PermissionDeniedError as exc: await self.audit.record_failure(...); raise`.
- **Make `record_failure` hard to break:**
  - Wrap rollback, add, commit and a best-effort second rollback in one `try/except Exception`, then log `audit_write_failed action=<a>`.
  - Let `CancelledError` propagate.
  - The caller re-raises the original exception object.
  - Tests:
    - Monkeypatch `repo.add` and `commit` to raise, and assert the same 403, 409 or 401 body.
    - Assert the business state is unchanged after a denial.
    - Run under `create_savepoint`, with test setup committed first, because the rollback discards uncommitted setup.
- **Login failures (AC1):**
  - Write the row only after Argon2 (credential-checked), so audit volume can never exceed the cost of hashing.
  - Unknown email: `actor_kind=anonymous`, no target, IP and user agent only.
  - Known email: target the user. An optional `reason` of `invalid_credentials|user_disabled` is safe because it is never in the response.
  - Failed verify or reset tokens write nothing.
- **Abuse resistance.** Do **not** cap audit writes per IP or per actor. A cap is a suppression primitive: spam until it trips, then make the real attempt unrecorded. Throttle the *request* instead:
  - Login is already bounded by the 20/min per-IP limit, the email backoff and Turnstile.
  - Denials need an authenticated admin holding the base permission, so the actor is accountable and a burst is itself the evidence.
  - Add a metric or alert on `login_failed` and `denied` per hour. Purge bounds storage.
- **Retention defaults (spec Q6, owner decides):**
  - Keep every row 365 days, with the full IP stored at write time. Truncating at write would defeat spray and brute-force analysis, and later truncation would need UPDATE, which the trigger forbids.
  - Before `admin:audit:read` goes to any non-super role, add masking at read time: IPv4 /24, IPv6 /48, user agent hidden.
  - Alternative default: 180 days.
- **Purge.** Make it operator-only: a CLI command (`system:cli`) with no HTTP route.
  - Mechanism: the trigger allows **DELETE only** when `current_setting('audit.allow_purge', true) = 'on'` **and** `OLD.occurred_at < now() - interval '<floor>'`. Hard-code the floor in the migration (e.g. 90 days), so even someone who sets the flag cannot erase recent evidence. UPDATE and TRUNCATE stay blocked.
  - Prefer this over partitions, which need DDL and create drift risk for `alembic check`.
  - Run it in batches. Each batch's transaction writes an `audit_log.purged` row (`{"cutoff", "deleted": int}`), so no deletion is left without its row. Apply the same per-batch summary row to the email cleanup.
- **Carried items:**
  - Tie `_ALLOWED_KEYS` to `EMAIL_RETENTION_PURGED`, and require `int` values.
  - In `_valid_ip`, return `None` when `scope_id` is set, and normalise IPv4-mapped IPv6 to IPv4.
  - Strip C0 control characters and DEL (`\x00-\x1f\x7f`) from the user agent before truncating. This is required, not optional (Finding 5).
  - In `RequestIdMiddleware`, keep the inbound id only when `scope["client"]` is in `trusted_proxy_cidrs`; otherwise generate one. Cloudflare does not set the header. Document this in `docs/deploy.md`.

## Risks

- **The table owner can still bypass everything** (`SET audit.allow_purge`, `DISABLE TRIGGER`). The age floor limits the damage from setting the flag. The split runtime role (INSERT/SELECT only) remains the real fix, already tracked as a follow-up.
- **`record_failure` commits early**, which releases the app lock before the 403 returns. This is harmless today. A future guard placed after a write would rely entirely on the rollback.
- **Storage under a botnet spray.** With no cap, the worst case is about 20 rows/min per IP, at under 1 KB each. Only the retention period and the alert bound it.
- **Reuse revocation** happens at most once per device (later attempts fail `_usable`), so its volume is bounded.

## Open questions

1. Owner, spec Q6: is the default of 365 days with full IP acceptable, with a hard purge floor of 90 days? Is read-time IP masking required for future auditor roles? Should anything specific to Vietnam's Decree 13/2023 shorten this?
2. Should denied rows reuse the attempted action plus `outcome=denied` (recommended), or use separate `*.denied` actions?
3. Should `login_failed` for a known user carry `reason=user_disabled`, or only `invalid_credentials`?
4. Should reads of the audit log be audited once purge exists (ADM-004 follow-up)?
