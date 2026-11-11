# consult-api-platform-engineer — USR-02e

## Findings
1. **Ticket conflict:** AC1 says always mounted and public (D17), AC4 still lists "flag off → 404". D17 (owner) wins; replace that test with "default settings → 200" and "RBAC flag on → still 200, no route clash".
2. **The route map test will break.** `test_rbac_routes_declare_permissions` (tests/test_rbac_apps_api.py:56) collects every operation under `/api/v1/apps` and asserts `found == expected`; the catalog GET will appear there with `x-permission` = None.
3. **405 side effect.** With the flag off, `GET /api/v1/apps/{key}/roles` is mounted but the admin `POST` on the same path isn't → Starlette answers that POST with **405** (`Allow: GET`), not 404. No current test covers it, but it changes "not mounted ⇒ 404".
4. **No staff role list.** The 02b-2 contract had no staff roles list (roles were read "through the 02b-3 catalog"). The public catalog hides disabled and `admin` apps, so staff can't list roles for those; R3 (one auth class per endpoint) means a staff list needs a different path.
5. **Shared limiter.** `InMemoryRateLimiter` has one 10k-key cap across all scopes, and `_evict` sorts every bucket each time a new one is added past the cap → a public endpoint hit from many IPs (each IPv6 /64 a separate key) can evict `signup` buckets and burn CPU.
6. **Sort order.** `_NO_CONTROL` sits after `AppRead`; app names have no character check. Postgres sorting depends on DB collation.

## Recommendations
- **Document (AC2, D17):** new `RoleCatalogEntry {key, name, is_system, permissions: list[str]}` and `RoleCatalog {app: str, roles: [...]}` — not `RoleRead`. No `is_admin` (D17 lists the exposed fields; adding later is non-breaking). Sort roles and permission keys in Python (code-point order) so bytes/ETag don't depend on DB collation. One repository query (roles LEFT JOIN role_permissions → permissions), not N+1.
- **ETag (AC2):** serialize once with `RoleCatalog.model_dump_json().encode()`; `'"' + sha256(bytes).hexdigest()[:32] + '"'` (128-bit, strong). Pure helpers in `app/core/etag.py`: `strong_etag`, `if_none_match(header, etag) -> bool`, with unit tests. Header exactly `*` matches; otherwise match quoted tags via `(?:W/)?"([^"]*)"` (weak comparison, RFC 9110 §13.1.2); malformed → no match.
- **Responses:** order rate limit → 404 → build → 304/200. Return `Response(body, media_type="application/json")`; declare `response_model=RoleCatalog` and `responses` for 304/404/429 so OpenAPI shows them. 304: empty body, same `ETag` and `Cache-Control: no-cache`, no Content-Type. Keep `no-cache` per AC2 (`max-age=60` delays revocation by up to a minute and invites shared/CDN caching).
- **404 (AC3):** one `NotFoundError("App not found")` for unknown, disabled and `admin`; check `{key}` against `APP_KEY_RE` in the service first and return the same 404 without a DB query.
- **Rate limit (R3 class 3):** `rate_limit("role_catalog")` route dependency; `role_catalog_rate_limit` and `role_catalog_rate_window_seconds` in `Settings` (`ge=1`), a `_limit_for` entry, keys without values in `.env.example`. 404s and 304s count. Add a 429 test.
- **Mount (AC1):** separate `catalog_router` (e.g. `app/modules/rbac/catalog_router.py`) included in `app/api/router.py` next to `auth_router`; no `x-permission`.
- **Route map test:** add `("GET", "/api/v1/apps/{key}/roles"): None` with a "public, D17" comment; add a test that with default settings the only `/api/v1/apps` operation in OpenAPI is that GET; pin the POST → 405 behaviour in a test.
- **Carry-over:** in schemas.py move a stricter pattern above the app schemas, `r"^[^\x00-\x1f\x7f-\x9f  ‪-‮⁦-⁩]*$"` — keep it a raw string so pydantic-core's regex reads `\u` and validation messages stay ASCII. Add `AppName = Annotated[str, Field(min_length=1, max_length=255, pattern=_NO_CONTROL)]` for `AppCreate.name`/`AppUpdate.name`. Tests: 422 cases (`\x00`, `\x85`, `‮`, `⁦`, ` `) on app create/patch, role create/patch and permission description; assert the response doesn't echo the value.
- **Size:** ~110 production lines, 180–200 test lines.

## Risks
- **Shared IP bucket:** all mmlabs app backends on the one runner may reach the API from one IP and share a bucket — set a generous default (e.g. 120/min) and rely on SDK caching.
- **Limiter eviction (finding 5):** pre-existing; fixing it touches `app/core/rate_limit.py` → R7: keep out of this diff and log a follow-up unless appsec insists.
- **Existing names:** names already stored with bidi/C1 characters are still served; validation only covers new input.
- **Public docs:** the catalog will appear in the public `/docs` (fine).

## Open questions
1. Do staff need a roles listing for disabled and `admin` apps (finding 4)? If so, a follow-up ticket with a distinct admin path.
2. Rate-limit defaults for the catalog?
3. Is 405 for `POST …/roles` with the flag off acceptable?
