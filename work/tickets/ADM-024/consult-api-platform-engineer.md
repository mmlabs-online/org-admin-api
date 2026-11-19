ADM-024 consult (api-platform-engineer, read-only)

## Findings

1. **Existing ETag helpers don't fit.** `app/core/etag.py:8` `strong_etag` hashes response bytes, and `if_none_match` (`etag.py:13`) is a weak comparison with a 2048-char cap, used only by `catalog_router.py:34-36` and `jwks_router.py:30-34`. A body hash cannot be reversed into a value for `WHERE updated_at = :v`, so the ETag must encode `updated_at` itself. Reuse the same parsing hygiene (join header lines, length cap, malformed entries never match).
2. **`updated_at` semantics (`app/db/base.py:29-31`).** The column is `timestamptz`, with `server_default=func.now()` and a Python-side `onupdate=func.now()`. Postgres `now()` is the transaction start time, with microsecond precision. A `datetime` round-trips exactly through asyncpg, so equality in SQL is safe.
   - Both ORM `onupdate` and a `server_default` are in play. Changing `onupdate` to `clock_timestamp()` is not DB drift, because it is client-side, so `alembic check` stays clean.
   - The ORM only emits an UPDATE when an attribute actually changed, so a no-op PATCH does not bump `updated_at`. This matches the current "no audit row when nothing changed" behaviour (`organizations/service.py:111-114`, `entitlements/service.py:118`).
3. **Test hazard.** `tests/conftest.py:105` runs each test in one outer transaction with `create_savepoint`. `now()` is therefore constant inside a test, so "ETag changes after update" would fail or flake if both writes use `now()`. Production has one transaction per request, so it is fine there.
4. **Current update flow is not atomic.** `OrganizationService.update` (`service.py:60-72`) does `get` → `setattr` → `flush`. `EntitlementService.update` (`service.py:101-128`) does the same through `_get` → `get_for_org`. No row lock is taken, so a Python compare would leave a window.
5. **Entitlement `active` is derived** (`entitlements/repository.py:14`, joins org status and app status). The body can change without the entitlement's `updated_at` changing. `limits` is replaced whole (`service.py:114`).
6. **Routers.**
   - `organizations/router.py:30-35` has a local `_route` helper that sets `responses[code]` directly. `require_permission(errors=...)` (`admin_auth/deps.py:127-130`) accepts any 4xx except 401/403, so 412 is allowed.
   - `entitlements/router.py:71` uses `errors={404: _ENT_404}`.
   - `openapi.py:41` uses `response.setdefault("headers", ...)`, so extra declared response headers survive post-processing. 412 gets the envelope and `X-Request-ID` automatically (`openapi.py:37-43`).
7. **CORS.**
   - `ADMIN_POLICY.expose_headers` already contains `ETag` (`cors.py:47-50`).
   - `allow_headers` is `{authorization, content-type}` (`cors.py:45-46`). A preflight with `If-Match` is currently answered without allow headers, so the browser blocks it.
   - `tests/test_cors.py:311` asserts the exact string `"Authorization, Content-Type"` and must be updated.
8. **Error codes.**
   - `_HTTP_CODES` in `errors.py:186` has no 412.
   - `ErrorBody.code` description (`errors.py:136`) lists the codes.
   - There is no `AppError` subclass for 412 yet.

## Recommendations

**R-1. ETag format (AC1).** Emit a strong, opaque, quoted tag derived from `updated_at`. Use integer microseconds since the Unix epoch, computed exactly as `(dt - EPOCH) // timedelta(microseconds=1)`, never float seconds. Example: `ETag: "1763554800123456"`.
- Do not use isoformat. It carries timezone-offset ambiguity and has quoting noise.
- Do not use a weak tag. RFC 9110 §13.1.1 requires strong comparison for `If-Match`, so a client echoing `W/"…"` would never match.
- The "strong" claim is an approximation. The entitlement `active` flag can change without an ETag change. Document that the ETag validates the stored row, not derived fields. This is acceptable because the PATCH only touches stored fields.
- Optionally prefix a version marker (e.g. `"u1763…"`) so a later move to a version column is non-breaking. Not required.
- Put `version_etag(dt)` and `parse_if_match(values)` in `app/core/etag.py` as pure functions, unit-tested.
- Set `ETag` on GET one, PATCH, and also on POST 201 (additive, and it saves clients a GET). List items need no header. `updated_at` is in the body, and the ETag is derivable from it.

