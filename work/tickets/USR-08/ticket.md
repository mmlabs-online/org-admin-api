# USR-08: Client libraries @mmlabs/auth-client (TS) and mmlabs-auth (Python) — separate repo

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | architecture, auth, security |
| Attempts | 0 |
| Created | 2026-11-13 |
| PR | — |

## Goal
Shared client libraries so each mmlabs app does not re-implement login, account choice, token caching and token verification.

## Scope
- `@mmlabs/auth-client` (TS core + React wrapper; Angular only when an Angular app needs it): signup, login, listAccounts, getToken(audience) with in-memory cache and refresh before expiry, join, logout(one|all), per-origin `mm_account_id`, typed errors; `credentials:"include"` and `X-MM-App`.
- `mmlabs-auth` (Python): JWKS fetch/cache by `kid` (flushable, refetch on unknown `kid` at most once a minute), ES256 verification (`iss`, `aud`, `exp`, `typ`), role-catalog cache with ETag, `require_permission()` FastAPI dependency.
- Integration guide; publishing (private npm/PyPI or git install).

## Acceptance criteria
- [ ] Libraries live in a **separate repo** (owner decision 2026-11-13), e.g. `mmlabs-online/mmlabs-auth` with `ts/` and `python/`; CI and versioning there
- [ ] TS unit tests: account choice per origin, token refresh, error mapping
- [ ] Python tests: valid / expired / wrong-aud / unknown-kid token, ETag cache, JWKS cooldown
- [ ] Docs in org-admin-api `docs/consumers.md` link to the libraries

## Dependencies
USR-03b (join API). Owner must create the new repo and add it to the session.

## Notes / answers from owner
- 2026-11-13 (owner): split from USR-03b; libraries go in a separate repo for the `@mmlabs` libraries.
- Carried from USR-03b: USR-03c security review S1/S2 (flushable JWKS cache, `cooldown_duration=60`, emergency-rotation cache flush).
