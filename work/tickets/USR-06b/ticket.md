# USR-06b: Org invitations (create, accept by verified email)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Members of an organization can invite people into it for an app with `org_mode = required` (calechip first), and the invited person accepts with their verified email and gets the invited role in that org. Split out of USR-06 (owner, 2026-11-17).

## Spec reference
§5.8 Organizations; §5.3 Membership; identity-architecture.md §4 (invitation endpoints), D10; USR-06 plan.md `## Split proposal` and the USR-06 consult files.

## Acceptance criteria
- [ ] (USR-06 AC4) Invitations: create (by a member holding `<app>:org:invite` in that org, checked org-exact), accept by the invited verified email; expiring, single-use, stored hashed.
- [ ] (USR-06 AC6, invitation part) `invitation created` and `invitation accepted` + role grant are written to the audit log in the same transaction; no email or token in audit `changes`.
- [ ] (USR-06 AC7, invitation part) Tests: invite/accept happy path; accept by another verified account or an unverified one is refused; replayed, expired or revoked tokens get one uniform error; concurrent accepts → one succeeds; an inviter cannot grant a role beyond their own org permissions, an `is_admin` role, another app's role or an admin-app role; invite responses do not reveal whether the email has an account.

## Dependencies
Depends on USR-06 (slice 06a: org foundation, owner role, org-scoped tokens).

## Notes / answers from owner
- Open questions from the USR-06 plan, with recommended defaults (the owner said "defaults" on 2026-11-17; confirm at planning):
  - B1. Accepting also joins the app per `join_policy` (a `closed` app refuses and the invitation is not consumed).
  - B2. Anyone holding `<app>:org:invite` in that org may invite; invitable roles: same app, no `is_admin` permission, subset of the inviter's org permissions.
  - B3. Accept takes the token in the request body (`POST /api/v1/auth/invitations/accept {account_id, token}`), not the path.
  - B4. Link = registered origin of the inviting app + fixed path with `#token=`; TTL 7 days; caps per inviter, per org and per target email (counted in the Gmail daily cap); store only the email HMAC.
