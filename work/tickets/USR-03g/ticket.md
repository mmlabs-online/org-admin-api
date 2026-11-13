# USR-03g: Join approval flow (join_requests + staff approve/reject)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-13 |
| PR | — |

## Goal
Apps with `join_policy=approval` can accept join requests that staff approve or reject.

## Scope
`join_requests` table (schema from USR-03b `consult-database-optimizer.md`); `POST /api/v1/auth/apps/{key}/join` answers 202 `{"status":"pending","request_id"}` for `approval` apps (idempotent while pending); staff `GET /api/v1/apps/{key}/join-requests` (`Page[T]`), `POST …/join-requests/{id}/approve|reject` (Bearer class, behind the admin RBAC flag).

## Spec reference
§5.3 Membership, §5.8; identity-architecture.md §3, §4; D9

## Acceptance criteria
- [ ] Migration: `join_requests` with CHECK `(status='pending') = (decided_at IS NULL)`, partial unique `(app_id, user_id) WHERE status='pending'`, indexes; working `downgrade()`
- [ ] Join on an `approval` app → 202 pending, idempotent; `/token` `join_available` reflects it
- [ ] Approve takes `lock_app`, re-checks verified email, active user and default role, grants the default role at app scope; reject records `decided_by/decided_at`; non-pending → 409, unknown → 404
- [ ] Audit via the USR-03b `AuditRecorder`; tests for each path

## Dependencies
USR-03b.

## Notes / answers from owner
- 2026-11-13: split from USR-03b by the planner (consults recommended it to keep USR-03b ~400 lines); owner to confirm (USR-03b plan O1).
- Open: cooldown after rejection; what approve grants (default role at approval time is the proposal); re-join deny-list (USR-03b F6).
