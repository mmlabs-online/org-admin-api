# Consult — security-architect — USR-23 (2026-11-19)

USR-23 security consult (read-only). This covers the two read endpoints and the optional admin revoke. I read the ticket, `app/modules/organizations/router.py`, `app/modules/invitations/{models,repository,router}.py`, `app/modules/rbac/{models,repository,schemas,router}.py` (USR-17 `page_role_holders`), `app/modules/audit/actions.py` and `docs/identity-architecture.md` §4.

## Findings

1. **A ready pattern exists for the members query.** `RbacRepository.page_role_holders` (`app/modules/rbac/repository.py:541-571`) already:
   - picks explicit `User` columns (email, display_name, status), so `password_hash` can never reach the result;
   - uses `_is_active()` as the single database-clock definition of `active` (line 529);
   - runs a count statement plus one joined rows statement, so there is no N+1;
   - orders by `created_at DESC, id` for stable paging.

   `RoleHolderRead` (`app/modules/rbac/schemas.py:298`) is almost exactly the requested row; it only lacks `role_name`. USR-17 set the precedent of `admin:users:read` plus `Cache-Control: no-store` because rows carry emails (§8 Q6). USR-23 matches it.

2. **The invitation table holds secrets the response must never include.** `org_invitations` has `token_hash` (unique) and `email_hmac`, both 32 bytes. `OrgInvitation.__repr__` already hides them. The real risk is building the response with `model_validate(orm_obj)` against a schema that later gains a field, or a "dump everything" helper. Today no read schema for invitations exists; the cookie router only has `InvitationCreated` and `InvitationAccepted`.

3. **The status rules come from the data model.**
   - The CHECK `not_accepted_and_revoked` means accepted and revoked never both happen.
   - `consume()` only accepts before expiry, so an accepted row is always `accepted`, even if `expires_at` has since passed.
   - `supersede_pending()` (repository line 37) does not filter on `expires_at`. An expired invitation that is later superseded therefore becomes `revoked`, not `expired`. This is correct, but should be documented.
   - Precedence: `accepted` > `revoked` > `expired` (`expires_at <= now()`) > `pending`.

4. **`created_at` on an assignment is not always the "joined" date.** `RoleService` re-grants an expired duplicate in place, keeping the same id and `created_at` (`app/modules/rbac/service.py:1535-1537`). A user whose role expired and was re-granted will show the original date. This is a correctness/UX issue, not a security one, but the admin UI will label it "joined".

5. **Indexes cover both queries.** `ix_role_assignments_org_id` serves the members filter. `ix_org_invitations_org_id_created_at` serves the invitations filter and sort. No migration is needed. The ticket explicitly forbids a new column, and the read paths need none.

6. **The revoke action already exists in the audit vocabulary.** `ORGANIZATION_INVITATION_REVOKED` and `EntityType.INVITATION` are in `app/modules/audit/actions.py:26,38`. Today only the cookie supersede path uses it. No admin or cookie endpoint revokes a single invitation yet.

7. **Mounting the routes on the organizations router puts them in Bearer class 1 automatically.** That router has `dependencies=[Depends(current_admin)]` (`app/modules/organizations/router.py:23`), and the members permission is declared per route through `require_permission`.

## Recommendations

- **R3 / AC1 – members endpoint.**
  - Add the routes to `app/modules/organizations/router.py`, or a sibling router with the same `current_admin` dependency.
  - Members: `require_permission("admin:users:read", errors={404: "Organization not found"})`.
  - Invitations: `require_permission("admin:organizations:read", …)`.
  - Do not add an `admin:*` gate: this is consistent with USR-17, which includes `admin` app holders.

- **AC1 / R1 – members query.**
  - Add a repository method shaped like `page_role_holders`: filter `RoleAssignment.org_id == org_id`, optional `App.key == app`, and `active` via `_is_active()` / `not_(_is_active())`.
  - Add `Role.name` to the selected columns and keep the explicit `User` columns; never `select(User)`.
  - Apply the `app` filter to the count statement too, so `total` matches the rows (needs the Role/App join in the count).
  - Order by `RoleAssignment.created_at DESC, RoleAssignment.id`.

- **§8 Q6 / R5.** Set `response.headers["Cache-Control"] = "no-store"` on the members endpoint because rows carry emails. Also set it on the invitations endpoint as cheap defense in depth. It is not strictly required there: no emails, but it still reveals who invited whom and when.

