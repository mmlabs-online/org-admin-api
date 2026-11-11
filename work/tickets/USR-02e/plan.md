Status: READY

# Plan — USR-02e: Public role catalog with ETag

> Title note: the ticket title still says "(flag-gated)". The owner's Q1 answer (D17) and the
> orchestrator note of 2026-11-11 supersede that, the Goal's "without making it public" and the
> "flag off → 404" test. This plan follows D17: the catalog is **public and always mounted**. The
> "flag off → 404" test becomes "reachable with the RBAC flag off" (AC5 below).

## Spec summary
- spec-v1 §5.3: "The role catalog `GET /api/v1/apps/{key}/roles` is public (D17); app origins are
  limited to `mmlabs.online`, its subdomains and localhost (D19)."
- identity-architecture.md **D17**: "`GET /api/v1/apps/{key}/roles` joins R3 class 3 (public).
  Rate-limited per IP; 404 for unknown, disabled and `admin` apps; exposes only role key, name,
  `is_system` and permission keys."
- **D18**: "apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…`; the role catalog
  is a single document, not `Page[T]`."
- ADR-0001 Decision 5: "Apps verify locally via JWKS and map roles to permissions from a cached
  catalog (`GET /api/v1/apps/{key}/roles`, with ETag)."
- CLAUDE.md R3 class 3: "public — … the role catalog `GET /api/v1/apps/{key}/roles` (D17;
  rate-limited, 404 for unknown/disabled/`admin` apps)". R9 exception (D18).
- Contract origin: `work/tickets/USR-02b/plan.md` → Split proposal → 02b-3 (document shape, ETag
  algorithm, 304 rules, `catalog_router`).
- Carry-over (ticket Notes, USR-02d security review findings 1–2 and follow-ups 5–6): own minimal
  schema (not `RoleRead`); extend `_NO_CONTROL` to C1, bidi overrides/isolates and U+2028/U+2029;
  apply it to `AppCreate.name`/`AppUpdate.name` (NUL currently → 500).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/apps/{key}/roles` in its own router (`app/modules/rbac/catalog_router.py`), mounted through `app/api/router.py` (always, not behind `admin_rbac_api_enabled`), no `x-permission` | `tests/test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (catalog entry = `None`); `tests/test_role_catalog_api.py › test_only_catalog_route_listed_by_default`; reading `app/main.py` (unchanged gate) and `app/api/router.py` |
