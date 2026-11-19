# USR-12: Allow resend-verification for the admin app

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P2 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/42 |

## Goal
Decide, and record in ADR-0008 / the decision log, whether `POST /api/v1/auth/verify-email/resend` may accept `X-MM-App: admin`, so a signed-in staff member with an unverified email can get a new verification link from the admin UI. If allowed, implement it for resend only; signup and forgot-password stay refused for `admin`.

## Spec reference
§5.8 mmlabs Identity, "Email flows" (verification links; resend at most 1/min and 5/day per email, D3); ADR-0008 amendment "No admin context on mail-link routes" (ADM-002d, 2026-11-15); CLAUDE.md R3 (cookie class), R11 (verified email required for any app token).
This ticket revisits an accepted ADR decision: the planner must state the threat-model reason ADM-002d gave for refusing resend and why it does or does not still hold.

## Acceptance criteria
- [ ] The decision (allow or keep refusing) and its rationale are recorded as a dated amendment in ADR-0008 and in the decision log (`docs/identity-architecture.md`).
- [ ] If allowed: `POST /api/v1/auth/verify-email/resend` with `X-MM-App: admin` from a registered admin origin (`https://admin-portal.mmlabs.online`) sends the link to that validated `Origin`; it still requires the device cookie (cookie class) and the existing 1/min and 5/day limits apply unchanged.
- [ ] Signup and password forgot with `X-MM-App: admin` still return 403 `origin_not_allowed` before Turnstile, any write or any email (existing ADM-002d tests stay green).
- [ ] If allowed: tests cover admin resend success, an unregistered origin with `X-MM-App: admin` → 403, the rate limits for the admin app, and that an already verified account behaves as for other apps.
- [ ] `docs/consumers.md` (admin UI section) states what the admin UI can do for an unverified staff email.
- [ ] If refused: no code change; admin-pan is told so PAN-010b can be closed.

## Dependencies
Depends on ADM-002d (mail-link refusal), USR-04 (email verification).

## Notes / answers from owner
- 2026-11-16 (request from admin-pan, PAN-010b): staff with an unverified email get 403 `email_unverified` from `/auth/token` and the admin UI can only tell them to verify from another mmlabs app. The admin-pan owner decided to wait for the API to allow **resend only** for `admin`; signup and forgot-password stay refused. Final decision is org-admin-api's.
