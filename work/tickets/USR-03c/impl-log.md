# Implementation log — USR-03c

## Attempt 1 — 2026-11-12

### Files touched
| File | Required because |
|---|---|
| `pyproject.toml`, `uv.lock` | `uv add "PyJWT[crypto]>=2.10.1"` (resolved pyjwt 2.15.1, cryptography 50.0.2) — AC1, AC5, A1 |
| `app/core/jwt_keys.py` (new) | P-256 key parsing (fixed messages, `from None`), RFC 7638 `thumbprint`, `public_jwk`, frozen `KeySet` (`signing_key`/`jwks_body` `repr=False`, `sign`), `build_key_set` (ephemeral on `None`, duplicate-kid rejection) — AC5, AC6 |
| `app/core/config.py` | `jwt_signing_key`, `jwt_public_keys_extra`, `jwt_issuer`, `access_token_ttl_seconds` (60–3600), `token_*`/`jwks_*` rate-limit settings; validator `_jwt_keys_gate` (parse at startup, staging/production require key + explicit `https://<host>` issuer) — AC5, R5 |
| `app/core/rate_limit.py` | scopes `token`, `jwks` — AC1, AC6 |
| `app/main.py` | `app.state.key_set` + startup log (kids only / `jwt_signing_key_ephemeral`), JWKS router at root, `_CredentialedCORS` skipping `/.well-known/` — AC5, AC6 |
| `app/modules/tokens/__init__.py`, `errors.py`, `schemas.py`, `deps.py`, `service.py`, `jwks_router.py` (new) | `TokenService.issue` (plan §3 check order), new error codes, `TokenRequest` (`extra="forbid"`), `KeySetDep`, JWKS endpoint (ETag/304, `public, max-age=300`, `ACAO: *`, `application/jwk-set+json`) — AC1–AC6, R1 |
| `app/modules/auth/router.py` | `POST /auth/token` (cookie class: `rate_limit("token")` → `AppContextDep` → `DeviceDep`), forwards `device.new_cookie`, `no-store` + `Pragma` — AC1, R3 |
| `.env.example` | 8 new keys, commented, no values — R5 |
| `tests/conftest.py` | per-session test P-256 `JWT_SIGNING_KEY` and `JWT_ISSUER=https://api.test.invalid` before the app import; exports `TEST_JWT_SIGNING_KEY`, `TEST_JWT_ISSUER` |
| `tests/jwt_verifier.py` (new) | reference verifier mirroring `docs/consumers.md` — AC8 |
| `tests/test_jwt_keys.py`, `tests/test_auth_token.py`, `tests/test_token_verification.py`, `tests/test_jwks.py` (new); `tests/test_config.py` | planned tests — AC5, AC6, AC8 |
| `docs/adr/0005-access-token-signing.md` (new) | ADR — AC7 |
| `docs/consumers.md` (new) | consumer verifier checklist + SPA rules, ricotdin example — AC7 |
| `docs/deploy.md` | required config (`JWT_SIGNING_KEY`, `JWT_ISSUER`, `JWT_PUBLIC_KEYS_EXTRA`), owner action in deployment repo, "Signing key rotation" (planned + emergency) — AC7 |
| `docs/identity-architecture.md` | §4 `/token` + JWKS rows → USR-03c; "Token (D7)" line → ES256, `typ`, `jti`, `JWT_ISSUER`; S7 reference — AC7 |

