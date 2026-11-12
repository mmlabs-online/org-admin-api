# consult-identity-access-engineer — USR-03

### Findings
1. `get_session` rolls back whenever a request raises (`app/db/session.py`). If the code that detects cookie reuse revokes the device session and then raises a 401, the revocation is undone. The same thing happens to a failed-login counter stored in the DB.
2. `effective_roles` does not check app status or user status (see the `AuthzService` docstring). It also returns `[]` for non-members, so `/token` has to check those cases itself.
3. `main.py` adds a global `CORSMiddleware` from `settings.cors_origins` with `allow_headers=["*"]` and credentials. That conflicts with D12, which says CORS comes from `apps.origins` per request.
4. The ticket's `403 {error:"not_member", join_available}` does not match the R6 envelope.
5. A 60 s grace for the old cookie gives a session-fixation window. A compromised `*.mmlabs.online` subdomain can plant a cookie (cookie tossing). The `__Host-` prefix would block that, but it requires `Path=/`, which conflicts with D13.

### Recommendations
- **Cookie and hash (AC2).** `secrets.token_bytes(32)` encoded base64url. Store the SHA-256 as `bytea`. Put a unique index on `token_hash` and a partial unique index on `prev_token_hash`. A slow hash is not needed because the token is high-entropy. If the request carries more than one `mm_device` cookie, return 401 and do not treat it as reuse.
- **Rotation (AC4).** Look up by `h = sha256(cookie)`, matching either `token_hash` or `prev_token_hash`:
  - Revoked or not found: 401 and clear the cookie.
  - Matches `token_hash`: if `now − rotated_at ≥ 24h`, rotate with a compare-and-swap: `UPDATE … SET prev_token_hash=token_hash, prev_valid_until=now()+60s, token_hash=:new, rotated_at=now() WHERE id=:id AND token_hash=:h RETURNING`. If 0 rows come back, another request rotated first. Re-read and handle it as "matches prev within grace".
  - Matches prev and `now ≤ prev_valid_until`: accept, but do **not** rotate again and do not send `Set-Cookie`. This includes `/login`. Otherwise two logins in parallel can each set a cookie, the browser may keep the older one, and the device gets revoked by mistake.
  - Matches prev after the grace: this is reuse. Revoke the device session and all its account sessions, commit before returning the 401 (finding 1), and log a structured event.
  - Use the database clock, as the RBAC repository already does.
- **Fixation.** At login, if the presented device session has no active account sessions, mint a new device session instead of rotating. When a request authenticates with the prev value during the grace, allow only account sessions created before `rotated_at`.
- **Login (AC3, AC8).**
  - Normalise the email the same way signup does.
  - Order of checks: per-IP limit (new `login` scope, about 20/min) → per-email backoff (429 `login_backoff` with `Retry-After`; no Argon2 run and no counting) → Turnstile when failures ≥ 3 (missing token: 403 `turnstile_required`; invalid: `turnstile_failed`) → Argon2.
  - Unknown email: verify against a dummy hash made at startup with the current parameters, through the same `CapacityLimiter`.
  - A disabled user still goes through the password check, then gets the same generic 401 and does not reset the counter.
  - On success: rehash if `needs_rehash`, and reset the counter.
  - Account sessions: `INSERT … ON CONFLICT (device_session_id, user_id) WHERE revoked_at IS NULL DO UPDATE SET last_used_at`.
  - Login should also require `Origin` and `X-MM-App` (§2 step 1), which closes login CSRF.
- **Backoff storage.** Use a DB table `login_failures(email_hash pk, failures, last_failed_at, blocked_until)`, reusing the email-hash helper planned for `email_send_log`. Increment atomically with `UPSERT … failures+1 RETURNING`, and write it in a separate session or commit (finding 1).
  - The block lasts `min(2^(f−5), 15)` minutes once f ≥ 5.
  - Counters for unknown emails go in the same table, so the responses look identical.
  - Why not memory: the ADR-0002 in-memory store loses counts on restart, and filling its 10k keys can evict a target's bucket (fail-open). Amend ADR-0002 §1 to record this.
- **Token (AC5).**
  - Require `audience == X-MM-App` and `Origin ∈ origins` of that app. Otherwise a compromised app could mint tokens for every other app.
  - Check order: device/account session (account on another device, revoked, or user disabled → 401) → app unknown or disabled → 403 `app_disabled` → 403 `email_unverified` → roles empty → 403 envelope with `code:"not_member"`, `details:{join_available: join_policy != "closed"}`.
  - `org_id` not null → 422 until USR-06.
  - Claims: `iss` (from settings), `aud`, `sub`, `email`, `email_verified`, `roles`, `iat`, `exp` (900 s). Header `typ: at+jwt`.
- **JWT library.** PyJWT[crypto], which is maintained. Reject python-jose, which is unmaintained. joserfc would also do, but PyJWT is the smaller dependency surface. Use EdDSA (Ed25519) and pin the allowed algorithms when verifying.
  - Keys: PKCS8 PEM in a `SecretStr` env var (current signing key), plus an optional list of extra public keys to publish in JWKS during rotation. `kid` = RFC 7638 thumbprint.
  - Staging and production refuse to start without a valid key.
  - JWKS response: `Cache-Control: max-age=300`.
  - Rotation steps: publish the next key, wait at least the cache TTL, switch the signing key, keep the old public key for at least TTL + 15 min.
- **Logout (AC6).** The body must be exactly one of `account_id` or `all`; otherwise 422. An `account_id` from another device returns 204 and changes nothing, so it cannot be used to probe. `{all:true}` revokes the device and its accounts and sends `Max-Age=0` with the same `Path` and no `Domain`. Document that JWTs already issued stay valid for up to 15 min.
- **CORS (AC9).** Use a dedicated middleware for `/api/v1/auth`: echo the origin only if it is in `apps.origins` (cached with a short TTL), add `Vary: Origin`, and allow only the `Content-Type` and `X-MM-App` headers. Reject a missing `Origin` with 403.

### Risks
- A Set-Cookie response that gets lost means reuse after 60 s and a logout of every account on the device. This favours security over availability.
- Reuse is detected only one rotation back.
- Argon2 concurrency is capped at 2, so a login flood from many IPs causes queueing (a DoS).
- Requests carrying an old prev value revoke the device by design.
- Per-email backoff can be triggered against a victim on purpose, by design (D11).

### Open questions
1. Device-session idle and absolute expiry (suggestion: 30 days idle and 1 year absolute, with a persistent `Max-Age`)?
2. When does the failure counter reset (24 h after the last failure)?
3. Require Turnstile per IP after N failures as well, to slow password spraying?
4. Accept `Path=/api/v1/auth` without `__Host-`, or change D13?
5. EdDSA or RS256: do any consumers lack Ed25519 support?
