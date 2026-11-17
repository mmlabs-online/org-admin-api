**Consult: USR-03g REST contract (api-platform-engineer, read-only)**

Checked against current code: `membership/service.py`, `membership/schemas.py`, `membership/errors.py`, `tokens/service.py`, `organizations/auth_service.py`, `invitations/service.py`, `rbac/assignments_router.py`, `tests/test_route_auth_classes.py`, and the USR-03b consults.

## Findings
1. **The ticket text is stale.**
   - The admin RBAC flag is gone (ADM-002b). Staff routes mount unconditionally and need `current_admin` plus exactly one `require_permission` (see `assignments_router.py`). Drop "behind the admin RBAC flag" from the ticket.
   - The audit mechanism is `AuditLogger.record(action, actor, ctx, target, app_key, changes)`. `AuditRecorder` no longer exists.
2. **Join today** (`POST /api/v1/auth/apps/{key}/join`, cookie class):
   - It always returns 200 `{"status":"member"}`. `JoinResponse.status` is `Literal["member"]` with a "USR-03g adds pending (202)" comment.
   - Check order is: key == X-MM-App (else 403 `app_mismatch`), `lock_joinable_app`, account (401 `session_invalid`), verified email (403 `email_unverified`), then existing membership (200, nothing written), then `join_default_role`.
   - `join_default_role` raises 403 `join_approval_unavailable` for `approval`, 403 `join_closed` for `closed`, and 403 `join_unavailable` for a missing or `is_admin` default role.
   - `JoinApprovalUnavailableError` is documented "until USR-03g".
3. **`join_available` is computed inline in three places.**
   - They are `tokens/service.py:77` and `organizations/auth_service.py:89`, each as `policy == OPEN and default_role_id is not None`, plus the 403 `not_member` details.
   - Today it is false for `approval`. The USR-03g comment in `tokens/service.py` says "approval true again".
4. **Org apps (`org_mode=required`).**
   - Join stays app-scope. `_grant_locked(org_id=None)` is fine.
   - Org create (`org:create`) and org invitations both require existing app membership, so an approved join unlocks them.
   - Invitations `_unavailable_reason` requires `join_policy == OPEN` (S1 guard). So org invitations stay unavailable on `approval` apps.
   - `InvitationService.accept` calls `join_default_role` for a not-yet-member invitee. That is unreachable for `approval` apps because the guard runs first. Keep it unreachable.
5. **`_grant_locked` details.**
   - It raises 409 "already assigned" on an active duplicate.
   - `_guard_privileged` requires `admin:*` when the role holds an `is_admin` permission. Join already refuses admin default roles, so approve must refuse them too.
   - `granted_by` comes from `actor_user_id(actor)`, so passing the staff principal records who approved.
6. **Route-class test.**
   - `test_every_route_is_classified` and `test_bearer_route_permission_map` require every new Bearer route in `BEARER` with its single key. They also check openapi `x-permission`, `security`, and 401/403 responses.
   - `_fill()` needs a new path-param name for `{request_id}`, or the sweeps (`401_without_token`, `401_with_cookie_only`, `credential_on_bearer_routes_401`) fail on `.format_map`.
   - The join route is already in `COOKIE`, so no allow-list change is needed there.
   - The new paths live under `/apps/{key}/…`, not `/auth/`, so the CORS matcher already treats them as admin.
7. **No admin permission key fits cleanly.**
   - Existing keys are `admin:apps:read|write`, `admin:users:read|write`, `admin:roles:manage|assign`, `admin:organizations:*`, and `admin:audit:read`.
   - Approving is a role grant, which is exactly what `admin:roles:assign` guards.
   - Listing exposes end-user emails.

## Recommendations
**Join (cookie class, unchanged route)**
- `approval`, no membership:
  - Return **202** `{"status":"pending","request_id":<uuid>}`.
  - Set `response.status_code = 202` dynamically on the same handler, and keep `Cache-Control: no-store` and the cookie re-set.
  - Document both 200 and 202 in `responses=`.
- A repeated join while a request is pending returns **202** with the same `request_id`.
  - Use insert-or-return-existing on the partial unique index.
  - Write no audit row and no new row on the replay.
- Keep the member check before the policy check, so a member of an `approval` app gets 200 `member`.
- `JoinResponse` becomes a discriminated union on `status`: `member` (no `request_id`) or `pending` (`request_id` required). This is additive and gives typed SDKs.
- Order for `approval`: `lock_joinable_app`, account, verified, membership, then policy.
  - Validate the default role (exists, no `is_admin`) at request time too, else 403 `join_unavailable`. This avoids queueing requests that can never be approved.
  - A pending request needs no `role_id` column. Approve uses the default role at approval time (answers ticket open Q2).
- Split `join_default_role` into a policy-free `usable_default_role(rbac, app)` and a policy check. Then `open` join, approve and the invitation path share one role validator.
- Remove `JoinApprovalUnavailableError`. The `approval` 403 was explicitly provisional, so changing it to 202 is not a v1 break. Say so in the changelog and in `docs/consumers.md`.
- Add audit action `join_request.created` (actor EndUser, new target `join_request`), written only on the first insert.

**`/token`, orgs and `join_available`**
- Extract one helper, e.g. `join_available(app) -> bool`, returning true when policy is `open`, or `approval` with a usable default role. Use it in all three places.
- Keep `join_available` true while a request is pending. The re-POST is idempotent and answers 202, so that is truthful.
- Add the additive field `details.join_pending: bool`, so UIs can show "waiting for approval" instead of a Join button. It needs a per-user pending lookup on the 403 path only.
- This adds no new status code. Adding a key to the `details` object is within the additive rules.