| 2 | Per-IP rate limit, scope `role_catalog`, configurable (R5); 304 and 404 responses count; over limit → 429 `rate_limited` with `Retry-After` | `test_role_catalog_api.py › test_catalog_rate_limited_429`, `› test_catalog_304_and_404_count_toward_limit`; `.env.example` lists the two keys without values |
| 3 | Canonical document `{"app", "roles": [{"key", "name", "is_system", "permissions"}]}`: roles sorted by key, permission keys sorted, nothing else (no ids, `is_admin`, descriptions, app fields, timestamps) | `› test_catalog_document_shape_and_order`, `› test_catalog_exact_key_set`, `› test_catalog_empty_app_and_role_without_permissions` |
| 4 | Strong `ETag` = `"` + first 32 hex of SHA-256 of the exact response bytes + `"`; `Cache-Control: no-cache`; stable across calls; changes after a permission or role change | `› test_catalog_etag_is_hash_of_body`, `› test_catalog_etag_stable`, `› test_catalog_etag_changes_after_change` (grant, revoke, role rename, role add, role delete) |
| 5 | `If-None-Match` → 304 for exact tag, `W/` tag, list containing the tag, `*`; empty body, same `ETag` and `Cache-Control`, no `Content-Type`; non-matching or malformed → 200 | `› test_catalog_304_variants` (parametrized), `› test_catalog_304_response_shape`, `› test_catalog_if_none_match_no_match_200`; unit tests in `tests/test_etag.py` |
| 6 | 404 for unknown, disabled and `admin` apps, and for keys that fail `APP_KEY_RE` (no DB query); identical bodies | `› test_catalog_404_cases_identical` |
| 7 | Reachable with the RBAC flag off (default settings) and with it on; flag on, `POST …/roles` still reaches the staff route; flag off, `POST …/roles` → 405 (pinned) | `› test_catalog_reachable_flag_off`, `› test_catalog_reachable_flag_on_no_clash`, `› test_post_roles_flag_off_is_405` |
| 8 | Carry-over: `_NO_CONTROL` also rejects C1, bidi/format characters and LS/PS; applied to `AppCreate.name`, `AppUpdate.name`, `RoleName`, `Description`; NUL in an app name → 422 (not 500); response does not echo the value | `tests/test_rbac_apps_api.py › test_app_name_control_chars_422` (create + patch), `tests/test_rbac_roles_api.py › test_role_name_format_chars_422`, `tests/test_rbac_permissions_api.py › test_description_format_chars_422`, `› test_vietnamese_names_accepted` |
| 9 | `make check` green; `alembic check` no drift (no schema change) | CI / `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/etag.py` (new) | `strong_etag(body: bytes) -> str`, `if_none_match(values: list[str], etag: str) -> bool` (pure helpers) | AC4/AC5; HTTP concern reusable by JWKS later (USR-03); unit-testable |
| `app/modules/rbac/catalog_router.py` (new) | Public `router = APIRouter(prefix="/apps", tags=["role-catalog"])` with `GET /{key}/roles` | AC1 (own router), R3 class 3 |
| `app/api/router.py` | `api_router.include_router(catalog_router)` | AC1/AC7: always mounted, outside the flag branch in `main.py` |
| `app/modules/rbac/schemas.py` | Move + widen `_NO_CONTROL` above `AppCreate`; add `AppName`; use it on `AppCreate.name`/`AppUpdate.name`; add `RoleCatalogEntry`, `RoleCatalog` | AC3, AC8 |
| `app/modules/rbac/repository.py` | `catalog_rows(app_id) -> list[tuple[str, str, bool, str | None]]` (one query) | AC3, R1 |
| `app/modules/rbac/service.py` | `RoleCatalogService.read_catalog(app_key) -> RoleCatalog` | AC3, AC6, R1 |
| `app/core/config.py` | `role_catalog_rate_limit: int = Field(default=120, ge=1)`, `role_catalog_rate_window_seconds: int = Field(default=60, ge=1)` | AC2, R5 |
| `app/core/rate_limit.py` | Add `"role_catalog"` entry to `_limit_for` | AC2 (no other change: eviction is a follow-up) |
| `.env.example` | `ROLE_CATALOG_RATE_LIMIT=`, `ROLE_CATALOG_RATE_WINDOW_SECONDS=` | R5 |
| `docs/adr/0003-rbac-keys-and-matching.md` | "Amended 2026-11-11 (USR-02e)" line; new §11 "Public role catalog" (shape, canonical order, ETag, 304 rules, `no-cache`, 404 set, rate-limit scope, display-name character filter) | Planner rule 7: contract consumed by `mmlabs-auth` (USR-03b) and USR-05 |
| `docs/identity-architecture.md` | §4 API table row for `GET /api/v1/apps/{key}/roles`: auth column "app credential (USR-05) or public read (decide in plan)" → "public (D17), per-IP limit"; owner column "USR-02e" | Doc contradicts D17 after this ticket; only that one row |
| `tests/test_etag.py` (new) | Unit tests for the helpers | AC4/AC5 |
| `tests/test_role_catalog_api.py` (new) | API tests for the catalog | AC1–AC7, R8 |
| `tests/test_rbac_apps_api.py` | Add catalog to the route map; app-name character tests | AC1, AC8 |
| `tests/test_rbac_roles_api.py` | Role-name format-character tests | AC8 |
| `tests/test_rbac_permissions_api.py` | Description format-character test | AC8 |

