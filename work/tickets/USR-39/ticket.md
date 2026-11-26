# USR-39: Org token for a non-member org via the app's super-admin role (requested by simap SIM-043)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | M |
| Areas | auth, security, api |
| Gate | plan-approval |
| Depends on | USR-38 |
| Needs Ming | OQ84, OQ85, OQ86, OQ87, OQ88, OQ89 (spec §8; answer at the plan gate) |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | — |
| PR | — |

## Goal
A staff holder of an app's designated super-admin role can mint an access token for any org of that app without being a member, with an explicit opt-in claim, a short lifetime and an audit row per mint.

## Scope reference
- `docs/spec-v1.md` §5.16 Token, TTL, Revocation, Audit, Consumers (proposals; OQ84–OQ89). ADR-0030 / D42 (written in USR-38).
- Cross-repo requester: simap SIM-043.

## Scope
- `POST /api/v1/auth/token` with `org_id`: when the account has no role in that org, succeed only if the app has a designated super-admin role, the account holds it, the account still holds active `admin:super-admin` (mint-time check, OQ82), and the org is active and an org of the app (OQ89); otherwise the same `org_access_denied` as today (no existence oracle).
- Claims (proposed, OQ84): usual claims, `roles` = holder's app-scope roles (includes `<app>:super-admin`), `org` = X, `org_roles: []`, `ent` as for any org token, opt-in `org_access: "super_admin"`. Member path wins when the holder has org roles in X (normal token, no `org_access`).
- TTL (OQ85): new env key `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS` (default 3600, ≤ `ACCESS_TOKEN_TTL_SECONDS`) in `app/core/config.py` and `.env.example`; `expires_in` reflects it.
- Audit (OQ86): one row per super-admin mint (`token.super_admin_org_issued`: actor, app, org, `jti`; no email) in the mint transaction; normal mints stay log-only.
- Security extras (OQ87, e.g. per-account mint rate limit, recent re-auth) as decided at PLAN.
- `docs/consumers.md`: claim contract, opt-in rule, the single documented exception to "authorize org data from `org_roles` only" (OQ88).

## Out of scope
- Org picker (USR-40); token deny-list; staff MFA (OQ3); `mmlabs-auth` accessor and simap changes (their repos).

## Acceptance criteria
- [ ] A holder mints a token for a non-member org: claims as above, `org_roles: []`, `org_access: "super_admin"`, `exp - iat` = the new TTL.
- [ ] A holder who is a member of that org gets a normal member token (no `org_access`, normal TTL).
- [ ] An ordinary account still gets `org_access_denied`; unknown, suspended or not-of-the-app org → `org_access_denied` for a holder too.
- [ ] Removing the role, the designation, or the holder's `admin:super-admin` blocks the next mint at once.
- [ ] Exactly one audit row per super-admin mint, none for normal mints; the token never contains another app's role.
- [ ] New config key validated (≤ normal TTL) and listed in `.env.example`; `docs/consumers.md` updated.

## Technical notes
- Files: `app/modules/tokens/service.py` (`issue`, branch only where `org_roles` is empty), tokens router (TTL wiring), `app/core/config.py`, `.env.example`, rbac authz helper, audit, `docs/consumers.md`.
- Architect consult: cookie-class services already write audit rows with `EndUser(user.id)` and the request session commits on success — no new plumbing, no migration for the action string. Consumer tokens have no `sid`, so logging out does not end them before `exp`.

## Test plan
- API tests per criterion; config validation test; claim snapshot test.

## Notes / answers from Ming

## Handoff
