# ADR-0005: Access-token signing

Status: Accepted (USR-03c plan, 2026-11-12)
Date: 2026-11-12

## Context
D7 gives every mmlabs app its own short-lived JWT for the account the user picked on this device
(`POST /api/v1/auth/token`). Apps verify it locally against the public keys at
`/.well-known/jwks.json` (spec §5.8 "Verification by apps"). S7 asks for asymmetric keys from env,
a `kid` and a documented rotation. The USR-03c consults split on the algorithm: the software
architect recommended ES256, the identity and security consults EdDSA (Ed25519).

## Decision
1. **Algorithm: ES256** (ECDSA P-256, SHA-256).
   - Every verifier stack supports it: PyJWT[crypto] (ricotdin's backend), Node `jose`, and
     WebCrypto in every browser. Ed25519 arrived in WebCrypto only recently.
   - RFC 9864 deprecates the polymorphic `EdDSA` name in favour of `Ed25519`; libraries will go
     through a renaming period while every consumer pins the name. `ES256` does not change.
   - Security is equivalent for a 15-minute token. ECDSA nonce generation is done by
     `cryptography` (OpenSSL), not by our code.
   - Rejected: EdDSA (naming churn above), RS256 (large keys and tokens, no benefit).
   - Verifiers pin `algorithms=["ES256"]`; every JWKS key carries `alg: "ES256"`, `use: "sig"`.
2. **Key format.** `JWT_SIGNING_KEY` (secret) is the **base64 of a PEM P-256 private key on one
   line** (PKCS#8 or SEC1), because the deploy writes a single-line env file. Only this form is
   accepted. `JWT_PUBLIC_KEYS_EXTRA` (not secret) is a JSON list of base64 SPKI public PEMs,
   published in the JWKS during a rotation. Parse errors carry a fixed message, never the value.
   Staging and production refuse to start (API, `migrate`, CLI) without a valid key and an explicit
   `JWT_ISSUER` of the form `https://<host>`; local and test fall back to an ephemeral per-process
   key.
3. **`kid` = RFC 7638 thumbprint** of the public key (SHA-256 over `crv`, `kty`, `x`, `y`;
   base64url). It is computed, never configured, so it cannot drift from the key. Duplicate keys
   (an extra equal to the signer, or listed twice) are refused at startup.
4. **Header** is exactly `{"alg": "ES256", "kid": <thumbprint>, "typ": "at+jwt"}`. `typ` follows
   RFC 9068 naming so a verifier can tell access tokens from other JWTs, but the token is **not** a
   full RFC 9068 token: there is no `client_id` and no `scope`.
5. **Claims contract** (frozen within v1): `iss` (`JWT_ISSUER`; production
   `https://api.mmlabs.online`), `aud` (one string, the app key), `sub` (user id, never the
   account-session id), `email`, `email_verified` (always `true`, see 9), `roles` (sorted app-scope
   role keys of this app, `AuthzService.effective_roles`), `iat`, `exp`, `jti` (uuid4). `org` is
   added by USR-06. Adding a claim is not breaking; removing or renaming one is.
6. **Lifetime:** `ACCESS_TOKEN_TTL_SECONDS` = 900 (bounds 60–3600). No refresh token, no
   revocation list, no `jti` denylist: a disabled user, a removed role or a logout keeps a live
   token for at most the TTL (accepted under D7). A leaked key is fixed only by rotation
   (`docs/deploy.md`).
7. **JWKS:** `GET /.well-known/jwks.json`, public, per-IP limit, body built once at startup
   (`{"keys": [signer, *extras]}`, public members only), strong `ETag` / 304,
   `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *` without credentials (the
   credentialed CORS middleware skips `/.well-known/`). Verifiers refetch on an unknown `kid`, at
   most once a minute.
8. **`audience=admin` is refused** (403 `audience_not_supported`) until ADM-002: nothing verifies
   admin tokens yet, the admin TTL and staff 2FA are open, and origins are not yet exclusive per
   app.
9. **`/token` errors** (R6 envelope), checked in this order: 429 `rate_limited`; 403
   `app_header_required` / `origin_not_allowed` (unknown **or disabled** app, bad Origin — one code,
   so no oracle on which app keys exist; the ticket's `app_disabled` is therefore not used);
   401 `session_invalid` (cookie); 422 `validation_error`; 403 `audience_mismatch` (`audience` ≠
   `X-MM-App`); 403 `audience_not_supported`; 422 `org_not_supported` (until USR-06); 401
   `session_invalid` without clearing the cookie (unknown, foreign, revoked or too-new account
   session, or disabled user); 403 `email_unverified` (S5); 403 `not_member` with
   `details.join_available = join_policy != "closed"` (no non-expired app-scope role).

## Consequences
- New dependency `PyJWT[crypto]>=2.10.1` (adds `cryptography`; 2.10.0 has CVE-2024-53861).
- The deploy needs a new secret (`JWT_SIGNING_KEY`) and `JWT_ISSUER` before the release that
  contains USR-03c (`docs/deploy.md`).
- Consumers follow the checklist in `docs/consumers.md`.
- Until USR-04 ships no real account has a verified email, so nobody can get a token (D16).
