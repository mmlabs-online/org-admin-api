# consult-security-architect: USR-03g (join requests)

Code checked: `app/modules/membership/service.py`, `app/modules/auth/router.py:245-270`, `app/core/rate_limit.py`, `app/modules/admin_auth/deps.py`, `app/modules/rbac/service.py` (`_grant_locked`, `_locked_app`, `update`), `app/modules/rbac/repository.py:68`, `app/modules/users/repository.py`, `app/modules/audit/actions.py`, `app/modules/tokens/service.py:74-82`, `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py`, `migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`, and the USR-03b consults.

## Findings

**F1. The ticket is out of date on auth (scope conflict).** The ticket says the staff endpoints sit "behind the admin RBAC flag". ADM-002 has landed and no such flag exists in the code. Every Bearer route now needs `**require_permission(...)`. It also has to be listed in `BEARER` in `tests/test_route_auth_classes.py` (`test_bearer_route_permission_map`) and in the mutating map in `tests/test_audit_coverage.py`. Flag this to the owner and treat R3 class 1 as the requirement.

**F2. No permission covers this yet, and the key format limits the options.** `_PERMISSION_RE` allows only `admin:<a>:<b>`, so `admin:apps:members:manage` (proposed in the USR-03b consult) is not valid. Approving grants a role, so a weaker gate than `admin:roles:assign` would let staff grant roles they otherwise could not. The list shows full emails, which `admin:users:read` already exposes through `UserRead`.

**F3. Queue flooding.**
- `rate_limit("join")` counts per IP only (20/min by default) and lives in process (ADR-0002). There is no per-account limit.
- The partial unique index caps each user at one pending request per app. But nothing stops a loop of reject, re-request immediately, reject again. Each cycle writes a new row and a new audit entry and lands back in the staff queue.
- With many accounts, the queue size is bounded by how many verified accounts an attacker can create (sign-up rate limit + Turnstile + email verification). That is real friction, but it is not a hard limit.

**F4. Enumeration.**
- Join already requires `{key} == X-MM-App`, bound to the Origin (`AppMismatchError`). An app can only file requests into its own queue, so join requests add no way to enumerate apps or users.
- On the staff side the risk is cross-app ID confusion: the path `/apps/{key}/join-requests/{id}` with an `id` that belongs to another app. That matters most once staff permissions are scoped per app.

**F5. Races.**
- **Default role change, app disable, policy change:** all of these go through `_locked_app` / `lock_app`, so if approve takes `lock_app` first it is serialized against them. Good.
- **User disable:** this uses `get_for_update` (`FOR NO KEY UPDATE`), which conflicts with `get_for_share`. Approve must lock the user row FOR SHARE, after the app lock (ADR-0003 §10 order: app, then user).
- **Existing open join path:** it reads the user with plain `users.get()` and no lock. Disable can therefore race an open join. This is minor and existing; it goes to follow-ups.

**F6. The grant can fail in a way that blocks the queue.** `_grant_locked` raises 409 "Role is already assigned" when the user already holds the role. If staff gave the user a role by hand while the request was pending, approve would always return 409 and the request could never be approved. An expired assignment is replaced in place, which is fine.

**F7. `/token join_available`** is currently `policy == open and default_role_id is not None` (`tokens/service.py:77`). The comment there already expects USR-03g to make `approval` count as available again.

**F8. Join response.** `JoinResponse(status="member")` is always 200. The 202 pending path must still apply the device-cookie rotation (`set_device_cookie`) and `Cache-Control: no-store`.

**F9. PII and staff UI risk.**
- Staff already see full emails at `GET /users`, so showing them in the queue is not a new exposure if the queue uses the same permission gate.
- `display_name` is set by the user and shown to staff. Treat it as untrusted: stored-XSS risk in the admin UI, and it can be used to social-engineer staff (e.g. a name like "pre-approved by security").

## Recommendations

**R1. Permissions** (AC "staff endpoints", R3).
- `GET …/join-requests` → `admin:users:read`.
- `POST …/approve` and `…/reject` → `admin:roles:assign`.
- This needs no new seed migration and can never grant more than staff can already grant.
- If the owner wants narrower keys (`admin:join-requests:read` / `:decide`), they need a data migration in the same pattern as `5e2a9c7d1f04`, and decide must still imply nothing beyond the default role.
- Register both routes in `BEARER`. Keep them out of `/api/v1/auth/*` (the CORS scopes are disjoint).

**R2. Approve algorithm** (AC 3, R1, R4). All in one transaction:
1. `key == admin` → 404 (before any read).
2. `_locked_app(key)` → 404 if unknown.
3. `SELECT join_requests … WHERE id=:id AND app_id=:app_id FOR UPDATE` → 404 if missing, including when the id belongs to another app (no oracle). Not `pending` → 409 `join_request_decided`.
4. The app must be `active` and its policy still `approval`. Otherwise → 409 `join_unavailable` (deny by default: a closed or disabled app admits nobody). Do **not** reuse `OriginNotAllowedError` / `lock_joinable_app` here; those are errors for the cookie class.
5. `users.get_for_share(user_id)`: active and `email_verified_at` set, otherwise 409 `user_inactive` / `email_unverified`. Never 200.
6. Read `default_role_id` from the locked app row. It must be non-NULL and pass `role_has_admin_permission == False`, otherwise 409 `no_default_role`. Reuse the checks in `join_default_role`, but do not reuse its `approval` raise.
7. If the user is already a member (`authz.effective_roles` is non-empty), mark the request approved, skip the grant and record `granted: false`. Otherwise `_grant_locked(..., actor=principal)`, so `_guard_privileged` applies.
8. Set `status`, `decided_by=principal.user_id` and `decided_at=now()` from the DB clock.

