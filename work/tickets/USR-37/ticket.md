# USR-37: Single role-assignment POST — correct OpenAPI errors and add stable 409 reasons

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Size | S |
| Areas | api |
| Gate | plan-approval |
| Depends on | ADM-023 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | claude@2026-11-27T00:54Z |
| PR | — |

## Goal
The documented errors of `POST /api/v1/users/{user_id}/role-assignments` match what the API really returns, and its 409s carry a stable reason clients can map. Helps admin-pan's single-assign dialog and PAN-033.

## Scope reference
- `docs/spec-v1.md` §5.15 Part 4 (owner, 2026-11-26, OQ77). Reason naming aligned with USR-26 (`changed_concurrently`).

## Scope
- Correct the OpenAPI responses: 404 `not_found`; 409 `conflict` (remove the wrong "app disabled, user unverified or disabled" text); 422 reasons `unknown_role`, `org_id_not_allowed`, `unknown_org`, `expiry_not_allowed`, `expires_in_past`; 403 `admin_wildcard_required` and `self_assignment_forbidden`.
- Add `details: [{reason}]` to the 409s, keeping `code: conflict`: `assignment_exists` (active duplicate or racing insert) and a concurrent-change reason (owner's example `concurrent_change`; PLAN aligns with USR-26's `changed_concurrently`).
- The next release publishes the corrected OpenAPI (admin-pan regenerates types).

## Out of scope
- Any other behaviour change on this endpoint; target-state checks (OQ78).

## Acceptance criteria
- [ ] OpenAPI for the POST lists exactly the real 401/403/404/409/422 outcomes with the reasons above; no mention of checks the code does not make.
- [ ] Active duplicate → 409 `conflict` with `details[0].reason = assignment_exists`; concurrent change → 409 with the chosen concurrent-change reason.
- [ ] All other responses unchanged (additive, R9); existing tests stay green.
- [ ] Tests assert each 409 reason and the OpenAPI responses of the route.

## Technical notes
- Files: `app/modules/rbac/assignments_router.py` (responses), `app/modules/rbac/service.py` (409 details); both High paths.
- If USR-36 lands first, the batch may reuse the same reason names in `failed` errors.

## Test plan
- API tests for both 409 reasons; OpenAPI snapshot / schema assertion for the route.

## Notes / answers from Ming
- 2026-11-26 (Ming, chat): "duyệt hết; câu 1–34 theo mặc định; cho merge USR-34, USR-35".
- Plan approved (2026-11-26, batch 2026-11-26-2); all blocking questions answered with the plan defaults.

## Handoff
