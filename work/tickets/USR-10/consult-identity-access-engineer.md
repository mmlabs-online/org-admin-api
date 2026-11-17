USR-10 consult (identity-access-engineer, read-only)

The "~15 min" docs AC is stale (production TTL is now 12 h). Spec §5.10, the ticket and the OQ7 answer disagree on what permission the list endpoint needs. Revoking a staff member's sessions does not end their current admin token.

## Findings

F1. **Data model** (`/home/user/org-admin-api/app/modules/sessions/models.py`). The account session (`account_sessions`: `id`, `device_session_id`, `user_id`, `created_at`, `last_used_at`, `revoked_at`) is the per-user row. Its `id` is the `account_id` apps send to `/auth/token`. Partial unique index: one active row per (device, user). IP, user agent, `last_seen_at`, `created_at` and `revoked_at` live on `device_sessions`.
- The device's `ip`/`user_agent` are written once, when the device row is created (`add_device`). Rotation and later logins never update them (`start_for_login` reuses a device that already has signed-in accounts).
- On a shared browser, the IP/UA shown for user B's session can be **user A's IP from A's first sign-in**. It is "first seen", not "last seen", and may belong to another person.
- `last_seen_at` is touched at most hourly. `account_sessions.last_used_at` changes only on login (upsert), not on `/auth/token`.

F2. **"Active" is more than `revoked_at IS NULL`.** A device stops resolving when it is revoked, idle longer than `DEVICE_SESSION_IDLE_DAYS` (30), or older than `DEVICE_SESSION_MAX_DAYS` (365) (`DeviceSessionService._usable`). Idle or max expiry does **not** revoke the account rows, and nothing cleans them up. So a list filtered only on `account_sessions.revoked_at IS NULL` will show dead sessions forever. A revoked device always has its accounts revoked too (`revoke_device` revokes the accounts first).

F3. **Existing revocation code** (`/home/user/org-admin-api/app/modules/sessions/repository.py`):
- `revoke_all_accounts_of_user(user_id) -> int` is already used by password reset (`auth/password_reset.py:110`) and by disable (`users/service.py:81`). It revokes the user's account sessions on every device and leaves device rows alone, which is exactly the "revoke all" semantics in §5.10. It returns only a count, not ids.
- `revoke_account(device_id, account_id, created_before)` is scoped by device, not by user. It fits logout, not the admin "revoke one".
- `revoke_device` must **not** be used here: it signs out every account on the browser.

F4. **`/auth/token` after a revoke** (`tokens/service.py`). `get_active_account` requires `revoked_at IS NULL` on that device, otherwise `SessionInvalidError`. A revoked account therefore gets 401 `session_invalid`. Other accounts on the same device and the cookie itself keep working. No code change is needed for the AC test.

F5. **Admin per-request reload** (`admin_auth/service.py` `principal()`) checks only that the user exists, is `active`, is verified, plus their effective admin permissions.
- It does **not** check account sessions, and admin tokens carry no `sid` or account-session claim.
- So revoking a staff member's sessions blocks new tokens, but **their current `aud=admin` token keeps working for up to 12 h + 30 s leeway**.
- ADR-0005 amendment risk 4 and ADR-0008 §7 already list "staff session revoke" as not covered. A `sid`/`tokens_valid_after` check is a recorded follow-up.

F6. **Consumer-app tokens** are frozen until `exp`: up to `ACCESS_TOKEN_TTL_SECONDS` (900 by default, 43200 in production since USR-13) plus leeway. The ticket's "~15 min" docs AC is stale. Spec §5.10 already says 12 h.

F7. **Existing self-change and admin-role guards** (`users/service.py` `set_status`, `rbac/actor.py`, `core/errors.py`):
- Self guard: `user.id == actor_user_id(actor)` raises `SelfStatusChangeForbiddenError` (403 `self_status_change_forbidden`).
- Admin-role guard (G5): `rbac.user_has_privileged_assignment(user_id)` (any assignment, expired included, of an admin-app role or a role holding an `is_admin` permission) leads to `require_super(actor)`, which raises 403 `admin_wildcard_required`.
- Both are wrapped in `except DENIED_ERRORS: record_denied(...); raise`, which is the ADM-004b pattern.

F8. **Audit rules** (`audit/service.py`):
- Keys matching `hash|token|password|cookie|email|secret` are refused. `account_session_id(s)` and `account_sessions_revoked` are fine.
- Plain `require_permission` 403s are not recorded. `record_denied` applies only to service guards on mutators.
- The audit row's own `ip`/`user_agent` come from the staff caller's request context. That is normal and is not the target's PII.

F9. **Scope conflict on the list permission:**
- Spec §5.10: "Permission `admin:*` … for list and revoke".
- Ticket AC: `admin:*` for both revoke actions only.
- OQ7 answer: admin-role targets need `admin:*`, which implies a lesser permission (e.g. `admin:users:read`) for other targets.

The spec wins under CLAUDE.md. With `admin:*` on every route, the admin-role rule is automatically met.

## Recommendations

R-a (AC1, §5.10, F2). List `account_sessions JOIN device_sessions` with:
- `account.user_id = :user_id` and `account.revoked_at IS NULL`;
- device `revoked_at IS NULL`, `last_seen_at >= now() - idle` and `created_at >= now() - max`, using the same settings as `DeviceSessionService`, with the DB clock.

Order by `created_at DESC, id`, return `Page[T]`. Fields:
- `id` (the account session id);
- `created_at`, `last_used_at`;
- `device_last_seen_at`, `ip`, `user_agent`.

If spec §5.10's "revoked state / include revoked" filter is wanted, add it as an explicit query flag. Otherwise note the gap against the spec.
- Label IP/UA in the schema and docs as "device first seen", per F1.
- Return 404 when the target user does not exist, never an empty page.