No other file. `app/main.py` is **not** changed (the flag branch keeps gating only the staff router).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_etag.py › test_strong_etag_format` | `"` + 32 lowercase hex + `"`, equals `sha256(body).hexdigest()[:32]` |
| `› test_if_none_match_exact`, `› test_if_none_match_weak_prefix`, `› test_if_none_match_list`, `› test_if_none_match_star` | true cases (list with spaces, several headers joined) |
| `› test_if_none_match_no_match`, `› test_if_none_match_malformed` | false for `None`/`[]`/empty, other tag, unquoted tag, `W/` without quotes, embedded quote, `*` inside a list, header over 2048 chars |
| `tests/test_role_catalog_api.py › test_catalog_document_shape_and_order` | roles sorted by key (insert out of order), permissions sorted, JSON equals expected dict |
| `› test_catalog_exact_key_set` | top-level keys `{"app","roles"}`; each role keys `{"key","name","is_system","permissions"}`; `permissions` is a list of strings |
| `› test_catalog_empty_app_and_role_without_permissions` | app with only a role with no grants → `"permissions": []`; app with no roles → `"roles": []` |
| `› test_catalog_etag_is_hash_of_body` | `ETag` == `'"' + sha256(resp.content).hexdigest()[:32] + '"'`; `Cache-Control: no-cache`; `Content-Type: application/json` |
| `› test_catalog_etag_stable` | two GETs → same ETag and bytes |
| `› test_catalog_etag_changes_after_change` | ETag changes after each of: grant, revoke, role rename, new role, role delete (use factories / ORM in `db_session`) |
| `› test_catalog_304_variants` (parametrized: `etag`, `W/etag`, `"x", etag`, `*`) | 304 |
| `› test_catalog_304_response_shape` | 304: `content == b""`, same `ETag`, `Cache-Control: no-cache`, no `content-type`, no non-zero `content-length` |
| `› test_catalog_if_none_match_no_match_200` | stale tag, malformed tag → 200 with body |
| `› test_catalog_404_cases_identical` | unknown key, disabled app, `admin`, `UPPER`, 40-char key → 404 `not_found`; response bodies byte-identical; no `ETag` header |
| `› test_catalog_reachable_flag_off` | default `client` fixture → 200 |
| `› test_catalog_reachable_flag_on_no_clash` | `make_client(admin_rbac_api_enabled=True)`: GET → 200 catalog; `POST …/roles` → 201 staff route still works |
| `› test_post_roles_flag_off_is_405` | default client: `POST /api/v1/apps/stoktok/roles` → 405 (pinned behaviour, A6) |
| `› test_only_catalog_route_listed_by_default` | `create_app(Settings())` OpenAPI: the only `/api/v1/apps*` operation is `("GET", "/api/v1/apps/{key}/roles")` with no `x-permission` |
| `› test_catalog_rate_limited_429` | `make_client(role_catalog_rate_limit=2, role_catalog_rate_window_seconds=60)`: 3rd GET → 429 `rate_limited`, `Retry-After` set |
| `› test_catalog_304_and_404_count_toward_limit` | limit 2: one 404 + one 304 → next request 429 |
| `tests/test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (update) | add `("GET", "/api/v1/apps/{key}/roles"): None` with a "public, D17" comment |
| `› test_app_name_control_chars_422` (parametrized: `\x00`, `\x85`, `‮`, `⁦`, ` `, `​`, `﻿`) | create and patch → 422 `validation_error`; response text does not contain the submitted name |
| `tests/test_rbac_roles_api.py › test_role_name_format_chars_422` (same parametrization) | role create/patch → 422, no echo |
| `› test_vietnamese_names_accepted` | role name `"Quản trị viên"` → 201 (combining/precomposed Vietnamese letters pass) |
| `tests/test_rbac_permissions_api.py › test_description_format_chars_422` | `"a‮b"`, `"a\x85b"` → 422 |

## Approach
1. **Character filter (carry-over, AC8)** — `schemas.py`: move `_NO_CONTROL` above `AppCreate` as a raw
   string so the Rust regex engine reads the escapes and messages stay ASCII:
   ```
   # Display text: no C0/DEL/C1 controls, no bidi embeddings/overrides/isolates, no zero-width,
   # line/paragraph separators or BOM (NUL is refused by Postgres `text` → would be a 500).
   _NO_CONTROL = r"^[^\x00-\x1f\x7f-\x9f؜​-‏ -‮⁠-⁩﻿]*$"
   AppName = Annotated[str, Field(min_length=1, max_length=255, pattern=_NO_CONTROL)]
   ```
   `AppCreate.name: AppName`; `AppUpdate.name: AppName | None = None`. `RoleName` and `Description`
   keep using `_NO_CONTROL` (now wider). Verified locally: pydantic 2.14 rejects `\x00`, `\x85`,
   `‮`, ` `, `‍`, `﻿` and accepts `"Quản trị viên"`.
