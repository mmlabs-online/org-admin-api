# consult-software-architect: USR-03c

### Findings
1. **Reusable pieces are already in place.** USR-03c needs no new session code:
   - `AppContextDep` handles the Origin and `X-MM-App` checks.
   - `DeviceDep` returns `ResolvedDevice.accounts_created_before`, and `SessionRepository.get_active_account(..., created_before=)` already applies the N1 cutoff.
   - `AuthzService.effective_roles` returns the roles, but it does not check user status, email verification or app status. The token service has to check those itself.
   - `app/core/etag.py` (`strong_etag`, `if_none_match`) covers the JWKS ETag/304.
   - `rate_limit()` needs one new scope, `jwks`.
2. **There is no JWT or crypto library yet.** The only packages near this are `argon2-cffi` and `httpx`. `PyJWT[crypto]` would add `cryptography`. That is a new dependency, not a stack change (the stack table lists layers, not libraries), but the plan should still name it for the owner.
3. **The docs disagree on the algorithm.** `identity-architecture.md` line 146 says "EdDSA or RS256", while the USR-03 consults and the ticket say EdDSA or ES256. The new ADR must decide, and §4 must be updated to match.
4. **"Member" is not defined in code.** My reading is: at least one active app-scope role, i.e. `effective_roles(user, app)` is non-empty. The plan must state this.
5. **Two things block real tokens today.** Under D16 no user is verified until USR-04 ships, so `/token` returns 403 `email_unverified` for everyone, ricotdin included. Tests must create verified users with a factory.
6. **Static CORS will add credentials to the JWKS response.** `main.py` uses the static `CORSMiddleware` with credentials. For listed origins it will add `Access-Control-Allow-Credentials` on top of the `ACAO: *` the JWKS route sets. USR-03e must exempt `/.well-known/`.

### Recommendations
- **Module layout (R1):** create `app/modules/tokens/` with:
  - `keys.py`: a pure `KeySet`. It parses the PEMs, derives each `kid`, builds the public JWKS and is built once into `app.state.key_set`.
  - `issuer.py`: pure; claims in, JWT out.
  - `service.py`: `TokenService`, which checks the account session, user status, email verification, membership and roles.
  - `errors.py` and `jwks_router.py`. Mount the JWKS router in `create_app` without the prefix, like `health_router`.

  The `POST /token` route stays in `auth/router.py`, because it needs the cookie and app-context dependencies and the cookie handling, and it calls `TokenService`.
- **Key config (R5, R11):**
  - `JWT_SIGNING_KEY`: a `SecretStr` holding a PKCS#8 PEM. Also accept it base64-encoded, so it fits on one env line in Docker.
  - `JWT_VERIFY_KEYS`: a JSON list of public PEMs, used for the next or previous key during rotation. These are not secret.
  - `JWT_ISSUER`, e.g. `https://api.mmlabs.online`, and `ACCESS_TOKEN_TTL_SECONDS=900`.
  - `kid` is the RFC 7638 thumbprint, computed and never configured, so it cannot drift from the key.
  - Validate in a `model_validator`: the key parses, its curve matches the chosen algorithm, and there are no duplicate kids.
  - Staging and production refuse to start without a signing key and issuer, using the same pattern as `IDENTITY_HMAC_KEY`. Local and test generate a temporary key per process.
- **Algorithm: ES256 (P-256), which I recommend over EdDSA.**
  - Every JOSE library supports it, including PyJWT/`PyJWKClient` and `jose` in Node and the browser through WebCrypto.
  - It avoids two things that may change: the "EdDSA" name versus the "Ed25519" name in RFC 9864, and Ed25519 support in older Safari WebCrypto.
  - Signature size and speed are irrelevant at a 15-minute token lifetime.
  - EdDSA is also acceptable. Whichever is chosen, verifiers pin the single algorithm.
  - JWKS entries carry `kid`, `alg`, `use:"sig"` and the public fields only.
- **ADR-0005** "Access-token signing": record the algorithm, the PEM env format, the `kid` thumbprint, the rotation steps and the claims. Also state that `typ` is `at+jwt` without full RFC 9068 compliance (there is no `client_id` claim).
- **Rotation runbook** (`docs/deploy.md`):
  1. Add the next public key to `JWT_VERIFY_KEYS` and deploy.
  2. Wait at least the JWKS `max-age` (300 s) plus a margin for consumer caches.
  3. Swap `JWT_SIGNING_KEY` and move the old public key into `JWT_VERIFY_KEYS`.
  4. After the token lifetime plus clock skew (about 20 min), remove the old key.
  5. For an emergency (compromised key), skip step 2 and accept that live tokens fail.
- **Reference verifier test:** in the tests, verify with `jwt.PyJWKClient` against the app's JWKS, giving it `algorithms=[...]`, `audience` and `issuer`. That is exactly how ricotdin will verify.
- **Size:** about 350–420 production lines plus about 400 test lines. Keep it as one ticket; nothing splits out cleanly.

### Risks
- With more than one uvicorn worker, a temporary key per process breaks local tokens. Local runs use one worker, and production is protected by the startup refusal.
- `PyJWKClient` fetches with urllib's default User-Agent, which Cloudflare bot rules may block. ricotdin should pass `headers=`, and `mmlabs-auth` should set it.
- Token roles go stale for up to 15 minutes after a role is revoked or a user is disabled. This is accepted by D7, but the ADR should state it.
- Clock skew: issue `iat` from the server clock and recommend verifiers allow `leeway` of about 30 s.

### Open questions
1. ES256 or EdDSA? (I recommend ES256.) Also, line 146 of `identity-architecture.md` needs correcting either way.
2. Can the owner confirm that adding `PyJWT[crypto]` (and so `cryptography`) is acceptable?
3. Is "member" defined as "has at least one app-scope role"? And when a user has no roles, should the token still be issued with `roles: []`, or should it be `not_member`?
4. Should a mismatch between `audience` and `X-MM-App` get its own code (`audience_mismatch`) or reuse `origin_not_allowed`?
5. Is the `JWT_ISSUER` value the API's public URL? It must stay stable, because every consumer pins it.
