# ADM-042: Usage consume, refund and current-usage read for app backends

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth, security |
| Gate | plan-approval |
| Depends on | ADM-039, ADM-041 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | claude@2026-11-22T03:49Z |
| PR | — |

## Goal
Both the app and this API enforce limits: the app backend consumes before each AI action and is refused when the user is over the limit.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ59, OQ60, OQ66.

## Scope
- Counters (metric, user, period_start, used) and a usage-event ledger (idempotency key unique per app, amount, status) + migration.
- App-credential routes (scopes from ADM-041): read a user's limits/used/remaining per metric; atomic consume (single conditional upsert; 409 `usage_limit_exceeded` when over; idempotent replay); refund by idempotency key; estimate-then-settle for duration metrics.
- Disabled users or non-members → 403; unknown/archived metric → 404; per-key rate limit; no CORS; no per-use audit rows (the ledger is the record).
- Scope column values extended (migration) per ADM-041.

## Acceptance criteria
- [ ] Consume within limit, at limit, over limit (409); parallel consumes never exceed the limit (concurrency test).
- [ ] Idempotent replay returns the original result; a different payload with the same key → 409.
- [ ] Refund restores the amount, never below 0; settle adjusts an estimate.
- [ ] Wrong scope 403, other app 403, admin JWT 401.

## Notes / answers from Ming
- Plan approved (2026-11-22, batch 2026-11-22-1) — Ming: "duyệt hết; câu 1–22 theo mặc định" (all batch questions answered with the defaults).

## Handoff
