# Consult — security-architect — USR-28 (2026-11-27)

**Findings**
- `users/router.py:45`: `email` GET query param; `users/repository.py:166` `ilike %x%`; an exact CITEXT lookup exists at `repository.py:90-97`; sign-up normaliser `auth/service.py:39` (`strip().lower()`).
- `Dockerfile:18`: uvicorn default access log; nothing in `app/` configures `uvicorn.access` or strips query strings → `GET /api/v1/users?email=full@addr` in container stdout; `docs/deploy.md:179` operators read them with `docker compose logs`.
- `docs/deploy.md:4`: Cloudflare Tunnel — edge, Logpush/analytics, cloudflared see the full URL (we can't strip it); browser history, admin-pan route state, `Referer` too.
- `app/core/cors.py:42-46`: `ADMIN_POLICY` non-credentialed, allows POST, needs `Authorization` — cross-origin POST needs a preflight; Bearer never sent automatically.

**Recommendations**
1. `POST /api/v1/users/search`, JSON `{email, email_match: "exact"|"contains", status, app, role_id, include}` + `limit`/`offset`, 200 `Page[UserRead]` — the only option that keeps emails out of logs we don't control; a new R9 exception (POST read, 200).
2. Defence in depth: drop query strings from our own access log for every route (`--no-access-log` + a path-only access-log middleware, or a `uvicorn.access` filter) — cheap; covers remaining GET callers (R5/R11).
3. Exact: `User.email == normalize_email(x)` on CITEXT (reuse `auth/service.py:39`, not `cli.py:210`); `Page` with 0 or 1 item, not 404.
4. Keep GET `email=`, mark deprecated in OpenAPI, point admin-pan PAN-015c at POST.
5. `Content-Type: application/json` (415), bounded body, `require_permission("admin:users:read")`; tests 401, 403, 422 malformed email, exact hit, case-insensitive hit, no partial.
6. CSRF: no ambient credential — no extra control; don't add to the cookie-class CORS policy.

**Risks**
- Enumeration low: substring search already leaks more; exact adds nothing new; read audit out of scope (R4).
- Until admin-pan switches, GET `contains` still puts emails in Cloudflare URLs.
- A POST read may be mistaken for a mutation by caches, retries, audit middleware — ensure no audit entry.
- Route registry must list the new route as Bearer.

**Open questions for Ming (defaults)**
1. POST search plus log stripping (stripping alone leaves emails in Cloudflare/browser URLs).
2. Deprecate GET `email=` now, keep for all of v1.
3. Strip query strings for all routes.
4. Rate limiting on search — no (staff-only Bearer).