**R-2. If-Match parsing (AC2).** `parse_if_match(list[str]) -> IfMatch` returns one of:
- absent → no check, unchanged behaviour (R9);
- `*` alone → matches any existing resource, so the check passes (404 still takes precedence);
- a list of entity-tags → the set of parsed `datetime`s;
- unparseable or no usable tag → "never matches".

Details:
- Join multiple header lines with `,`, as `if_none_match` does. Apply a length cap (2048) over which nothing matches.
- Split on commas and trim whitespace.
- A weak tag (`W/"…"`) never matches under strong comparison, so it is dropped.
- A tag must be well-formed quoted `^[0-9]{1,20}$`, and the integer must be bounded before building the datetime, to avoid `OverflowError`.
- `*` inside a list is invalid and never matches.
- Malformed header → fail closed with 412, not 400 and not silently ignored. A client that sent a precondition must never get an unconditional write.
- Multiple tags: the precondition passes if the row's current tag equals any of them. In SQL, `updated_at = ANY(:vs)`, or compare in the pure helper only for the `*` and absent cases.

**R-3. Where the compare happens (R1, AC3).**
- The router does HTTP only. It reads the header with `Header(...)`, calls `parse_if_match`, and passes a neutral value (`None | ANY | frozenset[datetime]`) to the service. It also sets the `ETag` response header, using a `Response` parameter, from `read.updated_at`.
- The service raises `PreconditionFailedError`. It never touches `HTTPException`.
- The repository owns the atomic write. Add a repo method such as `update_if_version(id, expected, values) -> Row | None`, built as `update(Model).where(id == …, updated_at.in_(expected)).values(**changed, updated_at=func.clock_timestamp()).returning(...)`.
  - Under READ COMMITTED, a concurrent writer blocks on the row lock, then re-evaluates the `WHERE` on the new row version. It sees a different `updated_at` and returns 0 rows, so this is atomic.
  - Order of checks is 403/401 (deps), then 404 (existing `get`, unchanged), then 412. A 0-row result after the 404 check means 412. A row deleted in between is a rare race and also surfaces as 412, so re-select only if you want 404 for it.
  - For the entitlement path, keep the existing `_get` for 404 and then run the conditional UPDATE.
  - No-change PATCH (only nulls, or an empty body) issues no UPDATE. For `If-Match` in that case, do a plain `SELECT … WHERE updated_at = ANY(...)` check and return the current state. Nothing is written, so atomicity is moot.
- Skip `If-Match` handling entirely when the header is absent, so the current ORM flow is untouched (R9).
- No audit row on 412 (AC2). It is raised before `audit.record`, and R4 is satisfied as nothing mutated. Do not write a `denied` audit row for it: `DENIED_ERRORS` (`errors.py:99`) is for guard refusals, and a 412 is not one.
- The audit "before" snapshot: take it from the row read before the UPDATE, as today. A race can make it slightly stale, but a conditional UPDATE guarantees it was the version the client saw when `If-Match` was used.
- Simpler alternative if the conditional-UPDATE plumbing is awkward with the ORM identity map: `SELECT … FOR UPDATE` in the repo `get` when `If-Match` is present, then compare in the service. This is also atomic. The owner chose conditional UPDATE, so prefer it, but note this as the fallback. After a Core UPDATE, `refresh(org)` / `populate_existing` is needed (the entitlement repo already uses it).

**R-4. `updated_at` bump and test determinism.** In the conditional UPDATE set `updated_at=func.clock_timestamp()` explicitly. Do not rely on `now()`, or the "ETag changes after update" test is flaky inside the per-test transaction.
- Whether to also change `TimestampMixin.onupdate` (`db/base.py:30`) to `clock_timestamp()` is an owner/plan call. It is a global behaviour change for all tables, though harmless (no migration).
- If the mixin is left alone, the unconditional (no `If-Match`) PATCH path still bumps via `now()`. In tests that means two PATCHes in one transaction share the same ETag. For consistency, either route both paths through the same repo write, or change the mixin and say so. I recommend changing the mixin so both paths agree.

**R-5. 412 error (AC4).** Add to `errors.py`:
```
class PreconditionFailedError(AppError):
    status_code = 412  # status.HTTP_412_PRECONDITION_FAILED
    code = "precondition_failed"
```
- Message: "Resource was modified; re-fetch and retry". `details` is `null`, per the existing "otherwise null" rule in `ErrorBody` (`errors.py:142-146`).
- Optionally attach the current `ETag` header via `AppError.headers` (`errors.py:23-29`) so a client can retry without a GET. It is cheap because the row is in hand, but optional.
- Add `412: "precondition_failed"` to `_HTTP_CODES` (`errors.py:186`) for framework-raised cases.
- Update the `ErrorBody.code` description (`errors.py:136`) to list `precondition_failed`.
- Update R6 in CLAUDE.md and the spec (owner already approved).
- Do not introduce 428. `If-Match` stays optional.

