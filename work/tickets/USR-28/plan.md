Status: READY

> BLOCKED only on the batch approval round (`## Blocking questions`). Every question has a default.
> With the defaults this plan is implementable as written, and the predicted tier is **Medium**.

# Plan — USR-28: Exact email lookup for staff user search

## Scope summary
- Ticket goal: "Staff assigning a role can find exactly one user by email: `GET /api/v1/users?email=` is a
  case-insensitive substring match (`ilike %x%`), so `an@x.com` also returns `joan@x.com`. The email also travels
  in the URL and can reach proxy/API access logs."
- Spec §5.8 (docs/spec-v1.md:97): "**Admin:** staff can list, look up, disable/enable users, …"
- Spec §5.15 current contract (docs/spec-v1.md:336): "`GET /api/v1/users` (`admin:users:read`): filters `email`
  (case-insensitive substring) and `status`; `Page[UserRead]` …, newest first (`created_at` desc, `id`)."
- CLAUDE.md R5: "No secrets, tokens or passwords in code, logs or error details." R11: "never log passwords,
  tokens or full emails in errors."
- Ticket AC1 allows "`email_exact=` or `email_match=exact|contains` (default `contains`, so current clients keep
  working); chosen at PLAN". **Chosen: `email_match=exact|contains`**, default `contains`.
- Ticket AC2 allows "Either a `POST /api/v1/users/search` with the email in the body (stays out of URLs and logs),
  or access logs drop query strings for `/api/v1/users`". **Chosen: `POST /api/v1/users/search`.** Dropping query
  strings from the access log is Blocking question 1 (default: separate follow-up ticket).
- `work/decisions.md`: nothing on user search (grep for `users/search`, `email_match`: no hits).
- Consumer: admin-pan PAN-015c (ticket Dependencies).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Exact, case-insensitive match after the same normalisation as sign-up (`normalize_email`: `strip().lower()`, app/modules/auth/service.py:39-40), via `email_match=exact` | `tests/test_users_search.py` › `test_get_exact_hit`, `test_get_exact_case_insensitive_and_trimmed`, `test_post_exact_hit`, `test_post_exact_case_insensitive_and_trimmed` |
| 1a | No partial hit in exact mode | `test_get_exact_no_partial_hit`, `test_post_exact_no_partial_hit` (`an@x.test` does not return `joan@x.test`) |
| 1b | Default stays `contains`; current clients unchanged | `test_get_default_is_contains` (no `email_match` → `joan@` returned for `an@`), `test_get_explicit_contains_matches_default`; existing `tests/test_users_admin.py` and `tests/test_users_filters.py` pass unchanged |
| 1c | `email_match` without `email` → 422 | `test_email_match_without_email_422` (GET and POST, `unprocessable`, `details[0] = {field: email_match, reason: email_required}`); `test_get_bad_email_match_422` (`email_match=fuzzy` → `validation_error`) |
| 2 | `POST /api/v1/users/search`: email in the JSON body, same filters / pagination / `include` as the GET, 200 `Page[...]` | `test_post_parity_with_get` (same items, total, limit, offset for a combined filter set incl. `app`, `role_id`, `status`, `include=["access"]`); `test_post_include_access_shape`; `test_post_pagination_bounds` (limit 0 / 101 / offset -1 → 422; defaults 20 / 0) |
| 2a | The searched email does not appear in any log record the app writes for the POST, and the request URL carries no query string | `test_post_email_not_logged` (caplog at DEBUG over the whole request; assert the email string is in no `record.getMessage()`; assert the request URL is exactly `/api/v1/users/search`) |
| 2b | Body hardening (same reader as the batch POST): 411 / 413 / 415 / 422 | `test_post_requires_content_length_411`, `test_post_body_too_large_413` (> 4 KiB), `test_post_wrong_content_type_415`, `test_post_malformed_body_422` (invalid JSON, unknown field `extra="forbid"`, `email` > 254 chars, bad `app` key, non-UUID `role_id`; error details never echo the input) |
| 2c | Read-only: no audit entry | `test_post_writes_no_audit` (count of `audit_log` rows unchanged after a POST search) |
| 2d | Unknown filter values behave as on the GET | `test_post_unknown_app_422`, `test_post_unknown_role_422` (`unprocessable`, reasons `unknown_app` / `unknown_role`) |
| 3 | 403 without `admin:users:read`, 401 without token | `test_search_requires_users_read` (GET `email_match=exact` and POST: `admin:orgs:read` → 403 `permission_denied`; `admin:users:read` → 200), `test_search_requires_token` (401 on both) |
| 3a | R3 route registry lists the new route as Bearer `admin:users:read` | `tests/test_route_auth_classes.py` passes with the new row (it fails if the route exists without a row) |
| 3b | OpenAPI documents the enum and the POST body | `test_openapi_search_contract` (`email_match` enum `["exact","contains"]` on the GET; POST `requestBody` `application/json` schema has `email`, `email_match`, `status`, `app`, `role_id`, `include`, `limit`, `offset`; `x-permission`/permission extra = `admin:users:read`) — only when docs are enabled in the test settings (they are in test; check `docs_enabled` fixture) |
| all | `make check` green | `make check` |

