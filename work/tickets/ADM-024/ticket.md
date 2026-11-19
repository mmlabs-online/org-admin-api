# ADM-024: Optimistic concurrency (ETag / If-Match) on PATCH for organizations and entitlements

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api, architecture |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Two staff editing the same organization or entitlement no longer silently overwrite each other (entitlement PATCH replaces `limits` as a whole).

## Spec reference
§5.1 Organizations and §5.6 Org product entitlements (existing PATCH). The spec has no concurrency requirement; this is a contract improvement requested by admin-pan — owner to confirm it is in scope.

## Acceptance criteria
- [ ] GET and PATCH of an organization and of an entitlement return an `ETag` (e.g. derived from `updated_at` or a version).
- [ ] PATCH accepts an optional `If-Match`; on mismatch it returns 412 with the error envelope and changes nothing (no audit row). Without `If-Match` behaviour is unchanged (R9).
- [ ] The check and the update are atomic (no window between compare and write).
- [ ] 412 is added to the error codes (R6) and declared in OpenAPI; CORS exposes `ETag` and allows `If-Match` for admin origins (ADM-012).
- [ ] Tests: matching and stale `If-Match`, no header, ETag changes after update, 404/403 unchanged.

## Dependencies
ADM-006, ADM-012, ADM-013. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
- R6 lists the allowed codes; adding 412 needs the owner's OK at PLAN.
- **Answered (owner, 2026-11-19):**
  - In scope.
  - 412 is added to the R6 codes (CLAUDE.md and spec). `If-Match` stays optional; a mismatch returns 412 `precondition_failed` and changes nothing.
  - The ETag is derived from `updated_at`, so no migration. The check is atomic via a conditional UPDATE (`WHERE updated_at = :v`).