**R-6. OpenAPI (AC4).**
- `If-Match` request parameter: declare via `if_match: Annotated[list[str] | None, Header(description=...)]` (alias is auto-derived to `If-Match`). A `list[str]` receives multiple header lines. The description should state: strong entity-tags from a prior `ETag`; `*` matches any existing resource; weak tags never match; absent means unconditional; mismatch gives 412 and changes nothing.
- Declare 412 on the two PATCH routes through `require_permission(errors={404: …, 412: "Stale If-Match (code precondition_failed); nothing changed"})`. For organizations, add it to the `_route(...)` dict (`router.py:96`).
- Declare the `ETag` response header on the 200 (GET/PATCH) and 201 (POST) responses via `responses={200: {"description": …, "headers": {"ETag": ETAG_HEADER}}}`, with a shared constant. Define it once, near `_REQUEST_ID_HEADER` in `app/core/openapi.py` or in `etag.py`. Schema: `{"type": "string"}`, with the format described. `_postprocess` already deep-copies.
- Merge into the existing `responses` carefully: `require_permission` returns its own `responses` dict, so when adding a 200 entry to it, make sure FastAPI's merge keeps the `response_model` schema (it does when the key has only `description`/`headers`).
- Regenerate any committed OpenAPI snapshot after the change. I found no `.json` snapshot under `docs/` via my grep, but `render_openapi` exists in `openapi.py:73`, so check for a committed snapshot test.

**R-7. CORS (AC4).** In `ADMIN_POLICY` (`cors.py:42-52`):
- add `"if-match"` to `allow_headers`;
- set `allow_headers_value="Authorization, Content-Type, If-Match"`;
- leave `expose_headers` as is (`ETag` is already there);
- update the assertion at `tests/test_cors.py:311` and add a preflight test for `If-Match`.

Identity policy is untouched: it is credentialed and these are Bearer routes. A browser cannot request `If-Match` today without a failing preflight, so this change is required. Fixed `Access-Control-Allow-Headers` is only echoed on success, so no wildcard is needed.

**R-8. Tests (AC5).** Matching tag → 200 and the ETag changes. Stale tag → 412, body unchanged, no audit row. `*` → 200. Weak tag → 412. Malformed → 412. List containing the current tag → 200. No header → unchanged. 404 for an unknown id wins over 412. 403 without permission is unchanged. GET and PATCH ETag equal for the same state. A concurrency test with two sessions is ideal, but the conftest uses a single connection, so rely on the SQL WHERE guarantee and a repo-level unit test that a mismatched `WHERE` returns None.

## Risks

- **Transaction-start `now()`** (Finding 3 and R-4): a flaky or incorrect "ETag changes" test, and in theory two commits in the same microsecond would be indistinguishable. Use `clock_timestamp()`. The collision window is negligible in production.
- **Derived fields not covered by the ETag** (entitlement `active`): a client can see a changed `active` with the same ETag. Documentation only.
- **Fail-closed on malformed `If-Match`** is stricter than ignoring it. It is intentional for write safety. Note it in the API docs.
- **Changing `TimestampMixin.onupdate`** is global. Do it only if the plan accepts it, and run the full suite, since audit and other tests may compare timestamps.
- **R1 leakage:** parsing the header in the router is fine (HTTP), but do not let the router compare or catch SQL results.
- **Contract:** adding 412 to the error codes is additive, and `If-Match` optional keeps v1 compatible (R9).

## Open questions

1. Should `TimestampMixin.onupdate` become `clock_timestamp()` globally, or only the new conditional UPDATE? (I recommend global.)
2. Should POST 201 also return `ETag`? (Recommended, additive.)
3. Should the 412 response carry the current `ETag` header? (Optional; I lean yes.)
4. On a row deleted between the 404 check and the conditional UPDATE, accept 412 or re-select and return 404?
5. Should `If-Match` be supported on `DELETE` for both resources? It is out of the ticket's scope, so leave it for follow-ups (R7).
6. Should `If-None-Match` / 304 on these GETs be added? It is not requested, so leave it as a follow-up.

Files referenced: `app/core/{etag,errors,cors,openapi}.py`, `app/db/base.py`, `app/modules/admin_auth/deps.py`, `app/modules/organizations/{router,service,repository}.py`, `app/modules/entitlements/{router,service,repository}.py`, `tests/test_cors.py`, `tests/conftest.py`.
