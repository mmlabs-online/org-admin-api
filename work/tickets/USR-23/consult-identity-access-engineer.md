# Consult — identity-access-engineer — USR-23 (2026-11-19)

USR-23 identity and access consult (read-only). This is a design review only: I changed no files and ran no checks.

## Findings

1. **Auth class.** Both new routes are R3 class 1 (Bearer). Put them on the existing `organizations` router in `app/modules/organizations/router.py`. That router already has router-level `Depends(current_admin)` and a `_route(key, errors)` helper, so 401 and 403 come before any 404 for free.
   - Members needs `admin:users:read`.
   - Invitations needs `admin:organizations:read`.
   - Both permissions are already seeded (`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`), so no permission migration is needed.

2. **Members list can almost be copied from USR-17.**
   - Endpoint pattern: `list_role_holders` in `app/modules/rbac/router.py:293-320`, which already sets `Cache-Control: no-store` with the comment "rows carry emails".
   - Query pattern: `RbacRepository.page_role_holders` in `app/modules/rbac/repository.py:541-571`. It is two statements (count plus rows), selects explicit `User` columns so `password_hash` can never reach the result, and gives deterministic order (`created_at desc, id`).
   - Row builder: `_assignment_rows()` in the same file at lines 534-539. It joins Role and App and computes `active` from the single database-clock definition, `_is_active()`.
   - Schema: `RoleHolderRead` (`app/modules/rbac/schemas.py:298`) already has every field the ticket asks for except `role_name`, and it names the id `id` rather than `assignment_id`.
   - Note: `RoleHolderRead.active` says it ignores user, org and app status. That caveat should carry over, so the admin panel does not read `active` as "effective in tokens".

3. **Invitations.**
   - Model: `OrgInvitation` (`app/modules/invitations/models.py`) stores only `email_hmac` and `token_hash`. A CHECK makes `accepted_at` and `revoked_at` mutually exclusive, and expiry is compared against the database clock (`func.now()`) everywhere.
   - Index: `ix_org_invitations_org_id_created_at` covers listing by org, ordered by `created_at`.
   - "Revoked" already covers superseded invitations. `supersede_pending` (`app/modules/invitations/repository.py:37`) sets `revoked_at` and audits with `changes={"reason":"superseded"}`.
   - The repository has no revoke-by-id method yet.

4. **Accept can leave an invitation "pending" even though it can never be used.** `_recheck` (`app/modules/invitations/service.py:367`) refuses the token, without consuming it, when the inviter has since been disabled or lost `org:invite`, or the role became admin. The derived `status` will still say `pending` for these.

5. **Audit for the optional DELETE needs no migration.** `AuditAction.ORGANIZATION_INVITATION_REVOKED` and `AuditTarget.INVITATION` already exist (`app/modules/audit/actions.py:26,38`). The `audit_log.action` column only has a format CHECK, not an enum. `AuditLogger.record` accepts an `AdminPrincipal` actor.

6. ADR-0014 (`docs/adr/0014-org-invitations.md:60`) already lists "list/revoke endpoints and member management" as follow-ups, so this ticket is in line with it.

## Recommendations

**R-a. Members (AC1)**
- Add an org-scoped variant next to `page_role_holders`: filter `RoleAssignment.org_id == org_id`, optional `App.key == app`, and optional `active` using `_is_active()` / `not_(...)`.
- Add `Role.name` to the selected columns. Keep the explicit-column rule and select nothing from `User` beyond `email`, `display_name` and `status`.
- Order by `RoleAssignment.created_at desc, id`.
- That makes two statements per request, so no N+1.
- Validate `app` with `Query(pattern=APP_KEY_RE)`, as in `assignments_router.py`. An unknown but well-formed app key should return an empty page, not 404, so it says nothing either way.
- Set `response.headers["Cache-Control"] = "no-store"` (§8 Q6, and it matches USR-17).

**R-b. Org existence check**
- In the service, call `OrganizationService.get(org_id)` (or the repository) first and raise the existing org-not-found error for unknown orgs. Return 404 only after the permission check, which the dependency order already guarantees.
- List members and invitations for suspended or disabled orgs too. Staff need to see them.

