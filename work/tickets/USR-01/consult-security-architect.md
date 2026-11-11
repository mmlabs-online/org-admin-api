# consult-security-architect — USR-01

## Findings

1. **422 responses leak the password (High, R5/R11).** `_validation_error_handler` in `app/core/errors.py` returns `jsonable_encoder(exc.errors())`; Pydantic v2 includes `input` in each error, so a weak-password 422 echoes the submitted password, and an invalid-email 422 echoes the full email. A 422 on `email` also echoes the whole body dict (password included).
2. **Per-IP limiter would see one IP (High, D2/S3).** The image runs `uvicorn` without `--proxy-headers`; all traffic arrives via cloudflared, so `request.client.host` is the cloudflared container IP. A naive per-IP limit becomes one global bucket: ~20 signups/min locks out everyone. Conversely, trusting `X-Forwarded-For` lets attackers bypass the limit by rotating the header.
3. **Staff `/api/v1/users` endpoints would be public (Critical once real users exist).** The §6.1 accepted risk was made when public endpoints exposed only org metadata. USR-01 opens sign-up, so the table fills with real PII; without ADM-002, `GET /api/v1/users` is an email dump for anyone, and disable/enable lets anyone lock out any user (and later any staff member). This is materially larger than the risk the owner accepted.
4. **Sign-up 409 = account enumeration oracle (Medium, D11 spirit).** Login is careful to give identical errors, but signup's 409 tells an attacker an email is registered. Turnstile + per-IP limit raise cost but don't close it.
5. **Argon2 CPU/memory DoS (Medium).** argon2-cffi is synchronous; on the event loop each hash blocks it. N parallel requests × memory_cost (e.g. 64 MiB) can exhaust the Mac runner without a concurrency cap; unbounded password length.
6. **Timing oracle:** if the duplicate check runs before hashing, 409 returns in ~1 ms vs ~50–100 ms for 201.
7. **Email-bombing via later USR-04 hook:** once `on_signup` sends verification mail, anyone can sign up a victim's address and trigger mail to it, and burn the Gmail daily quota (§7 risk).

## Recommendations

- **AC 422/R11:** strip `input` and `ctx` from validation error details globally (keep `loc`, `msg`, `type`); type password as `SecretStr`. Add a test that the password string never appears in any 4xx body.
- **AC rate limiter / S3:** derive client IP from `CF-Connecting-IP` **only** when a setting like `TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP` is set and the peer is in `TRUSTED_PROXY_CIDRS`; otherwise fall back to `request.client.host`. Never use `X-Forwarded-For`. Key IPv6 by /64. Limiter behind an interface; in-memory sliding window is acceptable for v1 with one uvicorn worker, but it needs TTL eviction + a cap on stored keys, and the single-process limitation must be written down. 429 with `Retry-After`. Tests: spoofed `X-Forwarded-For` does not bypass; per-IP bucketing.
- **AC Turnstile:** order: rate limit → schema → Turnstile → hash → insert (bots never cost Argon2 time). Siteverify with `remoteip`, check `success` + expected `hostname`/`action`, short timeout, **fail closed**. Settings must refuse to start in `production` if the Turnstile secret is missing or the fake verifier is selected. `TURNSTILE_SECRET_KEY` in `.env.example` without value (R5).
- **AC passwords:** min 10, **max 128 chars (≈512 bytes UTF-8) checked before hashing**; Argon2id params from config; hash in `anyio.to_thread` behind a semaphore (e.g. 4) to bound CPU/memory. Rehash-on-login when params change (USR-03).
- **AC 409 / timing:** always hash before touching the DB, and detect duplicates via the citext unique-constraint `IntegrityError` rather than a pre-check — constant-ish timing and no race between concurrent signups. Keep the 409 body generic (no user id, no echoed email).
- **AC staff endpoints / R3:** build the repository/service/schemas/tests now, but **do not mount the router in production**: mount only when `ADMIN_USERS_API_ENABLED=true` (default false), and make startup fail if that flag is true while `app_env=production` and ADM-002 isn't present. Tests enable it via the app factory. ADM-002 removes the flag and adds `require_permission("admin:users:read"/"admin:users:write")`.
- **R5/R11 logging:** never log request bodies; log a masked email (`a***@d***.com`) or an HMAC of it if needed; never log the Turnstile token or the password hash; `User` repr must exclude the hash.
- **USR-04 hook:** pass `on_signup` the user id only; record now that USR-04 must apply its 1/min and 5/day per-email limits to the first verification mail too, not only resends.

## Risks

- Until ADM-002, existing admin endpoints (organizations) stay public; signup adds no new exposure only if the users router stays unmounted.
- In-memory limiter resets on every deploy and breaks if workers > 1; brief bursts after restart are possible.
- If someone exposes the container port directly (not only via the tunnel), `CF-Connecting-IP` becomes forgeable; the trusted-proxy CIDR check is the guard.
- Mass account creation can still fill `users` even with all of the above; unverified accounts can't join apps/get tokens (D2/D16), so the impact is clutter and email-bomb potential, not access.

## Open questions

1. **Signup 409 vs non-enumerating signup:** should signup return 409 (ticket) or the same 202 for new and existing emails, with USR-04 sending an "you already have an account" mail? The latter closes the enumeration leak but changes the AC and the v1 contract — must be decided before release (R9: no breaking changes in v1). Owner decision.
2. Owner agreement to keep `/api/v1/users` unmounted in production until ADM-002 (vs. explicitly extending the §6.1 accepted risk to user PII and the disable action).
3. Common-password source: bundled top-10k list vs offline HIBP k-anonymity check (outbound call; needs network allowlist and fail-open/closed definition).
4. Should unverified accounts be purged after N days to limit bot-signup clutter?
