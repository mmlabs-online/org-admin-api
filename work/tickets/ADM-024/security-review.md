# ADM-024 — Security review

## Attempt 1

Static review only: `git diff origin/main...HEAD` plus DB-free `uv run python -c` calls to `parse_if_match` / `version_etag`. pytest and `make check` were not run; the reviewer confirms R8.

**Overall: PASS.** No blockers and no should-fix items, only two small notes.

### Findings

- **GREEN: the new GET does not leak existence or cross-org data.**
  - `GET /organizations/{org_id}/entitlements/{entitlement_id}` (`app/modules/entitlements/router.py:85-95`) uses `require_permission(READ)` and inherits the router-level `Depends(current_admin)` (`router.py:16-20`). A caller who is unauthenticated or lacks the permission gets 401/403 before any DB lookup, so they cannot probe whether an id exists.
  - `EntitlementService.get` (`service.py:55-60`) checks the org first, then calls `get_view(org_id, ent_id)`, which filters on both `org_id` and `id` (`repository.py:85-92`). An entitlement of another org returns 404, and the 404 messages are generic.
  - PATCH order is 403 → 404 → 412, so a 412 and its current ETag only reach a WRITE holder addressing a real resource.
- **GREEN: `If-Match` parsing is robust** (`app/core/etag.py:67-91`).
  - The joined header is capped at 2048 characters before splitting. Tags use an anchored `fullmatch` limited to 1–18 digits: no ReDoS and no unbounded integer. A value past datetime's maximum is caught as `OverflowError`.
  - The weak `W/` prefix, negative numbers, `*` inside a list, `",,,,"` and over-long input all give an empty `IfMatch`, so the request fails safely with 412. An empty or whitespace-only header is treated as absent, i.e. the update runs unconditionally as before.
  - A 2 KB worst case parses in about 0.15 ms.
  - `updated_at` survives an exact microsecond, timezone-aware round trip through `version_etag` and `parse_if_match`.
- **GREEN: the conditional write is atomic and writes no audit row on 412.**
  - `update_if_version` (`organizations/repository.py:59-70`, `entitlements/repository.py:117-128`) is a single `UPDATE … WHERE id AND updated_at = :expected RETURNING id`. Under READ COMMITTED a concurrent writer causes 0 rows and then 412, so nothing is lost between compare and write.
  - `bumped_updated_at` (`app/db/base.py:17-23`) gives each conditional write a strictly increasing version, which closes the ABA gap.
  - `PreconditionFailedError` is raised before the audit write, and the session rolls back on any exception, so there is no audit row and no partial write. The no-op path skips both the UPDATE and the audit. R4 holds.
  - No mass assignment: `values` comes only from `model_dump(exclude_unset=True)` of `extra="forbid"` schemas, and the DB CHECK constraints still apply to the Core UPDATE.
- **GREEN: the CORS change is narrow** (`app/core/cors.py:45-46`). `If-Match` is added only to `ADMIN_POLICY` (`credentials=False`); `IDENTITY_POLICY` and the cookie routes are unchanged.
- **GREEN: the ETag discloses nothing new.** It is `updated_at` in microseconds, which is already in the response body, and every response carrying it sits behind the same permission as the body. The 412 message (`errors.py:135-145`) is a fixed string with no ids, field values or internals.
- **NOTE: the entitlement conditional UPDATE does not filter on `org_id`** (`entitlements/repository.py:122`).
  - Safe today: `_get(org_id, ent_id)` checks org ownership just before, in the same transaction, and `org_id` is not patchable.
  - Defense in depth would add `org_id` to the WHERE. Follow-up.
- **NOTE: an unconditional PATCH can move the version backwards** (`app/db/base.py:39`, `onupdate=func.now()`).
  - A PATCH without `If-Match` stamps the transaction start time. If it waits on a lock behind a conditional write, it can commit an earlier `updated_at` than the value it replaces.
  - That only matters if the old value repeats to the microsecond, so the practical risk is negligible.

### Follow-ups (out of scope)
1. Add `org_id` to the WHERE of `EntitlementRepository.update_if_version`.
2. Optionally use `bumped_updated_at` for unconditional updates as well, so ETags only ever increase.
