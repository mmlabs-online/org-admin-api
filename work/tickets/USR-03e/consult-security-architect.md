# consult-security-architect — USR-03e

## Findings
1. `app/main.py` currently adds a static Starlette `CORSMiddleware` from `CORS_ORIGINS` with `allow_credentials=True` and `allow_methods`/`allow_headers=["*"]`, applied to every path except `/.well-known/`. The only thing standing between this and `*` with credentials is the validator in `config.py`. It also ignores `apps.origins`, disabled apps and the loopback filter.
2. The CSRF gate is already correct and reads the DB on every request: `AppContextService.check` requires an active app, rejects a missing or `null` Origin, normalizes the origin, matches it exactly and drops loopback in staging/production. So CORS staleness can only let an origin read responses. It cannot let one perform state changes. The TTL bounds read exposure only.
3. With D20 (`__Host-mm_device`, `Path=/`), the cookie goes with every API request. CORS scoping therefore decides which routes a sibling `*.mmlabs.online` page can read with credentials.
4. Origins are not unique across apps, and the admin app is not exclusive (USR-03 Q3, USR-03c F3). This does not weaken CORS itself, but it matters for `aud=admin` tokens.

## Recommendations
- **AC1 (exact match):** compare the raw `Origin` header byte-for-byte against the cached set of normalized origins and echo the raw value. Do not echo a normalized form that differs from what the browser sent. Never echo `null` or an absent Origin. Send `Access-Control-Allow-Credentials: true` only together with an echoed origin. There is no wildcard path at all.
- **Cache:** one set per process, a 30–60 s TTL, single-flight refresh, and set-membership lookups, so unknown origins never cost a DB query. Invalidate only after the app write commits, otherwise a concurrent refresh can reload the old set and keep it for a full TTL. Other workers and replicas rely on the TTL alone, so document that. If a refresh fails, keep the last good set until its TTL ends, then serve an empty set (fail closed).
- **`Vary: Origin`** on every response under the scope, including those without Origin or with an unknown one. Cloudflare largely ignores `Vary`, so `/api/v1/auth/*` must also send `Cache-Control: no-store`. Add a test for that.
- **Preflight (AC2):** answer in the middleware and never pass OPTIONS to the routers. Check `Access-Control-Request-Method` ∈ {GET, POST} and that the request headers are a subset of {content-type, x-mm-app}. On a mismatch or unknown origin, return 400 or 204 with no ACAO headers. `Max-Age: 600`.
- **AC4:** the middleware must be outermost so 401/403/422/429 carry CORS headers. Add `Access-Control-Expose-Headers: Retry-After`. Unhandled 500s pass through `ServerErrorMiddleware` and will not carry CORS headers. That is acceptable; note it.
- **Scope:** `/api/v1/auth/*` only. Keep the `/.well-known/` exemption (`ACAO *`, no credentials). Before ADM-002, admin `/api/v1/*` routes should get no CORS at all; they are unauthenticated and disabled in production. Remove `CORS_ORIGINS` and its `*` guard, and update `.env.example` and `docs/deploy.md`.
- **Logging:** a debug-level, sampled or rate-limited line for rejected origins (normalized, truncated to 64 characters). Never log cookies or headers wholesale.

## Risks
- If a removed origin or disabled app stays readable for up to the TTL, that is response-read exposure only. Accept it.
- If the scope check uses a prefix match, `/api/v1/authx` would match. Match on `/api/v1/auth/` or the exact path.
- A compromised registered subdomain can still read its own app's responses. This is inherent to D5.

## Open questions
1. Is origin uniqueness across apps (at least admin exclusivity) a follow-up ticket before ADM-002 enables `aud=admin`? My recommendation is yes, as a follow-up.
2. After ADM-002, should the admin UI's Bearer calls use non-credentialed CORS from `apps[admin].origins`, under a separate ticket?
3. Local development without `CORS_ORIGINS`: are loopback origins registered in the local DB acceptable?
