# USR-04: Email verification via Gmail SMTP (sender, dispatcher, verify, resend)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/21 |

## Goal
Users verify their email and reset a forgotten password through one-time, expiring links sent by Gmail SMTP, behind a swappable `EmailSender` interface.

## Scope
`EmailSender` interface + Gmail SMTP implementation (`smtp.gmail.com:587`, STARTTLS, App Password from env) + test fake; async sending with retry; `email_tokens`, resend limits; endpoints verify / resend / forgot / reset; signup hook from USR-01 sends the verification email.

## Spec reference
§5.8 (Email flows); identity-architecture.md §4, §5 S6, §7; decisions D2, D3

## Acceptance criteria
- [ ] `EmailSender` interface; Gmail SMTP implementation configured only from env (`.env.example` lists keys without values); a fake for tests
- [ ] Sending is async (not in the request path) with retry and backoff; failures logged without the token or full address
- [ ] Verification and reset tokens: random, single-use, expiring, stored hashed (R11)
- [ ] Resend limit per email: 1/min and 5/day for verification and for reset → 429 beyond
- [ ] `POST /api/v1/auth/password/forgot` always 202 (no enumeration), Turnstile required; `POST /api/v1/auth/password/reset` sets the new password (policy from USR-01) and revokes **all** account sessions of that user
- [ ] Verifying sets `email_verified_at`; afterwards `/api/v1/auth/token` and join work (USR-03/03b)
- [ ] Runbook note: Gmail daily sending limits and what to do when hit; how to switch provider
- [ ] Tests with the fake sender: verify flow, expired/used token, resend limits, reset flow + sessions revoked, retry on transient SMTP error

## Dependencies
USR-01, USR-03, USR-03c (token for the AC "afterwards /api/v1/auth/token works").

## Notes / answers from owner
- 2026-11-11: renamed from ADM-010; provider decided (D3, was Q8). Priority P1: a verified email is required to join any app or get any token (D2), and there is no interim path (D16).
- 2026-11-12 (owner): use the same Gmail sender account the owner already set up for the mmlabs site repo. Its address and App Password go only into this service's deploy env (never into this repo); the owner provides them at deploy time.
- 2026-11-12: planner split this ticket. Forgot/reset (and its AC: forgot 202 + Turnstile, reset revoking all sessions, reset tests) moved to **USR-04b**. Endpoint paths follow identity-architecture §4 (`/auth/verify-email`, `/auth/verify-email/resend`). See plan.md (A1–A9) for owner-confirm assumptions.
