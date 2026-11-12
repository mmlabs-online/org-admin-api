# Review — USR-03c

## Attempt 1 — 2026-11-12
Verdict: PASS

Judged against the ACs as corrected by plan.md (D-1/D-2/D-3) and recorded in ticket.md's orchestrator note
(ES256; `audience_mismatch`; `audience_not_supported` for `admin`; unknown/disabled app → `origin_not_allowed`;
`org_not_supported`; `docs/consumers.md`).

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", ruff format "259 files
  already formatted", mypy "Success: no issues found in 69 source files", pytest "718 passed in 31.76s".
  Matches impl-log (718 passed); the format count (259 vs 258) differs by one file, with a clean `git status`.
  This is consistent with a file added by the concurrent security review and does not affect the result.
- alembic check (no migration in this ticket): against `org_admin_test` → "No new upgrade operations
  detected." (The un-migrated dev DB reports "Target database is not up to date", as impl-log says. Not related.)
- Extra manual checks:
  - With env vars only (`APP_ENV=production`, `JWT_ISSUER`, `JWT_SIGNING_KEY`, …), `Settings()` loads. Without
    `JWT_ISSUER` it fails with the JWT_ISSUER message. So `model_fields_set` does pick up the env-sourced issuer.
  - The runbook commands in `docs/deploy.md` (`openssl genpkey … | base64 | tr -d '\n'` and
    `base64 -d | openssl pkey -pubout | base64 | tr -d '\n'`) produce values that `build_key_set` accepts as the
    signer and as an extra. Both give the same kid.

### Acceptance criteria
- [x] AC1 `POST /api/v1/auth/token`: 200 body, `no-store` + `Pragma`, ES256, exact header
  `{alg,kid,typ:at+jwt}`, exact claim set, `exp-iat==900`, uuid4 `jti`, sorted roles. Evidence:
  `app/modules/auth/router.py:147-177`, `app/modules/tokens/service.py:86-106`, `app/core/jwt_keys.py:91-97`;
  `tests/test_auth_token.py::test_token_happy_path` and `::test_token_roles_exclude_other_apps` pass.
- [x] AC2 (corrected): `audience != X-MM-App` → 403 `audience_mismatch` (`service.py:57-58`). `admin` → 403
  `audience_not_supported` (`service.py:59-60`). Origin is bound by `AppContextDep`. Tests:
  `test_token_audience_mismatch`, `test_token_admin_audience_refused`, `test_token_bad_origin_403` (bad and
  missing Origin).
- [x] AC3: the account lookup uses `get_active_account(device_id, account_id, created_before=accounts_created_before)`
  (`service.py:66-70`). A missing user or one with status ≠ active → 401 `session_invalid` with no `headers`, so
  the cookie is not cleared. Tests: `test_token_unknown_account_401_keeps_cookie`,
  `_other_device_account_401`, `_revoked_account_401`, `_previous_cookie_newer_account_401`,
  `_previous_cookie_older_account_200`, `_disabled_user_401` (all assert no `Set-Cookie`).
- [x] AC4 (corrected): an unknown or disabled app → `origin_not_allowed` (`test_token_unknown_app_403`,
  `test_token_disabled_app_403`). `email_unverified` (`test_token_email_unverified_403`). `not_member` with
  `join_available` for open/approval/closed (`test_token_not_member`) and for an expired assignment
  (`test_token_expired_assignment_not_member`). `org_id` → 422 `org_not_supported` with `[{field, reason}]`
  (`test_token_org_id_422`). All responses use the R6 envelope through `AppError` subclasses
  (`app/modules/tokens/errors.py`).
- [x] AC5: `jwt_signing_key: SecretStr | None`, base64 PEM P-256 only, `kid` = RFC 7638 thumbprint
  (`jwt_keys.py:66-73`), `JWT_PUBLIC_KEYS_EXTRA` with duplicate rejection, `_jwt_keys_gate`
  (`config.py:155-183`) with fixed messages and `from None`. Staging/production require a key and an
  `https://<host>` issuer. Tests: `tests/test_jwt_keys.py` (9 tests) and `tests/test_config.py::test_jwt_*`,
  `::test_access_token_ttl_bounds`, all passing.
