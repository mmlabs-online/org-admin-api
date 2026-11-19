## ADM-024: architecture consult on optimistic concurrency for org and entitlement PATCH

The biggest problem is the clock. `now()` is the transaction start time, and the test fixture runs a whole test inside one transaction. So within a test, `updated_at` never changes when a row is updated. The "ETag changes after update" test cannot pass, and a "stale If-Match" test would see a stale tag still match, unless the conditional UPDATE sets `updated_at` with `clock_timestamp()` (F2, Rec 2).

### Findings

**F1. Both PATCHes load the row, change it and flush, with no lock and no version check.**
- Organizations, `app/modules/organizations/service.py:60-72`: `session.get` (no lock, `repository.py:24-25`) → `setattr` per field → `flush()` → `refresh()` → `_record`. `_record` writes the audit row only when `diff` is non-empty (`service.py:111-114`).
- Entitlements, `app/modules/entitlements/service.py:101-128`: same shape. `get_for_org` uses `populate_existing`, no lock (`repository.py:102-107`). `limits` is replaced as a new dict (`service.py:114`, no MutableDict).
- Two staff working in parallel: last writer wins. For `limits` this drops the other person's keys, which is the reason for the ticket.
- If nothing changed, SQLAlchemy emits no UPDATE, so `updated_at` and the ETag stay the same and no audit row is written. Good for ETag stability.

**F2. Who sets `updated_at`, and when two updates can get the same value.**
- `app/db/base.py:29-31`: `server_default=func.now(), onupdate=func.now()`.
  - `onupdate` is applied by SQLAlchemy at flush time, not by a DB trigger. No trigger on these tables in `migrations/`.
  - So only ORM or Core UPDATEs from this app bump it. Raw SQL and FK actions do not.
- `now()` is the transaction start time (same as `transaction_timestamp()`), at microsecond precision (timestamptz). asyncpg returns timezone-aware microsecond `datetime`s, so the round trip is exact.
- In production, two versions of one row get the same value only if two writing transactions started in the same microsecond, or the clock steps back. Rare, but it is the ABA case.
  - A transaction that started earlier but commits later writes an *older* timestamp. The ETag still differs, so equality checks work, but `updated_at` is not monotonic.
- **In tests it always happens.** `tests/conftest.py:98-106` uses one outer `conn.begin()` with `join_transaction_mode="create_savepoint"`, so `now()` is constant for the whole test. Create and then PATCH give the same `updated_at`.

**F3. Other writers of `updated_at`.** None besides the two PATCHes.
- Organizations:
  - inserts only: staff create (`organizations/service.py:54`) and end-user create (`organizations/auth_service.py:119`);
  - delete removes the row;
  - users FK `ON DELETE SET NULL` on `created_by` (`organizations/models.py:41-43`) changes the `created_by` field in the response without bumping `updated_at`.
- Entitlements: grant is an insert (`entitlements/service.py:78-87`), revoke is a delete.
- Manifest, rbac and stats only read these tables (`rbac/repository.py:157,441,519`). `PATCH /apps/{key}` changes `org_mode`/`status` on `apps` and not on `org_entitlements`.
- Consequence: entitlement `active` and `app_key`, and org `created_by`, can change without the ETag changing. That is fine for a concurrency token, but the ETag must not be offered as a cache validator (`If-None-Match`/304) on these routes.

**F4. Audit.**
- `AuditLogger.record` only adds and flushes; it never commits (`audit/service.py:141-154`). The request session commits at the end (`app/db/session.py:24-35`).
- Raising an `AppError` rolls back, so a 412 can never leave an audit row.
- Do **not** use `record_failure`/`record_denied` (`audit/service.py:156-222`). They commit a standalone row, which the owner ruled out.

**F5. Existing ETag machinery is for something else.**
- `app/core/etag.py` hashes response bytes and implements `If-None-Match` with weak comparison.
- `If-Match` needs **strong** comparison (RFC 9110 §13.1.1): `W/` tags never match, `*` matches any existing resource.

**F6. CORS.**
- `ADMIN_POLICY` already exposes `ETag` (`app/core/cors.py:47-49`).
- `allow_headers` is only `authorization, content-type` (`cors.py:45-46`), and the preflight rejects any header it does not list (`cors.py:137`).
- So admin-pan's PATCH with `If-Match` fails CORS until `if-match` / `If-Match` is added.

**F7. Scope gap: there is no GET for a single entitlement.**
- `entitlements/router.py` has list, POST, PATCH and DELETE only.
- The acceptance criterion "GET … of an entitlement returns an ETag" has no endpoint to attach to.

**F8. Errors.**
- `app/core/errors.py` has no 412 class.
- Responses are declared per route through `require_permission(errors=…)` (entitlements) and `_route` (organizations, `router.py:30-35`).

### Recommendations