**Staff endpoints** (put them in a new router with `prefix="/apps"` or in the apps router; mounted unconditionally)

| Method | Path | Permission | Success | Errors |
|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/join-requests?status=pending\|approved\|rejected&limit&offset` | `admin:users:read` | 200 `Page[JoinRequestRead]` | 401, 403, 404 unknown app, 422 |
| POST | `…/join-requests/{request_id}/approve` | `admin:roles:assign` | 200 `JoinRequestRead` | 404, 409 |
| POST | `…/join-requests/{request_id}/reject` | `admin:roles:assign` | 200 `JoinRequestRead` | 404, 409 |

- List:
  - Default `status=pending`, stated explicitly in the openapi description.
  - Order `created_at, id`, matching the `(app_id, status, created_at, id)` index.
  - The permission is `admin:users:read` rather than `admin:apps:read` because rows carry user email.
  - If the owner prefers a single `apps` family, `admin:apps:read` also works. Decide once. Whichever is chosen, add it to the `BEARER` map.
- Approve and reject are POST actions returning 200 with the updated resource. Do not use `PATCH`/`DELETE` and do not return 201 or 204. This is consistent with R9, which has no verb-resource exceptions, and approve needs to return the granted role.
- `JoinRequestRead`: `id`, `app_key`, `user_id`, `email`, `display_name`, `status`, `created_at`, `decided_at`, `decided_by`, `granted_role_key` (null unless approved, or null when the user was already a member).
- 404 (`not_found`) covers an unknown app, an unknown request id, and a request that belongs to another app. Check `id` and `app_id` together. Use one answer, no oracle.
- 409 for a non-pending request: `join_request_not_pending`. Approving an already-approved request also gets 409, not an idempotent 200.
- Approve (inside `lock_app`, then `FOR UPDATE` on the request, same lock order as join):
  - Re-check: user active, email verified, default role usable (exists, no `is_admin`).
  - If the user already has any effective role in the app, mark the request approved and skip the grant. Do not surface `_grant_locked`'s "already assigned" 409 for that case.
  - Failures that are state problems should be **409 with stable codes**: e.g. `join_request_user_inactive`, `join_request_email_unverified`, `join_unavailable`. Do **not** reuse the 403 `PermissionDeniedError` subclasses (`JoinUnavailableError`, `EmailUnverifiedError`). A 403 on a staff route means "missing permission", and that would mislead staff clients and tests.
  - Grant with the staff principal as actor, so `granted_by` is the staff user. Do not use `SYSTEM_JOIN`.
- Reject: needs no user re-check. It sets `status`, `decided_by` and `decided_at` and writes no grant.
- Audit (R4, same transaction):
  - `join_request.approved`, with a nested `role_assignment.granted` or the existing `_record_grant` helper, plus `join_request.rejected`.
  - `changes={"status": change("pending", "approved"|"rejected")}`. Check the `audit/diff.py` allowlist for the new fields.
  - Update the actions catalogue and any purge or doc listing tests that enumerate `AuditAction`.

**Tests to add**
- Extend `tests/test_route_auth_classes.py`: three entries in `BEARER`, and a `request_id` UUID in `_fill()`.
- Join: 202 pending, 202 replay (same `request_id`, one row), 200 member, 403 `closed`, 403 on a bad default role, `approval` with unverified email (403), and 401.
- `/token`: `not_member` on an `approval` app gives `join_available=true`, with `join_pending` true after a request.
- Staff: happy approve and reject (assignment present or absent), 404 (unknown id, cross-app id), 409 non-pending, 409 each stale-state case, 401 and 403 with a token lacking the key, and the list `Page` shape with a status filter.
- Concurrency: two parallel approves yield exactly one grant.

## Risks
- Escalation: approve must refuse any default role with an `is_admin` permission. Otherwise an `admin:roles:assign` holder could use approvals as a privileged-grant side door, and `_guard_privileged` would only catch it when the actor lacks `admin:*`.
- A default role change between request and approval silently changes what is granted. This is accepted (the owner leans toward the default role at approval time), but the response must show `granted_role_key` and the audit row must record it.
- `register-app` and manifest sync can silently reset `default_role_id` (S1 2026-11-13). Approve must fail with 409, never 500, when it is NULL.
- No cooldown: after a reject, a user can re-request immediately (the partial unique index covers only pending). Rate limit `join` bounds the rate, but rows are unbounded. Pending requests also never expire.
- Disabled or unverified users with pending rows clutter the staff list. Filter by user status or surface it in the read model.
- Deleting a user or app cascades the rows (migration FKs), so no orphaned PII.

## Open questions
1. Which list permission: `admin:users:read` (recommended, because of email) or `admin:apps:read`?
2. Re-request after a reject: allow immediately (recommended for v1, with a note), or cooldown or deny-list (F6)? If a cooldown, which code (403 `join_request_rejected`, or 429 with `Retry-After`)?
3. Should pending requests expire or be bulk-purgeable?
4. Should `join_pending` in `not_member` details ship in this ticket? I recommend yes, because it is cheap and additive.
5. Is it acceptable that invitations stay unavailable on `approval` apps? That is the current S1 guard (org invitations require `open`), not a change in this ticket.

Paths: `/home/user/org-admin-api/work/tickets/USR-03g/ticket.md`, `/home/user/org-admin-api/app/modules/membership/service.py`, `/home/user/org-admin-api/app/modules/membership/schemas.py`, `/home/user/org-admin-api/app/modules/tokens/service.py`, `/home/user/org-admin-api/app/modules/organizations/auth_service.py`, `/home/user/org-admin-api/app/modules/rbac/assignments_router.py`, `/home/user/org-admin-api/tests/test_route_auth_classes.py`.
