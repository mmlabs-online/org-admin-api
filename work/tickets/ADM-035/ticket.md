# ADM-035: Deployer credential — data model and staff issue, list, revoke

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Size | M |
| Areas | db, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | claude@2026-11-20T07:30Z |
| PR | — |

## Goal
The deployment repo gets its own machine key, able only to report deploy results, managed by the super admin.

## Scope reference
`docs/spec-v1.md` §5.13 (OQ43); ADR-0021 §2–§3.

## Scope
- New table `deployer_credentials` (key_id, secret SHA-256 hash, created_by, created_at, last_used_at, revoked_at), at most 2 active; migration with `downgrade()`.
- Key format `mmdk_<key_id>_<secret>` (prefix-aware codec; wrong prefix rejected before any DB read).
- Bearer `admin:*` routes: `POST /api/v1/deployer-credentials` (value shown once, `Cache-Control: no-store`), `GET` list (no secrets), `DELETE /{id}` revoke; all audited. No CLI.
- `current_deployer` dependency for use by ADM-036 (no route uses it yet besides tests).

## Out of scope
- The deploy-report route (ADM-036).

## Acceptance criteria
- [ ] Issue/list/revoke work, are audited and need `admin:*` (401/403 tests); a 3rd active key → 409.
- [ ] Only the hash is stored; the value appears once and never in logs or audit.
- [ ] A revoked key is refused immediately; `last_used_at` updates on use.
- [ ] `mmak_` keys and admin JWTs are refused by `current_deployer` (401) and `mmdk_` keys by every other auth class.

## Technical notes
- Files: `app/modules/deployer_credentials/`, migration, `tests/test_route_auth_classes.py`, `docs/deploy.md` (issuing and rotating the key).

## Test plan
- API and dependency tests; migration round-trip.

## Notes / answers from Ming
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 1, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 4, tokens ~262k (consultants + planner×1 opus).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
