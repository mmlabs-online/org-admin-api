# ADM-008: Ecosystem users and password credentials

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Store mmlabs users with email + Argon2id-hashed passwords: the account model every mmlabs app will share.

## Spec reference
§5.8 mmlabs Identity (Users, Passwords, Admin); §8 Q7 (sign-up policy)

## Acceptance criteria
- [ ] `users` table + migration: UUID id, email (unique case-insensitive), password hash, status (`active`/`disabled`), `email_verified_at`, timestamps; downgrade works
- [ ] Passwords hashed with Argon2id (parameters in config); minimum length and common-password rejection; hash never returned or logged
- [ ] Account creation endpoint per the sign-up policy answered in §8 Q7 (open sign-up or staff-created/invite), returning 201 / 409 duplicate email / 422 weak password
- [ ] Staff endpoints: list (paginated, filter by email/status), get, disable/enable a user
- [ ] Tests: create, duplicate email in different case → 409, weak password → 422, hash is Argon2id and verifies, disabled user listed with status

## Notes / answers from owner
- 2026-11-11: users are ecosystem-wide (one account for all mmlabs apps), email + password managed by this service.
- Open: §8 Q7 sign-up policy. If unanswered, the planner should BLOCK on it.
- Staff endpoints are unauthenticated until ADM-002; note the permission each will need (R3).
