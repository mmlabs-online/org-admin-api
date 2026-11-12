Status: READY

# Plan — USR-03c: Signing keys, per-app token endpoint, JWKS and key rotation runbook

One ticket, no split (about 400 production lines, no migration; see `## Split proposal`). The plan
makes several decisions that outlive the ticket. They go into a new **ADR-0005**. Items the owner
should confirm are listed under `## Assumptions` (A1–A8). None of them blocks implementation.

## Spec summary

spec-v1.md §5.8:

> **Per-app access tokens:** an app asks for its own short-lived JWT for a chosen account (and org, if
> any), carrying only that app's roles (D7). Not a member → `not_member` with `join_available`; joining
> follows the app's `join_policy` (D8–D9).

> **Verification by apps:** public keys at `/.well-known/jwks.json`; apps verify signature, `iss`, `aud`
> and expiry locally. Claims: `sub`, `email`, `email_verified`, `aud`, `roles`, `org` (if scoped), `iat`,
> `exp`. Backends use `mmlabs-auth` (Python) (D4).

> Unverified users may log in but must verify their email before joining any app or getting an app token.

identity-architecture.md:

> D7 — `POST /auth/token {audience, account_id, org_id?}` → JWT (~15 min): iss, aud, sub, email,
> email_verified, roles, org?, iat, exp; asymmetric signing, keys from env, `kid`.

> §4 — `POST /api/v1/auth/token` `{audience, account_id, org_id?}` | cookie + `X-MM-App` | per-app JWT,
> or `403 {error:"not_member", join_available}` / `403 email_unverified`

> §4 — `GET /.well-known/jwks.json` | public | signing public keys (`kid`)

> S5 — no join and no app token for an unverified email (D2).
> S7 — signing keys from env, `kid`, documented rotation; apps must check `aud`.

> D13 — Only `/.well-known/jwks.json` stays at the root.
> D16 — Joining an app and getting an app token stay blocked until USR-04 ships.