**R-c. Response schemas**
- Define new read models instead of reusing `RoleHolderRead`, so the AC field names are exact (`assignment_id`, `role_name`).
- Define `OrgInvitationRead` with an explicit field list.
- Do not use `from_attributes` validation on the ORM `OrgInvitation` entity. Build the model from explicitly selected columns, never selecting `token_hash` or `email_hmac`.
- Add a test that asserts the JSON keys equal the expected set exactly. That is stronger than "no token fields", and it catches a later `model_validate(row)` regression.

**R-d. Derived invitation status (AC2)**
- Compute it in SQL with a CASE on the database clock: `accepted_at IS NOT NULL` → accepted; `revoked_at IS NOT NULL` → revoked; `expires_at <= now()` → expired; else pending.
- Use the same expression for the `status` filter (a pending filter must also require `expires_at > now()`), so the filter and the field cannot disagree.
- Join Role and App for `role_key` and `app`.
- Order by `created_at desc, id`.
- Also send `Cache-Control: no-store`. The rows have no emails, but they link user ids (`invited_by`, `accepted_by`) to org membership, and the cost is zero.

**R-e. Optional DELETE: worth doing only if done this way (AC4)**
- Permission `admin:organizations:write`. No `requires_super`: invitation roles can never be admin roles, which create and `_recheck` both enforce.
- Add a repository `revoke(org_id, invitation_id)` as one conditional `UPDATE ... SET revoked_at = now() WHERE id = :id AND org_id = :org_id AND accepted_at IS NULL AND revoked_at IS NULL RETURNING id`. This is atomic against `consume()` and against accept's `get_pending_for_update` row lock. Do not do a read followed by a write.
- Scoping by `org_id` from the path is required; without it, another org's invitation id could be revoked (IDOR). Answer 404 for an id that is unknown or belongs to another org.
- When the UPDATE affects no row but the invitation exists, return 409 (`conflict`, reason `not_pending`) for accepted or already-revoked invitations.
- Decide expired explicitly: either allow revoking (harmless, and tidies the status) or 409. Allowing it is simpler for the admin panel.
- Write the audit entry in the same transaction (R4): `ORGANIZATION_INVITATION_REVOKED`, actor = the staff principal, `target=(INVITATION, id)`, `app_key`, `org_id`, `changes={"reason":"staff"}`. That separates it from `"superseded"`.
- No locking change is needed. The update takes a single row lock, and accept takes the app lock before the row, so there is no lock-order inversion.

**R-f. Tests (AC5, R8)**
On top of the AC list:
- Invitations JSON key set is exact.
- A staff member with only `admin:organizations:read` gets 403 on members.
- Org A's members and invitations never include org B's rows.
- App-scope assignments (`org_id IS NULL`) never appear in members.
- `status=expired` uses the database clock: set `expires_at` in the past via SQL.
- If DELETE ships: 204, a second DELETE gives 409, an id from another org gives 404, an audit row exists, and a revoked token then fails accept with `token_invalid`.

**R-g. Docs.** Add both routes (and DELETE if shipped) to the endpoint table in `docs/identity-architecture.md` §4 (D14 requires every endpoint to declare its auth class). Update the ADR-0014 follow-up line.

## Risks

- **Permission split.** `accepted_by` and `invited_by` on the invitations list expose member user ids to holders of `admin:organizations:read` who lack `admin:users:read`. The ids are opaque UUIDs that only `users:read` can resolve, so this is low risk, but it is a deliberate choice. The alternative is to put invitations under `admin:users:read` too.
- **Misleading `pending`** (Finding 4). Low security impact, but the admin panel may show "pending" for an invitation that can never be accepted. Document it in the field description, or (out of scope) add a `stale` hint later.
- **Accidental leak of the hashes.** Exposing `email_hmac` would let anyone holding the HMAC key, or anyone with an offline list of candidate emails plus a leaked key, confirm who was invited. The exact-key test in R-c is the guard.
- **Approval gate.** The ticket touches RBAC and identity data, which is a High path: Ming must approve at PLAN, and no agent may merge it. No migration is expected; if the planner finds one is needed, that also pushes it to High.

## Open questions

1. Invitations permission: keep `admin:organizations:read` as the ticket says, or use `admin:users:read` because `accepted_by` and `invited_by` are member identities?
2. If DELETE ships: should revoking an expired invitation return 204 (my recommendation) or 409?
3. Should the members `active` description also mention that the user may be disabled and the org suspended? `user_status` is returned, but org status is not.
4. Should the members endpoint also take a `user_status` filter? The admin panel may want to hide disabled users. Not in the AC; it is cheap to add later without breaking anything.
