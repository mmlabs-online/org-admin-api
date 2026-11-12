# USR-03e: Dynamic CORS from `apps.origins`; remove or restrict `CORS_ORIGINS`

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | api, security, auth |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | — |

## Goal
browsers can call the identity endpoints only from registered app origins (D12 / S4).

## Spec reference
§5.8; identity-architecture.md §4–§5; D7, D12, D13, D19; split from USR-03 (`work/tickets/USR-03/plan.md` → "Split proposal").

## Acceptance criteria
- [ ] Pure-ASGI middleware (e.g. `app/core/cors.py` behind an `OriginRegistry` protocol, implemented in
  rbac as a cache of active apps' origins with a 30–60 s TTL invalidated by app writes) replaces the
  static `CORSMiddleware` for `/api/v1/auth/*`: echoes `Origin` only when it is in some active app's
  origins, with `Access-Control-Allow-Credentials: true`; always `Vary: Origin`; unknown origin → no
  ACAO headers.
- [ ] Preflight: methods `GET, POST`, headers `Content-Type, X-MM-App`, `Max-Age` ~600.
- [ ] Loopback origins ignored in staging/production.
- [ ] Error responses (401/403/429) also carry the CORS headers so clients can read the envelope.
- [ ] `CORS_ORIGINS` removed, or kept only for non-credentialed admin Bearer calls (owner decides; admin
  UI origins could come from `apps[admin].origins` without credentials).
- [ ] `.env.example` / `docs/deploy.md` updated.
- [ ] Tests: preflight allowed vs unknown origin, credentialed echo, `Vary`, 401 body readable
  cross-origin, disabled app's origin not echoed, cache invalidation on app update.

## Dependencies
USR-03, USR-02. Can run in parallel with USR-03c.

## Notes / answers from owner
- 2026-11-12: created by the orchestrator from the USR-03 planner's split.
