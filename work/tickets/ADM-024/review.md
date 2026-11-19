## Attempt 1 — 2026-11-19
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): green. ruff "All checks passed!", mypy "Success: no issues found in 149 source files", pytest "2652 passed, 1 warning in 332.33s". Matches impl-log (2652 passed).
- alembic check: "No new upgrade operations detected." (no schema change; R2 not triggered).
- OpenAPI inspected directly: PATCH org 200 keeps `$ref OrganizationRead` plus the `ETag` and `X-Request-ID` headers; 412 references the envelope; GET entitlement 200 and POST org 201 keep their response_model schema plus `ETag`.

### Acceptance criteria
- [x] GET and PATCH of an org and of an entitlement return an `ETag` derived from `updated_at`. Evidence: `app/core/etag.py` `version_etag`; routers set the header in `organizations/router.py` (create/get/update) and `entitlements/router.py` (grant/new get/update). Tests: `test_create_org_returns_etag`, `test_get_org_returns_etag`, `test_patch_org_returns_etag`, `test_grant_entitlement_returns_etag`, `test_get_entitlement_returns_etag`, `test_patch_entitlement_returns_etag`. All pass.
- [x] PATCH accepts an optional `If-Match`. A mismatch gives 412 with the envelope, changes nothing and writes no audit row. Without the header behaviour is unchanged. Evidence: service branches in `organizations/service.py` `update` and `entitlements/service.py` `update`; the no-header branch keeps the original setattr/flush/refresh/audit sequence. Tests: `test_patch_org_stale_if_match_412` and `test_patch_entitlement_stale_if_match_412` check the envelope, the current `ETag`, an unchanged row and an unchanged audit count. Also `..._weak_or_malformed_if_match_412`, `..._weak_if_match_412`, `..._without_if_match_unchanged`, and no edits to existing PATCH tests (the only removed test lines are the CORS allow-headers assertions, which had to change).
- [x] Check and update are atomic. Evidence: the repositories' `update_if_version` runs one `UPDATE … WHERE id = :id AND updated_at = :expected RETURNING id`. A lost race is re-read via `current_version`: 404 if the row is gone, else 412. Tests: `test_repo_update_if_version_mismatch_returns_false`, `test_update_if_version_mismatch_returns_false`, `test_patch_org_lost_race_412`.
- [x] 412 is in the R6 codes and declared in OpenAPI. CORS allows `If-Match`, and `ETag` is exposed for admin origins. Evidence: CLAUDE.md R6 (owner-approved), `errors.py` `PreconditionFailedError` + `_HTTP_CODES[412]`, `cors.py` ADMIN_POLICY (`ETag` was already in expose_headers). Tests: `test_patch_412_and_if_match_declared`, `test_etag_header_declared`, `test_preflight_admin_origin_allowed_if_match`, updated `test_cors.py` cases.
- [x] Tests cover matching and stale `If-Match`, no header, the ETag changing after an update, and 404/403 unchanged. Evidence: `test_patch_org_matching_if_match_changes_etag`, `test_patch_entitlement_matching_if_match_changes_etag`, `test_patch_org_unknown_id_404_beats_412`, `test_patch_org_if_match_403_and_401`, `test_patch_entitlement_404_beats_412_and_403_unchanged`. The new entitlement GET has R8 coverage: `test_get_entitlement_returns_etag`, `test_get_entitlement_404s`, `test_get_entitlement_401_and_403`.

### Issues
None.

### Scope
Every changed file is in the plan's file list, except `tests/test_route_auth_classes.py`. That file is justified under "Out-of-plan files" in impl-log, because the new GET route must be mapped to `admin:entitlements:read`. The `work/backlog.md` and ticket.md state changes are workflow bookkeeping.

### Rules R1–R11
No violations found.
- R1: routers only parse the header and set response headers. Services raise `PreconditionFailedError` (an AppError that carries headers, like the `RateLimitedError` precedent). The conditional SQL lives in the repositories.
- R3: the new GET is Bearer with `require_permission("admin:entitlements:read")`.
- R4: the audit row is written in the same transaction on a successful conditional write. No audit row on a 412 or a no-op.
- R6: one envelope, and 412 was added with owner approval.
- R9: all changes are additive: an optional header, a new response header, and a new GET.

### Nits (do not block)
- Mixed clocks: the conditional path writes `GREATEST(clock_timestamp(), updated_at + 1µs)` but the unconditional path still uses `now()` (TimestampMixin). A later header-less PATCH in a transaction that started earlier could set `updated_at` lower than a value written by a conditional PATCH. A strong-ETag collision would need an exact repeat of an earlier value, so the risk is theoretical. The plan accepted this (Assumption 1).
- `_with_etag` is duplicated in `organizations/router.py` and `entitlements/router.py`. It could live next to `ETAG_RESPONSE_HEADER` in `app/core/etag.py`.
- `tests/test_openapi_contract.py` `_EXPECTED_ERRORS` still lists only `{"404"}` for both PATCH routes. 412 is covered by the separate `test_patch_412_and_if_match_declared`, so listing it in the map is optional.

### Follow-ups (out of scope)
- `If-Match` on DELETE for orgs and entitlements, and on the other PATCH routes (apps, roles, users).
- `If-None-Match`/304 on single-resource GETs. This needs a representation-based tag, because the version tag does not cover derived fields.
- Consider moving `TimestampMixin.onupdate` to the same strictly increasing expression, so both paths share one clock.
