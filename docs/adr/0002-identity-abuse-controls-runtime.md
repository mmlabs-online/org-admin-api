# ADR-0002: Identity abuse controls at runtime (rate limit, client IP, Turnstile, Argon2)

Status: Accepted (USR-01 plan, 2026-11-11)
Date: 2026-11-11

## Context
Sign-up (USR-01), and later login and forgot-password (USR-03/USR-04), are public endpoints
(R3 class 3). They need a per-IP rate limit, Cloudflare Turnstile and Argon2id hashing (R11,
identity-architecture.md §5 S3). The API runs as **one uvicorn process in one container**, behind
Cloudflare Tunnel (`cloudflared`), on a small Intel Mac.

## Decision
1. **In-process rate limiter.** `app/core/rate_limit.py`: fixed window per `(scope, ip_key)`, limits
   from settings, `rate_limit("<scope>")` route dependency, 429 `rate_limited` + `Retry-After`.
   Expired keys are evicted and the key count is capped (10 000). Counters reset on restart.
   This is only correct for a single process: **before running more than one worker or replica,
   replace it with a shared store** (Postgres UNLOGGED table or Redis) behind the same `RateLimiter`
   protocol.
2. **Client IP.** The peer address, or `CF-Connecting-IP` only when the peer is inside
   `TRUSTED_PROXY_CIDRS` (cloudflared's Docker network). `X-Forwarded-For` is never read. IPv6 is
   bucketed by /64. With `TRUSTED_PROXY_CIDRS` unset in production, every client shares one bucket.
3. **Turnstile fails closed.** Missing secret, Cloudflare test secret in production, network error,
   timeout (5 s), non-200 or non-JSON → verification fails (403 `turnstile_failed`). A missing secret
   does not stop the app from starting. Only error codes are logged — never the token or secret.
4. **Argon2id off the event loop.** Hash/verify run in worker threads behind an anyio
   `CapacityLimiter` (`ARGON2_MAX_CONCURRENCY`, default 2) to bound CPU and memory
   (≈ 2 × 64 MiB). Parameters come from settings; staging/production refuse values below the OWASP
   floor (m = 19 MiB, t = 2).

## Consequences
- USR-03/USR-04 reuse the limiter (new scopes), client-IP resolution and the hasher.
- The deployment must set `TRUSTED_PROXY_CIDRS`, `TURNSTILE_SECRET_KEY` and
  `TURNSTILE_ALLOWED_HOSTNAMES` before sign-up is released.
- Scaling out requires the shared limiter first.
