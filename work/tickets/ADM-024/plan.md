Status: READY

# Plan: ADM-024, optimistic concurrency (ETag / If-Match) on PATCH for organizations and entitlements

## Spec summary
- §5.1: "CRUD for organizations (slug, name, description, status `active`/`suspended`). Paginated list."
- §5.6: "Which org can use which product, with plan and limits; enable/disable per org." The 2026-11-17 ADM-006 note says: "staff Bearer endpoints under /api/v1/organizations/{org_id}/entitlements (admin:entitlements:read|write)."
- The spec has no concurrency requirement. Ticket "Notes / answers from owner" (2026-11-19) brings this into scope. The owner decided:
  - "In scope."
  - "412 is added to the R6 codes (CLAUDE.md and spec). `If-Match` stays optional; a mismatch returns 412 `precondition_failed` and changes nothing."
  - "The ETag is derived from `updated_at`, so no migration. The check is atomic via a conditional UPDATE (`WHERE updated_at = :v`)."
- CLAUDE.md R6 today says: "Codes: 201 create, 204 delete, 401, 403, 404, 409, 422." This ticket adds 412, as the owner approved.
- CLAUDE.md R9 says: "No breaking changes inside v1." Every change here is additive: an optional request header, a new response header, a new error code that only appears when a client sends `If-Match`, and one new GET route.
- Flagged spec conflict (non-blocking): the spec has no list of error codes, so "add 412 to the spec" becomes a dated ADM-024 note under §5.1 and §5.6 (step 9).

