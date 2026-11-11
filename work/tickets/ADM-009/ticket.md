# ADM-009: Login, access/refresh tokens and JWKS

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Users log in with email + password and get tokens that every mmlabs app can verify locally through JWKS.

## Spec reference
§5.8 mmlabs Identity (Login, Verification by apps, Abuse protection)

## Acceptance criteria
- [ ] `POST /api/v1/auth/login` → access token (JWT, asymmetric signature, ~15 min) + refresh token (opaque, stored hashed, ~30 days); disabled users cannot log in
- [ ] `POST /api/v1/auth/refresh` rotates the refresh token; reusing an old one revokes that session (401). `POST /api/v1/auth/logout` revokes it
- [ ] `GET /.well-known/jwks.json` publishes the public key(s) with `kid`; private key comes from env (R5, R11); key rotation documented
- [ ] Claims `iss`, `aud`, `sub`, `email`, `email_verified`, `iat`, `exp`; `GET /api/v1/auth/me` returns the current user from the access token
- [ ] Login rate limit + temporary lockout; same 401 body for unknown email and wrong password
- [ ] `docs/auth-integration.md`: how a mmlabs app verifies tokens (JWKS URL, issuer, audience, example)
- [ ] Tests: valid login, wrong password, unknown email (same response), disabled user, expired/tampered token, refresh rotation and reuse detection, logout, lockout

## Notes / answers from owner
- Depends on ADM-008.
- 2026-11-11: self-managed email + password (no external IdP).