1. **Compare first, then a conditional write (AC "atomic").** Service flow, the same for both modules:
   - a. Load the row as today. Missing → 404 (404 stays ahead of 412).
   - b. If `If-Match` is present and does not match `etag(row)` → raise `PreconditionFailedError`. Nothing is written and there is no audit row.
   - c. Work out which fields actually change. If none, return the row unchanged: 200, same ETag, no audit. This keeps today's no-op behaviour.
   - d. If `If-Match` was present, call a repository method `update_if_unchanged(id, expected_updated_at=row.updated_at, values) -> Model | None`. It runs `UPDATE … SET <values>, updated_at = <Rec 2> WHERE id = :id AND updated_at = :expected RETURNING *` (ORM-enabled `update().returning(Model)`, or `refresh` afterwards).
   - e. 0 rows means the race was lost → 412. Under READ COMMITTED, Postgres re-checks the WHERE on the newly committed row, so there is no gap between compare and write.
   - f. The `before` snapshot from step a is exact, because the WHERE proved the row had not changed. Write the audit row with `record` in the same transaction, as today (R4).
   - **Without `If-Match`, keep the current code path exactly** (last writer wins). Using a conditional write there would turn lost races into 412s for clients that never sent the header, which breaks R9.
   - Alternative: `SELECT … FOR UPDATE` followed by the existing `setattr`/flush.
     - Gains: simpler, reuses the code as it is.
     - Costs: a row lock held until commit, and it is not the "conditional UPDATE" the owner approved.
     - Mention it in the plan only as the fallback.

2. **Clock source for the conditional write:** `SET updated_at = GREATEST(clock_timestamp(), updated_at + interval '1 microsecond')`.
   - Strictly increases per row, which removes the ABA case in F2.
   - Makes the tests work despite F2's constant `now()`.
   - It is an expression in the repository, not a schema change, so there is no migration and no `alembic check` drift.
   - Leave `TimestampMixin` as it is (R7). Note that the no-`If-Match` path keeps using `now()`.
   - The test "ETag changes after update" has to use `If-Match` to go through this path, or the plan must accept that path's limitation. Say so explicitly in the plan.

3. **ETag format and parsing.**
   - Format: strong, opaque, built from `(id, updated_at)` with `updated_at` normalised to UTC microseconds. For example `"<epoch_us>"`, or a short SHA-256 of `f"{id}:{iso_utc}"`.
   - Always compute it from the value just read back (after RETURNING or refresh), never from the request.
   - Add a pure `if_match(values) -> IfMatch(any: bool, tags: frozenset[str])` parser next to `app/core/etag.py`. It should drop `W/` tags, accept `*`, cap the length and treat malformed input as no match.
   - The router parses headers and sets the `ETag` header through `Response`. The service receives the `IfMatch` value and the `etag_of` function, so it stays HTTP-free (R1).

4. **Endpoints that return an ETag:** GET org, PATCH org, PATCH entitlement, and preferably both 201 POSTs. For the entitlement GET, see open question 1.

5. **Errors and docs.**
   - Add `PreconditionFailedError(AppError)`: `status_code=412`, `code="precondition_failed"`.
   - Declare 412 on both PATCH routes.
   - Update the R6 code list in CLAUDE.md and the spec §5.1/§5.6 change notes, as the owner instructed.

6. **CORS:** add `if-match` to `ADMIN_POLICY.allow_headers` and `If-Match` to `allow_headers_value` (`cors.py:45-46`). Add a preflight test.

7. **Tests** (each runs inside one transaction, so Rec 2 is mandatory):
   - matching `If-Match` → 200 with a new ETag and one audit row;
   - stale → 412 envelope, row unchanged, audit count unchanged;
   - `*` → 200;
   - weak or malformed tag → 412;
   - no header → current behaviour;
   - no-op PATCH with a matching tag → 200, same ETag, no audit;
   - 404 beats 412 for an unknown id;
   - 401/403 unchanged.
   - A true race test is not feasible in the per-test transaction. Instead, test the lost-race branch at repository level with an `expected` value that does not match.

### Risks
- **Test clock (F2):** without Rec 2, the tests can only be made green in misleading ways.
- **R9:** applying the conditional path when no `If-Match` is sent is a breaking change. The two paths must stay separate.
- **ETag not tied to the representation (F3):** a client or proxy using it with `If-None-Match` would get wrong 304s. Do not add `If-None-Match` support on these routes.
- **Mixed clocks:** the no-`If-Match` path still uses `now()`. In theory it could write back an earlier timestamp value. That needs a transaction that started in the same microsecond as an earlier version's writer; negligible, and worth documenting.
- **Identity map:** sessions use `autoflush=False` and `expire_on_commit=False` (`db/session.py:21`). After a Core UPDATE the ORM object is stale unless the update uses `returning(Model)` with synchronize, `refresh`, or `populate_existing`. `_view` already uses `populate_existing`; the organization path needs care.
- **Lock order:** the conditional UPDATE locks only the target row until commit. Grant still locks the app row (`entitlements/service.py:66`). No new lock-order cycle, as far as I can see.

### Open questions
1. Entitlement ETag on read (F7): add `GET /api/v1/organizations/{org_id}/entitlements/{entitlement_id}` (new endpoint: READ permission, plus the R8 tests), or put an `etag` field on each list item? Adding the GET is cleaner and is additive under v1.
2. Does the owner accept `clock_timestamp()`/`GREATEST(...)` (Rec 2) as part of "ETag derived from `updated_at`, conditional UPDATE"? It needs no migration.
3. Should the 201 POST responses also carry `ETag`? It is cheap and lets admin-pan edit straight after creating.
4. Should `If-Match` with `*` be accepted (RFC behaviour), or treated as malformed and answered with 412?