## Acceptance criteria → verification
| # | Criterion (ticket) | How the reviewer verifies it |
|---|---|---|
| 1 | GET and PATCH of an organization and of an entitlement return an `ETag` derived from `updated_at`. | `test_organizations.py › test_get_org_returns_etag`, `test_patch_org_returns_etag`; `test_entitlements_api.py › test_get_entitlement_returns_etag`, `test_patch_entitlement_returns_etag`. Each asserts `resp.headers["etag"] == version_etag(parse(body["updated_at"]))`. POST 201 also returns it (`test_create_org_returns_etag`, `test_grant_entitlement_returns_etag`). |
| 2a | PATCH accepts an optional `If-Match`. On a mismatch it returns 412 with the error envelope, changes nothing, and writes no audit row. | `test_patch_org_stale_if_match_412` and `test_patch_entitlement_stale_if_match_412`. Each checks: status 412; body `{"error": {"code": "precondition_failed", "message": ..., "details": null}}`; an `ETag` header equal to the current tag; a re-GET body equal to the earlier body; the audit row count unchanged. Weak, malformed and over-long tags also give 412 (`..._weak_if_match_412`, `..._malformed_if_match_412`). |
| 2b | Without `If-Match`, behaviour is unchanged (R9). | `test_patch_org_without_if_match_unchanged`, `test_patch_entitlement_without_if_match_unchanged`. All existing PATCH tests in `tests/test_organizations.py` and `tests/test_entitlements_api.py` must pass without edits. The reviewer also reads the diff: the no-header branch of `update()` keeps the current code path. |
| 3 | The check and the update are atomic, with no window between compare and write. | Code reading: the write is a single `UPDATE … WHERE id = :id AND updated_at = :expected RETURNING id` in the repository. Tests: `test_organizations.py › test_repo_update_if_version_mismatch_returns_false`, `test_entitlements_repository.py › test_update_if_version_mismatch_returns_false`. A lost race (0 rows) gives 412: `test_patch_org_lost_race_412` monkeypatches the repo method to return False. |
| 4a | 412 is added to the error codes (R6) and declared in OpenAPI. | `errors.py` has `PreconditionFailedError` and `_HTTP_CODES[412]`. CLAUDE.md R6 lists 412. `test_openapi_contract.py › test_patch_412_and_if_match_declared` checks that both PATCH operations have a `412` response referencing the envelope, an `If-Match` header parameter (not required), and an `ETag` header on 200. `test_etag_header_declared` covers GET org, GET entitlement, the PATCHes and the POST 201s. |
| 4b | CORS exposes `ETag` and allows `If-Match` for admin origins (ADM-012). | `test_admin_cors.py › test_preflight_admin_origin_allowed_if_match`; the updated `test_preflight_admin_origin_allowed`, which asserts `"Authorization, Content-Type, If-Match"`; `test_cors.py › test_admin_policy_preflight_allowed`, updated. `ETag` is already in `expose_headers`: `test_admin_cors.py` already asserts that exposure. Confirm it, or add an assertion on an actual PATCH response. |
| 5 | Tests: matching and stale `If-Match`, no header, ETag changes after update, 404 and 403 unchanged. | The tests in the "Tests to write" table below, plus green `make check`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/etag.py` | Add `version_etag(dt) -> str`, `IfMatch` (frozen dataclass: `any: bool`, `versions: frozenset[datetime]`), `parse_if_match(values: list[str] \| None) -> IfMatch \| None`, constant `MAX_IF_MATCH = 2048`, OpenAPI constant `ETAG_RESPONSE_HEADER` and text `IF_MATCH_DESCRIPTION`. The existing `strong_etag` and `if_none_match` stay untouched. | AC1, AC2. Pure, unit-testable helpers shared by both modules. |
| `app/core/errors.py` | Add `PreconditionFailedError(AppError)` (412, `precondition_failed`; ctor takes `current: datetime` and sets header `ETag`). Add `412: "precondition_failed"` to `_HTTP_CODES`. Add `precondition_failed` to the `ErrorBody.code` description. | AC2, AC4 (R6). |
| `app/core/cors.py` | `ADMIN_POLICY`: add `"if-match"` to `allow_headers`; set `allow_headers_value="Authorization, Content-Type, If-Match"`. | AC4. Browsers block the PATCH preflight today. |
| `app/db/base.py` | Add module function `bumped_updated_at(column)`, which returns `func.greatest(func.clock_timestamp(), column + timedelta(microseconds=1))`. `TimestampMixin` is **not** changed. | AC3 and AC5 ("ETag changes"). A strictly increasing value per row; see Approach §3. |
| `app/modules/organizations/repository.py` | Add `update_if_version(org_id, expected: datetime, values: dict[str, object]) -> bool` and `current_version(org_id) -> datetime \| None`. | AC3 (R1: the SQL lives in the repository). |
| `app/modules/organizations/service.py` | `update(..., precondition: IfMatch \| None = None)`. The new conditional branch is described in Approach §4. The existing branch is kept verbatim. | AC2, AC3. |
| `app/modules/organizations/router.py` | PATCH: add the `If-Match` header param, pass the parsed value, set the `ETag` response header, declare 412 and the `ETag` response header. GET one and POST: set and declare `ETag`. | AC1, AC2, AC4. |
| `app/modules/entitlements/repository.py` | Add `update_if_version(ent_id, expected, values) -> bool` and `current_version(org_id, ent_id) -> datetime \| None`. | AC3. |
| `app/modules/entitlements/service.py` | Add `get(org_id, ent_id) -> EntitlementView` (org 404, then entitlement 404). `update(..., precondition=None)` gets the same conditional branch as organizations. | AC1 (new GET), AC2, AC3. |
| `app/modules/entitlements/router.py` | **New** `GET /{entitlement_id}` (READ permission, 404 `_ENT_404`, `ETag`). PATCH: add `If-Match`, 412 and `ETag`. POST 201: add `ETag`. | AC1, AC2, AC4. |
| `CLAUDE.md` | R6 line only. Change `Codes: 201 create, 204 delete, 401, 403, 404, 409, 422.` to `Codes: 201 create, 204 delete, 401, 403, 404, 409, 412 (stale If-Match, ADM-024), 422.` | Owner decision (ticket answers). |
| `docs/spec-v1.md` | Add one dated ADM-024 note under §5.1 and one under §5.6 (text in step 9). | Owner decision ("CLAUDE.md and spec"). Also documents the new entitlement GET. |
| `docs/adr/0011-admin-cors.md` | Add an amendment line: `Allow-Headers: Authorization, Content-Type, If-Match` (ADM-024). | Keeps the ADR true. Line 32 states the old value. |
| `docs/consumers.md` | In the admin section (near line 93): add one bullet on ETag, `If-Match` and 412 usage, and the new entitlement GET. | admin-pan is the requester. This is the consumer contract doc. |
| `tests/test_etag.py` | Unit tests for `version_etag` and `parse_if_match`. | AC1, AC2. |
| `tests/test_organizations.py` | API and repository tests for orgs. | AC1–AC3, AC5. |
| `tests/test_entitlements_api.py` | API tests for entitlement GET and PATCH. | AC1, AC2, AC5, R8 (new GET). |
| `tests/test_entitlements_repository.py` | Repository test: `update_if_version` returns False on a mismatch. | AC3. |
| `tests/test_admin_cors.py` | Update line 101 to the new allow-headers value. Remove `"if-match"` from the disallowed parametrize at line 116 and replace it with `"if-none-match"`, so the case stays disallowed. Add an allowed `If-Match` preflight test. | AC4. The existing test asserts the old contract. |
| `tests/test_cors.py` | Update the line 311 assertion. Add `("authorization, if-match", True)` to the case-insensitivity parametrize at line 319. | AC4. |
| `tests/test_openapi_contract.py` | Add `("get", f"{_E}/{{entitlement_id}}"): {"404"}` to `_EXPECTED_ERRORS`. Add the 412, If-Match and ETag declaration tests. | AC4. The new route must be listed. |

No other file may be touched without a note in `impl-log.md` (R7).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_etag.py › test_version_etag_is_quoted_epoch_microseconds` | `"1763554800123456"` for a known UTC datetime. A non-UTC aware datetime gives the same tag (normalised). Exact integer arithmetic, no float. |
| `test_etag.py › test_parse_if_match_absent` | `None` and `[]` give `None` (no check). |
| `test_etag.py › test_parse_if_match_star` | `["*"]` and `[" * "]` give `IfMatch(any=True)`. |
| `test_etag.py › test_parse_if_match_round_trip` | `parse_if_match([version_etag(dt)]).versions == {dt}`. |
| `test_etag.py › test_parse_if_match_list_and_multiple_lines` | `['"1", "2"']` and `['"1"', '"2"']` both give two versions. |
| `test_etag.py › test_parse_if_match_never_matches` | Each gives `IfMatch(any=False, versions=∅)`: `W/"1"`; `"abc"`; unquoted `1`; `"*"` inside a list (`'*, "1"'`); 19+ digits or overflow (e.g. `"999999999999999999"`, which is past year 9999); a header over 2048 chars; empty quotes `""`. |
| `test_organizations.py › test_create_org_returns_etag` | AC1. POST 201 `ETag` matches the body's `updated_at`. |
| `test_organizations.py › test_get_org_returns_etag` | AC1. |
| `test_organizations.py › test_patch_org_returns_etag` | AC1. No header; `ETag` matches the body. |
| `test_organizations.py › test_patch_org_matching_if_match_changes_etag` | AC2 and AC5. GET, then PATCH with that ETag: 200, the body changed, the new ETag differs from the old, exactly one `organization.updated` audit row. A second PATCH with the new ETag: 200, and its ETag differs again (strictly increasing). |
| `test_organizations.py › test_patch_org_stale_if_match_412` | AC2. Get tag A; PATCH with A (gives B); PATCH with A again: 412 envelope, response `ETag == B`, the row is unchanged, the audit count is unchanged. |
| `test_organizations.py › test_patch_org_if_match_list_containing_current` | `If-Match: "0", <current>` gives 200. |
| `test_organizations.py › test_patch_org_if_match_star` | `If-Match: *` gives 200 and the update is applied. |
| `test_organizations.py › test_patch_org_weak_if_match_412` | `W/<current>` gives 412. |
| `test_organizations.py › test_patch_org_malformed_if_match_412` | `If-Match: garbage` gives 412 and nothing changes. |
| `test_organizations.py › test_patch_org_noop_with_matching_if_match` | Body `{}` or same values with the current tag: 200, the same ETag, no audit row. |
| `test_organizations.py › test_patch_org_without_if_match_unchanged` | AC2b. The update is applied, `ETag` is present, the audit row is written, as before. |
| `test_organizations.py › test_patch_org_unknown_id_404_beats_412` | Unknown id with a stale `If-Match` gives 404. |
| `test_organizations.py › test_patch_org_if_match_403_unchanged` | No WRITE permission plus `If-Match` gives 403 `permission_denied`. |
| `test_organizations.py › test_patch_org_if_match_401_unchanged` | No Bearer gives 401. |
| `test_organizations.py › test_patch_org_lost_race_412` | Monkeypatch `OrganizationRepository.update_if_version` to return False: 412 with `ETag` of the current version, no audit row. |
| `test_organizations.py › test_repo_update_if_version_mismatch_returns_false` | Repository with a wrong `expected` returns False and the row is unchanged. With the right `expected` it returns True and `updated_at` is strictly greater. |
| `test_entitlements_api.py › test_get_entitlement_returns_etag` | New GET happy path. The body equals the matching list item, and `ETag` matches it. |
| `test_entitlements_api.py › test_get_entitlement_unknown_org_404`, `…_unknown_entitlement_404`, `…_other_orgs_entitlement_404` | R8 404 cases for the new GET. |
| `test_entitlements_api.py › test_get_entitlement_403`, `…_401` | R8 auth cases for the new GET. A read-only staff member (READ only) gets 200. |
| `test_entitlements_api.py › test_grant_entitlement_returns_etag` | POST 201 `ETag`. |
| `test_entitlements_api.py › test_patch_entitlement_returns_etag` | AC1. |
| `test_entitlements_api.py › test_patch_entitlement_matching_if_match_changes_etag` | AC2 and AC5, with `limits` replaced: the new ETag differs and one `entitlement.updated` audit row is written. |
| `test_entitlements_api.py › test_patch_entitlement_stale_if_match_412` | The ticket's motivating case. Two clients hold tag A. Client 1 PATCHes `limits` and gets 200. Client 2 PATCHes `limits` with A: 412, client 1's `limits` are kept, no audit row from client 2, and the 412 carries the current `ETag`. |
| `test_entitlements_api.py › test_patch_entitlement_if_match_star`, `…_weak_if_match_412`, `…_noop_with_matching_if_match`, `…_without_if_match_unchanged` | As for organizations. |
| `test_entitlements_api.py › test_patch_entitlement_404_beats_412`, `…_if_match_403_unchanged` | AC5. |
| `test_entitlements_repository.py › test_update_if_version_mismatch_returns_false` | AC3 at repository level. True on a match, with `updated_at` strictly increasing. |
| `test_admin_cors.py › test_preflight_admin_origin_allowed_if_match` | Preflight PATCH `/organizations/{id}` with `authorization,content-type,if-match` gives 204 with `Allow-Headers: Authorization, Content-Type, If-Match`. |
| `test_openapi_contract.py › test_patch_412_and_if_match_declared` | Both PATCHes: `412` references the envelope; the `If-Match` param has `in: header` and is not required; 200 has an `ETag` header. |
| `test_openapi_contract.py › test_etag_header_declared` | `ETag` is in the response headers of GET org (200), GET entitlement (200), POST org and POST entitlement (201). |

