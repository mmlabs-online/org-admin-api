# USR-08: Client libraries @mmlabs/auth-client (TS) and mmlabs-auth (Python) — separate repo

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
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
- [ ] (admin-pan, 2026-11-16) React wrapper (provider + hooks) usable by `admin-pan`
- [ ] `getToken('admin')` (any audience): in-memory cache, refresh before expiry, single-flight concurrent calls; never persisted to storage
- [ ] `listAccounts()` and account choice persisted per origin as `mm_account_id`; logout one / all
- [ ] Typed errors: `not_member`, `email_unverified`, `session_invalid`, `rate_limited` (with `retryAfter` from `Retry-After`), plus a generic fallback
- [ ] `credentials:"include"` and `X-MM-App` only on `/api/v1/auth/*`; Bearer-only calls never send cookies
- [ ] Resend verification (`POST /api/v1/auth/verify-email/resend`) and fetching a fresh token after the email is verified

## Dependencies
USR-03b (join API). Owner must create the new repo and add it to the session.

## Notes / answers from owner
- 2026-11-13 (owner): split from USR-03b; libraries go in a separate repo for the `@mmlabs` libraries.
- Carried from USR-03b: USR-03c security review S1/S2 (flushable JWKS cache, `cooldown_duration=60`, emergency-rotation cache flush).
- 2026-11-16 (orchestrator): **BLOCKED**, because the library repo does not exist yet. No `mmlabs-online/mmlabs-auth` (or similar) appears in the account's repositories. The owner needs to create it and add it to the session. Priority raised to P1: it blocks real login in `admin-pan` (PAN-010).
- 2026-11-16 (owner, optional): consider having the client also cover forgot / reset / verify-email later. `admin-pan` calls those three public endpoints directly with `credentials:'omit'` today.
- 2026-11-16 (owner): repo created: `mmlabs-online/mmlabs-auth` (private). Initial commit on `main` (README, .gitignore); work goes on branch `claude/blissful-wozniak-fx4lm9` with a PR into `main` (owner merges). **Scope: both libraries in this ticket** (`ts/` `@mmlabs/auth-client` + React, and `python/` `mmlabs-auth`).