- [x] AC6: JWKS at the root (`main.py:78`, `jwks_router.py`), rate-limited `jwks`, public members only, strong
  ETag / 304 (exact, `W/`, list), `public, max-age=300`, `application/jwk-set+json`, `ACAO: *`. No
  `Access-Control-Allow-Credentials`, even with the credentialed CORS configured (`_CredentialedCORS`,
  `main.py:25-32`; `test_jwks_no_credentials_with_cors_origin`, with a `/healthz` control).
  `tests/test_jwks.py` (7 tests) passes.
- [x] AC7: `docs/deploy.md`:
  - required config: `JWT_SIGNING_KEY`, `JWT_ISSUER`, `JWT_PUBLIC_KEYS_EXTRA`, plus the owner action;
  - "Signing key rotation" (planned and emergency), commands checked by hand.

  Also delivered: `docs/adr/0005-access-token-signing.md`, `docs/consumers.md` (verifier checklist plus a PyJWT
  sketch), and `docs/identity-architecture.md` §4 / "Token (D7)" / S7 updated.
- [x] AC8: `test_token_two_accounts_one_device_independent`. `tests/test_token_verification.py` covers:
  - tampered payload;
  - another key with the same kid;
  - `alg:none`;
  - HS256 key confusion;
  - expired token;
  - wrong aud and wrong iss;
  - unknown kid;
  - `typ: JWT`;
  - a valid control.

  `kid` matches the JWKS (`test_token_happy_path`, `test_jwks_public_keys_only`). Missing cookie → 401
  (`test_token_missing_cookie_401`). Missing or bad headers → 403 (`_missing_app_header_403`,
  `_bad_origin_403`). Rotation is forwarded and committed (`test_token_rotation_forwards_set_cookie`), and it
  rolls back on error (`test_token_error_after_rotation_rolls_back`). Rate limits: 429 for `token` and `jwks`.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the router only does HTTP (cookie forwarding and headers). `TokenService` holds the rules and raises
  `AppError` subclasses. SQL stays in the existing repositories. `KeySet` is pure and lives in `core`.
- R2: no schema change, and `alembic check` is clean.
- R3: `/token` is class 2 (rate_limit → `AppContextDep` → `DeviceDep`; Origin plus `X-MM-App`). JWKS is class 3
  (public and rate-limited).
- R4: there is no admin mutation.
- R5: every new key is in `.env.example` with no value. No key material is logged (only kids). `SecretStr`
  and `repr=False` are used for the private key (`test_key_set_repr_has_no_private_material`).
- R6: all errors use the envelope.
- R7: every changed code file appears in the plan's file list. The `work/` and `docs/spec-v1.md` changes come
  from earlier owner-answer commits and are not part of this ticket.
- R8: green, and there are tests for every path.
- R9: D13/D18 paths.
- R10: not an OIDC provider (no discovery and no authorize endpoint).
- R11: tokens, the cookie and the email are not logged (`test_token_logs_no_secrets`). A verified email is
  required.

### Nits (do not block)
- `docs/consumers.md` error table: `403 audience_not_supported` is missing. A consumer only sees it for
  `admin`, but the table should list every code `/token` can return.
- `config.py:155-171`: when both the key and the extras are set, `build_key_set` parses the signing key twice at
  startup. This is harmless.

### Follow-ups (out of scope)
- impl-log follow-up 1: the `fileConfig` call in `migrations/env.py` disables existing `app.*` loggers during
  tests, so the suite's earlier "no secrets in logs" assertions are vacuous. Fix with
  `disable_existing_loggers=False`. This is worth a `/solo` or a small ADM ticket.
- Plan F1–F5:
  - F1: ADM-002 admin audience, origin exclusivity, admin TTL.
  - F2: USR-03e keeps `/.well-known/` uncredentialed.
  - F3: the owner sets the deployment-repo secret and `JWT_ISSUER`. This is a release blocker for the release
    that contains USR-03c.
  - F4: `last_used_at`.
  - F5: CLAUDE.md R11 cookie text.
