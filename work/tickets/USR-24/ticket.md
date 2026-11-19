# USR-24: Optimistic concurrency on PUT role permissions

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Areas | api, db |
| Attempts | 0 |
| Created | 2026-11-19 |
| Gate | plan-approval |
| Claimed by | claude@2026-11-19T08:17Z |
| PR | — |

## Goal
Two staff editing the same role's permissions no longer silently overwrite each other: `PUT /api/v1/apps/{key}/roles/{role_id}/permissions` is a full replace with no version check today, and `RoleDefinitionRead.updated_at` is not bumped by a permission replace (only by rename), so clients cannot use it as a version.

## Spec reference
§5.3 Authorization (roles and permissions are data managed by staff). Extends ADM-024 (same precondition shape for organizations, entitlements and roles).

## Acceptance criteria
- [ ] Every permission replace bumps a version visible on `RoleDefinitionRead` and `RoleRead` (bump `updated_at`, or an integer `version`; chosen at PLAN, consistent with ADM-024).
- [ ] The PUT accepts an optional precondition (`If-Match` with an ETag from `GET …/role-definitions/{role_id}`, or a body `expected_version`; chosen at PLAN with ADM-024). On mismatch: 412 (owner OK'd 412 for ADM-024, 2026-11-18) or 409 with `details: [{"reason": "role_changed"}]` — one choice, documented; nothing written, no audit row.
- [ ] Without a precondition the PUT behaves as today (R9).
- [ ] Compare and write are atomic (no window between check and replace).
- [ ] Tests: matching version succeeds and bumps it; stale version → 412/409 with the reason, permissions unchanged, no audit row; no precondition still works.

## Dependencies
USR-02d, USR-16. Shape shared with ADM-024 (plan together or after it). Consumer: admin-pan roles screen (PAN-030). Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner

- 2026-11-19 (owner): Plan approved (2026-11-19, batch 2026-11-19-1); questions 6–9 (batch numbering; plan.md questions 1–4) per the defaults in plan.md — "duyệt hết; câu 1–9 theo mặc định".

## Handoff
### 2026-11-19 — claude — BLOCKED (waiting plan approval, batch 2026-11-19-1)
- Agent calls: 3, tokens ~215k (api-platform-engineer×1 53k, database-optimizer×1 59k, planner×1 opus 103k)
- Done: consult (2), plan.md READY (builds on ADM-024), predicted tier high (app/modules/rbac/**).
- In progress / next: Ming answers batch questions 6–9 + plan approval → `/run-batch --resume`.
- Gotchas: same rbac files as USR-23 — rebase needed. Session-mandated branch used instead of `ticket/USR-24`.
