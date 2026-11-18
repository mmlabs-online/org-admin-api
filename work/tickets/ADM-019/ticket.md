# ADM-019: Guard org_mode required → none while org data exists

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P3 |
| Areas | api, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Changing an app from `org_mode=required` to `none` must not silently orphan org-scoped data.

## Spec reference
§5.6, ADR-0013, ADR-0015; ADM-006 follow-up

## Acceptance criteria
- [ ] `PATCH /api/v1/apps/{key}` setting `org_mode` from `required` to `none` returns 409 (`conflict`, details reason e.g. `org_mode_in_use`) when the app has org entitlements or org-scoped role assignments
- [ ] No change when nothing org-scoped exists; the reverse change (none → required) unaffected
- [ ] Audit: refused attempt writes no mutation row (existing pattern); tests for both blockers and the allowed case

## Dependencies
ADM-006.

## Notes / answers from owner
- 2026-11-17 (owner): block with 409 (option chosen).
