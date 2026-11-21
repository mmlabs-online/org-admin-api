# ADR-0026: Org member removal (cookie class)

Status: Proposed (accepted when the owner merges USR-30)
Date: 2026-11-21

Amends ADR-0014 §2 and its Consequences ("no member removal in v1; staff only", "member management").

## Context
simap asked that an org owner can remove another person from an organization without staff.
ADR-0014 deferred member management; spec §5.7 listed it as Later. The owner approved member
removal at the USR-30 plan gate (batch 2026-11-21-1, all defaults) and the spec carries a dated line.
The ticket's four-segment permission `<app>:org:members:remove` is invalid under ADR-0003
(exactly `<app>:<resource>:<action>`), so the key is `<app>:org:remove-member`.

## Decision

1. **Route and class.** `POST /api/v1/auth/orgs/{org_id}/members/{user_id}/remove`, body
   `{account_id}` (the caller's signed-in account; `{user_id}` is the target). R3 class 2: cookie,
   `Origin`, `X-MM-App`. The app comes only from `X-MM-App`. Success is **204** with
   `Cache-Control: no-store`. `POST` (not `DELETE`) because CORS for cookie routes allows only GET
   and POST (`app/core/cors.py`).
2. **Permission.** `<app>:org:remove-member`, reserved scope `org` (like `<app>:org:invite`; a
   contradicting declaration is 422 `reserved_scope`), checked org-exact in the service. The app
   declares it in its manifest; **staff** add it to the app's org owner role, because the manifest
   sync refuses to change that role (`role_not_manifest_managed`). The default role never holds it
   (ADR-0015 §3).
3. **Check order and codes.** App active (else 403 `origin_not_allowed`); `org_mode` must be
   `required` (422 `org_not_supported`); caller session and verified email (401 `session_invalid`,
   403 `email_unverified`); per-user limit (429); caller holds a role in the org (else 403
   `org_access_denied`, one answer for an unknown, suspended or not-a-member org, ADR-0014 §1);
   caller holds the permission (403 `org_remove_member_forbidden`); owner role set (403
   `org_member_remove_unavailable`); target is not the caller (403 `self_removal_forbidden`, also
   for a sole owner); target holds a non-expired role of the app in the org (404 `member_not_found`);
   rank rule; last-owner rule (409 `last_org_owner`).
4. **Rank rule.** 403 `member_outranks_caller` when a role of the target holds an `is_admin`
   permission or the target's org permissions are not a subset of the caller's (`permits`, so
   `<app>:*` covers every key). Co-owners (equal sets) may remove each other.
5. **Last owner.** Removal is refused when the target holds the owner role and no other holder
   counts: a permanent assignment (`expires_at IS NULL`) of an active, verified user. `lock_app` is
   the only lock and the first statement; every `role_assignments` writer takes it, so concurrent
   removals queue and the second recounts. Two owners removing each other: one gets 204, the other
   403 `org_access_denied` (its caller lost access) or 409 when its caller is not an owner.
6. **Effect.** Deletes the target's non-expired org-scoped assignments of the app in that org.
   The account, app-scope roles, other apps and other orgs are untouched. The target's pending
   invitations to that org and app are revoked (reason `member_removed`), so the person cannot
   rejoin by accepting one.
7. **Audit (R4).** One `organization.member_removed` row in the same transaction (actor = caller,
   target = user, app, org, `role_keys`, `count`, `was_owner`; no email), plus one
   `organization.invitation_revoked` row per revoked invitation. Refusals write no row for end users
   (warning log with ids only).
8. **Revocation lag.** Access tokens already issued keep `org_roles` until `exp` (up to
   `ACCESS_TOKEN_TTL_SECONDS`, 12 h in production). New tokens with that `org_id` are refused (403).
   Apps re-check via the USR-29 member read (or force a token refresh) before destructive org actions.
9. **Retry.** A retry after success is 404 `member_not_found`; treat a 404 after a timeout as done.
10. **Limits.** Per IP 30/hour and per user 10/10 min (`ORG_MEMBER_REMOVE_*`).

Rejected: an app-credential variant (the app would name the actor); a `DELETE` verb (CORS); a token
version or deny list (breaks offline JWKS verification); 200 with a summary body.

## Consequences
- Staff revoking the last owner stays unguarded (a staff action).
- Co-owner takeover is possible by design; recovery goes through staff.
- Removals are serialized per app by `lock_app`.
- A removal leaves no row, so the USR-29 `updated_since` read cannot see it; apps run a full read.
- Follow-ups: self-service "leave org", a cookie-side member list, a guard on staff revoke of the
  last owner, a push or webhook on removal.
