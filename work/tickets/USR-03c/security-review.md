# Security review — USR-03c

## Attempt 1

**Verdict: no 🔴 blockers.** I found 2 🟡 should-fix items, both in the docs and runbook, plus some 🟢 notes. I ran only the allowed subset (`tests/test_jwt_keys.py`, `tests/test_token_verification.py`, `tests/test_jwks.py`): 31 passed. I did not run the full suite or `make check`; the reviewer owns that.

### What I checked and found sound
- **Algorithm pinning and alg confusion.** The server only signs; it never decodes tokens. Signing is fixed to `ALGORITHM = "ES256"` (`app/core/jwt_keys.py:23`). The header is `{"alg","kid","typ":"at+jwt"}` (`jwt_keys.py:92-97`). The reference verifier pins `algorithms=["ES256"]` (`tests/jwt_verifier.py`). It is tested against `alg=none`, HS256 key confusion, a different key under the same `kid`, an unknown `kid` and `typ=JWT` (`tests/test_token_verification.py:84-133`).
- **`kid` handling.** The `kid` is an RFC 7638 thumbprint: sorted members, fixed 32-byte coordinates (`jwt_keys.py:58-72`). It is never configured. A duplicate key, or an extra key equal to the signer, is refused (`jwt_keys.py:111-113`).
- **Key material is never logged or echoed.**
  - Parse errors use a fixed message and `from None` (`jwt_keys.py:40-55`).
  - `Settings` has `hide_input_in_errors=True` (`app/core/config.py:24`), and the `_jwt_keys_gate` message is fixed.
  - I confirmed this empirically: malformed private keys, malformed extras and invalid JSON in `JWT_PUBLIC_KEYS_EXTRA` all produce errors with no key bytes and no PEM marker.
  - `KeySet` sets `repr=False` on the key and the body (`jwt_keys.py:84-87`).
  - Startup logs show only the kids (`app/main.py:68-75`).
  - `token_issued` logs ids, aud, kid and jti, but never the token or email (`app/modules/tokens/service.py:97-104`).
  - The test signing key is generated per run, not committed (`tests/conftest.py`).
- **No private members in the JWKS.** `public_jwk` builds an allow-list without `d` (`jwt_keys.py:75-78`). Tests assert the exact member set (`tests/test_jwks.py:15-24`, plus the extras test).
- **Audience and origin binding.**
  - `AppContextDep` runs before `DeviceDep`. It requires an active app plus an `Origin` registered for that app, with loopback origins dropped in staging/production (`app/modules/auth/service.py:89-106`).
  - `audience` must equal `X-MM-App` (`service.py:57-58`).
  - `admin` is refused before any account lookup (`service.py:59-60`).
- **Account-session isolation.**
  - `get_active_account` filters by `device_id`, not revoked, and honours `accounts_created_before`, the grace-window fixation cutoff (`app/modules/sessions/repository.py:120-135`, `service.py:66-68`).
  - Unknown, foreign or revoked accounts and disabled users all give the same 401 `session_invalid`, so there is no oracle.
  - `email_unverified` and `not_member` can only be reached for the device's own active account.
  - An AppError after cookie rotation rolls the rotation back (`get_session` rollback). This is tested (`test_token_error_after_rotation_rolls_back`).
- **Claims.**
  - `sub` is the user id, not the account-session id.
  - `aud` is a single string.
  - `roles` gets a second filter on the app prefix (`app/modules/rbac/authz.py:40-43`).
  - `email_verified` is only `true` after the explicit check.
  - `jti` is uuid4, and the TTL is bounded to 60–3600.
  - The token response sets `Cache-Control: no-store` and `Pragma: no-cache`.
- **CORS exemption scope.** It is a prefix check on the raw `scope["path"]` for `/.well-known/` (`app/main.py:29`). JWKS is the only route under that prefix. Starlette and uvicorn do not normalise `..`, so a path like `/.well-known/../api/...` cannot reach another route. JWKS sends `ACAO: *` without credentials, and credentialed CORS stays active on other paths (tested, `tests/test_jwks.py:63-72`).
- **Dependency.** The lock resolves `pyjwt 2.15.1` and `cryptography 50.0.2`. The floor `>=2.10.1` excludes 2.10.0 (CVE-2024-53861).
- **Startup refusal.** Staging/production refuse to start without a valid key or with a non-`https://<host>` issuer. The issuer must be explicitly set (`model_fields_set`) (`config.py:173-182`).