2. **Schemas (AC3)** — dedicated output models, never built from ORM attributes:
   ```
   class RoleCatalogEntry(BaseModel):
       key: str
       name: str
       is_system: bool
       permissions: list[str]  # sorted permission keys

   class RoleCatalog(BaseModel):
       """Public role catalog (D17). Field order is part of the ETag; append-only."""
       app: str
       roles: list[RoleCatalogEntry]  # sorted by key
   ```
3. **Repository (R1)** — one query, no N+1:
   ```
   async def catalog_rows(self, app_id) -> list[tuple[str, str, bool, str | None]]:
       select(Role.key, Role.name, Role.is_system, Permission.key)
         .select_from(Role)
         .outerjoin(RolePermission, RolePermission.role_id == Role.id)
         .outerjoin(Permission, Permission.id == RolePermission.permission_id)
         .where(Role.app_id == app_id)
   ```
   No ORDER BY (sorting is done in Python so bytes do not depend on DB collation).
4. **Service (R1, AC3, AC6)** — `RoleCatalogService.read_catalog(app_key)`:
   - `re.fullmatch(APP_KEY_RE, app_key)` fails → `NotFoundError("App not found")` (no DB query);
   - `app = await repo.get_app_by_key(app_key)` (no lock: read-only); `None`, `status != active` or
     `key == ADMIN_APP_KEY` → the same `NotFoundError("App not found")`, no `details`;
   - group rows into `{role_key: (name, is_system, set(perm_keys))}`, skip `None` perm keys and keys
     whose prefix ≠ `app.key` (ADR-0003 §4 second check); build `RoleCatalog(app=app.key,
     roles=[… sorted by key, permissions sorted])`. Keys are ASCII, so Python `sorted` = code-point
     order. Lives on the existing `RoleCatalogService` (it is the catalog service; no new class).
5. **ETag helpers (AC4/AC5)** — `app/core/etag.py`:
   - `strong_etag(body)` → `'"' + hashlib.sha256(body).hexdigest()[:32] + '"'` (128 bits);
   - `if_none_match(values, etag)`: join `values` with `,`; if total length > 2048 → `False`;
     stripped value `== "*"` → `True`; else split on `,`, strip each, drop an optional `W/` prefix,
     accept only `"<chars without '"'>"`, ignore malformed entries, return `True` if any opaque tag
     equals `etag` (weak comparison, RFC 9110 §13.1.2). Comparison is constant work; no regex
     backtracking risk (plain string ops).
6. **Router (R1: HTTP only)** — `catalog_router.py`:
   ```
   @router.get(
       "/{key}/roles",
       response_model=RoleCatalog,
       dependencies=[rate_limit("role_catalog")],
       responses={304: {"description": "Not modified"}, 404: {...}, 429: {...}},
       summary="Public role catalog (D17)",
   )
   async def get_role_catalog(session: SessionDep, request: Request, key: str) -> Response:
       catalog = await RoleCatalogService(session).read_catalog(key)
       body = catalog.model_dump_json().encode()
       etag = strong_etag(body)
       headers = {"ETag": etag, "Cache-Control": "no-cache"}
       if if_none_match(request.headers.getlist("if-none-match"), etag):
           return Response(status_code=304, headers=headers)
       return Response(content=body, media_type="application/json", headers=headers)
   ```
   Order: rate limit (dependency) → 404 → build → 304/200. No `openapi_extra=permission(...)`.
   Module docstring: "Public endpoint — R3 class 3 (D17). Never add admin data here."
   `model_dump_json()` emits compact JSON with UTF-8 (non-ASCII not escaped); the ETag hashes
   exactly the bytes sent.
7. **Mount (AC1/AC7)** — `app/api/router.py` includes `catalog_router` next to `auth_router`.
   Starlette matches routes in order and keeps searching after a method mismatch, so with the flag
   on, `POST …/roles` still reaches the staff router (included later); with the flag off it gets
   405 (A6).
8. **Rate limit (AC2, R5)** — settings + `_limit_for["role_catalog"]`; `.env.example` keys.
9. **Docs** — ADR-0003 §11 and the identity-architecture §4 row (Files table).
10. **Tests** as listed; `make check`.

