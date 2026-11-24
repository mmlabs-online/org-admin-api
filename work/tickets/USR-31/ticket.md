# USR-31: Pending org invitations in the app-facing member read (requested by simap)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | S |
| Areas | api, auth, security, db |
| Gate | plan-approval |
| Depends on | USR-29 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-24 |
| Claimed by | — |
| PR | — |

## Goal
An org-mode app backend (first consumer: simap) can see the org's pending invitations for its own app next to the member list, so Managers can see who is invited but not yet joined and plan seats for them.

## Scope reference
- USR-29 (app-facing member read, ADR-0025 / D34): pending invitations were listed there as an optional extra and deferred to a follow-up (plan Q11, approved 2026-11-21).
- Invitations: ADR-0014 (org invitations); USR-23 staff read of invitations (no invitee email, token or hash).
- ⚠️ No spec section covers an app-facing invitation read yet; the plan adds a dated line to spec §5.8 (next to the USR-29 line) for Ming's confirmation at the plan gate.

## Scope
- Server-to-server read with the existing `org-members:read` app credential (runtime key, ADR-0025); PLAN decides between a new route (e.g. `GET /api/v1/apps/{key}/orgs/{org_id}/invitations`, `Page[T]`) and an opt-in field on the USR-29 member read, and whether a separate scope is needed.
- Item fields: invitation id, role key(s) of `{key}`, `created_at`, `expires_at`. Never the invitee email, token or token hash (as USR-23). Only pending, unexpired invitations of `{key}` in that org.
- Same guards as USR-29: one 404 `org_access_denied` for unknown / unrelated / suspended orgs, 422 `org_not_supported` for `org_mode = none`, per-IP and per-key rate limits, `Cache-Control: no-store`, no CORS, audit row per call (ids and counts only).
- Docs: `docs/consumers.md` "Organizations" section; ADR-0025 amendment line; registry entry in `tests/test_route_auth_classes.py` if a route is added.

## Out of scope
- Creating, revoking or resending invitations from the app (cookie-class invitation routes already exist, ADR-0014).
- Showing invitee emails to apps.

## Acceptance criteria
- [ ] With simap's `org-members:read` key, simap gets the pending, unexpired invitations of simap in an org; accepted, revoked and expired invitations and other apps' invitations are not listed.
- [ ] No invitee email, token or hash appears in the response, logs or audit rows (tested).
- [ ] Another app's key → 403 `app_credential_mismatch`; wrong scope → 403 `app_credential_scope`; admin JWT or deployer key → 401.
- [ ] Unknown, unrelated and suspended orgs give the same 404 body; `org_mode = none` → 422.
- [ ] The contract is documented in `docs/consumers.md` (section "Organizations").

## Technical notes
- Files: `app/modules/organizations/app_router.py` / `app_service.py`, invitations repository query, `tests/test_route_auth_classes.py`, ADR-0025 amendment line, `docs/consumers.md`, spec §5.8 dated line.
- Reuse the USR-29 org-visibility query and runtime-key path (no `lock_app`).
- Risk: invitations reveal planned membership; keep fields minimal.

## Test plan
- API tests per criterion; route-class registry test; no-email log/audit test.

## Notes / answers from Ming
- 2026-11-24: created at Ming's request ("tạo USR-31, USR-32") as the USR-29 follow-up for pending invitations (simap request).

## Handoff