## Approach
1. **ETag format (`app/core/etag.py`).** `version_etag(dt)` returns `'"' + str((dt.astimezone(UTC) - EPOCH) // timedelta(microseconds=1)) + '"'`. It is strong, opaque and exact (microseconds are the timestamptz precision).
   - Document that it validates the **stored row**. Derived fields (entitlement `active`, `app_key`; org `created_by` after an FK SET NULL) can change without a new ETag. Do **not** add `If-None-Match`/304 on these routes; that is a follow-up, see consult-software-architect F3.
2. **`parse_if_match`** returns `None` when the header is absent or empty (unconditional).
   - It joins header lines with `,`. Over `MAX_IF_MATCH` it returns a never-matching value.
   - A trimmed header equal to `*` gives `IfMatch(any=True)`.
   - Otherwise it splits on `,` and keeps only strong, well-formed tags `"<1–18 digits>"`. It converts each with `EPOCH + timedelta(microseconds=n)`, and catches `OverflowError` (drop the tag).
   - `W/` tags, `*` inside a list and malformed entries are dropped. With nothing left, it returns `IfMatch(any=False, versions=frozenset())`, which always yields 412 (fail closed, RFC 9110 §13.1.1 strong comparison).
3. **Clock for the conditional write (`app/db/base.py`).** The conditional UPDATE sets `updated_at = GREATEST(clock_timestamp(), updated_at + interval '1 µs')`. This value is strictly increasing per row, so there is no ABA problem, and it changes even inside the per-test outer transaction, where `now()` is constant (conftest L98–106).
   - `TimestampMixin.onupdate` stays `now()` (narrowest change, R7). The no-`If-Match` path keeps its current behaviour and clock.
   - Consequence, stated for the reviewer: tests of "ETag changes after update" go through the `If-Match` path. The no-header tests assert only that `ETag` equals the body's `updated_at`.
   - No schema change, so `alembic check` stays clean (R2 not triggered).