Rules: **R1** router builds HTTP response only, service decides 404/document, repository holds the
query. **R2** no schema change. **R3** class 3 public, rate-limited, no `x-permission`; staff routes
untouched. **R4** read-only, no audit. **R5** two new env keys, no secrets. **R6** 404/429 via
`AppError` envelope; 304 has no body (a 405 from Starlette keeps the framework shape, same as the
existing unmounted-route 404). **R7** limiter eviction, NFC, origin subdomain rule, staff role
listing → follow-ups. **R8** tests above. **R9** D18 exception (key path, single document).
**R11** no tokens, emails or secrets in the response or logs.

Size estimate: ~140 production lines (etag 35, router 40, schemas 25, service 25, repository 12,
config/rate-limit/router/env 8) + ADR ~25 + ~260 test lines.

## Specialist input used
`consult-api-platform-engineer.md`:
- F1 (ticket conflict; replace "flag off → 404" with reachable-when-off/on) — adopted (AC7).
- F2 (route map test breaks) — adopted: add the entry with `None` + default-settings OpenAPI test.
- F3 (405 for POST with flag off) — adopted and pinned in a test (A6).
- F4 (no staff role list for disabled/`admin` apps) — out of scope per orchestrator → follow-up FU1.
- F5 (shared limiter cap/eviction cost) — rejected for this diff (R7, pre-existing, `rate_limit.py`
  core change) → follow-up FU2.
- F6 (sort order / collation) — adopted: sort in Python.
- Rec. document schemas `RoleCatalogEntry`/`RoleCatalog`, no `is_admin` — adopted.
- Rec. one LEFT JOIN query — adopted.
- Rec. `app/core/etag.py` with `strong_etag` / `if_none_match` + unit tests — adopted.
- Rec. `*` exact, weak comparison, malformed → no match — adopted.
- Rec. `Response` + `response_model` + `responses` for 304/404/429 — adopted.
- Rec. keep `no-cache` (not `max-age=60`) — adopted (AC2 of the ticket).
- Rec. key regex check before DB, one `NotFoundError` — adopted.
- Rec. `role_catalog` scope, settings `ge=1`, 404/304 count, 429 test — adopted.
- Rec. separate `catalog_router` included in `app/api/router.py` — adopted.
- Rec. carry-over regex + `AppName` + no-echo tests — adopted, with the security architect's wider
  class (adds ALM, zero-width/LRM/RLM, word joiner range, BOM).
- Risk "shared IP bucket for all app backends" — adopted: default 120/min (A1), not 60.
- Open Q1/Q2/Q3 — answered as FU1, A1, A6.

`consult-security-architect.md`:
- F1 (D17 wins; mount outside the flag branch) — adopted.
- F2 (identical 404 for unknown/disabled/`admin`) — adopted, byte-identical test.
- F3 (no size caps per app) — not in this ticket (changes USR-02d create paths) → follow-up FU4.
- F4 / release prerequisite (`TRUSTED_PROXY_CIDRS`, F9) — adopted as a release prerequisite in the
  PR; refusing to start in production with an empty list rejected here (A7) → follow-up FU3.
- F5 (`_NO_CONTROL` gaps, app name unfiltered) — adopted (AC8).
- Rec. dedicated model built field by field, exact key-set test — adopted.
- Rec. no `details` on 404 — adopted.
- Rec. `Vary: Accept-Encoding` if compression is on — not needed: no compression middleware (A4).
- Rec. SHA-256 truncated to 128 bits over compact UTF-8 bytes — adopted (pydantic's
  `model_dump_json` = compact separators, non-ASCII unescaped).
- Rec. cap `If-None-Match` length, ignore malformed entries — adopted (2048 chars).
- Rec. wider character class — adopted (A3).
- Rec. NFC normalization of names — rejected for this ticket (A5): not in the ticket or carry-over,
  changes stored data; ETag stability does not depend on it (we hash stored bytes) → follow-up FU5.
- Rec. 60/60 s default — rejected in favour of 120/60 s (A1), configurable.
- Risk "consumers must render names as plain text"; "authorize from token claims, catalog is for
  display/mapping" — adopted as a sentence in ADR-0003 §11.
- Open Q2 `default_role` in the catalog — rejected: D17 lists the exposed fields.

## Assumptions
- **A1 — Rate limit:** `ROLE_CATALOG_RATE_LIMIT=120` per `ROLE_CATALOG_RATE_WINDOW_SECONDS=60` per
  IP (IPv6 /64), in-process per worker (ADR-0002). Generous because app backends on the one runner
  may share an egress IP; apps cache and revalidate with `If-None-Match`. 304 and 404 count.
