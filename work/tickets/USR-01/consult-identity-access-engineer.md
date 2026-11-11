# consult-identity-access-engineer — USR-01

## Findings

1. **422 responses would echo the password.** `_validation_error_handler` (`app/core/errors.py`) returns `jsonable_encoder(exc.errors())`; Pydantic puts the failing value in `input`, so a weak/too-long password in `/auth/signup` comes back in the body — violates R5/R11. The same handler serves login/reset later.
2. **Missing deps.** `pyproject.toml` has no `argon2-cffi`, no `email-validator` (needed for Pydantic `EmailStr`), and `httpx` is dev-only but Turnstile needs it at runtime.
3. **Client IP behind the tunnel is wrong.** The Dockerfile runs plain `uvicorn` (`docs/deploy.md`): uvicorn only trusts `X-Forwarded-For` from 127.0.0.1, so `request.client.host` will be the cloudflared container's Docker IP — a per-IP limiter would throttle **everyone together**.
4. **Single-process app on a small Intel Mac (Docker VM).** Argon2id is memory-hard and CPU-blocking; running it on the event loop stalls all requests.
5. **409 on duplicate email enables account enumeration.** The AC asks for it; this is a deliberate tradeoff against R11/D11's "identical error" principle (which is scoped to login). Turnstile + per-IP limit make bulk probing costly.

## Recommendations

- **AC2 / R11 — Argon2id:** `PasswordHasher` wrapper in `app/modules/identity/passwords.py`, params from settings: `ARGON2_TIME_COST=3`, `ARGON2_MEMORY_KIB=65536`, `ARGON2_PARALLELISM=4` (argon2-cffi/RFC 9106 defaults); refuse anything below OWASP floor (m≥19456 KiB, t≥2) at startup. Benchmark on the Mac, target ~200–500 ms; drop to m=19456,t=2,p=1 if too slow. Always hash via `anyio.to_thread.run_sync` behind a semaphore (e.g. 4) to cap peak memory ≈ 4×m. Expose `verify()` + `needs_rehash()` now so USR-03 rehashes on login. Exclude `password_hash` from every schema and `__repr__`.
- **AC2 — Password policy (one module, reused by login/reset):** `unicodedata.normalize("NFKC", pw)` before checking and hashing; never strip/trim. Length counted in code points: min 12 (configurable), max 128 — put `max_length` on the Pydantic field so oversized input is rejected before any hashing. No composition rules (NIST 800-63B). Common-password check: bundle a top-100k list (SecLists, MIT — add to `THIRD_PARTY_NOTICES.md`) as a gz file loaded once into a `frozenset`, compare lowercased. Also reject password == email or its local part. Put it behind a `BreachedPasswordChecker` interface so HIBP k-anonymity can be added later (fail-open); no outbound call in v1.
- **AC3 / R5 / R11 — Validation errors:** strip `input` (and `ctx` values) from every 422 `details` entry in all handlers; map password failures to code `weak_password` with a generic reason (`too_short`/`too_common`). Add a test asserting the password never appears in any response.
- **AC3 — Email:** `email-validator` with `check_deliverability=False` (no DNS), `allow_smtputf8=False` for v1, max 254. Trim + lowercase the whole address before storing (citext still guards uniqueness). Do **not** strip dots or `+tag`. Insert and catch `IntegrityError` → 409 (no check-then-insert race). Log emails only masked/hashed.
- **AC3 — Signup order (cheapest first):** schema validation → per-IP limit → Turnstile → password policy → hash → insert. A 409 thus costs the caller a Turnstile solve.
- **AC4 — Turnstile:** interface `TurnstileVerifier.verify(token, remote_ip, action)`; httpx impl POSTs to `https://challenges.cloudflare.com/turnstile/v0/siteverify` with `secret`, `response`, `remoteip`, `idempotency_key` (UUID, allows a retry). One shared `AsyncClient`, timeout ~5 s. Check `success` **and** `action == "signup"` and `hostname` in a configured allowlist. Token field `max_length=2048`. Bad token → 403 `turnstile_failed`. Siteverify timeout/5xx → **fail closed**. `TURNSTILE_SECRET_KEY: SecretStr` in `.env.example`; refuse to start in production if empty or a test key. Tests: fake via dependency override (no network); local dev: Cloudflare test secrets `1x0000000000000000000000000000000AA` (pass), `2x0000000000000000000000000000000AA` (fail), `3x0000000000000000000000000000000AA` (token spent).
- **AC5 — Client IP:** one `client_ip()` dependency in `app/core`: if the direct peer is in `TRUSTED_PROXY_CIDRS` (config; the tunnel/proxy Docker subnet), use `CF-Connecting-IP` (parsed with `ipaddress`); otherwise the peer address. Never trust `X-Forwarded-For`, never `--forwarded-allow-ips='*'`. Key IPv6 clients by /64. 429 carries `Retry-After`.

## Risks

- If container port 8000 is ever published on the host/LAN, a too-broad trusted CIDR lets anyone forge `CF-Connecting-IP`.
- In-memory limiter resets on restart and breaks with >1 worker; architect/db consult should decide on Postgres-backed state (same question for USR-03 per-email backoff).
- Fail-closed means a Cloudflare outage blocks signup — acceptable, but make it visible in logs.
- 64 MiB hashes on a low Docker-VM memory limit under burst could OOM the container; the semaphore bounds this.
- Any NFKC/policy rule must be shared with login and reset or users get locked out.

## Open questions

1. Min length 12 or 15? NIST 800-63B-4 asks 15 when password is the only factor; staff 2FA is still open (OQ3).
2. Status code when siteverify is unreachable: 503 (not in R6's list) or 403? And should R6 add 429?
3. Keep 409 on duplicate email (AC) or always return 201 and handle duplicates by email in USR-04 — needs owner sign-off.
4. Allowed Turnstile hostnames: static config list, or derive from `apps.origins` (the `apps` table isn't in this ticket)?
5. What is the cloudflared container's Docker subnet, and is port 8000 published on the Mac? Needs checking in `mmlabs-online/deployment`.