4. **Service flow (both modules, R1).** `update(..., precondition: IfMatch | None = None)`:
   - a. Load the row as today (`self.get` / `self._get`). 404 is raised first, so the order is 401/403 (deps), then 422 (body), then 404, then 412.
   - b. If `precondition is None` or `precondition.any`, run the **existing code verbatim**: setattr, flush, refresh, audit. Nothing changes without the header (R9). `*` means "any current version", so no version condition is needed. Atomicity is moot because every version satisfies the precondition.
   - c. Otherwise, if `row.updated_at not in precondition.versions`, raise `PreconditionFailedError(current=row.updated_at)`.
   - d. Compute `values` with the same rules as the existing loop:
     - org: `name` and `status` with `None` are skipped; `description` may be set to `None`;
     - entitlement: any `None` is skipped.
     - Keep only fields whose value differs from the row. If the result is empty, return the row unchanged: 200, same ETag, no audit, no write.
   - e. `before = diff.snapshot(row, …)`, then `ok = await repo.update_if_version(id, expected=row.updated_at, values=values)`.
   - f. If not `ok` (lost race), `current = await repo.current_version(...)`. If `None`, raise `NotFoundError`, using the same message as `get`. Otherwise raise `PreconditionFailedError(current=current)`.
   - g. If `ok`, `await session.refresh(row)` (the pattern the services already use), then write the audit row via the existing `_record` (orgs) or `audit.record` (entitlements). This happens in the same transaction (R4). For entitlements, return `await self._view(...)`.
   - No audit row is ever written on 412. The error is raised before any `record`, and the request transaction rolls back. Do **not** use `record_failure`/`record_denied`. `PreconditionFailedError` is not added to `DENIED_ERRORS`.
