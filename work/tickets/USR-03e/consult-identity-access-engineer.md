# consult-identity-access-engineer — USR-03e

**Findings**
- `app/main.py` adds a static `_CredentialedCORS` (Starlette, `allow_methods/headers=*`, credentials) for every path except `/.well-known/`. `AppContextService.check` (`auth/service.py`) already does the per-app binding: it reads from the DB on every request with no cache, takes the active app plus the normalized `Origin`, and drops loopback origins in staging/production.
- A preflight never carries the value of `X-MM-App`, only its name. So CORS can only check whether the origin belongs to *some* active app (the union). The binding of origin to app has to stay in the handler. That split is correct, and the handler check is the actual CSRF control. CORS only decides whether the page can read the response.
- One uvicorn process runs (Dockerfile), so in-process cache invalidation reaches everything today.
- The cookie is `__Host-mm_device` with `Path=/` (D20). CLAUDE.md R11 still says `mm_device` with `Path=/api/v1/auth`. That is doc drift and should be flagged to the owner.
- `ricotdin.mmlabs.online` and `api.mmlabs.online` are the same site (eTLD+1 `mmlabs.online`), so a `SameSite=Lax` cookie is sent on credentialed `fetch`, and third-party-cookie blocking does not apply.

**Recommendations**
- AC1: CORS uses the TTL cache, and `AppContextService` keeps its uncached DB read. Both use one shared helper, `allowed_origins(app, app_env)` (normalize plus loopback filter), so the rules cannot drift apart. When an app is disabled, the handler rejects at once and CORS may lag by up to the TTL. That is fail-closed and acceptable.
- AC1/AC2: answer an unknown-origin preflight with 204, `Vary: Origin` and no ACAO. Do not return 403 or Starlette's 400. Never pass `OPTIONS` through to the router. Normalize the request `Origin` before the set lookup. Treat `null` as unknown.
- AC4: the new middleware must run outside `ExceptionMiddleware` so 401/403/429 envelopes carry ACAO. Unhandled 500s produced by `ServerErrorMiddleware` will still lack it. Document that or accept it.
- AC5: send credentialed CORS only on `/api/v1/auth/*`. For admin Bearer endpoints, keep `CORS_ORIGINS` without `Allow-Credentials`. Leave `/.well-known/` as it is.
- Add to `docs/consumers.md`:
  - The SPA sends `credentials: "include"`, `Content-Type: application/json` and `X-MM-App: ricotdin`, and runs from a registered origin (exact scheme and host, no port).
  - A CORS failure looks like a network `TypeError`, not a 403.
  - For local dev, run the SPA on `http://localhost:<port>` against a local API on `http://localhost:8000`. Use the same hostname on both sides: `127.0.0.1` vs `localhost` is cross-site, and the cookie will not be sent.
  - A local SPA can never use the production API, because loopback origins are ignored there and the request is cross-site.
  - Safari may refuse `Secure`/`__Host-` cookies over `http://localhost`, so use Chrome or Firefox, or local https.

**Risks**
- **Staging SPA on the production API:** `https://ricotdin-staging.mmlabs.online` is allowed under D19. If it is registered as an origin of the `ricotdin` app, staging and production share accounts, and a token with `aud=ricotdin` is accepted by both backends. Prefer a separate app key, `ricotdin-staging`, with its own audience.
- **Any `*.mmlabs.online` subdomain is same-site.** If one is compromised, or a third-party page is hosted there, it can trigger cookie-bearing requests. The `Origin` check plus `X-MM-App` in the handler is what blocks it, so keep that check on every cookie endpoint.
- The process-local cache becomes stale across replicas if workers are ever scaled out. The TTL bounds that.

**Open questions**
1. Should `CORS_ORIGINS` be removed, or kept for non-credentialed admin calls (AC5)?
2. Is there a staging API host, or will staging SPAs call `api.mmlabs.online`? And is it a separate app key or a shared one?
3. TTL value: 30 s or 60 s?
4. Should CLAUDE.md R11 be updated to match D20?