### Tests added / changed
- `test_jwt_keys.py` › `test_parse_private_key_roundtrip_and_kid_is_rfc7638` (PKCS#8, SEC1), `test_parse_rejects_garbage_non_base64_and_wrong_curve` (not-base64, not-pem, p384, ed25519, rsa), `test_public_key_in_signing_slot_rejected`, `test_private_key_in_extra_rejected`, `test_parse_public_key_rejects_wrong_curve`, `test_duplicate_kid_rejected`, `test_jwks_members_allowlist`, `test_key_set_repr_has_no_private_material`, `test_ephemeral_key_set_when_none`
- `test_config.py` › `test_jwt_signing_key_required_in_production` (staging, production), `test_jwt_signing_key_malformed_rejected_without_echo` (key, extras), `test_jwt_issuer_required_in_production` (unset, http, trailing slash, port, empty × staging/production), `test_jwt_keys_optional_locally`, `test_access_token_ttl_bounds` (59, 3601)
- `test_auth_token.py` › `test_token_happy_path`, `test_token_roles_exclude_other_apps`, `test_token_two_accounts_one_device_independent`, `test_token_unknown_account_401_keeps_cookie`, `test_token_other_device_account_401`, `test_token_revoked_account_401`, `test_token_previous_cookie_newer_account_401`, `test_token_previous_cookie_older_account_200`, `test_token_disabled_user_401`, `test_token_email_unverified_403`, `test_token_not_member` (open/approval/closed), `test_token_expired_assignment_not_member`, `test_token_audience_mismatch`, `test_token_admin_audience_refused`, `test_token_org_id_422`, `test_token_body_validation_422` (5 cases), `test_token_missing_cookie_401`, `test_token_missing_app_header_403`, `test_token_bad_origin_403`, `test_token_unknown_app_403`, `test_token_disabled_app_403`, `test_token_rotation_forwards_set_cookie` (strict), `test_token_error_after_rotation_rolls_back` (strict), `test_token_rate_limited_429`, `test_token_logs_no_secrets`
- `test_token_verification.py` › `test_reference_accepts_valid` (control), `test_reference_rejects_tampered_payload`, `_other_key_same_kid`, `_alg_none`, `_hs256_key_confusion`, `_expired`, `_wrong_audience`, `_wrong_issuer`, `_unknown_kid`, `_typ_jwt`
- `test_jwks.py` › `test_jwks_public_keys_only`, `test_jwks_headers`, `test_jwks_304_on_etag` (exact, `W/`, list), `test_jwks_lists_extra_keys`, `test_jwks_no_credentials_with_cors_origin` (with a `/healthz` control proving the credentialed middleware is active), `test_jwks_rate_limited_429`, `test_jwks_body_stable_across_requests`

### Checks (actual output summary)
Local Postgres was already reachable on `localhost:5432` (no `service postgresql start` needed).
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 1: ruff check "All checks passed!", ruff format "258 files already formatted", mypy "Success: no issues found in 69 source files", pytest "718 passed in 32.67s".
- Run 2 (same command): identical, pytest "718 passed in 32.05s".
- `alembic check` (against `org_admin_test`, at head): "No new upgrade operations detected." (no schema change; the dev DB `org_admin` is not migrated locally, so `alembic check` there reports "Target database is not up to date", unrelated).

### Out-of-plan files
None. (`tests/test_rate_limit.py` does not enumerate scopes, so it was not changed, as the plan allows.)

### Deviations from plan
- D-1/D-2/D-3 as recorded in plan.md (`app_disabled` unreachable → `origin_not_allowed`; new codes `audience_mismatch`, `audience_not_supported`, `org_not_supported`; ES256).
- `KeySet` also carries `extra_kids` (for the startup log line `extra_kids=[…]` the plan asks for).
- `_jwt_keys_gate` reports extras errors with a combined message ("JSON list of distinct base64 PEM P-256 public keys, none equal to the signing key") rather than two separate messages; still fixed text, no echo.
- `test_token_verification.py` signs test tokens with a local `KeySet` built from the conftest key (same key and `kid` as the served JWKS, asserted in `test_reference_accepts_valid`) rather than calling `/token` for each case; the served JWKS is still used for verification.
- `test_token_logs_no_secrets` re-enables the `app.modules.tokens.service` / `app.modules.sessions.service` loggers via `monkeypatch`: see follow-up 1.
- `docs/consumers.md` Python sketch is formatted by `ruff format` (ruff formats Markdown code blocks).

### Follow-ups (noticed, not done)
1. **Log assertions are vacuous suite-wide:** `migrations/env.py` calls `fileConfig(alembic.ini)` from the session-scoped `_migrate_db` fixture, which disables every logger that already exists (`disable_existing_loggers=True`), i.e. all `app.*` loggers imported by conftest. Existing "no secret in logs" tests (e.g. `test_auth_login.py`) therefore pass without the app logging anything. Fix: `fileConfig(..., disable_existing_loggers=False)` in `migrations/env.py` (or reset in conftest).
2. F1–F5 from plan.md (ADM-002 admin audience/TTL/origin exclusivity; USR-03e keep `/.well-known/` uncredentialed; owner deployment-repo secret + `JWT_ISSUER`; `last_used_at` touch; CLAUDE.md R11 cookie text).
3. **Release blocker (owner, F3):** the deployment repo must provide `JWT_SIGNING_KEY` and `JWT_ISSUER` before the release containing USR-03c, or `migrate` refuses to start.

### Scope self-check
- Could the diff be smaller? No — every file maps to a plan row; no refactors.
- Diff size: tracked files +344 / −8 (incl. 79 lines `uv.lock`); new files ~1,385 lines (production ~350, tests ~880, docs ~160).
