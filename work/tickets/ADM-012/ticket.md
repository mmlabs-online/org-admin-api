# ADM-012: Non-credentialed CORS for the Bearer admin endpoints

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | api, security, auth |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | — |

## Goal
The admin UI (`admin-pan`, a browser SPA at `https://admin-portal.mmlabs.online`) can call the Bearer admin endpoints under `/api/v1/*` cross-origin. CORS on those paths is non-credentialed and echoes only the registered origins of the `admin` app.

## Spec reference
§2 (one admin API used by the mmlabs admin web UI), §5.2 staff authentication, §5.8 abuse protection ("CORS only for registered app origins"); R3 class 1 (Bearer); ADR-0006 (dynamic CORS), ADR-0008 (admin API authentication). The ADM-002 ticket notes deferred this work until an admin UI existed (owner, 2026-11-12, USR-03e A1).

## Acceptance criteria
- [ ] Preflight (`OPTIONS`) on a Bearer admin path from an origin in `apps[admin].origins` returns 2xx with:
  - `Access-Control-Allow-Origin` equal to that origin, plus `Vary: Origin`;
  - `Access-Control-Allow-Methods` `GET, POST, PATCH, PUT, DELETE`;
  - `Access-Control-Allow-Headers` `Authorization, Content-Type`;
  - a bounded `Access-Control-Max-Age`;
  - **no** `Access-Control-Allow-Credentials`.
- [ ] Actual cross-origin requests with `Authorization: Bearer` from that origin carry `Access-Control-Allow-Origin` (no credentials header) and `Access-Control-Expose-Headers: Retry-After` (plus `ETag` where an admin route sets it). This holds for success, error and 401/403 responses.
- [ ] An origin not registered on `admin` gets no CORS headers. The admin CORS uses only `apps[admin].origins` and never other apps' origins. A request without `Origin` (server-to-server) is unchanged.
- [ ] Admin endpoint responses carry `Cache-Control: no-store`.
- [ ] `/api/v1/auth/*` CORS is unchanged: credentialed, using the calling app's `apps.origins`, with its existing methods and headers. Existing CORS tests stay green. The public role catalog and `/.well-known/jwks.json` keep their current behaviour; this ticket decides and records whether they get CORS.
- [ ] Tests cover:
  - preflight from a valid and from an invalid admin origin;
  - a real request with `Authorization`;
  - no `Allow-Credentials` on any admin path;
  - `/auth/*` unchanged;
  - the origin cache picks up `set-admin-origins` changes within the existing TTL.
- [ ] ADR-0006 is amended (or a new ADR added), and `docs/consumers.md` gets an admin-UI section: Bearer only, no cookies on admin endpoints.

## Dependencies
ADM-002 (Bearer admin auth), ADM-002c (`set-admin-origins`), USR-03e (dynamic CORS).

## Notes / answers from owner
- 2026-11-16 (owner): the admin UI hostname is **`admin-portal.mmlabs.online`**, not `admin.mmlabs.online` as `docs/deploy.md` says (docs fix in ADM-014). The admin UI sends no cookie to admin endpoints, only `Authorization: Bearer`. The `__Host-mm_device` cookie is used only for `/api/v1/auth/*` through `@mmlabs/auth-client`.
- Operator step after release: `set-admin-origins --origin https://admin-portal.mmlabs.online` through the ops workflow.
