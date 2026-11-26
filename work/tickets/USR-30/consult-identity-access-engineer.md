# Consult — identity-access-engineer — USR-30 (2026-11-21)

**Findings**
- Grammar: `<app>:org:remove-member` is valid. Each segment matches `[a-z][a-z0-9-]{0,47}` (docs/adr/0003-rbac-keys-and-matching.md:22). The 4-segment key simap asked for is not valid.
- The org-exact check to reuse sits in `InvitationService.create`, not in `rules.py`. It runs `authz.org_role_keys` (empty → 403 `org_access_denied`), then `org_permissions` + `permits` (app/modules/invitations/service.py:165-170). The repo query is org-exact and unexpired, and only counts active orgs (app/modules/rbac/repository.py:447-495). The user guard (active session, user active, verified) is at service.py:103-115. `rules.is_subset` is at rules.py:14-16.
- **Conflict:** ADR-0014 §1 gives unknown, suspended and not-a-member orgs one **403 `org_access_denied`** (docs/adr/0014-org-invitations.md:12-14), and `/auth/token` does the same (app/modules/tokens/service.py:111-113). The ticket says "404 as USR-29".
- ADR-0014 §2 says "(no member removal in v1; staff only)" (adr/0014:16-17), and its Consequences list member management as a follow-up (adr/0014:60). This needs to be amended explicitly.
- Denied audit: `record_denied` writes nothing for non-staff actors (app/modules/audit/service.py:210-212). Invitations write no denied rows. So for cookie-class routes, today's "existing pattern" is **no denied audit row**. `record_failure` accepts any actor (service.py:156-187).
- Revocation lag is already accepted elsewhere: revoking sessions leaves other apps' tokens valid until `exp` (12 h + 30 s leeway) (app/modules/users/router.py:74-82). There is no token-version or deny mechanism. After removal, `/auth/token?org_id=` for that org gets 403 `org_access_denied` because `org_roles` is empty (tokens/service.py:111-116).
- `org_owner_role_id` requires `org_mode='required'` (app/modules/rbac/models.py:82). A default role cannot hold a `scope='org'` permission (rbac/service.py:996-1002), so the new permission cannot leak to every member through the default role.

**Recommendations**
1. **Permission:** apps declare it in the manifest with `scope='org'` and `is_admin=false`. Apps that don't declare it simply can't use the route: nobody can hold it, so callers get 403. No server-side registry is needed. If someone grants it through an app-scope role it does nothing, because the check reads org roles only. Holders of `<app>:*` pass via `permits`, which is fine because those roles are staff-granted.
2. **Order of checks:** `lock_app` → `org_mode != required` → 422 `org_not_supported` (match invitations, service.py:160-162; `optional` apps have no owner role, so no last-owner guard is possible) → caller via `_user` (401/403 `email_unverified`) → caller has no org roles → **403 `org_access_denied`** (keep ADR-0014/token consistency; record the ticket deviation at the gate) → lacks permission → 403 → caller == target user (compare `user_id`, not account) → 403 `self_removal_forbidden` → target has no unexpired `<app>` org role → 404. Do the self check before the target lookup so it can't be used as an oracle.
3. **Owner removing a co-owner:** reuse the subset rule from ADR-0014 §2. The caller may remove a target only if, for every role the target holds there, the role's permissions pass `is_subset(role_keys, caller_org_permissions)`. Also refuse when any of those roles holds an `is_admin` permission. Otherwise 403 `member_outranks_caller`. Result: an owner can remove a co-owner (equal rights), but a delegated non-owner who holds `remove-member` cannot remove an owner. This mirrors what you're allowed to invite.
4. **Last-owner rule and locking:** order is app lock (existing) → `SELECT … FROM organizations WHERE id=… FOR NO KEY UPDATE` → count unexpired `org_owner_role_id` assignments in that org, excluding the target. If the target holds the owner role and that count is 0, return 409 `last_org_owner`. Locking the org row serializes two owners removing each other without relying on row-set `FOR UPDATE` semantics. Fail closed if `org_owner_role_id` is NULL: 422 `org_not_supported`, or refuse removal of anyone.
5. **Delete:** remove all of the target's `<app>` assignments in that org with `org_id = org`, expired ones included. That way no stale row can be revived by a later expiry edit. Only unexpired ones count toward the 404 and the audit `removed_roles`. Audit in the same transaction, with no email.
6. **Revocation:** accept the 12 h lag; don't bump anything. Org roles are bearer claims, so a version bump would need a new claim and every consumer would have to check it online, which goes against the JWKS-offline model (spec §5.8). Document it in consumers.md: apps doing sensitive org writes should re-mint the token often or check server-side. A push or webhook can be a follow-up.
7. **Denied audit:** write `denied` rows through `record_failure(actor=EndUser)` only once the caller is a confirmed org member: no permission, self, outranks, last owner. Write nothing for 401, bad Origin or non-member, so outsiders can't flood the audit table. This deliberately extends the current pattern, so state it in the ADR.
8. **ADR form:** create a new **ADR-0026 / D35**, "Org member removal (cookie class)". Add a one-line "Amended by ADR-0026" to ADR-0014 §2 and Consequences. ADR-0014 is about invitations; removal has its own guards and audit policy.

**Risks**
- A removed member keeps working through the app until `exp`, which can be up to 12 h.
- Without the subset rule, any holder of `remove-member` could remove an owner, which would let a delegate take over the org.
- Staff paths that delete owner assignments don't take the org lock. That's acceptable because staff are exempt from the last-owner rule, but say so in the ADR.
- The target's app-scope roles stay, so the target can still get non-org tokens. That is intended.

**Open questions for Ming (defaults)**
- A. Non-member caller or unknown/suspended org: 403 `org_access_denied` or 404? **Default 403**, consistent with ADR-0014 and `/auth/token`.
- B. Subset/outrank rule (rec. 3)? **Default yes**: owners can remove co-owners, delegates can't remove owners.
- C. Response code? **Default 204.**
- D. Live tokens: accept the lag? **Default yes, documented.** Webhook/push as a follow-up.
- E. Denied rows for refusals to confirmed members? **Default yes** (rec. 7).
- F. Should `org_mode=optional` be supported? **Default no** (422), since optional apps have no owner role.
- G. A self-service "leave org" route? **Default: follow-up ticket**, not in USR-30.
