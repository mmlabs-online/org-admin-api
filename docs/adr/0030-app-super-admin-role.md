# ADR-0030: Per-app super-admin role for org apps

Status: Accepted for §1-§6 (owner, 2026-11-26: all plan defaults of USR-38); §7 accepted (owner, 2026-11-26: all plan defaults of USR-39); §8 accepted in
principle, details fixed by the USR-40 plan
Date: 2026-11-26

Decision-log row D42 (`docs/identity-architecture.md`). Spec §5.16, OQ80-OQ83, OQ90.
Amends ADR-0008 (privileged-role rules G2/G4/G5), ADR-0013 and ADR-0015 (org-token rule, §7).

## Context
simap (SIM-043) needs mmlabs staff to reach every org of an `org_mode = required` app without
being members. The owner decided: the power is a **per-app role of the target app**
(`simap:super-admin`), generic for org apps, and **never** `admin:super-admin` in an app token.
USR-38 ships the designation and the grant rules; USR-39 (token minting) and USR-40 (org picker)
build on them.

## Decision
1. **Designation by data.** Nullable `apps.super_admin_role_id`, modelled on
   `org_owner_role_id`: composite FK to a role of the same app, CHECKs: only
   `org_mode = 'required'`, never the `admin` app, distinct from the default and org-owner roles.
   Set or cleared by `PATCH /api/v1/apps/{key}` with `admin:*` (a no-op needs nothing). The role
   is not `is_system` (staff must be able to edit its permissions); system roles (`<key>:member`)
   cannot be designated. Preconditions at designation: the role has no assignments (any scope,
   expired included, 409 `role_has_assignments`), no `scope = org` permission and no `<app>:*`
   (422). Deleting the designated role and switching `org_mode` to `none` while designated are
   refused (409 `super_admin_role_designated`).
2. **Privileged by designation.** The designated role is privileged because of the designation,
   not its permission content: granting, renewing and revoking it needs `admin:*` (ADR-0008 G4),
   holders count for the G5 user-status guard, and a role PUT on it needs `admin:*` (G2). One
   predicate in three places that change together: `role_is_privileged` (read model),
   `AssignmentService._role_is_privileged` (used by `_require_privileged_grant` and
   `_forbid_self`) and `RbacRepository.user_has_privileged_assignment`;
   the Python test is `is_designated_super_admin_role`.
