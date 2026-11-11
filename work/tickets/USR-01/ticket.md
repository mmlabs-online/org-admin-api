# USR-01: Users, passwords (Argon2id) and sign-up with Turnstile

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Store mmlabs users (one account for every app) with Argon2id passwords, and let anyone sign up, protected by Cloudflare Turnstile and a per-IP rate limit.

## Scope
`users` table and the Identity module skeleton; password hashing/policy; `POST /api/v1/auth/signup`; Turnstile verification behind an interface; per-IP limiter; staff user endpoints. Out of scope: sessions/login (USR-03), sending the verification email (USR-04 — signup only records the user as unverified and calls an `on_signup` hook).

## Spec reference
§5.8 (Users, Sign-up, Passwords); identity-architecture.md §3, §4, §5 S3; decisions D1, D2, D11

## Acceptance criteria
- [ ] `users` table + migration as in identity-architecture.md §3 (email citext unique, `email_verified_at` NULL, status `active|disabled`); downgrade works
- [ ] Argon2id hashing (parameters in config); minimum length and common-password rejection; hash never returned or logged (R11)
- [ ] `POST /api/v1/auth/signup {email, password, turnstile_token}` → 201 (user unverified) · 409 duplicate email (any case) · 422 weak password / bad email · 400/403 invalid Turnstile · 429 over the per-IP limit; responses never reveal more than needed
- [ ] Turnstile verification behind an interface with a test fake; secret key from env (`.env.example` lists the key, no value)
- [ ] Per-IP rate limiter (shared component, reused by login/forgot later) with configurable limits
- [ ] Staff endpoints: list (paginated, filter email/status), get, disable/enable a user — note the `admin:users:*` permission each needs (R3)
- [ ] Tests for every bullet above, including case-insensitive duplicate and Argon2id verification

## Dependencies
None (first identity ticket).

## Notes / answers from owner
- 2026-11-11: renamed from ADM-008. Open sign-up decided (D2, was Q7).
- 2026-11-11 (D13): endpoints under `/api/v1/auth/*`.
