# ADR-0014: Org invitations

Status: Accepted (owner, 2026-11-17: "defaults" on the USR-06 plan questions B1-B4)
Date: 2026-11-17

## Context
Members of an org need to bring other people into it (calechip first). The invitee proves the
address with a verified account. Invitations carry a bearer token in an email, so they must be
single use, expiring, hashed at rest, and must not become an account or membership oracle.

## Decision
1. **Org-exact checks.** Create needs `<app>:org:invite` held in exactly that org
   (`RbacRepository.org_permission_keys`: org-exact, unexpired, org `active`, never the app+org
   union). Unknown, suspended and not-a-member orgs share one 403 `org_access_denied`.
2. **Subset rule.** The invited role is a role key of the calling app, holds no `is_admin`
   permission, and every permission of it passes `permits(inviter_org_permissions, key)`. The
   owner role is invitable by an owner (no member removal in v1; staff only; _amended by ADR-0026: members holding `<app>:org:remove-member` can remove members_).
3. **Storage.** `org_invitations` stores `SHA-256(token)` and an `HMAC` of the normalized email
   (`IDENTITY_HMAC_KEY`); no email column. Rotating the HMAC key voids pending invitations.
   Token: `secrets.token_urlsafe(32)`, link `{validated Origin}/accept-invitation#token=...`,
   TTL `INVITATION_TTL_HOURS` (168). Re-inviting the same `(org, app, email)` revokes the
   previous pending invitation (audited, reason `superseded`).
4. **Token in the body.** Accept is `POST /auth/invitations/accept {account_id, token}`
   (cookie class), never a path parameter, so the token stays out of access logs.
5. **Bound to the email.** The accepting account must be active and verified and its email HMAC
   must match. A different verified account gets 403 `invitation_email_mismatch` and the
   invitation is not consumed. Unknown, used, expired, revoked/superseded and other-app tokens
   get one 422 `token_invalid`.
6. **Atomic single use.** The pending row is read `FOR UPDATE` under the app lock and consumed
   by a conditional UPDATE; two concurrent accepts give one 200 and one 422.
7. **Re-checks at accept.** The role must still be admin-free; the inviter must still exist, be
   active, hold `org:invite` in that org and a superset of the role's permissions (this also
   fails for a suspended org). Otherwise 422 `token_invalid`, not consumed.
8. **Join on accept (B1).** Accepting joins the app with the default role when the account has
   no app-scope role (`app.joined` audited). Closed or unusable apps refuse, nothing consumed.
9. **No oracle on create.** The create path never reads `users` by email and answers the same
   201 for every target, including the silent per-target limit (1/min, 3/24 h, across orgs),
   which sends nothing but still supersedes this org's own pending invitations for that address
   (so a downgrade re-invite never leaves the higher role live). The global daily cap and the
   invite share say nothing about the target and answer 429 `rate_limited` with `Retry-After`
   (as verify and reset), so a cap used up by others is visible. Per-inviter (5/day) and per-org
   pending (50) caps are about the caller and answer 403 `invitation_limit_reached`. A member
   holding `org:invite` can supersede another member's pending invitation to the same address
   by re-inviting it (the effect stays inside the org).
10. **Email cap.** Purpose `invite` has a 10 % share of `EMAIL_DAILY_CAP`; verify 70 %, reset 40 %
    (ADR-0007 amendment) keep the verify >= 50 % and reset >= 20 % floors.
11. **S1 guard (data only, fail closed; defense in depth since ADR-0015).** An org token carries
    app-scope roles plus the org roles in `roles`, so a default role holding an org-level
    permission would widen a narrower invited role. Invitations therefore work only while
    `org_mode = required`, `join_policy = open`, the default and owner roles are set, the default
    role has no admin permission, and the default role holds no `scope = 'org'` permission.
    Otherwise 403 `org_invite_unavailable` (create and accept). The earlier owner-superset
    precondition and the overlap check are gone. The guard can be retired once every org-mode
    consumer authorizes org data from `org_roles` only (ADR-0015).

## Consequences
- Residual risk (moved to ADR-0015): a staff-granted app-scope role carrying org-level permissions
  still reaches every org token through `roles`; consumers must authorize from `org_roles`.
- Gmail dots and plus tags are not folded: the invitee signs in with exactly the invited address.
- Follow-ups: retention purge of `org_invitations`, cookie-side list/revoke endpoints and the rest of member management (removal shipped in USR-30, ADR-0026). Staff list and revoke (Bearer, `/api/v1/organizations/{org_id}/invitations`) shipped in USR-23.