R-b (AC2, F3). Revoke one should be a new repository method:

```sql
UPDATE account_sessions
SET revoked_at = now()
WHERE id = :sid
  AND user_id = :uid
  AND revoked_at IS NULL
RETURNING id
```

Scoping by `user_id` gives the AC's 404 for "another user's session" with no oracle. For an id of this user that is already revoked, I suggest 404 as well (it is not an active session) and no audit row, like a no-op logout. Do not add a device filter.

R-c (AC2, F3). Revoke all should reuse the predicate of `revoke_all_accounts_of_user` but with `RETURNING id`. The audit row needs the ids, so either:
- add `revoke_all_accounts_of_user_ids()` and implement the count method on top of it, or
- add a new method and leave the two current callers unchanged (minimal diff, R7).

Revoke every non-revoked row, including those on expired devices (same as password reset). Leave device rows alone, which meets the AC: other users on a shared device stay signed in. A revoke-all with nothing to revoke should return 204 with no audit row, or one row with count 0. The planner decides; be consistent.

R-d (AC3, OQ7, F7, F9).
- Put `require_permission("admin:*")` on all three routes. This follows the spec; flag the ticket/OQ7 difference to the owner.
- In the service, before any read or write, guard in this order:
  1. 404 if the user is missing;
  2. self check (`target_user_id == actor_user_id(actor)`) → 403 with a new code, e.g. `self_session_change_forbidden` (`PermissionDeniedError` subclass, added to `DENIED_ERRORS`);
  3. `if await rbac.user_has_privileged_assignment(uid): require_super(actor)` (G5 reuse).
- Keep G5 even though the route already requires `admin:*`. It enforces OQ7 if the list permission is later relaxed. It can only be tested by calling the service with a principal that lacks `admin:*`.
- Apply the self guard to list as well (owner: "list/revoke").

R-e (AC4, F8).
- Actions: `user.session_revoked` and `user.sessions_revoked`. Target `(AuditTarget.USER, user_id)`, actor = staff principal.
- `changes`: `{"account_session_id": id}` for revoke one; `{"account_sessions_revoked": n, "account_session_ids": [...]}` for revoke all. Consider capping the id list (e.g. 100) with the count always exact.
- No IP, UA, device id, cookie or token.
- On denial, the revoke mutators call `record_denied(...)` with `changes={"reason": code}`. The list is a read: its self-guard 403 writes no row, consistent with "reads not audited". Say so in the plan.

R-f (AC5 tests, beyond what the AC lists):
- **Shared device:** users A and B on one cookie; revoke all of A → A's `/auth/token` returns 401 `session_invalid`, B's still returns 200, and the cookie still resolves.
- **Self guard:** list and revoke on yourself → 403, with a denied audit row for revoke only.
- **G5:** a target with an admin-app role, and an actor without `admin:*` at service level → 403 `admin_wildcard_required` plus a denied row.
- **Expired device:** a session on an idle device is not listed, but revoke all still counts it.
- **Single revoke:** revoking A's session on device 1 leaves A's session on device 2 working.

R-g (AC6, docs). Rewrite the AC as: "already-issued access tokens stay valid until `exp` (≤ `ACCESS_TOKEN_TTL_SECONDS` + 30 s leeway; 12 h in production)". State the admin nuance explicitly in the API description and in `docs/identity-architecture.md`/spec §5.10:
- Revoking a staff member's sessions does not end their current admin token.
- To cut admin access now, revoke their admin role or disable them, and keep that in place for at least TTL + leeway.
- Reference ADR-0005 §6 risk 4.

R-h (R1). Put the work in `app/modules/sessions/` (repository methods) plus a staff service. Routes go under the users router, e.g. `GET /api/v1/users/{user_id}/sessions`, `DELETE /api/v1/users/{user_id}/sessions/{session_id}` (204), `DELETE /api/v1/users/{user_id}/sessions` (204). The api-platform consultant decides on DELETE-collection vs POST. Register the routes in `tests/test_route_auth_classes.py` through `require_permission`.

## Risks

1. **Compromised staff account:** the main reason to use this feature on an admin is the one case it does not fully handle (F5). Without the docs from R-g, staff will think they ended a 12 h admin token. The real kill switch is disable or role revoke, held for at least TTL + leeway. Re-enabling within that window brings the token back.
2. **IP/UA accuracy and PII (F1):** first-seen values, possibly another person's on a shared browser, shown to staff. Spec Q6 (PII handling) is still open.
3. **Concurrent login during revoke all:** a login can commit right after the UPDATE and create a fresh session. This is acceptable: the user proved the password. Staff wanting a durable cut-off should use disable or password reset.
4. **No cleanup of stale `account_sessions` rows (F2):** the table grows without bound. That is a follow-up, not this ticket.
5. **Size of the revoke-all audit row** if a user has many sessions; mitigate with the cap in R-e.

## Open questions

1. **List permission:** `admin:*` for every target (spec §5.10, recommended), or `admin:users:read` with `admin:*` only for admin-role targets (the reading of the OQ7 answer)? The spec, the ticket and the OQ7 answer must be made consistent.
2. Should the list include revoked or expired sessions behind a flag ("revoked state; default non-revoked" in the spec), or only active ones (ticket)?
3. Already-revoked id on revoke one: 404 (recommended) or 204 no-op? And should a revoke-all with nothing to revoke write an audit row?
4. Q6/PII: may staff see IP and user agent, and should they be labelled "first seen on this device"?
5. Should the follow-up "`sid` claim + account-session check in the admin reload" (ADR-0005 follow-up) be prioritized now that staff revoke exists? It would make this feature actually end admin tokens.
