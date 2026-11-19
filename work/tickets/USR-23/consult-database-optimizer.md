# Consult — database-optimizer — USR-23 (2026-11-19)

Consult report for USR-23 (database, read-only). The model and index facts below are read from the code and not yet verified with EXPLAIN.

## Findings
- **Existing precedent.** USR-17 `RoleRepository.page_role_holders` (`app/modules/rbac/repository.py:541`, router `app/modules/rbac/router.py:302`) already does most of the members query. It:
  - runs a count statement plus a rows statement, joined to `User` through explicit columns only, so `password_hash` can't leak;
  - supports an `active` filter via `_is_active()`;
  - takes an `org_id` filter;
  - orders by `created_at DESC, id`;
  - sets `Cache-Control: no-store`.
- **Members query shape.** `role_assignments` joins `users`, `roles` and `apps` in one statement, so there's no N+1.
  - `ix_role_assignments_org_id` exists, so `WHERE org_id = :id` is indexed.
  - `uq_role_assignments_user_role_org` has `nulls_not_distinct`, so a user with two roles in one org gives two rows. That fits the one-row-per-assignment spec.
  - `role_id -> roles.id` and `user_id -> users.id` are primary-key joins.
  - `roles.app_id -> apps.id` supplies `app` (the key).
  - The existing `_assignment_rows()` helper probably already selects most of the assignment and role columns. Reuse it and add the `org_id` filter. Check whether it exposes `role_key`, `role_name` and `app`.
- **Invitations query shape.** `org_invitations` has `ix_org_invitations_org_id_created_at (org_id, created_at)`. That serves `WHERE org_id = ? ORDER BY created_at DESC` with a backward index scan.
  - Join `roles` on `role_id` and `apps` on `app_id` for `role_key` and `app`.
  - `invited_by` and `accepted_by` are plain nullable user ids (`ON DELETE SET NULL`). Don't join `users`, because the ticket requires ids only and no email.
- **Status filter.** The `status` filter is derived and not stored, so it can't use an index. It's still a cheap residual filter inside one org's rows.
  - Define the CASE once and use it for both the filter and the output column, so the two can't diverge:
    - `accepted_at IS NOT NULL` -> accepted
    - `revoked_at IS NOT NULL` -> revoked
    - `expires_at <= now()` -> expired
    - otherwise -> pending
  - The check constraint `not_accepted_and_revoked` makes the first two mutually exclusive.
  - Precedence: use accepted, then revoked, then expired. An accepted or revoked invitation that has also passed its expiry date should still read accepted or revoked.
  - Use `func.now()`, which is the transaction time. That is consistent with the existing `consume` and `get_pending_for_update`, which already use `func.now()`.
- **Revoke is cheap.** `supersede_pending` already exists and `AuditAction.ORGANIZATION_INVITATION_REVOKED` and `AuditTarget.INVITATION` are already defined. A staff revoke needs one conditional `UPDATE ... SET revoked_at = now() WHERE id = :id AND org_id = :org AND accepted_at IS NULL AND revoked_at IS NULL RETURNING id`, plus an audit entry in the same transaction.
- **No migration needed.** No new column or index is required. Both permissions are already seeded (`admin:organizations:read` in the seed migration, `admin:users:read` in use). `admin:organizations:write` is presumably seeded too; confirm that.
- **Ownership.** The members query belongs in the rbac module, or in organizations with a read-only query. The invitations list belongs in the invitations module. Both need an org-existence check, which returns 404 for an unknown org.

## Recommendations
1. **Members endpoint (AC1).**
   - Add `page_org_members(org_id, app_key, active, limit, offset)` to the rbac repository by reusing the `page_role_holders` pattern. Use a count statement plus a rows statement.
   - Filter on `RoleAssignment.org_id == org_id`.
   - The `app` filter is `App.key == :app`, validated with `APP_KEY_RE`.
   - Order by `created_at DESC, id`, which is stable for paging. Do not order by `created_at` alone.
   - Select explicit `User` columns only.
   - Set `Cache-Control: no-store`.
   - Use `require_permission("admin:users:read", errors={404: "Unknown organization"})`.
   - Do the org-exists check first, so a 404 is returned for an unknown org and not an empty page.
2. **Invitations endpoint (AC2).**
   - Add `page_for_org(org_id, status, limit, offset)` to `InvitationRepository`, with `status` as an enum `Query`.
   - Select explicit columns only, never the entity. This is a hard guard that `token_hash` and `email_hmac` can't leak.
   - Use a response schema with no hash fields, and add a test that asserts the body keys exactly.
   - Add `Cache-Control: no-store`. The ticket doesn't ask for it, but the rows include user ids. This is optional.
   - Use `admin:organizations:read`.
3. **Revoke (AC4, recommended: include it).**
   - Add `DELETE /api/v1/organizations/{org_id}/invitations/{id}`, returning 204 with `admin:organizations:write`.
   - Run the conditional `UPDATE ... RETURNING` described above.
   - When no row is returned, tell the two cases apart:
     - the invitation doesn't exist or belongs to another org -> 404;
     - it's already accepted or revoked -> decide between an idempotent 204 and a 409.
   - Write the audit entry in the same transaction with `actor` set to the admin principal, `org_id`, `app_key` and `changes={"reason": "staff"}`.
   - The existing `ORGANIZATION_INVITATION_REVOKED` action needs no new enum value, but check that `_supersede` only accepts `EndUser` actors. The staff path may need the admin-principal actor type, as in other staff services.
   - Making `org_id` part of the `WHERE` clause blocks cross-org ids.
4. **Tests.**
   - Cover the ticket's list, plus:
     - an `active` filter test;
     - an `app` filter test;
     - the invitation status filter for each of the four statuses;
     - a case with an expired and an accepted invitation at the same time;
     - an `offset` beyond the total;
     - a revoke attempt using another org's id -> 404;
     - an audit row present after a revoke.
   - Create invitations in tests with an explicit `expires_at` in the past. The `expiry_after_creation` check requires `created_at` to also be set earlier, or set `created_at` explicitly.

## Risks
- **Sorting not covered by the index.** A members list for a large org sorted by `created_at DESC, id` does a sort on the rows from `ix_role_assignments_org_id`. That is fine at expected size (tens to hundreds of members). If an org ever has thousands of assignments, `(org_id, created_at DESC, id)` would help. Don't add it now (R7).
- **`total` semantics.** `total` counts assignments, not distinct users, as for USR-17. Document it in the schema description.
- **PII.** Member emails are exposed, but only to Bearer staff holding `admin:users:read`, with `no-store`. This is consistent with USR-17 and spec §8 Q6.
- **Clock consistency.** `func.now()` is the transaction start time, and a long transaction can skew it slightly. That's acceptable and matches the existing code. Don't mix in a Python clock for `status` or `active`.
- **`roles.id` FK.** `role_assignments.role_id -> roles.id` has no cascade. This only matters if a role is deleted, which the existing rules presumably already guard.

## Open questions
- Should revoking an already accepted or revoked invitation return 409 or an idempotent 204? I suggest 409 for accepted (the revoke is meaningless) and 204 for already-revoked. This is the planner's call.
- Should the `status` filter accept multiple values? I suggest not. Use a single value.
- Should the `admin:organizations:read` and `admin:users:read` split be kept? The members list exposes user PII under `admin:users:read`. Confirm that a role with only `admin:organizations:read` should not see members. The ticket already says this, so it is fine.
- Does the existing org service expose an `exists`/`get` that raises `NotFoundError`? If it does, reuse it. Otherwise add a small repository `exists` check and avoid loading the full row.