- **AC1 / AC2 – org existence.** Look up the org first (`OrganizationService.get` or repo `get`) and raise the existing org NotFound so the 404 envelope and message match. An unknown `app` filter value should return an empty page, not 404: app keys are public through the role catalog, so there is no oracle concern either way. Keep it simple.

- **AC2 – invitation status in SQL.**
  - Compute `status` as a SQL `CASE` using `func.now()`, in the precedence order of Finding 3. Use the same expression in the `?status=` filter and the count, so paging and `total` stay consistent and the database clock is used (spec).
  - Make `status` a `StrEnum`; an invalid value gives 422.
  - Select explicit columns: `id, app_id → App.key, role_id, Role.key, invited_by, created_at, expires_at, accepted_at, accepted_by, revoked_at, status`.
  - Do not select the entity and `model_validate` it.

- **AC2 / AC3 – invitation schema.** Declare `InvitationAdminRead` with `model_config = ConfigDict(extra="forbid")` (or just plain fields) and no `email*` or `token*` fields. AC3: no email, no hint and no HMAC-derived value (not even a truncated or hex HMAC; it would be a correlation handle across orgs).

- **AC5 – tests that prove nothing leaks.**
  - Assert that the JSON keys of each invitation row are exactly the expected set. That catches future leaks better than checking only that `token_hash` and `email_hmac` are absent.
  - Check that the raw token from a fixture invitation does not appear anywhere in `response.text`.
  - Assert the `Cache-Control: no-store` header on both endpoints.

- **AC4 – optional `DELETE /organizations/{org_id}/invitations/{id}`.** If the planner includes it:
  - Use `admin:organizations:write`.
  - Scope the lookup to `org_id`: an invitation from another org gives 404, which prevents an IDOR across orgs.
  - Use a conditional `UPDATE … SET revoked_at = now() WHERE id = :id AND org_id = :org AND accepted_at IS NULL AND revoked_at IS NULL RETURNING id`. Do not read then write: racing an accept must not produce both states (the CHECK would raise an IntegrityError → 500).
  - Behavior I'd propose for the planner (my suggestion, not decided):
    - accepted → 409 `invitation_not_pending`;
    - already revoked → 204 idempotent with no second audit row (or 409; pick one and test it);
    - expired but unrevoked → allow the revoke (harmless, gives a clean status).
  - R4: write `ORGANIZATION_INVITATION_REVOKED` in the same transaction. The actor is the staff principal; the target is `EntityType.INVITATION`. The diff must hold only `revoked_at` (and maybe `org_id`, `app`, `role_id`), never `token_hash` or `email_hmac`.
  - Revoking must not touch role assignments an accepted invitation already granted.

- **R7.** Do not add a cookie-side revoke or an inviter email here; list them as follow-ups if wanted.

- **R11.** Add no logging of row contents. Error details for 404 should carry only the org id path parameter, not emails.

## Risks

- **Medium:** a future schema change in the invitations response could leak `token_hash` or `email_hmac` (for example someone switches to `from_attributes` on the ORM object). The test that asserts the exact key set mitigates this.
- **Medium:** if DELETE is done as read-then-write, a race with `consume()` turns into a 500 from the CHECK. It could also break the audit invariant (audit row written for a revoke that did not stick).
- **Low:** the invitations list under `admin:organizations:read` shows `invited_by` and `accepted_by` user ids to staff who lack `admin:users:read`. These are UUIDs only, not emails, which I consider acceptable. If the UI wants inviter emails, they must be fetched through `/users/{id}` (needs `admin:users:read`). Do not embed them here.
- **Low:** the members list includes `admin` app org-scoped assignments, if any exist, and disabled users. This is consistent with USR-17; `user_status` is exposed so the UI can mark them.
- **Low / UX:** `created_at` is not the true join date after an in-place re-grant (Finding 4).
- **Process:** areas include auth/RBAC, so this is a High path. The owner approves at PLAN and the appsec-engineer security review applies.

## Open questions

1. Is DELETE (revoke) in scope? If yes: already-revoked → 204 idempotent or 409? Accepted → 409 (recommended)?
2. Should the members response label `created_at` as "joined" given re-grant in place? Or should the doc note "first granted; preserved across expired re-grants"?
3. Should `active` stay pure "not expired" (as USR-17) even when the user or org is disabled? Recommend yes, with the same field description as `RoleHolderRead.active`.
4. Should `Cache-Control: no-store` also apply to the invitations list? Recommended, but the AC only requires it on members.
5. Should the invitations default to all statuses or `pending`? The join-requests list defaults to `pending`; the AC implies all with an optional filter. Please confirm, because admin-pan PAN-018 depends on the default.
