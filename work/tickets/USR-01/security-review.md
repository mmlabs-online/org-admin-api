# Security review — USR-01

## Attempt 1 — 2026-11-11

**Verdict: no 🔴 blockers.** Reviewed `git diff origin/main...HEAD` at 90b08bb. `make check` passes (ruff, format, mypy 36 files, 50 pytest). R3/R5/R6/R11 are met for this slice: Argon2id with t=3/m=64 MiB/p=4 defaults and an OWASP floor enforced in staging/prod, hashing in a worker thread capped by `CapacityLimiter(2)`, NFKC without trimming, 128-char `SecretStr` cap before hashing; 422 details reduced to `type/loc/msg`; `User.__repr__` hides the hash; Turnstile fails closed (missing secret, Cloudflare test secret in prod, timeout, non-200, non-JSON); `X-Forwarded-For` never read, `CF-Connecting-IP` only from trusted CIDRs, IPv6 bucketed by /64; signup hashes before touching the DB and detects duplicates via the citext unique constraint (timing roughly constant between 201 and 409, no pre-check race). The only new public endpoint is `POST /api/v1/auth/signup`, public by design (R3), per-IP limited and Turnstile-gated.

### Findings

1. 🟡 **Hostname check silently off in production when `TURNSTILE_ALLOWED_HOSTNAMES` is empty** — `app/modules/auth/turnstile.py:710-713` (and `app/core/config.py:106` "empty = hostname not checked"). **Risk:** the only Turnstile path that fails open: if prod is deployed without the allowlist, any token minted for our sitekey on any hostname is accepted. ADR-0002 relies on the deploy remembering to set it, but nothing enforces it. **Fix:** in `verify`, fail closed when the allowlist is empty in production:
   ```python
   allowed = self._settings.turnstile_allowed_hostnames
   if not allowed and self._settings.app_env == "production":
       logger.error("turnstile allowed hostnames not configured")
       return False
   if allowed and body.get("hostname") not in allowed:
   ```
   (do the empty check before the network call, next to `_secret()`, so no siteverify request is made), plus `test_empty_hostname_allowlist_fails_closed_in_production`.

2. 🟡 **IPv4-mapped IPv6 addresses mishandled in client-IP resolution** — `app/core/client_ip.py:52-55` and `:66-72`. **Risk:** (a) if the peer arrives as `::ffff:172.18.0.3` (dual-stack bind or a future proxy change), it won't match IPv4 trusted CIDRs → header ignored → all users share one bucket (signup DoS). (b) If `CF-Connecting-IP` is ever IPv4-mapped, `ip_key` buckets it into `::/64` → all such clients share one bucket. **Fix:** unwrap in both places: `if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped: addr = addr.ipv4_mapped` — applied to `peer_ip` before the CIDR test and to the parsed header / `ip_key` input. Also pre-parse `trusted_proxy_cidrs` into `ip_network` objects once (settings validator or limiter construction) instead of re-parsing every CIDR per request.

3. 💭 **Rate-limiter eviction is O(n log n) on the event loop and can be used to evict other counters** — `app/core/rate_limit.py:256-264`. **Risk:** once the 10,000-key cap is reached with no expired keys, every new key triggers a full scan + sort of 10k entries (several ms blocking the loop per request) and evicts the oldest legitimate counters. Requires >10k distinct /64s or IPv4s within one window, so low today. **Fix:** keep windows in a `collections.OrderedDict` (all windows in a scope share one length, so insertion order = expiry order): on insert `move_to_end(bucket)`, then `while len(d) > max_keys: d.popitem(last=False)`, plus drop expired from the front — O(1) amortised.

4. 💭 **Uvicorn proxy-header rewriting not pinned off** — `Dockerfile` CMD (not in this diff). **Risk:** uvicorn's `--proxy-headers` is on by default with `FORWARDED_ALLOW_IPS` (default 127.0.0.1). If the deploy ever sets `FORWARDED_ALLOW_IPS=*`, uvicorn rewrites `request.client.host` from `X-Forwarded-For`; a client could then claim a trusted-CIDR peer and pick its own `CF-Connecting-IP`, defeating the "XFF never trusted" design. **Fix:** add `--no-proxy-headers` to the uvicorn CMD (or record it in ADR-0002). Follow-up.