### Findings

🟡 **S1: The emergency rotation runbook overstates how fast a leaked key stops working.** `docs/deploy.md:113` says "Live tokens then fail at verifiers". That is not guaranteed.
- **Risk:** Verifiers keep the leaked public key in their cached JWKS until the cache expires (up to 1 h per `docs/consumers.md:42-43`, 300 s in the PyJWKClient sketch) or until an unknown-`kid` refetch replaces the set. In that window an attacker holding the leaked private key can mint tokens with the old `kid`, and verifiers accept them. A consumer that sets `PyJWKClient(cache_keys=True)` keeps the per-`kid` key for the life of the process. A Cloudflare cache rule on `/.well-known/` would add `max-age=300` more.
- **Fix:** In the emergency section, state that the exposure window is the consumers' JWKS cache lifetime. Add the step "restart or flush the JWKS cache of every consumer (ricotdin, …) right after the redeploy". In `docs/consumers.md`, add "do not use `cache_keys=True`; the JWKS cache must be flushable".

🟡 **S2: The PyJWKClient sketch does not enforce "refetch at most once a minute", which leaves an unknown-`kid` refetch-amplification DoS.** The checklist says it at `docs/consumers.md:43`, but the sketch at `docs/consumers.md:65-70` does not set `cooldown_duration`.
- In the locked 2.15.1 the default cooldown is 30 s, which is not "once a minute".
- A consumer on an older version that still meets the stated floor (`>=2.10.1`, `consumers.md:50`) may have no cooldown at all. I have not verified which PyJWT release added `cooldown_duration`.
- **Risk:** If there is no cooldown, every forged token with a random `kid` makes the consumer refetch the JWKS. Our per-IP JWKS limit (120/min, `config.py`) then returns 429 to that consumer's egress IP, and legitimate verification breaks. That is a cheap DoS of the consuming app.
- **Fix:** Add `cooldown_duration=60` to the sketch. Raise the consumer floor in item 9 to the first PyJWT version that has `cooldown_duration` (2.15.1 has it; verify the exact release), or tell consumers to throttle refetches themselves.

🟢 **N1: `UnsupportedAlgorithm` is not caught.** `parse_private_key_b64` and `parse_public_key_b64` catch `(ValueError, TypeError, binascii.Error)` (`app/core/jwt_keys.py:40`, `:51`). `cryptography.exceptions.UnsupportedAlgorithm` is not a `ValueError` subclass. It escapes the `Settings` validator as a raw traceback instead of the fixed message. Its message holds no key material, so this is hardening only: add it to the tuple.

🟢 **N2: The key-generation command prints the private key to the terminal.** The command at `docs/deploy.md:76` writes the base64 private key to stdout, so it can end up in scrollback or the clipboard. Suggest piping it straight into the secret store, for example `… | tr -d '\n' | gh secret set ORG_ADMIN_API_JWT_SIGNING_KEY --repo mmlabs-online/deployment`.

🟢 **N3: The JWKS 429 response has no `Access-Control-Allow-Origin: *`.** Browser callers cannot read `Retry-After`. App backends, the intended consumers, are not affected. Cosmetic.

🟢 **N4: The ephemeral key fallback depends on `APP_ENV` being set correctly.** It applies whenever `APP_ENV` is `local`/`test`, which is the default when it is unset. A production deploy that drops `APP_ENV` would sign with a per-process ephemeral key and `iss=http://localhost:8000`. This fails closed, because consumers pin `iss` and the `kid` is unknown to them. Each uvicorn worker would also publish a different JWKS. The existing gates in `config.py` follow the same pattern, so this is not a regression; the warning log at `app/main.py:69` covers it.

### Follow-ups (out of scope)
- **F1:** Decide whether staging/production should fail closed when `APP_ENV` is not explicitly set. Today only the admin-API flags require it to be explicit.
- **F2:** When `org` claims land (USR-06), re-check `roles` for cross-org leakage and keep `org_not_supported` until then.
- **F3:** When ADM-002 enables `aud=admin`, re-review: separate TTL, staff 2FA, and origins that belong to one app only (ADR-0005 §8).
- **F4:** Consider monitoring alerts on `jwt_signing_key_ephemeral` in any non-local log stream.
