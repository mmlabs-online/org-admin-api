# USR-03c: Signing keys, per-app token endpoint, JWKS and key rotation runbook

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P1 |
| Areas | architecture, auth, security, api |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | — |

## Goal
an app gets its own short-lived JWT for the account it chose on this device, verifiable locally through `/.well-known/jwks.json` (D7, §5.8 "Per-app access tokens", "Verification by apps").

## Spec reference
§5.8; identity-architecture.md §4–§5; D7, D12, D13, D19; split from USR-03 (`work/tickets/USR-03/plan.md` → "Split proposal").

## Acceptance criteria
- [ ] `POST /api/v1/auth/token {audience, account_id, org_id?}` (cookie class: `AppContextDep` +
  `DeviceDep`) → 200 `{access_token, token_type:"Bearer", expires_in, audience}`, `Cache-Control:
  no-store`; JWT ~15 min, asymmetric (algorithm decided in its plan; consults recommend EdDSA/Ed25519
  with `PyJWT[crypto]`, pinned algorithms), header `kid`, `typ: at+jwt`; claims `iss`, `aud`, `sub`,
  `email`, `email_verified`, `roles` (`AuthzService.effective_roles`), `iat`, `exp`, `jti`.
- [ ] `audience` must equal `X-MM-App` (else 403 `origin_not_allowed` or a dedicated code), Origin bound
  to that app (USR-03 `AppContextService`).
- [ ] Account session must belong to this device, be active and, when the cookie was the previous value,
  be created before `rotated_at`; user `disabled` → 401 `session_invalid`.
- [ ] Errors in the R6 envelope: app unknown/disabled → 403 `app_disabled`; unverified → 403
  `email_unverified`; non-member → 403 `not_member` with `details:{"join_available": join_policy !=
  "closed"}`; `org_id` not null → 422 until USR-06.
- [ ] Signing keys from env (`SecretStr`, PEM), `kid` (e.g. RFC 7638 thumbprint), extra public keys for
  rotation; staging/production refuse to start without a valid key.
- [ ] `GET /.well-known/jwks.json` (public, root mount, per-IP limit): public keys only (test: no `d`),
  `ETag` / 304, `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *` without
  credentials.
- [ ] Key rotation runbook in `docs/deploy.md`; ADR for algorithm and key format.
- [ ] Tests: two accounts on one device, switching does not affect the other; wrong audience / expired /
  tampered token rejected by a reference verifier; `kid` matches JWKS; every error code above; 401/403
  for missing cookie/headers.

## Dependencies
USR-03, USR-03f (commit before response, cookie-tossing defence), USR-02

## Notes / answers from owner
- 2026-11-12: created by the orchestrator from the USR-03 planner's split.
- 2026-11-12 (USR-03 security review N1): `/token` must apply `accounts_created_before` when the previous cookie value is used.
