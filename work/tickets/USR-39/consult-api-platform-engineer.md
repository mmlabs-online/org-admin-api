# Consult — api-platform-engineer — USR-39 (2026-11-26)

**Findings**
- Request/response shape unchanged: `TokenRequest {audience, account_id, org_id}`, `TokenResponse {access_token, token_type, expires_in, audience}` (`app/modules/tokens/schemas.py:9-21`). `expires_in` from `IssuedToken` (`service.py:172`), wired in `auth/router.py:197,205`; the router passes `settings.access_token_ttl_seconds` to `TokenService(ttl_seconds=…)`, so `self.ttl` is one value for all tokens.
- Deny point `service.py:132-134`: empty `org_role_keys` → `OrgAccessDeniedError` (`org_access_denied`, `tokens/errors.py:30`), one answer for unknown, suspended and foreign orgs. Claims at `service.py:136-140`: `org`, `org_roles` (org-exact), `ent` (skipped for `admin`); `roles` is the union from `effective_roles(…, org_id)`.
- Router OpenAPI `responses` (`router.py:179`) list only generic 401/403/422/429.
- `docs/consumers.md:622-627`: "authorize org data only from `org_roles`" and "reject if `org` present and `org_roles` absent"; 654-656 repeats it as the `mmlabs-auth` follow-up. Claim list 617-620 and the token-lag note 331-332 ("12 h") need the new lifetime.
- Spec §5.16 (418-422) and OQ84/85/88 (562-566) match the ticket. OQ84 also asks whether a holder may create orgs or accept invitations through this access (proposal: no change).

**Recommendations**
1. Additive under R9: one new optional claim, no new endpoint/status/code/response field. `expires_in` = seconds until this token's `exp`, computed from the TTL actually used.
2. `org_access_denied` byte-for-byte for every refusal (non-holder, unknown, suspended, not-of-app, no designation, holder without active `admin:super-admin`), same message, `details={}`. Holder checks only after `org_roles` comes back empty. Test identical bodies.
3. Pass `super_admin_ttl_seconds` into `TokenService` from `Settings`; validator 60 ≤ value ≤ `access_token_ttl_seconds`. Key in `.env.example` without value.
4. Claim `org_access` with single value `"super_admin"`, absent otherwise (never `null`). Consumers treat any other/unknown value as no access.
5. `docs/consumers.md`: add `org_access` to the claim list; reword line 626 (key always present, possibly `[]`; empty list grants nothing); single exception — only when `aud` = your app, `org` present and `org_access == "super_admin"`, grant the powers mapped from `<app>:super-admin` in `roles`, scoped to `org`, nothing else; verify like other claims; `ent` still per org; lag "up to 1 h for super-admin tokens"; logout does not end these tokens (no `sid`).
6. Audit `token.super_admin_org_issued` {app, org_id, jti}, no email, in the same session before returning (failed audit blocks the token).
7. Claim snapshot test (key set, `exp - iat`); route-auth-class registry unchanged (cookie class).
8. Per-account mint rate limit (OQ87) can reuse `rate_limit("token")` with a keyed bucket, counting only successful super-admin mints so a 429 never reveals holder status.

**Risks**
- `org_roles: []` read by naive consumers as "no access" (safe) or "reject token" (hard failure in `mmlabs-auth` as planned): coordinate the `mmlabs-auth` release before simap relies on it.
- `roles` includes `<app>:super-admin`; a consumer authorizing from `roles` would grant it without `org_access`.
- Timing/body differences between denial reasons = existence oracle.
- `expires_in` varies by org; clients caching 12 h must read `expires_in`.

**Open questions for Ming (defaults)**
- OQ84 claim `org_access: "super_admin"` — yes.
- OQ84 holder creates orgs / accepts invitations through this access — no, normal rules.
- OQ85 `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS`, 3600 — yes.
- OQ88 documented exception + cross-repo follow-ups (simap, `mmlabs-auth`) — confirmed.
- OQ87 per-account mint rate limit — ~30/hour/account, success only.
- OQ86 refused mints unaudited — yes.
- OQ89 "of the app" — spec (b): active orgs with an active role assignment or entitlement for the app.