**R3. Reject.** Allow it even when the app is disabled or the policy has changed, so staff can clean the queue. Use the same 404/409 rules. Do not take a reason field in v1; if the owner wants one, make it staff-only, bounded and never returned to the end user.

**R4. Cooldown after rejection** (closes F3, USR-03b F6).
- Add a `join_reject_cooldown_days` setting in `config.py` and `.env.example` (default 7–30), per R5.
- During the cooldown, join answers 403 `join_rejected` with no reason and no decider, and `/token join_available=false`. Back it with `ix_join_requests_app_user_decided` or reuse the user index.
- Optional backstop: a cap on pending requests per app (e.g. 1,000) that answers 429/503 and logs a warning.

**R5. `join_available`** (AC 2) = policy is `approval` + default role usable + not in cooldown. It stays true while a request is pending, since a repeated POST still returns 202. Optionally add a `join_status: "pending"` detail (an additive change).

**R6. Idempotent pending** (AC 2).
- Use `INSERT … ON CONFLICT (app_id,user_id) WHERE status='pending' DO NOTHING RETURNING`, then select the existing row.
- An idempotent repeat writes **no** audit row, which `test_noop_writes_no_row` enforces.
- Run the same pre-checks as the open path (verified email, active user, already a member → 200 `member`, never a new pending row).

**R7. Audit** (R4, AC 4). Add a new `AuditTarget.JOIN_REQUEST` and these actions:

| Action | Actor | Target | Changes |
|---|---|---|---|
| `app.join_requested` | `EndUser` | request | `{status: pending}` |
| `app.join_request_approved` | staff principal | request | `{status, role_key, role_assignment_id or granted:false}` |
| `app.join_request_rejected` | staff principal | request | `{status}` |

- Use `app_key` everywhere.
- Record `user_id` only, never the email or `display_name` (enforced by `test_no_secret_or_email_in_any_row`).
- If approve also writes `role_assignment.granted`, add it to the multi-row set explicitly. Otherwise keep one row.
- Permission denials are already recorded by the guard.

**R8. List response** (PII, R9).
- `Page[T]` with `limit ≤ 100`, `status` filter defaulting to `pending`, order `created_at, id`.
- Fields: `id`, `user_id`, `email` (full is acceptable under `admin:users:read`), `display_name`, `email_verified`, `user_status`, `created_at`, `decided_by`, `decided_at`.
- No password, session or device data.
- `Cache-Control: no-store`.
- Note for the admin UI: render `display_name` as text only.

**R9. Logs.** Log only `join_request_* request_id= app= user_id=`.

**R10. Tests** (R8). For each staff route: 401 without a token, 403 with the wrong permission, 404 for an unknown app, 404 for a request id from another app, 409 for an already-decided request.

Race scenarios to test:
- default role set to NULL while a request is pending → approve returns 409
- user disabled → 409
- policy changed to closed → 409
- user already a member → approve succeeds without a grant

Cookie flow:
- reject, then join → 403 during the cooldown
- repeated pending join → 202 with the same `request_id` and one audit row

## Risks
- The rate limiter is in-process and per IP. Behind more than one replica, or with rotating IPs, the per-IP limit is weak; the cooldown and the pending cap are the real controls.
- An approved user who is later removed by staff can re-request after the cooldown. Without a deny-list a "ban" does not hold (USR-03b F6, still open).
- If policy changes from approval to open, pending rows go stale (the user can now join directly). Approve then takes the "already member" path. That is harmless, but the queue looks noisy; optionally mark them approved by the system when the open join succeeds.
- `decided_by ON DELETE SET NULL` drops the decider from the row when that staff user is deleted. The audit log is then the only record, so audit retention/purge must keep these rows long enough.
- Decided rows grow without bound with no retention policy. This is a follow-up: a purge that keeps the latest rejection per (app, user) for the cooldown.

## Open questions (owner)
1. Permissions: reuse `admin:users:read` + `admin:roles:assign` (recommended), or seed new `admin:join-requests:*` keys?
2. Cooldown length after rejection, and does the user get `join_rejected` explicitly or a generic `join_unavailable`?
3. Does approve still work after the policy changes to `open`, or only while it is `approval`? (Recommended: allowed under `approval` or `open`, refused under `closed` or when the app is disabled.)
4. Do we need a permanent deny/ban record (USR-03b F6), or is that a follow-up ticket?
5. Should rejection carry a staff-only reason in v1?
6. Do we need a pending cap per app, and if so, what value?

Follow-ups (outside the ticket, R7):
- Open join reads the user without a lock (`membership/service.py:94`); use `get_for_share`.
- Retention purge for decided `join_requests`.
