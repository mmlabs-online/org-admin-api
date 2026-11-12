# ADR-0002: Identity abuse controls at runtime (rate limit, client IP, Turnstile, Argon2)

Status: Accepted (USR-01 plan, 2026-11-11)
Amended 2026-11-11 (ADM-011): §1, §2, Consequences.
Amended 2026-11-12 (USR-03): §5, Consequences.
Date: 2026-11-11

## Context
Sign-up (USR-01), and later login and forgot-password (USR-03/USR-04), are public endpoints
(R3 class 3). They need a per-IP rate limit, Cloudflare Turnstile and Argon2id hashing (R11,
identity-architecture.md §5 S3). The API runs as **one uvicorn process in one container**, behind
Cloudflare Tunnel (`cloudflared`), on a small Intel Mac.

## Decision
1. **In-process rate limiter.** `app/core/rate_limit.py`: fixed window per `(scope, ip_key)`, limits
   from settings, `rate_limit("<scope>")` route dependency, 429 `rate_limited` + `Retry-After`.
   Each scope has its own store, in window-end order, capped at 10 000 keys
   (`max_keys_per_scope`); eviction is O(1) amortised (expired entries, then the oldest, popped
   from the front), so flooding one scope never evicts another scope's buckets. Within one scope
   an attacker with many keys (e.g. rotating IPv6 /64s) can still evict live buckets (fail-open)
   until a shared store exists. Scopes are a fixed `Literal`; an unknown scope fails when the
   route dependency is built. Counters reset on restart.
   This is only correct for a single process: **before running more than one worker or replica,
   replace it with a shared store** (Postgres UNLOGGED table or Redis) behind the same `RateLimiter`
   protocol.
2. **Client IP.** The peer address, or `CF-Connecting-IP` only when the peer is inside
   `TRUSTED_PROXY_CIDRS` (cloudflared's Docker network). `X-Forwarded-For` is never read. IPv6 is
   bucketed by /64. With `TRUSTED_PROXY_CIDRS` unset in production, every client would share one
   bucket, so staging/production refuse to start (API and `migrate`) when it is empty or holds a
   non-private network, a `/0`, an IPv4 prefix shorter than /8 or an IPv6 prefix shorter than /48
   (ADM-011). Settings errors never print input values. uvicorn runs with `--no-proxy-headers`,
   so it never rewrites the peer address from `X-Forwarded-*`.
3. **Turnstile fails closed.** Missing secret, Cloudflare test secret in production, network error,
   timeout (5 s), non-200 or non-JSON → verification fails (403 `turnstile_failed`). A missing secret
   does not stop the app from starting. Only error codes are logged — never the token or secret.
4. **Argon2id off the event loop.** Hash/verify run in worker threads behind an anyio
   `CapacityLimiter` (`ARGON2_MAX_CONCURRENCY`, default 2) to bound CPU and memory
   (≈ 2 × 64 MiB). Parameters come from settings; staging/production refuse values below the OWASP
   floor (m = 19 MiB, t = 2).
5. **Login failure counters in Postgres** (USR-03, D11). Per-IP login limits use the in-process
   limiter (scope `login`, `LOGIN_RATE_LIMIT` per `LOGIN_RATE_WINDOW_SECONDS`, default 20/min).
   Per-email backoff does **not**: in memory it would reset on every deploy, fail open once 10 000
   keys are flooded (cheap with rotating IPv6 /64s, one request per new email) — which would also
   reset Turnstile-after-3, the main defence against distributed guessing — and could not be shared
   by a second worker. Instead table `login_failures (email_hmac PK, failures, last_failed_at,
   blocked_until)`, keyed by `HMAC-SHA256(IDENTITY_HMAC_KEY, normalized email)` so no email (or an
   unsalted, brute-forceable email hash) is stored. One upsert per **failed** login; the row is
   deleted on success; the counter restarts at 1 after 24 h without failures. Unknown emails are
   counted exactly like known ones. Backoff: failure 5 → 1 min, 6 → 2, 7 → 4, 8 → 8, ≥ 9 → 15
   (cap), answered with 429 `rate_limited` + `Retry-After` before any Argon2 work; Turnstile is
   required once an email has ≥ 3 failures. The failure is committed before the 401 is raised
   (ADR-0004). No hard lockout.

## Consequences
- USR-03/USR-04 reuse the limiter (new scopes), client-IP resolution and the hasher.
- The deployment must set `TRUSTED_PROXY_CIDRS` (the app refuses to start without it in
  staging/production, see `docs/deploy.md`), `TURNSTILE_SECRET_KEY` and
  `TURNSTILE_ALLOWED_HOSTNAMES` before sign-up is released.
- Scaling out requires the shared limiter first.
- The deployment must set `IDENTITY_HMAC_KEY` (staging/production refuse to start without it);
  rotating it resets every login failure counter. Stale `login_failures` rows need a cleanup job
  (follow-up).
