# ADM-040: Per-user usage limit overrides (staff)

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Size | S |
| Areas | db, api |
| Gate | plan-approval |
| Depends on | ADM-039 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | claude@2026-11-21T00:22Z |
| PR | — |

## Goal
Staff give one user more (or less) of a metric — raise or lower the per-period limit, optionally until a date — with a reason.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ62, OQ67.

## Scope
- Override table (user, metric, limit, optional `expires_at`, required reason, created_by) + migration; effective limit = unexpired override else metric default.
- Bearer routes (`admin:usage:write`) to set/remove an override for a user and metric; audited with the reason (no PII beyond ids).

## Acceptance criteria
- [ ] Set, replace, remove an override; expired overrides fall back to the default (DB clock).
- [ ] Reason required (422 without); audit rows; 401/403/404 tests.

## Notes / answers from Ming
- Plan approved (2026-11-20, batch 2026-11-20-2) — Ming: "câu 1–28 theo mặc định /run-batch --resume" (all batch questions answered with defaults; resume to BUILD taken as plan approval).

## Handoff
