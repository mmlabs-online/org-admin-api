# ADM-045: Usage event retention purge

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | S |
| Areas | db |
| Gate | plan-approval |
| Depends on | ADM-042 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
The per-use ledger stays bounded while per-period totals are kept.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ69.

## Scope
- Cleanup CLI: delete usage events older than 90 days (dry-run default, batched, counts only); keep per-period totals.

## Acceptance criteria
- [ ] Old events deleted, recent kept, totals untouched; dry-run changes nothing.

## Notes / answers from Ming

## Handoff
