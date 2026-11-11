# USR-04: Email verification and password reset

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Users can verify their email and reset a forgotten password through one-time, expiring links.

## Spec reference
§5.8 mmlabs Identity (Email flows); §8 Q8 (email provider)

## Acceptance criteria
- [ ] Verification and reset tokens: random, single-use, expiring, stored hashed (R11)
- [ ] Request reset always answers 202 (no account enumeration); confirming sets a new password and revokes all refresh tokens of that user
- [ ] Email sending behind an interface with the provider from §8 Q8; provider credentials from env
- [ ] Tests with a fake email sender: verify flow, expired/used token, reset flow, sessions revoked after reset

## Notes / answers from owner
- 2026-11-11: renamed from ADM-010.
- Depends on USR-01 and USR-03.
- Open: §8 Q8 email provider and sender address. If unanswered, the planner should BLOCK on it.
