# USR-03: Login, refresh and per-app access tokens, JWKS

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, auth, security, db, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Users log in once with email + password; each mmlabs app gets its own access token (audience = app, roles of that app only) and verifies it locally through JWKS.

## Spec reference
§5.8 mmlabs Identity (Login, Per-app access tokens, Verification by apps, Abuse protection); ADR-0001 §5

## Acceptance criteria
- [ ] `POST /api/v1/auth/login` → refresh token (opaque, stored hashed, ~30 days) and, when `audience` is given, an access token for that app; disabled users cannot log in
- [ ] `POST /api/v1/auth/token {"audience": "<app>"}` with a valid refresh token → access token (JWT, asymmetric signature, ~15 min) with `aud=<app>` and only that app's `roles` (+ `org` when scoped); 403 if the user has no non-expired role in that app or the app is disabled
- [ ] `POST /api/v1/auth/refresh` rotates the refresh token; reusing an old one revokes that session (401). `POST /api/v1/auth/logout` revokes it
- [ ] `GET /.well-known/jwks.json` publishes the public key(s) with `kid`; private key comes from env (R5, R11); key rotation documented
- [ ] Claims `iss`, `aud`, `sub`, `email`, `email_verified`, `roles`, `org`, `iat`, `exp`; `GET /api/v1/auth/me` returns the current user from the access token
- [ ] Login rate limit + temporary lockout; same 401 body for unknown email and wrong password
- [ ] `docs/auth-integration.md`: how a mmlabs app verifies tokens (JWKS URL, issuer, audience, example)
- [ ] Tests: valid login, wrong password, unknown email (same response), disabled user, expired/tampered token, wrong audience, token for an app without role → 403, refresh rotation and reuse detection, logout, lockout

## Notes / answers from owner
- 2026-11-11: renamed from ADM-009; per-app tokens added (ADR-0001).
- Depends on USR-01 and USR-02.
- 2026-11-11: self-managed email + password (no external IdP).