3. **Permission content.** The role takes `app`-scope permissions only: `scope = org` permissions
   and `<app>:*` are refused at designation, on the role PUT and on a permission scope flip.
   Reason: `roles` in a token is the union of app-scope and org roles (D10), so org permissions on
   an app-scope role reach every token of the app (ADR-0015 residual risk). Consumers grant org
   powers from the opt-in claim of USR-39. **Deviation from the spec §5.16 proposal** ("holds all
   of the app's permissions, org-scope ones included"), decided by the owner at the USR-38 gate.
4. **Grant rules, every path.** App scope only (`org_id` -> 422 `org_id_not_allowed`, before the
   org lookup); `admin:*` required (403 `admin_wildcard_required`); self-grant and self-revoke
   forbidden (403 `self_assignment_forbidden`; ADR-0008 self-actions rule, USR-25); the recipient must be an active, verified account
   holding an unexpired app-scope `admin:super-admin` (422 `recipient_not_staff_super_admin`,
   re-checked on renewal of an expired row); expiry optional. Enforced in `_grant_locked`, the
   single point of all grant paths (system actors fail `require_super`). Invitations refuse the
   role at create (403 `invite_role_forbidden`, no email) and at accept (invalid token). The
   manifest cannot declare, change or re-permission it (409 `role_not_manifest_managed`) and
   cannot send the designation (`extra="forbid"`). The bulk route (ADR-0029) refuses it at
   request level with 422 `super_admin_role`.
5. **Lifecycle.** No cascade revoke when the holder later loses `admin:super-admin`, is
   disabled or the designation is cleared. USR-39 (mint) and USR-40 (picker) **must** re-check
   live with `AuthzService.app_super_admin_access(user_id, app)`: designation set, app active and
   `org_mode = required`, an unexpired app-scope assignment, and a live staff super-admin.
   Clearing the designation is the kill switch (blocks every mint at once); staff should revoke
   the former holders afterwards. Holders count as ordinary members in `member_count`.
6. **Lock rules (ADR-0003 section 10).** The grant path holds the target app's lock and reads the
   `admin:super-admin` assignment with a plain read; it never calls `lock_app("admin")`: two
   `apps` row locks in one transaction could deadlock with an `admin:super-admin` revoke. The race
   with a concurrent revoke is closed by the mint-time re-check (§5).
7. **Org-token exception (amends ADR-0013 / ADR-0015; accepted at the USR-39 gate,
   2026-11-26).** `POST /api/v1/auth/token` with `org_id` when the account has no role in that
   org: the member path wins (normal token, normal TTL, no audit). Otherwise the mint succeeds
   only if `AuthzService.app_super_admin_access` holds (§5) and the org is an org of the app
   (OQ89 (b), `org_of_app_clause` in `rbac/repository.py`, shared with USR-40: `active` org with
   an unexpired org-scope assignment of any role of the app OR an entitlement of the app with
   `enabled = true`). Every other case, a suspended or unknown org included, is the same
   `org_access_denied` (byte-identical, no existence oracle; the org is looked up last).
   - Claims: the usual ones, `roles` = the holder's app-scope roles, `org`, `org_roles: []`,
     `ent` as for any org token, and `org_access: "super_admin"` (absent on every other token).
     Never `admin:super-admin` or any `admin:` key, never `sid`.
   - TTL: `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS` (60..43200; unset = `min(3600,
     ACCESS_TOKEN_TTL_SECONDS)`; startup fails above `ACCESS_TOKEN_TTL_SECONDS`).
   - Audit: one `token.super_admin_org_issued` row per mint in the request transaction (actor =
     the account's user, target = org, `app_key`, `org_id`, `changes` `{jti, account_session_id,
     role_id, expires_at}`; no email). A failed audit write fails the request, so no token leaves
     without its row. Refused and normal mints write no row.
   - Rate limit: per account, `SUPER_ADMIN_ORG_TOKEN_USER_RATE_LIMIT` per
     `..._WINDOW_SECONDS` (30 per 3600 s), counted only for mints that passed both checks; 429
     `rate_limited`. In-process (ADR-0002).
   - No recent re-authentication or MFA in v1 (`account_sessions` has no latest-sign-in time):
     follow-up together with staff MFA (OQ3). Residual risk: XSS on the app origin or a stolen
     `mm_device` cookie of a holder (up to 365 days); recommend a separate staff account per
     person. These tokens survive logout until `exp` (no `sid`, no deny-list).
   - Consumer rule (`docs/consumers.md`): grant super-admin powers in `org` only when the token
     is verified, `org` is present, `org_access == "super_admin"` and `roles` contains the app's
     designated role key. This is the single exception to "authorize org data from `org_roles`
     only". The holder creates orgs and accepts invitations under the normal rules.
8. **Picker (accepted 2026-11-26, USR-40; plan defaults).** `GET /api/v1/auth/orgs` decides the holder
   branch with `AuthzService.app_super_admin_access`, live on every call (including
   `admin:super-admin`), never cached. A holder lists every org of the app (`org_of_app_clause`,
   the same clause as the mint, §7) next to the orgs where the account is a member. Every item
   carries `access: "member" | "super_admin"` (always present, also for non-holders); member wins;
   `super_admin` items have `roles: []`. Suspended orgs appear only as `member` items. No query
   parameter. Not audited (read only); one info log line per holder list (no email, no org names).

## Rejected options
- `admin:super-admin` in app tokens: breaks the audience separation (ADR-0001).
- A reserved key `<app>:super-admin` created for every org app: magic names, opt-out impossible.
- Cascade revoke from the `admin` lock path: reverses the lock order (§6).

## Risks
End-user channel blast radius (OQ87), stale holders until the mint re-check, squatting of the
role key by a manifest before designation (mitigated by the designation preconditions; create the
role with the staff API).