## Ming verification after merge
- After admin-pan PAN-015c switches to `POST /api/v1/users/search` with `email_match: "exact"`: on
  `api.mmlabs.online`, assign a role by email and confirm in `docker compose logs` (docs/deploy.md:179) that the
  request line is `POST /api/v1/users/search` with no email in it.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/users/schemas.py` | Add `EmailMatch(StrEnum)` (`exact`, `contains`) and `UserSearch` body model (`extra="forbid"`) | AC1 enum, AC2 body | No |
| `app/modules/users/repository.py` | `UserRepository.list` (lines 154-200) gains `email_exact: bool = False`; exact → `User.email == email` (CITEXT, models.py:28, unique index), else the existing `ilike` (lines 165-166) unchanged | AC1 | No |
| `app/modules/users/service.py` | `UserService.list` (lines 79-113) gains `email_match: EmailMatch | None = None`; rule `email_match` without `email` → 422 `email_required`; exact → `normalize_email(email)` (import from `app.modules.auth.service`, import only) | AC1 business rule (R1) | No |
| `app/modules/users/router.py` | GET `""` (lines 27-109): new `email_match` query param, body moved into a shared helper; new `POST /search` declared **before** `/{user_id}` routes; shared `_users_page(...)` helper builds `Page[UserRead]` / `Page[UserWithAccessRead]` incl. the `include=access` branch (lines 89-109 today) | AC1, AC2, one response path for both routes | No |
| `tests/test_users_search.py` | New: all tests in `## Tests to write` | AC1-AC3, R8 | No (Low) |
| `tests/test_route_auth_classes.py` | Add `("POST", f"{V1}/users/search"): "admin:users:read"` next to line 115 | R3 registry | No (Low) |
| `CLAUDE.md` | R9: append the exception "`POST /api/v1/users/search` (a read with filters in the body, answers 200 `Page[T]`, not 201; D43)" | R9 exception must be recorded | No (Medium) |
| `docs/identity-architecture.md` | Decision-log row **D43** after D41/D42 (line 340): `email_match`, the POST search route, 200, Bearer `admin:users:read`, no audit, query-string stripping deferred | Decision log D-number | No (Low) |

Imported, not edited: `app/modules/auth/service.py` (`normalize_email`, High path, import only),
`app/modules/releases/body.py` (`read_json_body`, `inline_schema`), `app/modules/rbac/models.py` (`APP_KEY_RE`,
already imported by router and schemas), `app/core/pagination.py` (`Page`).

