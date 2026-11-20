# ADM-046: Usage fail-open grace and late reconciliation

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | M |
| Areas | api, db |
| Gate | plan-approval |
| Depends on | ADM-042 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
An app that opts in can keep AI features running during a short API outage and report the usage afterwards.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ66.

## Scope
- Per-app (or per-metric) opt-in grace setting (staff, audited); a non-refusing `record` mode on the consume route for replaying queued uses with their idempotency keys, which may push `used` above the limit (overage visible to staff).

## Acceptance criteria
- [ ] Record mode accepted only for opted-in apps; replays are idempotent; overage shown in staff reads.

## Notes / answers from Ming

## Handoff
