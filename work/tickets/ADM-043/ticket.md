# ADM-043: Staff usage reads

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | S |
| Areas | api |
| Gate | plan-approval |
| Depends on | ADM-042 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
Staff see a user's current usage per app and metric, and the kept history.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ65.

## Scope
- Bearer `admin:usage:read` routes: current period per user (limit, used, remaining, period_end), history from per-period totals and the 90-day ledger; `Page[T]`.

## Acceptance criteria
- [ ] Reads tested with overrides and expired overrides; 401/403/404.

## Notes / answers from Ming

## Handoff