- **A2 — ETag:** strong, `"` + first 32 hex chars (128 bits) of SHA-256 of the exact body + `"`.
  `If-None-Match` uses weak comparison (RFC 9110); `*` matches any existing catalog; header values
  over 2048 chars → treated as no match (200); multiple `If-None-Match` headers are joined.
- **A3 — Character class** (display text: app names, role names, permission descriptions):
  reject `U+0000–001F`, `U+007F–009F`, `U+061C`, `U+200B–200F`, `U+2028–202E`, `U+2060–2069`,
  `U+FEFF`. This rejects ZWJ/ZWNJ (some emoji sequences, some scripts) — acceptable for staff-set
  display names. Existing stored names are not re-validated (none exist outside local/test).
- **A4 — Caching headers:** `Cache-Control: no-cache` on 200 and 304 (revalidate every time; no
  edge caching). No `Vary` (no compression middleware). 404/429 carry no cache headers.
- **A5 — No NFC normalization** in this ticket (FU5).
- **A6 — 405:** with the flag off, `POST /api/v1/apps/{key}/roles` returns Starlette's 405
  (`Allow: GET`, framework body, like today's unmounted-route 404). Accepted and pinned; ADM-002
  removes the flag.
- **A7 — Empty `TRUSTED_PROXY_CIDRS` in production is not refused at startup** in this ticket: it
  would change config validation for every endpoint and could stop the running deploy. Setting it
  in the deployment repo (F9) is a **release prerequisite** for this ticket, stated in the PR body;
  startup refusal → FU3.
- **A8 — Read consistency:** the app lookup and the catalog query are two statements under READ
  COMMITTED, no lock; a concurrent change shows on the next revalidation (ETag changes).
- **A9 — Service home:** `read_catalog` goes on the existing `RoleCatalogService`; no new class.
- **A10 — Optional origin rule** (refuse `*.api.mmlabs.online`) is not done here (R7; origins/CORS
  belong to USR-03) → FU6.
- **A11 — Docs:** ADR-0003 gains §11; identity-architecture.md §4 row updated to match D17. The
  ticket title/Goal text is left for the orchestrator to fix (planner edits only plan.md).

## Blocking questions
None.

## Split proposal
None. Follow-ups for the orchestrator (not in this diff):
- **FU1** Staff role listing for disabled and `admin` apps (admin path, Bearer, `admin:apps:read`).
- **FU2** `InMemoryRateLimiter`: per-scope key caps and cheaper eviction (heap) so public catalog
  traffic cannot evict `signup` buckets.
- **FU3** Refuse to start in staging/production with empty `TRUSTED_PROXY_CIDRS` (or startup
  warning); until then F9 is a deploy checklist item.
- **FU4** Per-app caps on roles/permissions (e.g. ≤ 200 roles, ≤ 500 permissions) in USR-02d
  create paths.
- **FU5** NFC normalization of display names.
- **FU6** Refuse origins under `.api.mmlabs.online` (USR-02d security review, optional).
- **FU7** Fix the USR-02e ticket title/Goal wording ("flag-gated") in `ticket.md` and backlog.

## Data model / migration
None (no schema change; `alembic check` must stay clean).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/roles` | optional `If-None-Match` | 200 `application/json` `RoleCatalog {app: str, roles: [{key: str, name: str, is_system: bool, permissions: [str]}]}` — roles sorted by key, permissions sorted; headers `ETag: "<32 hex>"`, `Cache-Control: no-cache`. 304: empty body, same `ETag` and `Cache-Control` | 200, 304, 404 `not_found` (unknown, disabled, `admin`, malformed key; one message, no details), 429 `rate_limited` (`Retry-After`) | None — public, R3 class 3 (D17); per-IP limit `role_catalog` |

Example 200 body (compact on the wire):
```
{"app":"stoktok","roles":[{"key":"stoktok:member","name":"Member","is_system":true,"permissions":["stoktok:portfolio:read"]},{"key":"stoktok:owner","name":"Owner","is_system":false,"permissions":["stoktok:*"]}]}
```
Staff routes are unchanged; with `ADMIN_RBAC_API_ENABLED` off, `POST` on this path → 405.