CLAUDE.md R3 (class 2 cookie: "accounts, token, join, logout…"; class 3 public: "`/.well-known/jwks.json`"),
R5, R6, R11 ("signing keys from env, never in the repo, with `kid`"; "Verified email is required to …
get any app token").

ADR-0004 §5: "A request authenticated by the previous value may only use account sessions created
strictly before `rotated_at` (`ResolvedDevice.accounts_created_before`; USR-03b/03c must apply it)."

## Acceptance criteria → verification

The ticket's ACs are restated here with the corrections from this plan (marked **[changed]**). The
orchestrator should copy the corrections into `ticket.md` (see "AC corrections for the orchestrator"
below).

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `POST /api/v1/auth/token {audience, account_id, org_id?}` (cookie class: rate limit `token` → `AppContextDep` → `DeviceDep`) → 200 `{access_token, token_type:"Bearer", expires_in, audience}` with `Cache-Control: no-store` and `Pragma: no-cache`. **[changed]** JWT signed with **ES256** (P-256, ADR-0005), TTL `ACCESS_TOKEN_TTL_SECONDS` (default 900). Header is exactly `{alg:"ES256", kid, typ:"at+jwt"}`. Claims are exactly `iss`, `aud` (one string = app key), `sub` (user id), `email`, `email_verified` (`true`), `roles` (sorted `AuthzService.effective_roles`), `iat`, `exp` (= `iat` + TTL) and `jti` (uuid4). | `tests/test_auth_token.py::test_token_happy_path` checks the body, the headers, `expires_in == 900`, and that `tests/jwt_verifier.verify_reference` (PyJWT against the served JWKS) accepts the token. It also checks the exact header and claim key sets. |
| 2 | `audience` must equal the `X-MM-App` app key, else **[changed]** 403 `audience_mismatch`. Origin bound to that app by `AppContextDep` (403 `origin_not_allowed`). **[new]** `audience == "admin"` → 403 `audience_not_supported` until ADM-002 (decision A3). | `test_token_audience_mismatch`, `test_token_bad_origin_403`, `test_token_admin_audience_refused` |
| 3 | The account session must belong to this device and be active. When the request used the previous cookie value, it must also have been created before `rotated_at` (N1). Unknown, other-device, revoked or too-new account, or `disabled` user → 401 `session_invalid` **without** `Set-Cookie` (other accounts on the device stay signed in). | `test_token_unknown_account_401_keeps_cookie`, `test_token_other_device_account_401`, `test_token_revoked_account_401`, `test_token_previous_cookie_newer_account_401`, `test_token_previous_cookie_older_account_200`, `test_token_disabled_user_401` |
| 4 | Errors use the R6 envelope. **[changed]** An unknown or disabled app → 403 `origin_not_allowed` (from `AppContextDep`; `app_disabled` is not used, see deviation D-1). Unverified → 403 `email_unverified`. Non-member (no effective app-scope role) → 403 `not_member` with `details:{"join_available": join_policy != "closed"}`. `org_id` not null → **[changed]** 422 `org_not_supported`, `details=[{"field":"org_id","reason":"not_supported"}]` until USR-06. | `test_token_unknown_app_403`, `test_token_disabled_app_403`, `test_token_email_unverified_403`, `test_token_not_member` (parametrised open/approval/closed), `test_token_expired_assignment_not_member`, `test_token_org_id_422` |
| 5 | The signing key comes from env `JWT_SIGNING_KEY` (`SecretStr`, base64 of a PEM P-256 private key on one line). `kid` = RFC 7638 thumbprint, computed from the key, never configured. `JWT_PUBLIC_KEYS_EXTRA` (JSON list of base64 public PEMs) supports rotation. `JWT_ISSUER` is a setting. Staging and production refuse to start without a valid key or an explicit `https://` issuer. Local and test fall back to a per-process ephemeral key. Parse errors never echo the value. | `tests/test_jwt_keys.py::*`, `tests/test_config.py::test_jwt_signing_key_required_in_production`, `::test_jwt_signing_key_malformed_rejected_without_echo`, `::test_jwt_issuer_required_in_production`, `::test_jwt_keys_optional_locally` |
| 6 | `GET /.well-known/jwks.json` (public, root mount, per-IP limit `jwks`) returns public keys only (each key has exactly the members `{kty,crv,x,y,kid,alg,use}`). It sends a strong `ETag` and answers 304 on a match. Headers: `Cache-Control: public, max-age=300`, `Content-Type: application/jwk-set+json`, `Access-Control-Allow-Origin: *`, and **no** `Access-Control-Allow-Credentials`, even when the static `CORSMiddleware` is configured with that origin. | `tests/test_jwks.py::*` |
| 7 | Docs: the key rotation runbook and the new required config in `docs/deploy.md`. **[new]** A verifier checklist for consuming apps (ricotdin) in `docs/consumers.md`. ADR-0005 records the algorithm, key format, claims contract and rotation. identity-architecture.md §4 is corrected. | Reviewer reads `docs/adr/0005-access-token-signing.md`, `docs/consumers.md`, `docs/deploy.md`, identity-architecture.md §4 line "Token (D7)". |
| 8 | Tests: two accounts on one device are independent. The reference verifier rejects a wrong audience, wrong issuer, expired token, tampered token, a signature from another key with the same `kid`, an unknown `kid`, `alg:none`, HS256 key confusion and `typ` ≠ `at+jwt`. `kid` matches JWKS. Every error code above is covered. Missing cookie → 401, missing or bad headers → 403. | `tests/test_auth_token.py`, `tests/test_token_verification.py`; `make check` green |

### Deviations from the ticket (record in `impl-log.md`)

- **D-1 `app_disabled` is unreachable.** `AppContextDep` runs before the handler. It answers 403
  `origin_not_allowed` for an unknown **and** a disabled app, on purpose: it gives no oracle about which
  app keys exist (ADR-0004 §8, `AppContextService.check`). Since `audience` must equal `X-MM-App`, every
  unknown or disabled `audience` is stopped there. `app_disabled` is not added. All four consults agree.
- **D-2 New codes:**
  - `audience_mismatch` (403): safe, because the app is already proven by Origin + `X-MM-App`.
  - `audience_not_supported` (403): `admin` until ADM-002.
  - `org_not_supported` (422).
- **D-3 Algorithm ES256**, not the EdDSA the ticket mentions (see Approach §1 and ADR-0005).

### AC corrections for the orchestrator (copy into `work/tickets/USR-03c/ticket.md`)

- AC1: "asymmetric (algorithm decided in its plan …)" → "ES256 (ADR-0005)".
- AC2: "else 403 `origin_not_allowed` or a dedicated code" → "else 403 `audience_mismatch`;
  `audience=admin` → 403 `audience_not_supported` until ADM-002".
- AC4: "app unknown/disabled → 403 `app_disabled`" → "app unknown/disabled → 403
  `origin_not_allowed` (AppContextDep, no oracle)"; "`org_id` not null → 422" → "422
  `org_not_supported`".
- AC7: add "+ verifier checklist for consumers in `docs/consumers.md`".

## Files to create / modify

| File | Change | Required because |
|---|---|---|
| `pyproject.toml`, `uv.lock` | `uv add "PyJWT[crypto]>=2.10.1"`. This adds `cryptography`. Not `2.10.0`, which has CVE-2024-53861: a partial `iss` match. | AC1, AC5; owner confirmation A1 |
| `app/core/jwt_keys.py` (new, ~110 lines) | Pure, no ORM. Contents: <ul><li>`parse_private_key_b64`, `parse_public_key_b64`: P-256 only; they raise `ValueError` with a fixed message, `from None`.</li><li>`thumbprint(pub)`: RFC 7638 over `{"crv","kty","x","y"}`, sorted, no whitespace, SHA-256, base64url without padding.</li><li>`public_jwk(pub, kid)`.</li><li>`@dataclass(frozen=True) KeySet(signing_key [repr=False], signing_kid, jwks_body: bytes, jwks_etag: str)` with `sign(claims) -> str`.</li><li>`build_key_set(signing_b64: str \| None, extra_b64: list[str]) -> KeySet`: `None` means an ephemeral key. It rejects duplicate kids and an extra key equal to the signer.</li></ul> | AC5, AC6 |
| `app/core/config.py` | New settings: `jwt_signing_key: SecretStr \| None`, `jwt_public_keys_extra: list[str]`, `jwt_issuer: str = "http://localhost:8000"`, `access_token_ttl_seconds` (900, ge 60, le 3600), `token_rate_limit`/`_window_seconds` (60/60), `jwks_rate_limit`/`_window_seconds` (120/60). New validator `_jwt_keys_gate`. | AC5, R5 |
| `app/core/rate_limit.py` | `Scope` gains `"token"` and `"jwks"`, plus the two `_LIMITS` entries | AC1, AC6 |
| `app/main.py` | <ul><li>`app.state.key_set = build_key_set(...)` and a startup log line with the kids only. A missing key is only possible in local/test (Settings guarantees it); then log `jwt_signing_key_ephemeral`.</li><li>`include_router(jwks_router)` without a prefix.</li><li>A `CORSMiddleware` subclass that passes `/.well-known/` paths straight to the app (~10 lines).</li></ul> | AC5, AC6 |
| `app/modules/tokens/__init__.py` (new) | package | layout |
| `app/modules/tokens/errors.py` (new) | `AudienceMismatchError` (403), `AudienceNotSupportedError` (403), `EmailUnverifiedError` (403), `NotMemberError` (403, details), `OrgNotSupportedError` (`UnprocessableError`, code `org_not_supported`) | AC2, AC4, R6 |
| `app/modules/tokens/schemas.py` (new) | `TokenRequest` (`extra="forbid"`; `audience: str` with pattern `APP_KEY_RE`; `account_id: UUID`; `org_id: UUID \| None = None`), `TokenResponse`, `Jwk`, `JwkSet` (OpenAPI only) | AC1, AC6, R9 |
| `app/modules/tokens/service.py` (new, ~70 lines) | `TokenService.issue(app, device, data) -> IssuedToken`: the check order in Approach §3. Uses `SessionRepository.get_active_account`, `UserRepository.get` and `AuthzService.effective_roles`. Signs with `KeySet`. Logs `token_issued`. | AC1–AC4, R1 |
| `app/modules/tokens/deps.py` (new) | `get_key_set(request)` → `KeySetDep` | R1 (no `app.state` in the service) |
| `app/modules/tokens/jwks_router.py` (new) | `GET /.well-known/jwks.json`, tag `identity`, `rate_limit("jwks")`, ETag/304, headers | AC6 |
| `app/modules/auth/router.py` | `POST /token` route: `AppContextDep`, `DeviceCookieDoc`, `DeviceDep`, `TokenRequest`. Calls `TokenService` and forwards `device.new_cookie`. Sets `no-store` and `Pragma`. Declares `responses={401,403,422,429}`. | AC1, R3 class 2 |
| `.env.example` | `JWT_SIGNING_KEY=`, `JWT_PUBLIC_KEYS_EXTRA=`, `JWT_ISSUER=`, `ACCESS_TOKEN_TTL_SECONDS=`, `TOKEN_RATE_LIMIT=`, `TOKEN_RATE_WINDOW_SECONDS=`, `JWKS_RATE_LIMIT=`, `JWKS_RATE_WINDOW_SECONDS=`, each with a one-line comment and no values | R5 |
| `tests/conftest.py` | Before importing the app, set `JWT_SIGNING_KEY` to a fresh base64 P-256 PEM generated with `cryptography`, and `JWT_ISSUER=https://api.test.invalid`. Export `TEST_JWT_ISSUER`. This keeps the existing production-mode `Settings(...)` tests valid, the same pattern as `IDENTITY_HMAC_KEY`. | existing tests keep passing |
| `tests/jwt_verifier.py` (new) | `verify_reference(token, jwks, *, audience, issuer, leeway=30)`. It mirrors `docs/consumers.md`: <ul><li>rejects when `get_unverified_header` has `typ != "at+jwt"` or an unknown `kid`;</li><li>`PyJWKSet.from_dict(jwks)[kid]`;</li><li>`jwt.decode(..., algorithms=["ES256"], audience=, issuer=, options={"require": ["exp","iat","sub","jti","iss","aud"]})`;</li><li>asserts `email_verified is True`.</li></ul> | AC8 |
| `tests/test_jwt_keys.py` (new) | key parsing, thumbprint, JWKS members, sign, ephemeral | AC5, AC6 |
| `tests/test_config.py` | JWT settings gates | AC5 |
| `tests/test_auth_token.py` (new) | endpoint | AC1–AC4, AC8 |
| `tests/test_token_verification.py` (new) | reference-verifier negatives | AC8 |
| `tests/test_jwks.py` (new) | JWKS endpoint | AC6 |
| `tests/test_rate_limit.py` | Only if it enumerates scopes: add the new ones | AC1, AC6 |
| `docs/adr/0005-access-token-signing.md` (new) | ADR (content in Approach §8) | planner rule 7 |
| `docs/consumers.md` (new) | Verifier checklist and SPA rules for consuming apps, with ricotdin as the worked example (Approach §9) | AC7; ricotdin integration |
| `docs/deploy.md` | "Required runtime config": `JWT_SIGNING_KEY`, `JWT_ISSUER`, `JWT_PUBLIC_KEYS_EXTRA`. New section "Signing key rotation" (Approach §10). A release prerequisite note. | AC7 |
| `docs/identity-architecture.md` | Line ~146 "Token (D7)": "ES256 (P-256, ADR-0005), header `kid` + `typ: at+jwt`, claims + `jti`, `iss` from `JWT_ISSUER` (production `https://api.mmlabs.online`)". §4 rows for `/token` and JWKS: ticket USR-03 → USR-03c. S7: append "(ADR-0005, rotation in deploy.md)". | AC7; the docs currently say "EdDSA or RS256" |

The implementer must not touch other files without writing down why in `impl-log.md`. In particular:
no change to `sessions/` (`get_active_account` already takes `created_before`), no change to
`rbac/`, and no migration.

## Tests to write

| Test (file › name) | Covers |
|---|---|
| `test_jwt_keys.py › test_parse_private_key_roundtrip_and_kid_is_rfc7638` | Base64 PKCS#8 P-256 parses. The thumbprint equals a hand-computed SHA-256 over `{"crv","kty","x","y"}` JSON with sorted keys and no spaces. |
| `› test_parse_rejects_garbage_non_base64_and_wrong_curve` (parametrised: not base64, not PEM, P-384, Ed25519, RSA) | P-256 only. The message is fixed and never contains the input. |
| `› test_public_key_in_signing_slot_rejected` / `› test_private_key_in_extra_rejected` | slot types |
| `› test_duplicate_kid_rejected` | an extra key equal to the signer, or listed twice |
| `› test_jwks_members_allowlist` | Each key has exactly `{kty,crv,x,y,kid,alg,use}` with `alg="ES256"` and `use="sig"`. There is no `d`. The signer comes first, then the extras. |
| `› test_key_set_repr_has_no_private_material` | `repr(key_set)` contains no `d` or PEM |
| `› test_ephemeral_key_set_when_none` | `build_key_set(None, [])` works and gives a new kid on every call |
| `test_config.py › test_jwt_signing_key_required_in_production` (staging, production) | refused without a key; ok with one |
| `› test_jwt_signing_key_malformed_rejected_without_echo` | The `ValidationError` text does not contain the value. |
| `› test_jwt_issuer_required_in_production` | Staging/production: the issuer must be set explicitly, start with `https://`, and have no trailing slash or path. |
| `› test_jwt_keys_optional_locally` | `delenv JWT_SIGNING_KEY`; `Settings(app_env="local")` ok |
| `› test_access_token_ttl_bounds` | 59 and 3601 rejected |
| `test_auth_token.py › test_token_happy_path` | AC1 in full: body, headers, reference verifier, exact header and claim sets, `sub == str(user.id)`, `email`, `roles` sorted, `exp - iat == 900`, `jti` is a UUID, `kid` in JWKS |
| `› test_token_two_accounts_one_device_independent` | Two users on one device each get their own `sub`. After logout `{account_id: A}`, A → 401 and B → 200. |
| `› test_token_unknown_account_401_keeps_cookie` | 401 `session_invalid`, no `Set-Cookie` |
| `› test_token_other_device_account_401` | an account id from another device |
| `› test_token_revoked_account_401` | `revoked_at` set |
| `› test_token_previous_cookie_newer_account_401` / `› test_token_previous_cookie_older_account_200` | N1: previous cookie value within grace, using `shift` on `created_at` |
| `› test_token_disabled_user_401` | user status `disabled` |
| `› test_token_email_unverified_403` | `make_login_user(verified=False)` |
| `› test_token_not_member` (open → true, approval → true, closed → false) | `details.join_available` |
| `› test_token_expired_assignment_not_member` | only an expired assignment → `not_member` |
| `› test_token_roles_exclude_other_apps` | A role in another app is not in `roles`. |
| `› test_token_audience_mismatch` | `audience` ≠ `X-MM-App` → 403 `audience_mismatch` |
| `› test_token_admin_audience_refused` | Admin app with an origin configured in the test, plus a super-admin assignment → 403 `audience_not_supported` |
| `› test_token_org_id_422` | `org_not_supported` plus details |
| `› test_token_body_validation_422` (extra field, bad audience format, bad UUID) | `validation_error` |
| `› test_token_missing_cookie_401` | 401 `session_invalid` |
| `› test_token_missing_app_header_403` / `› test_token_bad_origin_403` / `› test_token_unknown_app_403` / `› test_token_disabled_app_403` | `app_header_required` / `origin_not_allowed` |
| `› test_token_rotation_forwards_set_cookie` (strict client) | `rotated_at` 25 h ago → 200 with `__Host-mm_device` `Set-Cookie`. The new hash is committed: the next request with the new cookie → 200. |
| `› test_token_error_after_rotation_rolls_back` (strict client) | Rotation due plus `not_member` → 403, no `Set-Cookie`, and the old cookie still works. |
| `› test_token_rate_limited_429` | `token_rate_limit=1` |
| `› test_token_logs_no_secrets` (caplog) | No access token, cookie value or email in the logs. `token_issued` is logged with `jti` and `kid`. |
| `test_token_verification.py › test_reference_rejects_tampered_payload` | one changed payload byte |
| `› test_reference_rejects_other_key_same_kid` | signed with another P-256 key using the served `kid` |
| `› test_reference_rejects_alg_none` | hand-built `{"alg":"none"}` token |
| `› test_reference_rejects_hs256_key_confusion` | HMAC-SHA256 over header.payload with the public PEM as the secret, built with the `hmac` module |
| `› test_reference_rejects_expired` | `KeySet.sign` with `iat`/`exp` in the past, beyond the leeway |
| `› test_reference_rejects_wrong_audience` / `› test_reference_rejects_wrong_issuer` | |
| `› test_reference_rejects_unknown_kid` | |
| `› test_reference_rejects_typ_jwt` | `typ: "JWT"` |
| `test_jwks.py › test_jwks_public_keys_only` | members allowlist; the `kid` equals the token header `kid` |
| `› test_jwks_headers` | `Content-Type: application/jwk-set+json`, `Cache-Control: public, max-age=300`, strong `ETag`, `ACAO: *` |
| `› test_jwks_304_on_etag` | 304, no body, `ETag` and `Cache-Control` still present; `W/` and lists handled by `if_none_match` |
| `› test_jwks_lists_extra_keys` | `jwt_public_keys_extra` override |
| `› test_jwks_no_credentials_with_cors_origin` | `make_client(cors_origins=[ORIGIN])` and a GET with that `Origin` → `ACAO: *`, no `Access-Control-Allow-Credentials` |
| `› test_jwks_rate_limited_429` | `jwks_rate_limit=1` |
| `› test_jwks_body_stable_across_requests` | same bytes and `ETag` |

## Approach

### 1. Algorithm: ES256 (decision; ADR-0005)

The consults split. The software architect recommends ES256; the identity and security consults
recommend EdDSA. **ES256 (ECDSA P-256, SHA-256)** is chosen:

- **ricotdin backend (PyJWT[crypto]):** ES256 has been supported in PyJWT for years, and
  `PyJWKSet`/`PyJWKClient` read EC JWKs. EdDSA works too, so this point is neutral.
- **Browser / Node `jose`:** the ricotdin SPA keeps the token in memory and never verifies it (it
  is a bearer token for its own backend). Other mmlabs consumers may verify at the edge or in Node.
  P-256 is in every WebCrypto implementation. Ed25519 only arrived in WebCrypto recently (older Safari
  and Chromium lack it).
- **Name stability:** RFC 9864 (fully-specified algorithms) deprecates the polymorphic `"EdDSA"`
  name in favour of `"Ed25519"`. Libraries will go through a renaming period, and every consumer pins
  the name. `ES256` does not change.
- **Security:** both are fine for a 15-minute token. Signature size and speed do not matter at this
  volume. ECDSA's nonce risk is handled by `cryptography` (OpenSSL), not by our code.
- **Rejected:** EdDSA (the naming churn above), RS256 (large keys and tokens; no benefit).

Every verifier pins `algorithms=["ES256"]`. The JWKS lists `alg:"ES256"` on each key.

### 2. Keys and config (R5, R11)

- `JWT_SIGNING_KEY` (`SecretStr | None`): **base64 of a PEM P-256 private key on one line**. The deploy
  writes a single-line Docker env file, so a multi-line PEM does not fit (security consult finding 5).
  Only the base64 form is accepted, so there is one format. Both PKCS#8 (`openssl genpkey`) and SEC1
  PEM load.
- `JWT_PUBLIC_KEYS_EXTRA` (`list[str]`, JSON list of base64 SPKI public PEMs). These are not secret.
  They are published in the JWKS so the next or previous key verifies during rotation.
- `JWT_ISSUER` (default `http://localhost:8000` for local/test). In staging/production it must be set
  explicitly (`"jwt_issuer" in self.model_fields_set`), match `^https://[a-z0-9.-]+$` (no path, port
  or trailing slash) and differ per environment. Production uses `https://api.mmlabs.online` (§4).
- `ACCESS_TOKEN_TTL_SECONDS` = 900 (60 ≤ x ≤ 3600).
- `kid` = RFC 7638 thumbprint of the public key. It is computed, never configured, so it cannot drift
  from the key.
- **Validator `_jwt_keys_gate`** (mode `after`):
  - In every env, if a key or extras are present, call `build_key_set(...)` so a bad PEM fails at
    startup, not on the first request. Parse errors are re-raised as `ValueError("JWT_SIGNING_KEY is
    not a valid base64 PEM P-256 private key")` (or `JWT_PUBLIC_KEYS_EXTRA …`) with `from None`.
    `hide_input_in_errors` is already on.
  - In staging/production the key is required, and so are the issuer rules above.
  - `migrate` and the CLI load the same Settings, so they also refuse to start. The deploy must
    provide the key first (§10).
- **`create_app`** builds `KeySet` once and stores it in `app.state.key_set`. It logs
  `jwt_keys_loaded signing_kid=… extra_kids=[…]`, or `jwt_signing_key_ephemeral kid=…` when local/test
  has no key. It never logs key material.
- An ephemeral key is per process. With more than one local uvicorn worker, tokens would not verify
  across workers. Local runs use one worker, so this is accepted (architect risk).

### 3. `POST /api/v1/auth/token`: check order (R3 class 2, R6)

Order of dependencies and checks. Each step is evaluated only if the earlier ones passed:

| Step | Check | Result |
|---|---|---|
| 1 | `rate_limit("token")`, per IP, 60/min | 429 `rate_limited` with `Retry-After` |
| 2 | `AppContextDep` | 403 `app_header_required` / `origin_not_allowed` (unknown or disabled app, bad Origin). Runs before the cookie is read, so a cross-site request never triggers rotation or reuse revocation. |
| 3 | `DeviceDep` | 401 `session_invalid`, clears the cookie when one was sent (existing). May rotate (≥ 24 h) and returns `new_cookie`. |
| 4 | Body (`TokenRequest`, `extra="forbid"`) | 422 `validation_error`. FastAPI raises sub-dependency errors before body errors, so this comes after steps 2–3. |
| 5 | `data.audience != app.key` | 403 `audience_mismatch` |
| 6 | `app.key == ADMIN_APP_KEY` | 403 `audience_not_supported` ("Tokens for this audience are not available yet") |
| 7 | `data.org_id is not None` | 422 `org_not_supported`, `details=[{"field":"org_id","reason":"not_supported"}]` |
| 8 | `get_active_account(device.device_id, data.account_id, created_before=device.accounts_created_before)` is `None` | 401 `session_invalid`, **without** `headers` (no cookie clearing: other accounts stay signed in) |
| 9 | `UserRepository.get(account.user_id)` is missing or `status != active` | the same 401 |
| 10 | `user.email_verified_at is None` | 403 `email_unverified` (S5, D16) |
| 11 | `roles = AuthzService.effective_roles(user.id, app.key)` is empty | 403 `not_member` with `details={"join_available": app.join_policy != "closed"}`. **Membership = at least one non-expired app-scope role of this app** (architect Q3). `org_id` is `None` here, so only app-scope roles count. |

Then sign:

```
now = int(clock())
header = {"alg": "ES256", "kid": key_set.signing_kid, "typ": "at+jwt"}
claims = {"iss": issuer, "aud": app.key, "sub": str(user.id), "email": user.email,
          "email_verified": True, "roles": roles, "iat": now, "exp": now + ttl,
          "jti": str(uuid.uuid4())}
```

- `email_verified` is always `true` because step 10 refused unverified users. It stays in the token for
  the D7 contract.
- There is no `nbf` and no `org` (USR-06 adds `org`). `clock` is `time.time`, injectable in the
  service constructor.
- Log `token_issued user_id=… account_session_id=… aud=… kid=… jti=…`. Never the JWT, the cookie or
  the email (R11).

The router returns 200 `TokenResponse(access_token, token_type="Bearer", expires_in=ttl,
audience=app.key)` with `Cache-Control: no-store` and `Pragma: no-cache`. If `device.new_cookie` is set,
it calls `set_device_cookie(...)`. That forwards the `DeviceDep` rotation, which would otherwise be
lost, the same way logout does. `SessionDep` (function scope) commits before the response, so the
rotation is never lost after a sent `Set-Cookie` (ADR-0004 §4, USR-03f). On any 4xx after step 3,
`get_session` rolls the rotation back and no `Set-Cookie` is sent, so the result is consistent (test
`test_token_error_after_rotation_rolls_back`).

- `/token` does not force a rotation and writes nothing else. `account_sessions.last_used_at` is not
  touched (follow-up F4).
- Error responses do not get `no-store`. They carry no secret, and `AppError.headers` is already used
  for `Set-Cookie` clearing. Rejected: a generic error-header change in `core/errors.py` (R7).
- The `X-MM-App` and cookie OpenAPI docs come from the existing `DeviceCookieDoc` and `AppContextDep`
  `Header`.

R1: the router handles HTTP only (cookie, headers). `TokenService` holds the rules and raises
`AppError` subclasses. Repositories do the SQL. `KeySet` is pure.

### 4. `audience=admin`: refused until ADM-002 (decision A3)

`/token` answers 403 `audience_not_supported` for `admin`. Reasons:

- Nothing verifies `aud=admin` tokens yet. ADM-002 is the consumer.
- ADM-002 still has open decisions: a shorter admin TTL (identity and security consults, Q1) and 2FA
  for staff (OQ3).
- Origins are not unique across apps (security finding 3). Minting admin tokens before the
  origin-exclusivity guard exists would let XSS on any app that shares an origin with `admin` mint
  them.

In practice the step is unreachable in production today (the seeded `admin` app has `origins = []`).
It is still coded so the decision is explicit and tested. **Follow-up for ADM-002** (F1): lift the
refusal, add the origin-exclusivity check (refuse when the request Origin is also in another app's
`origins`, or enforce unique origins on app writes), and decide the admin TTL.

### 5. JWKS

- `app/modules/tokens/jwks_router.py`: `router = APIRouter(tags=["identity"])`, `@router.get("/.well-known/jwks.json",
  response_model=JwkSet, dependencies=[rate_limit("jwks")], responses={304:…, 429:…})`. It is included
  in `create_app` without a prefix, like `health_router`. Never behind a flag.
- The body is built once at startup inside `KeySet`: canonical JSON bytes
  (`json.dumps(..., separators=(",", ":"), sort_keys=True)`) of `{"keys":[signer, *extras]}`. The ETag
  is `strong_etag(body)`. No database.
- Response headers: `ETag`, `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *`.
  - 200: `Response(content=body, media_type="application/jwk-set+json", headers=…)`.
  - 304: `if_none_match(request.headers.getlist("if-none-match"), etag)` → `Response(status_code=304,
    headers=…)`. Same pattern as `catalog_router`.
- Rate limit `jwks` is per IP, 120/min by default. It is generous because app backends may share
  one egress IP (security consult).
- **CORS interaction.** Starlette's `CORSMiddleware.send` adds `Access-Control-Allow-Credentials:
  true` to every response to a request that has an `Origin`. For a listed origin it also replaces
  `ACAO` with the echoed origin (verified in starlette 1.10 `cors.py`). That breaks AC6, so `main.py`
  wraps it:

```
class _CredentialedCORS(CORSMiddleware):
    """Static CORS until USR-03e; `/.well-known/` is public, uncredentialed (ACAO * set by route)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
```

  A preflight to JWKS is not needed (simple GET); an `OPTIONS` gets 405, which is fine. **Follow-up for
  USR-03e** (F2): the new CORS middleware must keep `/.well-known/` exempt (or uncredentialed `*`).

### 6. Rate-limit scopes

`Scope = Literal["signup", "role_catalog", "login", "token", "jwks"]`, plus `_LIMITS` entries reading
the new settings. These are in-process, per worker (ADR-0002); that limit is accepted.

### 7. Tests infrastructure

- `conftest.py` generates one P-256 key per test session:

  ```
  ec.generate_private_key(ec.SECP256R1()).private_bytes(PEM, PKCS8, NoEncryption())
  ```

  It sets the base64 of that key in `JWT_SIGNING_KEY` before the `app` imports, and sets
  `JWT_ISSUER=https://api.test.invalid`. This keeps every existing `Settings(app_env="production", …)`
  test valid without edits.
- Tests that need "no key" use `monkeypatch.delenv`.
- `verify_reference` works from the served JWKS dict (`PyJWKSet.from_dict`). `PyJWKClient` fetches
  over urllib and cannot reach the ASGI transport. The checks are the same ones `docs/consumers.md`
  prescribes.
- D16: tests create verified users with `make_login_user(verified=True)` (the default) and assign
  roles with `rbac_factories.make_role` / `assign`.

### 8. ADR-0005 "Access-token signing" (implementer writes it; Status: Accepted (USR-03c plan, 2026-11-12))

- **Context:** D7, S7, the consult split.
- **Decision:**
  1. Algorithm: ES256 (§1), with the rejected alternatives.
  2. Key format: base64 one-line PEM in `JWT_SIGNING_KEY` and `JWT_PUBLIC_KEYS_EXTRA`.
  3. `kid` is the RFC 7638 thumbprint.
  4. Header `{alg, kid, typ:"at+jwt"}`. `typ` follows RFC 9068 naming, but the token is **not** a full
     RFC 9068 token: there is no `client_id` and `scope` is not used.
  5. Claims contract, frozen within v1: `iss`, `aud` (single string), `sub` (user id, never the
     account-session id), `email`, `email_verified`, `roles` (sorted app-scope role keys), `iat`,
     `exp`, `jti`. `org` is added by USR-06. Adding a claim is not breaking; removing or renaming one
     is.
  6. TTL 900 s. No refresh token and no revocation list. A disabled user, a removed role or a logout
     keeps a live token for at most the TTL (accepted under D7). A leaked key is fixed only by rotation.
  7. JWKS: `max-age=300`, ETag, public. Verifiers refetch on an unknown `kid`, at most once a minute.
  8. `audience=admin` is refused until ADM-002 (§4).
  9. The error codes of `/token`, and why `app_disabled` is not used.
- **Consequences:**
  - New dependency `PyJWT[crypto]`.
  - The deploy needs a new secret.
  - Until USR-04, nobody can get a token (D16).

### 9. `docs/consumers.md` (new): "Verifying mmlabs access tokens", ricotdin as the worked example

- **Getting a token (SPA):** `POST https://api.mmlabs.online/api/v1/auth/token` with `credentials:
  "include"`, `X-MM-App: ricotdin`, body `{audience:"ricotdin", account_id}`. Keep the token **in
  memory only** (never `localStorage`, URLs or logs). Refresh it before `exp` by calling `/token` again.
  Send it only to ricotdin's own backend as `Authorization: Bearer`. The listed error codes and what to
  do for each (`not_member` → join flow USR-03b; `email_unverified`; 401 → re-list accounts or sign in).
  It needs USR-03e (CORS) in production.
- **Verifying (backend) checklist:**
  1. Pin `algorithms=["ES256"]`. Never read the algorithm from the token.
  2. Get keys only from `https://api.mmlabs.online/.well-known/jwks.json`. Never from token headers
     (`jku`, `x5u`, `jwk`).
  3. Select the key by `kid`. Cache the JWKS (≤ 1 h). On an unknown `kid`, refetch at most once a
     minute.
  4. Require `iss == "https://api.mmlabs.online"` exactly and `aud == "ricotdin"`.
  5. Require `exp`, `iat`, `sub`, `jti`. Leeway ≤ 60 s (30 s recommended).
  6. Require header `typ == "at+jwt"` and `email_verified is True`.
  7. Map `roles` to permissions only through ricotdin's own role catalog (`GET /api/v1/apps/ricotdin/roles`).
  8. Never log tokens.
  9. Use `PyJWT[crypto]>=2.10.1`.
  10. Set a `User-Agent` on the JWKS fetch (Cloudflare may block the urllib default; architect risk).
  11. Revocation lag: up to 15 minutes.
- A ~15-line PyJWT sketch (`PyJWKClient(url, cache_jwk_set=True, lifespan=300, headers={"User-Agent":
  …})`, `get_signing_key_from_jwt`, `jwt.decode(..., algorithms=["ES256"], audience=, issuer=,
  leeway=30, options={"require": [...]})`, then the `typ` check via `get_unverified_header`).
- Prerequisites for ricotdin, in order: USR-03c, USR-03e, USR-04, USR-07.

### 10. `docs/deploy.md`

**Required runtime config:**

- **`JWT_SIGNING_KEY`** (secret, env file written by the deploy, like `IDENTITY_HMAC_KEY`).
  - Generate with `openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 | base64 | tr -d '\n'`
    (portable on macOS and Linux). Use a different key per environment.
  - In staging/production **`migrate` and the API refuse to start** without it, so the deployment repo
    must provide it **before** merging the release PR that contains USR-03c. Otherwise the deploy fails
    at step 5 and the old API keeps running.
- **`JWT_ISSUER`** (not secret, `compose.yml` → `x-app.environment`): `https://api.mmlabs.online`.
  Never change it: every consumer pins it.
- **`JWT_PUBLIC_KEYS_EXTRA`** (not secret, `compose.yml`, JSON list in a quoted string): empty except
  during a rotation.

**Signing key rotation** (planned):

1. Generate the next key and derive its public key: `echo "$NEXT" | base64 -d | openssl pkey -pubout |
   base64 | tr -d '\n'`. Add that to `JWT_PUBLIC_KEYS_EXTRA` and redeploy. Check that the JWKS lists
   two kids. Wait at least the JWKS `max-age` (300 s) plus the longest consumer cache (≤ 1 h per
   `docs/consumers.md`).
2. Make the next key the signer in `JWT_SIGNING_KEY`, replace the extra entry with the **old** public
   key, and redeploy.
3. After the TTL (900 s) plus leeway (60 s) plus `max-age` (300 s), about 25 minutes, remove the old
   public key and redeploy.

**Emergency rotation** (key leaked): do step 2 immediately with no extra key, and redeploy. Live tokens
fail at verifiers. Clients get a 401 from their backend and call `/token` again, which the cookie still
allows. Verifiers refetch on the unknown `kid`. Expect about one failed request per active client.
Never keep a leaked public key in the JWKS.

**Owner action, outside this repo** (F3): `mmlabs-online/deployment`:

- add a repository secret (e.g. `ORG_ADMIN_API_JWT_SIGNING_KEY`);
- write it as `JWT_SIGNING_KEY=` into `~/.config/org-admin-api/org-admin-api.env` in
  `.github/workflows/deploy-org-admin-api.yml`;
- add `JWT_ISSUER: https://api.mmlabs.online` to `apps/org-admin-api/compose.yml` `x-app.environment`.

### Rules check

| Rule | How this plan meets it |
|---|---|
| R1 | Layering as above. |
| R2 | No schema change. |
| R3 | `/token` is class 2: cookie, Origin and `X-MM-App`. JWKS is class 3: public and rate-limited. |
| R4 | No admin mutation, so no audit entry. |
| R5 | All config from env; keys in `.env.example` without values. |
| R6 | Envelope for every error; 422 detail shape `[{field, reason}]`. |
| R7 | Follow-ups F1–F5, not in the diff. |
| R8 | Tests for every code path. |
| R9 | `/api/v1/auth/token` per D13. JWKS at the root per D13. |
| R10 | Not an OIDC provider: no discovery document, no `/authorize`. |
| R11 | Keys from env with `kid`. Tokens, cookie and email are never logged. Verified email is required. |

## Specialist input used

- **api-platform-engineer.**
  - Adopted: schemas with `extra="forbid"`; the error table; `audience_mismatch`; `org_not_supported`
    with details; forwarding `new_cookie`; `no-store` and `Pragma`; the `token` scope; a JWKS router at
    the root built once; `application/jwk-set+json`; the CORS exemption wrapper; a settings-based
    `iss`; no `jti` denylist; `jti` logged.
  - Changed: their check order put the account session before the user and org checks. Here
    `org_id` (422) runs before the account lookup, so a request outside v1 fails fast without
    revealing whether the account exists. This is harmless either way.
  - Rejected: `no-store` on error responses (no secret in them; would need a core error-handler
    change); `Vary: Cookie` (`no-store` makes it moot); a published `ErrorEnvelope` model (the
    existing `_ERR` convention is kept, R7).
- **identity-access-engineer.**
  - Adopted: the check order (steps 2–11); 401 without cookie clearing; membership = non-empty
    `effective_roles`; the claims, with `sub` = user id; no `nbf`/`org`; the TTL setting with bounds;
    the verifier checklist; the test matrix.
  - Rejected: touching `last_used_at` (not in the ACs; F4); a separate `account_session_invalid`
    code (the AC fixes `session_invalid`; the client re-lists accounts on 401; A5); EdDSA (§1).
  - Admin TTL question: answered by refusing `admin` (§4).
- **security-architect.**
  - Adopted: pinned algorithm; `kid` looked up only in the static key set; exact header; one-line
    base64 PEM; extras public only; duplicate-kid rejection; a generic parse error with `from None`;
    staging/production refusal; JWKS members allowlist; a generous JWKS limit; the `token` scope; the
    logging rules; the rotation runbook timings; SPA rules; the deploy secret workflow.
  - Origin exclusivity for `admin`: replaced by refusing `admin` until ADM-002 (§4, F1).
  - Rejected: EdDSA (§1); an `jku`-injection test (PyJWT never fetches header URLs and the reference
    verifier only uses the served JWKS; kept as a checklist item instead).
- **software-architect.**
  - Adopted: ES256; reuse of `AppContextDep`, `DeviceDep`, `get_active_account`, `etag` and
    `effective_roles`; ADR-0005 with the RFC 9068 caveat; the "member" definition; the rotation runbook
    shape; the size estimate (keep one ticket); the `User-Agent` and clock-skew notes.
  - Changed: `KeySet` lives in `app/core/jwt_keys.py`, not `modules/tokens/keys.py`, because the
    Settings validator must parse keys and `core` must not import `modules`. `issuer.py` is folded
    into `KeySet.sign`. Only base64 PEM is accepted, not raw PEM as well.

## Assumptions

Owner-confirm items, none blocking:

- **A1 New dependency `PyJWT[crypto]>=2.10.1`** (adds `cryptography`, a wheel available for
  linux/amd64 and macOS). It is a library, not a stack change. ricotdin uses the same library.
- **A2 ES256** instead of the EdDSA named in the ticket (§1, ADR-0005).
- **A3 `audience=admin` refused** with 403 `audience_not_supported` until ADM-002 (§4).
- **A4 Error codes:**
  - `app_disabled` is dropped; an unknown or disabled app → `origin_not_allowed` (D-1).
  - New: `audience_mismatch`, `audience_not_supported`, `org_not_supported`.
- **A5** An account-level 401 reuses `session_invalid`, without clearing the cookie. The client
  re-lists its accounts (USR-03b) and drops the stale `account_id`.
- **A6 Issuer:**
  - Production `JWT_ISSUER=https://api.mmlabs.online`.
  - There is no staging API today (`docs/deploy.md` describes production only). If one is added, it
    uses its own issuer and key.
- **A7 Limits:** `token` 60/min per IP and `jwks` 120/min per IP.
- **A8 D16:** no real account can get a token until USR-04 ships. ricotdin's integration order is
  USR-03c → USR-03e → USR-04 → USR-07 (USR-07 ticket).

## Blocking questions

None.

## Split proposal

None. Estimate:

| Part | Lines |
|---|---|
| `core/jwt_keys.py` | ~110 |
| `config.py` | ~45 |
| `tokens/` (service, errors, schemas, deps, jwks_router) | ~170 |
| `auth/router.py` | ~40 |
| `main.py` | ~20 |
| `rate_limit.py` | ~6 |
| **Production total** | **~390** |
| Tests | ~550 |
| Docs (ADR-0005, consumers.md, deploy.md) | ~200 |

There is no migration, and everything implements one spec feature (§5.8 per-app tokens and
verification). The JWKS and keys cannot be shipped without the token endpoint or the other way round
in a useful way, so one ticket it stays.

**Follow-ups** (not in this diff):

- **F1 (ADM-002):** lift the `admin` refusal; origin exclusivity or unique origins; admin TTL.
- **F2 (USR-03e):** keep `/.well-known/` outside credentialed CORS.
- **F3 (owner, deployment repo):** the `JWT_SIGNING_KEY` secret in the deploy workflow and
  `JWT_ISSUER` in compose. This must land before the release containing USR-03c.
- **F4:** `account_sessions.last_used_at` touch on `/token` (USR-03b account list).
- **F5:** CLAUDE.md R11 still says cookie `mm_device`, `Path=/api/v1/auth` (superseded by D20); fix via
  `/solo`.

## Data model / migration

None. No schema change. `/token` reads `account_sessions`, `users`, `apps`, `roles` and
`role_assignments` and writes only the existing `DeviceDep` rotation.

## API contract

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/token` | Headers: `Cookie: __Host-mm_device`, `Origin`, `X-MM-App: <key>`. Body `TokenRequest {audience: str (APP_KEY_RE), account_id: uuid, org_id?: uuid \| null}`, `extra=forbid`. | 200 `TokenResponse {access_token: str, token_type: "Bearer", expires_in: int, audience: str}`; `Cache-Control: no-store`, `Pragma: no-cache`; `Set-Cookie` only when `DeviceDep` rotated. | 200; 401 `session_invalid` (cookie: clears; account/user: does not clear); 403 `app_header_required`, `origin_not_allowed`, `audience_mismatch`, `audience_not_supported`, `email_unverified`, `not_member {join_available}`; 422 `validation_error`, `org_not_supported`; 429 `rate_limited` | R3 class 2 (cookie + Origin + `X-MM-App`); no Bearer |
| GET | `/.well-known/jwks.json` | optional `If-None-Match` | 200 `application/jwk-set+json` `{"keys":[{kty:"EC",crv:"P-256",x,y,kid,alg:"ES256",use:"sig"}, …]}`; headers `ETag` (strong), `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *`, no `Access-Control-Allow-Credentials` | 200, 304, 429 | R3 class 3 (public, per-IP `jwks`) |

JWT issued by `/token`:

```
header  {"alg":"ES256","kid":"<RFC 7638 thumbprint>","typ":"at+jwt"}
payload {"iss":"<JWT_ISSUER>","aud":"<app key>","sub":"<user uuid>","email":"<email>",
         "email_verified":true,"roles":["<app>:<role>", ...],"iat":<int>,"exp":<iat+900>,
         "jti":"<uuid4>"}
```
