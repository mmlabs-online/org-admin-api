# USR-29: App-facing read of an org's members for org-mode apps (requested by simap)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Size | M |
| Areas | api, auth, security, db |
| Gate | plan-approval |
| Depends on | ADM-034 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | claude@2026-11-21T03:50Z |
| PR | — |

## Goal
An `org_mode = required` app (first consumer: simap; calechip is the same shape) can list, from its backend, the members of one org for its own app, so Managers can assign seats and devices to people who have not opened the app yet and the app can show display names. simap keeps no person records of its own: every person is an mmlabs Identity account.

## Scope reference
- `docs/spec-v1.md` §5.3 (org-scoped roles), §5.8 Organizations, §5.1; ADR-0013 (org-scoped identity), ADR-0015 (`org_roles`), ADR-0016 (app credential, class 4), ADR-0021 (scoped app credentials, once PR #76 merges).
- ⚠️ No spec section covers an app-facing member read yet (USR-23 is the staff read; spec §5.7 lists "Org members view" as Later). The plan adds a short dated line to spec §5.8/§5.3 and needs Ming's confirmation at the plan gate.
- Today an app only learns about an account when that person opens the app; the only member list is the staff route `GET /api/v1/organizations/{org_id}/members` (USR-23, Bearer `aud=admin`).

## Scope
- Server-to-server read authenticated with an app credential (`Authorization: Bearer mmak_…`), e.g. `GET /api/v1/apps/{key}/orgs/{org_id}/members`. The credential's app must equal `{key}` (403 `app_credential_mismatch` otherwise). Built on the scoped app credentials of ADR-0021 / ADM-034 with a new read scope (name set at PLAN, e.g. `org-members`), so a key that can read members cannot push a manifest or write releases. Amend ADR-0016/ADR-0021 and the R3 class 4 text; add the route to `APP_CREDENTIAL` in `tests/test_route_auth_classes.py`.
- Response `Page[T]` (same pagination as other lists, stable sort), `Cache-Control: no-store`: `user_id` (= token `sub`), `email`, `email_verified`, `display_name`, `status` (`active` | `disabled`), `org_roles` (role keys of `{key}` held in that org, non-expired), `joined_at`. Only users holding at least one org-scoped role of `{key}` in that org; disabled users are listed with `status = disabled`.
- Optional filters: `updated_since` (incremental sync; what counts as an update is defined at PLAN), `status`.
- Optional extra (planner decides; may be a follow-up): the org's pending invitations for `{key}` (id, role, expires_at; never invitee email, token or hash — as USR-23).
- Errors: one 404 answer (`org_access_denied`) for an unknown org, an org with no relation to `{key}`, or a suspended org; 422 `org_not_supported` for `org_mode = none` apps; per-credential (and per-IP) rate limit; no CORS.
- Audit: one row per call (actor `SystemActor("app:{key}/{key_id}")`), ids and counts only, never the response body or emails. Never log emails (R11).
- Docs: the contract in `docs/consumers.md`, section "Organizations".

## Out of scope
- Writes from the app (adding/removing members, changing roles); a cookie-class browser variant.
- A `name` claim in access tokens (see Notes — proposed as a separate small ticket).

## Acceptance criteria
- [ ] With simap's credential (right scope), simap gets the members of an org where people hold simap org roles; paging and stable order tested.
- [ ] Another app's credential → 403 `app_credential_mismatch` (audited denied); a key of the wrong scope → 403 `app_credential_scope`; an admin JWT or a deployer key → 401.
- [ ] A user with no `{key}` org role in that org is not listed; a disabled user is listed with `status = disabled`; expired assignments do not count.
- [ ] Unknown, unrelated and suspended orgs all give the same 404 `org_access_denied`; an `org_mode = none` app gets 422 `org_not_supported`.
- [ ] Each call writes one audit row without emails or the body; no email appears in logs; `Cache-Control: no-store` asserted.
- [ ] `updated_since` and `status` filters tested (if included); the contract is documented in `docs/consumers.md` "Organizations".

## Technical notes
- Files: `app/modules/app_credentials/` (new scope), a new or existing org module router for the class-4 route, `tests/test_route_auth_classes.py`, `CLAUDE.md` R3, ADR amendment, `docs/consumers.md`, migration if the scope CHECK changes.
- Reuse the USR-23 member query (no N+1) filtered to `{key}`'s org-scoped roles.
- Risk: this is the second widening of class 4 after ADR-0021; a leaked member-read key exposes the emails of that app's org members (PII, §8 Q6). Scopes keep it from writing anything.
- Depends on ADM-034 for the scope column; if Ming wants this before ADM-034, the plan must add the scope column itself (then ADM-034 reuses it).

## Test plan
- API tests per criterion; route-class tests; migration round-trip if any.

## Notes / answers from Ming
- Plan approved (2026-11-21, batch 2026-11-21-1) — Ming: "duyệt hết; câu 1–25 theo mặc định" (all batch questions answered with the defaults).
- 2026-11-20 (orchestrator): requested by the simap project. Alternative to weigh: a `name` claim (display name) in per-app access tokens as a smaller first step for names only — does not cover "people who have not opened the app yet", so it complements rather than replaces this ticket. Proposed as a separate S ticket if Ming wants it.

## Handoff
