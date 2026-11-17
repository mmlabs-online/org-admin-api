# USR-17: Label and optional expiry for app credentials

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can tell app credentials apart and let a credential expire on its own (ADR-0016 follow-ups: credential `expires_at` and `label`).

## Spec reference
§5.3 Authorization (apps registry; D25 app credential auth class, ADR-0016 follow-ups).

## Acceptance criteria
- [ ] Optional `label` (short text, length cap and display-text rules from ADM-011) on issue; returned by the credentials list.
- [ ] Optional `expires_at` (future `timestamptz`) on issue; returned by the list with `active=false` once expired.
- [ ] An expired credential is refused on `PUT /api/v1/apps/{key}/manifest` exactly like a revoked one (same status and error, no extra detail).
- [ ] Hand-reviewed migration with working `downgrade()` (R2); issue audit row records label and expiry (no secret).
- [ ] Tests: issue with/without label and expiry, past `expires_at` → 422, expired key refused, list shows `active=false`.

## Dependencies
USR-05, USR-05b. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