**Predicted tier: Medium.** Reasoning with `.github/scripts/risk_tier.py` against `.github/risk-tiers.yml`
(high > low > default `medium`): `app/modules/users/*.py` match no `high:` glob (not `app/core/**`, not
`app/modules/auth/**`, basename does not start with `permissions`) → default medium; `tests/**`, `docs/**` → low;
`CLAUDE.md` → default medium (on purpose, risk-tiers.yml:34). Max = **medium**. Equivalent command:
`python3 -I .github/scripts/risk_tier.py --files app/modules/users/schemas.py app/modules/users/repository.py app/modules/users/service.py app/modules/users/router.py tests/test_users_search.py tests/test_route_auth_classes.py CLAUDE.md docs/identity-architecture.md`
→ `medium` (the planner had no shell; the orchestrator should run it to confirm). Review trigger
`appsec-engineer` (area `security`) applies by area, even though no trigger path is changed.

## Data model / migration
None. `users.email` is already `CITEXT UNIQUE NOT NULL` (app/modules/users/models.py:28), so exact equality is
case-insensitive and served by the unique index; no `lower()` on the column.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users` (changed, additive) | Query as today (`email` 1-254, `status`, `app`, `role_id`, `include`, `limit` ≤ 100, `offset`) **+ `email_match`: `exact` \| `contains`** (optional; absent = `contains`) | `Page[UserWithAccessRead] \| Page[UserRead]` (unchanged) | 200, 401, 403 `permission_denied`, 422 `validation_error` (bad enum / query) or `unprocessable` (`unknown_app`, `unknown_role`, `role_not_in_app`, **`email_required`**) | `admin:users:read` |
| POST | `/api/v1/users/search` (new) | `application/json`, ≤ 4 KiB, `Content-Length` required; body `UserSearch` `{email?: str 1..254, email_match?: "exact"\|"contains", status?: UserStatus, app?: str (APP_KEY_RE), role_id?: uuid, include?: ["access"], limit?: int 1..100 = 20, offset?: int ≥ 0 = 0}`, `extra="forbid"`; empty `{}` allowed (= unfiltered first page) | Same as GET: `Page[UserWithAccessRead]` when `include` contains `access`, else `Page[UserRead]` | **200** (read, not 201), 401, 403 `permission_denied`, 411 `length_required`, 413 `payload_too_large`, 415 `unsupported_media_type`, 422 `validation_error` / `unprocessable` (same reasons as GET) | `admin:users:read` |

Semantics (both routes, one service call):
- `contains` (default): today's `ilike '%<escaped email>%'` on the raw value — byte-for-byte unchanged.
- `exact`: `normalize_email(email)` then `User.email == value` (CITEXT). 0 or 1 item; never 404. A value that
  normalises to `""` (whitespace only) simply matches nothing.
- `email_match` given without `email` → 422 `unprocessable`, `details=[{"field": "email_match", "reason":
  "email_required"}]` (raised in the service, so GET and POST answer identically).
- Auth class R3 (1) Bearer: router-level `Depends(current_admin)` (router.py:24) + `require_permission(
  "admin:users:read")`. Authentication and permission run before the body is read (`read_json_body` in the
  handler, as rbac/router.py:493-509). No audit (read, R4 covers mutations only). No rate limit (staff Bearer).
- GET `email` stays; its description gains: "Prefer `POST /users/search` (keeps the email out of URLs and
  logs)". No `deprecated` flag (Blocking question 2).

## Tests to write
New file `tests/test_users_search.py`; copy fixtures `users_client`, `_user`, `_ids`, `_sid` from
`tests/test_users_filters.py:20-49` and the RBAC factories (`make_app`, `make_role`, `assign`) for the parity test.
Helper `_post(client, body)` sends `json=body` (httpx sets `Content-Length` and `Content-Type`).

| Test (file › name) | Covers |
|---|---|
| test_users_search.py › test_get_exact_hit | `email=an@x.test&email_match=exact` returns only `an@x.test` (total 1) |
| › test_get_exact_case_insensitive_and_trimmed | `email=%20AN@X.Test%20&email_match=exact` returns `an@x.test` |
| › test_get_exact_no_partial_hit | users `an@x.test`, `joan@x.test`; exact `an@x.test` → only `an@`; exact `n@x.test` → empty page (200, total 0) |
| › test_get_default_is_contains | no `email_match`: `an@x.test` → both users |
| › test_get_explicit_contains_matches_default | `email_match=contains` → same result as absent |
| › test_get_bad_email_match_422 | `email_match=fuzzy` → 422 `validation_error`, loc `["query","email_match"]` |
| › test_email_match_without_email_422 (parametrised GET / POST) | 422 `unprocessable`, reason `email_required` |
| › test_post_exact_hit | POST `{email, email_match: "exact"}` → one item |
| › test_post_exact_case_insensitive_and_trimmed | POST `" AN@X.Test "` exact → hit |
| › test_post_exact_no_partial_hit | POST exact `an@x.test` does not return `joan@x.test` |
| › test_post_default_is_contains | POST `{email}` → both users |
| › test_post_parity_with_get | same `items`/`total`/`limit`/`offset` for `{status, app, role_id, include: ["access"], limit: 1, offset: 1}` vs the GET query |
| › test_post_include_access_shape | items carry `access` + `access_truncated` only when `include` has `access` |
| › test_post_empty_body_lists_all | `{}` → 200, default `limit` 20, `offset` 0 |
| › test_post_pagination_bounds | `limit` 0 / 101, `offset` -1 → 422 `validation_error` with loc starting `body` |
| › test_post_unknown_app_422, test_post_unknown_role_422 | `unprocessable` reasons as on GET |
| › test_post_malformed_body_422 (parametrised) | invalid JSON; unknown field; `email` "" and 255 chars; `app` "Bad Key"; `role_id` "x"; `include` ["nope"] → 422; response body does not contain the submitted value |
| › test_post_requires_content_length_411 | raw request without `Content-Length` (chunked stream) → 411 `length_required` |
| › test_post_body_too_large_413 | body > 4 KiB → 413 `payload_too_large` |
| › test_post_wrong_content_type_415 | `content=b'{}'`, `Content-Type: text/plain` → 415 `unsupported_media_type` |
| › test_post_email_not_logged | caplog DEBUG on root; POST exact search with a unique email; email string absent from every captured message; `resp.request.url.query == b""` |
| › test_post_writes_no_audit | `select count(*) from audit_log` (via the audit model) unchanged |
| › test_search_requires_token (parametrised GET exact / POST) | 401 |
| › test_search_requires_users_read (parametrised GET exact / POST) | `admin:orgs:read` → 403 `permission_denied`; `admin:users:read` → 200 |
| › test_openapi_search_contract | GET param `email_match` enum; POST `requestBody` schema fields; permission extra `admin:users:read`; 200 response, no 201 |
| test_route_auth_classes.py › (existing registry tests) | new `("POST", "/api/v1/users/search")` row: Bearer, `admin:users:read`; machine credentials refused (401) |

No pure-logic module beyond what exists: the only rule is "exact → `normalize_email` + equality", tested through
the API; `normalize_email` already has its own coverage in auth tests.

## Approach
1. **schemas.py**: add
   - `class EmailMatch(StrEnum): EXACT = "exact"; CONTAINS = "contains"`.
   - `class UserSearch(BaseModel)`: `model_config = ConfigDict(extra="forbid")`; fields as in the API contract,
     `email: str | None = Field(None, min_length=1, max_length=254)`, `app: str | None = Field(None,
     pattern=APP_KEY_RE)` (import `APP_KEY_RE` from `app.modules.rbac.models`, already a dependency of this file),
     `limit: int = Field(20, ge=1, le=100)`, `offset: int = Field(0, ge=0)` (same bounds as
     `app/core/pagination.py:12-16`). Field descriptions copied from the GET query descriptions.
2. **repository.py** `list`: add keyword `email_exact: bool = False`; replace lines 165-166 with
   `if email is not None: conditions.append(User.email == email if email_exact else User.email.ilike(...))`
   (contains branch unchanged).
3. **service.py** `list`: add `email_match: EmailMatch | None = None`. First statement: if `email_match is not None
   and email is None` → `raise _filter_unprocessable("email_match requires email", "email_match",
   "email_required")` (before the app/role lookups, so it wins). `exact = email_match == EmailMatch.EXACT`;
   `if exact and email is not None: email = normalize_email(email)`. Pass `email_exact=exact` to the repo. Import
   `from app.modules.auth.service import normalize_email` (no cycle: auth/service imports `users.repository` and
   `users.models`, not `users.service`). Do **not** log the email.
4. **router.py**:
   - Extract `async def _users_page(service, items, total, *, limit, offset, include) -> Page[UserWithAccessRead]
     | Page[UserRead]` with the body of lines 89-109 (response building only, R1).
   - GET: add `email_match: Annotated[EmailMatch | None, Query(description=...)] = None`, pass it to
     `service.list`, return `await _users_page(...)`. Extend the `email` description ("case-insensitive substring
     by default; `email_match=exact` for one account; prefer `POST /users/search`"). Add `email_required` to the 422
     description string.
   - POST `/search`, declared right after the GET list and before `@router.get("/{user_id}")`:
     `status_code=200`, `response_model` as the GET, `summary="Search users (filters in the body)"`, description
     explaining 200 / read-only / no audit / email kept out of the URL; kwargs = `require_permission(
     "admin:users:read", errors={411, 413 (4 KiB), 415, 422})` plus `openapi_extra["requestBody"]` with
     `inline_schema(UserSearch)` — same pattern as `_batch_kwargs` (rbac/router.py:443-470). Handler takes
     `request: Request, session: SessionDep`, calls `read_json_body(request, UserSearch,
     cap=_SEARCH_MAX_BYTES (4 * 1024), timeout_s=_SEARCH_READ_TIMEOUT_S (10.0))`, then the same `service.list` and
     `_users_page` with `body.limit` / `body.offset`.
5. **tests/test_route_auth_classes.py**: add the registry row after line 115.
6. **CLAUDE.md** R9: append the D43 exception sentence (see Files). **docs/identity-architecture.md**: add row
   `| D43 | **Staff user search by exact email (owner-delegated at PLAN, 2026-11-27, USR-28):** `email_match=
   exact|contains` (default `contains`) on `GET /api/v1/users`; exact = `normalize_email` + CITEXT equality, 0 or 1
   item, never 404; `email_match` without `email` → 422 `email_required`. `POST /api/v1/users/search` takes the same
   filters, `include` and `limit`/`offset` in a JSON body (≤ 4 KiB) and answers 200 `Page[T]` — R9 exception (a read
   with a body, not 201); Bearer `admin:users:read`; no audit. Keeps the email out of URLs (Cloudflare, proxy,
   uvicorn access log, browser history). Access-log query-string stripping: follow-up <ticket>. |` (wording per
   Ming's answers).
7. `make check`.

Rules: R1 (rule in service, ORM in repository, response building in router), R3 (class 1, registry), R4 (read
— no audit; asserted), R5/R11 (no email logged; validation errors never echo input — `read_json_body`
strips `input`, body.py:75-81), R6 (411/413/415/422 via existing `AppError` subclasses), R8 (happy, 401/403,
422/415/413/411; no 404 case exists for a search — empty page), R9 (additive, recorded exception D43).

## Specialist input used
- api-platform-engineer rec 1 (`email_match`, default `contains`, 422 without `email`, CITEXT equality) — adopted;
  the "check the index" question is answered: `CITEXT unique` (models.py:28), so plain equality.
- api rec 2-3 (POST search, full filter set, 200 `Page[...]`, one service call, shared response helper) — adopted.
- api rec 4-5 (R9 exception + D-number; Bearer `admin:users:read`; registry; no audit) — adopted (D43). ADR line —
  not adopted as a new ADR file: a D-row plus the R9 entry, like D26 (assumption below).
- api rec 6 (tests incl. parity, no email in logs, OpenAPI) — adopted.
- api rec 7 (keep GET `email`, doc note, no deprecation) — adopted as default of Blocking question 2.
- api rec 8 / security rec 2 (strip query strings from our access log) — **deferred**: Blocking question 1.
  It can technically avoid High paths (a `logging.Filter` on `uvicorn.access` installed from `app/main.py`, which
  is Medium, in a module outside `app/core/`), but process-wide logging belongs in `app/core/` (next to
  `request_id.py`), changes production logging for every route, and moving it outside `app/core/` only to keep the
  tier Medium would sidestep the gate. If done now it goes in `app/core/access_log.py` (High) → PR High, not
  auto-mergeable.
- security rec 1, 3, 5 (POST body; `normalize_email` from auth, not `cli.py`; 0-or-1 `Page`, not 404; JSON 415,
  bounded body, 401/403/422 tests) — adopted (`read_json_body`, 4 KiB cap).
- security rec 4 (mark GET `email=` deprecated now) — not adopted by default (Blocking question 2).
- security rec 6 (no CSRF control; Bearer, `ADMIN_POLICY` CORS via `BearerRouteMatcher` — picks the route up
  automatically from `current_admin`) — adopted; nothing to change.
- security "rate limiting on search — no" — adopted.

## Assumptions
- **Hotspots (same batch 2026-11-27-1, all PLANNING):** USR-25, USR-26, USR-27 and ADM-026 may also edit
  `CLAUDE.md` (R9/R6), `docs/identity-architecture.md` (decision log) and `tests/test_route_auth_classes.py`. D43 is
  provisional: if another ticket of the batch merges a D43 first, renumber at rebase (and the R9 reference).
- `contains` keeps today's exact behaviour (no trimming or lowercasing; `ilike` is already case-insensitive), so
  existing callers see no change.
- `email_match` without `email` is a 422, not ignored (api rec 1); it is raised in the service as `unprocessable`
  (not `validation_error`) so GET and POST answer the same code and reason.
- An empty POST body `{}` is valid (unfiltered first page), mirroring a GET without query.
- Body cap 4 KiB and read timeout 10 s are route constants in `users/router.py` (like the batch POST's 16 KiB /
  10 s); the largest valid body is well under 1 KiB.
- `normalize_email` is imported from `app/modules/auth/service.py` without editing it (orchestrator: allowed).
  Moving it to a shared module would touch a High path; out of scope.
- No new ADR file: the decision is a contained, additive API exception recorded in D43 + R9 (as D26 for the stats
  dashboard). If Ming wants an ADR, it is docs-only (Low).
- `docs/spec-v1.md` is not edited: §5.15 line 336 is a dated "current contract (facts)" snapshot; the spec scope
  ("look up users", §5.8) already covers the change.
- Follow-ups (not in this diff, R7): (a) access-log query-string stripping (if Q1 = default); (b) admin-pan
  PAN-015c: switch the role-assign user picker to `POST /api/v1/users/search` with `email_match: "exact"`;
  (c) revisit GET `email` deprecation after PAN-015c.

## Blocking questions
See `## Blocking questions for Ming` below.

## Split proposal
None (estimated ~380 changed lines, most of it tests).

## Blocking questions for Ming
1. **Access-log query-string stripping** (consultants differ: security-architect — strip query strings from our
   uvicorn access log for all routes now; api-platform-engineer — follow-up). The POST already keeps the email
   out of URLs for clients that use it; stripping only protects our own container log (not Cloudflare, proxies or
   browser history) for GET callers until admin-pan migrates. Doing it properly touches `app/core/` (and
   `app/main.py`) → this PR becomes **High** and cannot be auto-merged.
   **Default: separate follow-up ticket (ADM-xxx, High), USR-28 stays Medium.**
2. **GET `email=` deprecation**: mark the `email` query parameter `deprecated` in OpenAPI now (security), or only
   add a description note pointing to `POST /users/search` (api)?
   **Default: description note only; no `deprecated` flag until admin-pan PAN-015c has migrated; `email` stays for
   all of v1.**
3. **Decision record**: is a decision-log row D43 + the R9 entry in `CLAUDE.md` enough, or do you want a separate
   ADR file for the POST-search exception?
   **Default: D43 + R9 only, no new ADR.**