5. **Repository (`update_if_version`).** Run `update(Model).where(Model.id == id, Model.updated_at == expected).values(**values, updated_at=bumped_updated_at(Model.updated_at)).returning(Model.id).execution_options(synchronize_session=False)`, then return `result.scalar_one_or_none() is not None`.
   - Under READ COMMITTED, a concurrent writer that commits first makes this UPDATE re-check its `WHERE` on the new row version and match 0 rows. That makes the compare and the write atomic.
   - `current_version` runs `select(Model.updated_at).where(...)`, a scalar select that bypasses the identity map.
   - The entitlement `limits` value is a fresh dict and JSONB binds directly.
6. **`PreconditionFailedError`.** `status_code = 412`, `code = "precondition_failed"`, message `"Resource was modified; re-fetch and retry"`, `details=None`, `headers={"ETag": version_etag(current)}`. It follows the `RateLimitedError` precedent (a service-raised error that carries a header), so the service never builds HTTP objects (R1).
7. **Routers.**
   - Declare `if_match: Annotated[list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)] = None`, and add a `response: Response` param.
   - The router only parses: `parse_if_match(if_match)` is passed to the service. It sets `response.headers["ETag"] = version_etag(read.updated_at)` from the returned read model.
   - Declare responses:
     - orgs: extend the `_route(...)` dict with `412`, and add `200`/`201` entries `{"description": ..., "headers": {"ETag": ETAG_RESPONSE_HEADER}}`;
     - entitlements: use `require_permission(errors={404: _ENT_404, 412: ...})`, and then add the `200` headers entry to the returned `responses`.
   - Check in the OpenAPI test that the `response_model` schema survives the merge.
   - New entitlement GET: `@router.get("/{entitlement_id}", response_model=EntitlementRead, **require_permission(READ, errors={404: _ENT_404}))`, which calls `EntitlementService.get`. Auth class 1 (R3), permission `admin:entitlements:read`. It is read-only, so R4 does not apply.