5. 💭 **Email fragments can still appear in 422 `msg`** — `app/core/errors.py:194-196`. **Risk:** stripping `input`/`ctx` works (tested), but `EmailStr`'s `msg` carries `email_validator` text, which for some errors quotes the offending characters or domain (e.g. "The domain name x.local is a special-use…"). At most a domain or a character, never the full email or the password, so acceptable under R11. **Fix (optional):** for `loc[-1] == "email"`, replace `msg` with a fixed "Invalid email address".

6. 💭 **128-char cap is counted before NFKC** — `app/modules/auth/schemas.py:584`. **Risk:** NFKC can expand some code points up to ~18× (U+FDFA), so the normalized password can reach ~2,300 code points (~9 KB). Argon2 cost does not depend meaningfully on input length, so not a DoS. **Fix:** none needed; optionally reject when `len(normalize_password(pw)) > 128` in `PasswordPolicy` to keep the documented limit exact.

7. 💭 **Migration may drop a `citext` it didn't create** — `migrations/versions/20261111_7b847bdc5230_create_users.py:880-881`. **Risk:** if the DBA pre-created `citext` in `org_admin`, `downgrade` drops it; it can't cascade, so it fails safely if other objects depend on it. **Deploy requirement:** `upgrade` needs CREATE on the database for role `org_admin` (citext is trusted on PG ≥ 13, no superuser needed). Confirm the deployment repo's role setup grants it, or `alembic upgrade` fails at deploy (loud failure, not a security issue).

8. 💭 **Signup 409 confirms an email is registered** — `app/modules/auth/service.py:638-639`. **Risk:** account enumeration; throttled by the per-IP limit and Turnstile, and the 409 body carries no id or email. Accepted in the plan (A1) with the non-enumerating 202 alternative logged as F2. The owner must decide before signup is released (R9: no breaking change inside v1).

9. 💭 **Production starts without a Turnstile secret** — `app/core/config.py:104-105`. **Risk:** every signup returns 403 and logs an error. This fails closed as Assumption A5 intends, but a misconfigured deploy shows up only in logs. **Fix (optional):** a `/readyz` signal or a startup warning.

**Dependencies checked:** argon2-cffi 25.1.0, argon2-cffi-bindings 26.1.0, cffi 2.1.1, pycparser 3.12, email-validator 2.3.0, dnspython 2.9.2 are new; httpx moved from dev to runtime. All expected for this feature and locked in `uv.lock`; no unexpected transitive additions. The bundled SecLists file is MIT and listed in `THIRD_PARTY_NOTICES.md`.

**No issues found:** `SecretStr` for secrets; logs carry only status codes, exception class names and Cloudflare error codes; the fake Turnstile verifier is reachable only through test `dependency_overrides` (`create_app` always wires the Cloudflare verifier); `extra="forbid"` blocks mass assignment; the rate limiter runs before body-validation errors, so malformed probes are counted (tested); the Turnstile token is capped at 2048.

### Follow-ups (out of scope)
- **F-a:** `--no-proxy-headers` in the Dockerfile uvicorn CMD (finding 4).
- **F-b:** request body size limit (middleware or Cloudflare rule); FastAPI reads the whole body before the rate-limit dependency runs. Already applies to every endpoint.
- **F-c:** USR-03 must reuse `normalize_password`, verify against a dummy hash for unknown emails (timing), and call `needs_rehash` on login.
- **F-d:** Turnstile `action` check once clients set an action (plan F7).
- **F-e:** non-enumerating signup decision (plan F2); per-email send limits on the first verification mail in USR-04 (plan F6).
- **F-f:** email homoglyph / Unicode policy: `email_validator` allows SMTPUTF8 local parts by default — decide `allow_smtputf8=False` or confusable checks before users appear in staff UIs.
- **F-g:** shared rate-limiter store before running more than one worker or replica (ADR-0002).
- **F-h:** existing CORS uses `allow_methods=["*"]`, `allow_headers=["*"]` with credentials; align with the R11 `apps.origins` / `X-MM-App` model when cookie auth arrives (USR-03).
