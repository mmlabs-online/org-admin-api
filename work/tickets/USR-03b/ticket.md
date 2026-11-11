# USR-03b: Accounts list, join flow, @mmlabs/auth-client and mmlabs-auth

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | architecture, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Apps can list the accounts signed in on the device, let a user join the app ("Continue as <account>"), and integrate with two small libraries: a framework-agnostic TS client for login pages and a Python package for app backends.

## Scope
`GET /auth/accounts`; `POST /auth/apps/{key}/join` with `join_policy` (open / approval → `join_requests` / closed); `@mmlabs/auth-client` (TS core + Angular and React wrappers); `mmlabs-auth` (Python: JWKS verification, role-catalog cache with ETag, `require_permission()` FastAPI dependency); integration guide.

## Spec reference
§5.3 Membership, §5.8; identity-architecture.md §2, §4, §6; decisions D4, D8, D9

## Acceptance criteria
- [ ] `GET /auth/accounts` (cookie) lists the device's active account sessions: account_id, email, email_verified, display name; never another device's
- [ ] `POST /auth/apps/{key}/join {account_id}` (cookie + `X-MM-App` + Origin check): verified email required (403 `email_unverified`); `open` → assigns the app's default role at app scope (`org_id NULL`), idempotent; `approval` → creates a pending `join_requests` row (202) + staff endpoint to approve/reject; `closed` → 403; audit event emitted (recorded via an interface until ADM-004 lands)
- [ ] `@mmlabs/auth-client`: signup, login, listAccounts, getToken(audience, {orgId}) with in-memory cache + refresh before expiry, join, logout(one|all), `mm_account_id` in localStorage, typed errors (`not_member`, `email_unverified`, backoff/Turnstile required); sends `credentials:"include"` and `X-MM-App`; posts credentials directly to the API
- [ ] Angular wrapper (service + route guard + HTTP interceptor) and React wrapper (provider + hooks), thin over the core
- [ ] `mmlabs-auth` (Python): JWKS fetch/cache keyed by `kid`, JWT verification (`iss`, `aud`, `exp`), role catalog cache with ETag, `require_permission("<app>:<res>:<action>")` FastAPI dependency
- [ ] `docs/auth-integration.md`: register app + origins, login page with the TS client, join UX ("Continue as <account>" = consent), backend verification with `mmlabs-auth`
- [ ] Tests: join per policy, idempotent join, unverified → 403; TS client unit tests (account choice per origin, token refresh, error mapping); Python package tests (valid/expired/wrong-aud token, ETag cache)

## Dependencies
USR-03 (and USR-02).

## Notes / answers from owner
- New ticket (2026-11-11). Where the libraries live (this repo `clients/` vs separate repos) and how they are published (npm/PyPI private?) — decide in the plan; may need the owner (BLOCK if it changes repo layout).
- This ticket is large; the planner should propose a split (e.g. API part / TS client / Python package) if over ~400 lines.