8. **CORS.** `ADMIN_POLICY` only. The identity policy is untouched, since these are Bearer routes.
9. **Docs.**
   - CLAUDE.md R6 line, as above.
   - Spec §5.1 and §5.6 note: `> 2026-11-19 (ADM-024, owner): GET/PATCH (and POST 201) return a strong ETag derived from updated_at; PATCH accepts optional If-Match (strong comparison, * = any) and answers 412 precondition_failed with the current ETag when stale, changing nothing; without If-Match behaviour is unchanged. 412 joins the error codes (R6).` §5.6 adds: `New GET /api/v1/organizations/{org_id}/entitlements/{entitlement_id} (admin:entitlements:read).`
   - ADR-0011 amendment line; consumers.md bullet.
10. **Run `make check`** (R8).

Size: roughly 280 production lines and 350 test lines. That is above the ~400-line guide counting tests, but it is one feature with a mechanical, mirrored shape across two modules. No split (see Split proposal).

## Specialist input used
- **api-platform-engineer, adopted:**
  - R-1 epoch-µs strong ETag, pure helpers in `etag.py`;
  - R-2 parsing rules (join lines, 2048 cap, drop `W/`, `*` alone, fail closed on malformed);
  - R-3 layering and repository-owned conditional UPDATE;
  - R-5 `PreconditionFailedError`, `_HTTP_CODES`, `ErrorBody` description, current ETag on 412;
  - R-6 OpenAPI declarations;
  - R-7 CORS;
  - R-8 test list.
- **api-platform-engineer, rejected:**
  - R-4 "change `TimestampMixin.onupdate` globally": a global behaviour change for every table, which this ticket does not need (R7). The architect's narrower option is used instead.
  - R-3 "plain SELECT for no-op with If-Match": the row is already loaded and compared in step 4c, so no extra query is needed.
- **software-architect, adopted:**
  - Rec 1, compare-then-conditional-write, keeping the no-`If-Match` path verbatim (R9);
  - Rec 2, `GREATEST(clock_timestamp(), updated_at + 1 µs)`, leaving the mixin alone;
  - F3, do not offer `If-None-Match` on these routes;
  - F4, no `record_failure`/`record_denied` on 412;
  - Rec 7, test list including no-op with a matching tag.
- **software-architect, rejected:** Rec 3, an ETag that hashes `(id, updated_at)`. A hash cannot be turned back into a value for `WHERE updated_at = :v` without an extra compare. Epoch-µs is reversible and the owner chose "derived from `updated_at`".
- **Resolved differences and open questions:**
  - Entitlement GET (architect OQ1, F7): add `GET …/entitlements/{entitlement_id}`. AC1 explicitly says "GET … of an entitlement". An `etag` field on list items would be a body change and still not a GET.
    - The route is additive in v1 (R9) and reuses the existing `get_view`. It needs only READ permission, which already exists, and costs about 15 lines plus the R8 tests.
    - Lists keep no ETag: `updated_at` is in each item and `version_etag` is documented, so a client can derive it.
  - `*` (architect OQ4): accept it per RFC 9110. It matches any existing row (404 still wins) and takes the unconditional path.
  - 412 carries the current `ETag` (both consults lean yes): adopted. It is cheap, because the row or `current_version` is already in hand.
  - POST 201 `ETag` (both consults recommend it): adopted. It is additive and lets admin-pan edit right after creating.
  - Row deleted between the 404 check and the conditional write (api OQ4): re-select with `current_version`. If the row is gone, answer 404; otherwise 412 with the current ETag.
  - `If-Match` on DELETE, and `If-None-Match`/304 (api OQ5/6): out of scope, recorded as follow-ups.
  - `SELECT … FOR UPDATE`: fallback only, not used. The owner chose a conditional UPDATE.

