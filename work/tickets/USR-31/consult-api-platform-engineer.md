# Consult — api-platform-engineer — USR-31 (2026-11-24)

**Findings**
- The invitation model carries exactly one role: `org_invitations.role_id` (app/modules/invitations/models.py:67, composite FK with `app_id` at :36). The ticket's "role key(s)" therefore resolves to a single `role_key`. USR-23 already exposes `role_key`, `created_at` and `expires_at` (app/modules/invitations/schemas.py:44-63). It also has a computed `status` and no email, token or hash.
- USR-29 is `GET .../members`. It uses `OrgMembersCredentialDep`, `rate_limit("app_org_members")`, `enforce_subject_limit(..., caller.key_id)`, `Page[T]`, `Cache-Control: no-store` and `_RESPONSES` (app_router.py:23-68). A second route in the same file reuses all of that.
- Member rows are one per user (ADR-0025 §6, docs/consumers.md:226-234). An invitee has no account or user id yet. An opt-in field on the member read would mix two different entities and break the `user_id` ordering, `status` filter, `updated_since` and the `total` count.
- The scope is `org-members:read`, and the registry maps one route to a scope set (tests/test_route_auth_classes.py:66,74). The CHECK constraint lists the scopes (models.py:47). A new scope would need a migration, which is a High path.

**Recommendations**
1. **Use a new route**, `GET /api/v1/apps/{key}/orgs/{org_id}/invitations`. It returns `Page[AppOrgInvitationRead]` (R9, plural noun) and sits beside the members route in `app_router.py`.
2. **Reuse `org-members:read`.** It is the same data domain (planned membership) and the same blast radius. This needs no migration and no CHECK change. A separate scope would be the safer choice only if the owner wants least privilege per key. I don't recommend it for an S ticket.
3. **Item schema, minimal:** `id` (UUID), `role_key` (full key of the app's org-scoped role), `created_at`, `expires_at`. Do not return `invited_by`, `status` (always pending) or `role_id`. Do not add `email_hint` either, per the ticket. Use a new schema, not `OrgInvitationRead`, so a field added to the staff read can't leak to apps.
4. **Sort and filters:** order by `(created_at, id)`. Using `id` as the tiebreaker keeps pages stable, as ADR-0025 §6 does for members. Order newest first, or oldest first if Ming prefers. The only query parameters are `limit` and `offset`. Add `role` (exact role key) only if simap asks. Skip `updated_since`, because invitations have no tombstones.
5. **Pending means:** not accepted, not revoked, `expires_at > db now()`, and `app_id = {key}`. The role key must belong to `{key}` (the composite FK guarantees this).
6. **Errors identical to USR-29:** 401, 403 `app_credential_mismatch`/`app_credential_scope`/`app_disabled`, 404 `org_access_denied`, 422 `org_not_supported` and `validation_error`, 429. Reuse the USR-29 org-visibility query, including the relation rule that the org holds a non-expired org role of the app (ADR-0025 §5).
7. **Registry:** add `ORG_INVITATIONS = f"{V1}/apps/{{key}}/orgs/{{org_id}}/invitations"` with `("GET", ORG_INVITATIONS): frozenset({"org-members:read"})`. Also extend the R3 class 4 (a) scope text in CLAUDE.md. That file is a High path and Ming's to approve.
8. **Docs and audit:**
   - Add a "Pending invitations" subsection after docs/consumers.md:250, with a field table and the same error table.
   - Add an ADR-0025 amendment line: the route joins the allow-list, and §8 audit gets `organization.invitations_read` (counts only).
   - Add a spec §5.8 dated line next to the USR-29 line at docs/spec-v1.md:88.
   - Use a separate rate-limit bucket name, `app_org_invitations`, and a per-key limit that shares the key.

**Risks**
- This is read-only and the fields are minimal. The residual risk is that it reveals planned headcount. The `org-members:read` blast radius (ADR-0025 §9) already covers emails, so it is no worse.
- Role keys may hint at the invitee's privilege level. This is acceptable, because the Manager sees the same in the app.
- A pending invitation can still fail on accept if the inviter lost invite rights (schemas.py:58-62). Document that "pending" is not a guarantee of a seat.
- `db now()` versus Python time can disagree at the expiry boundary. Use the database clock, as USR-23 does.
- Tests must assert that no email, token or hash appears in the response, logs or audit rows.

**Open questions for Ming (defaults)**
1. New route or an opt-in field on the member read? Default: new route.
2. Reuse `org-members:read` or add a new scope? Default: reuse, with no migration.
3. Sort order: newest first, or oldest first? Default: `created_at` ascending, then `id`.
4. Add a `role` filter now? Default: no, until simap asks.
