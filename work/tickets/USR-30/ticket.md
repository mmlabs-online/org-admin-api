# USR-30: Org owners remove a member's org roles from the app (requested by simap)

| Field | Value |
|---|---|
| State | REWORK |
| Priority | P1 |
| Size | M |
| Areas | api, auth, security, db |
| Gate | plan-approval |
| Depends on | USR-29 |
| Needs Ming | — |
| Attempts | 1 |
| Created | 2026-11-20 |
| Claimed by | claude@2026-11-21T13:49Z |
| PR | — |

## Goal
An org member holding a new org-level permission (simap grants it to its org owner role `simap:admin`) can remove another person from the org for that app, by removing that person's org-scoped roles of the app in that org — without staff and without touching the account.

## Scope reference
- `docs/spec-v1.md` §5.3 (org-scoped roles, `permissions.scope = 'org'`), §5.8 Organizations; ADR-0013, ADR-0014 (invitations: cookie class, org-exact `<app>:org:invite` check), ADR-0015 (`org_roles`).
- ⚠️ Spec and ADR-0014 list "member management" as a later follow-up; the plan adds a dated spec line and an ADR-0014 amendment (or a new ADR), confirmed at the plan gate.
- Second simap request after USR-29 (the member read); kept separate because it is a write in a different auth class.

## Scope
- **Auth class: cookie (R3 class 2), like invitations** (orchestrator's choice, 2026-11-20): `POST /api/v1/auth/orgs/{org_id}/members/{user_id}/remove` with body `{account_id}`, `mm_device` cookie + `Origin` check + `X-MM-App: <app>`. The actor is the signed-in org owner, so the audit row names a real user; an app-credential variant would have to trust the app backend to name the actor, so it is not chosen (Rejected alternative in the ADR).
- **Permission:** a new org-level permission per app, `<app>:org:remove-member` (ADR-0003 allows exactly `<app>:<resource>:<action>`; simap's suggested `<app>:org:members:remove` has four segments). Checked org-exact in the service, as `<app>:org:invite` is; no `require_permission`. Apps declare it in their manifest (`scope = 'org'`); simap grants it to `simap:admin`.
- **Effect:** deletes the target's non-expired org-scoped role assignments of `<app>` in that org (all of them). Never deletes or disables the account, never touches app-scope roles, other apps or other orgs.
- **Guards:** caller is active, email-verified, holds the permission in that org; caller ≠ target (403 `self_removal_forbidden`); cannot remove the last holder of the app's org owner role (`apps.org_owner_role_id`) in that org (409, reason `last_org_owner`); target with no `<app>` role in that org → 404 (no existence leak); `org_mode = none` app → 422 `org_not_supported`; suspended or unrelated org → 404 as USR-29. Concurrency: lock the org's owner-role holders so two owners cannot remove each other at once and leave none.
- **Audit (R4):** one row in the same transaction (actor = caller, target = the user, org id, app key, removed role keys/count; no email). Denied attempts follow the existing pattern.
- **Revocation lag:** documented — already-issued app tokens keep the removed `org_roles` until `exp` (up to `ACCESS_TOKEN_TTL_SECONDS`, 12 h in production); new tokens no longer carry them. Docs in `docs/consumers.md` "Organizations" and the ADR.
- Per-IP and per-account rate limit; CORS for the app's registered origins with credentials (like other `/auth/*` routes).

## Out of scope
- Changing a member's roles from the app (admin portal only for now); deleting or disabling accounts; an app-credential variant.

## Acceptance criteria
- [ ] An owner holding `<app>:org:remove-member` removes a member: the member's `<app>` org roles in that org are gone, app-scope roles and other orgs/apps untouched; 204 (or 200, set at PLAN).
- [ ] Refused: no cookie (401); bad Origin / missing `X-MM-App` (403); caller lacks the permission in that org (403); self-removal (403); last org owner (409 `last_org_owner`); unknown/unrelated target or org (404); `org_mode = none` (422).
- [ ] Two owners removing each other concurrently leaves at least one owner (concurrency test).
- [ ] One audit row per removal, no email; denied attempts audited per the existing pattern.
- [ ] After removal, `/auth/token` for that org no longer includes the removed roles; the lag for live tokens is documented.
- [ ] Route registered in the cookie class in `tests/test_route_auth_classes.py`; R3 class 2 list in `CLAUDE.md` mentions member removal.

## Technical notes
- Files: `app/modules/organizations/auth_router.py` / `auth_service.py` (or `invitations` rules reuse), rbac repository, ADR, `CLAUDE.md` R3 wording, `docs/consumers.md`.
- Reuse the invitations org-exact permission check (`app/modules/invitations/rules.py`).

## Test plan
- API tests per criterion; concurrency test; token claim test after removal.

## Notes / answers from Ming
- Plan approved (2026-11-21, batch 2026-11-21-1) — Ming: "duyệt hết; câu 1–25 theo mặc định" (all batch questions answered with the defaults).
- 2026-11-20 (orchestrator): requested by simap as a second item next to USR-29. Choices made here (Ming may change them at the plan gate): cookie class rather than app credential; permission name `<app>:org:remove-member` (the requested four-segment key is invalid); depends on USR-29 only for docs ordering in consumers.md — it can run independently if Ming prefers.

## Handoff