## Assumptions
Needs owner's decision (non-blocking; the plan proceeds with these defaults):
1. **Clock:** only the conditional write uses `GREATEST(clock_timestamp(), updated_at + 1 µs)`. `TimestampMixin` stays `now()`, so the no-header path keeps its current clock. Accepted as consistent with "ETag from `updated_at`, conditional UPDATE, no migration".
2. **New endpoint** `GET /api/v1/organizations/{org_id}/entitlements/{entitlement_id}` (admin:entitlements:read), added to satisfy AC1.
3. **`If-Match: *`** is accepted (RFC behaviour) and means "any existing version".
4. **Malformed, weak or over-long `If-Match`** gives 412 (fail closed), not 400 and not ignored.
5. **412 carries the current `ETag` header**, with `details: null`.
6. **POST 201** responses carry `ETag` too.
7. **No-op PATCH with a matching `If-Match`** gives 200 with the unchanged ETag. A no-op with a stale tag gives 412, because the precondition is evaluated before change detection.
8. The ETag covers stored fields only. Derived `active`, `app_key` and `created_by` can change under the same ETag (documented in consumers.md). `If-None-Match` is not supported on these routes.

Other assumptions:
- `tests/conftest.py`'s single connection makes a true two-connection race test unnecessary. The SQL `WHERE` guarantee is covered by the repository mismatch tests and the monkeypatched lost-race test.
- No committed OpenAPI snapshot file exists (none under `docs/`). If `make check` reveals one, regenerate it and note this in `impl-log.md`.

Follow-ups (not in this diff, R7): `If-Match` on DELETE for orgs and entitlements; `If-Match` on other PATCH routes (apps, roles, users); `If-None-Match`/304 on single-resource GETs (it would need a representation-based tag).

## Blocking questions
None.

## Split proposal
None. It is a single feature with mirrored changes in two modules. Splitting orgs from entitlements would ship a half contract to admin-pan and duplicate the shared core work (etag, errors, CORS, docs).

## Data model / migration
None. `updated_at` (timestamptz, existing) is the version. `TimestampMixin` is unchanged. `alembic check` must still report no drift.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/organizations/{org_id}` | — | `OrganizationRead` + header `ETag` (new) | 200, 401, 403, 404 | `admin:organizations:read` |
| POST | `/api/v1/organizations` | `OrganizationCreate` | `OrganizationRead` + `ETag` (new) | 201, 401, 403, 409, 422 | `admin:organizations:write` |
| PATCH | `/api/v1/organizations/{org_id}` | `OrganizationUpdate`; optional header `If-Match` (new) | `OrganizationRead` + `ETag` (new) | 200, 401, 403, 404, **412 `precondition_failed` (new, with `ETag`)**, 422 | `admin:organizations:write` |
| GET | `/api/v1/organizations/{org_id}/entitlements/{entitlement_id}` | — | `EntitlementRead` + `ETag` | 200, 401, 403, 404 | `admin:entitlements:read` (**new endpoint**) |
| POST | `/api/v1/organizations/{org_id}/entitlements` | `EntitlementCreate` | `EntitlementRead` + `ETag` (new) | 201, 401, 403, 404, 409, 422 | `admin:entitlements:write` |
| PATCH | `/api/v1/organizations/{org_id}/entitlements/{entitlement_id}` | `EntitlementUpdate`; optional header `If-Match` (new) | `EntitlementRead` + `ETag` (new) | 200, 401, 403, 404, **412 (new, with `ETag`)**, 422 | `admin:entitlements:write` |
| OPTIONS (preflight, admin origins) | all admin routes | `Access-Control-Request-Headers` may include `if-match` | `Access-Control-Allow-Headers: Authorization, Content-Type, If-Match` | 204 | — |

`ETag` format: `"<integer microseconds since Unix epoch of updated_at (UTC)>"`, strong. `If-Match`: a comma-separated list of strong tags, or `*`. Absent means unconditional, exactly as before.
