# ADM-041: Usage key scopes (ADR-0022, amends ADR-0021)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Size | S |
| Areas | architecture, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | claude@2026-11-20T06:36Z |
| PR | — |

## Goal
Record how app backends call this API for usage: new scopes on the scoped app-credential mechanism, before any usage route exists.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ56, OQ61, OQ66; ADR-0021 (scoped app credentials).

## Scope
- ADR-0021 amendment (or ADR-0021): scopes `usage:read`, `usage:write`, `limits:write`; `on_behalf_of` rules for app-admin calls (`<app>:usage:manage`, app-scope assignment only); idempotency keys; fail-closed default and opt-in fail-open grace; per-key rate limit; why token issuance stays ungated.
- R3 class 4 wording in `CLAUDE.md`; D-log entry in `docs/identity-architecture.md`.

## Acceptance criteria
- [ ] ADR accepted by Ming at the plan gate; CLAUDE.md R3 and the D-log updated in the same PR.
- [ ] No code change in this ticket (docs only).

## Notes / answers from Ming
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 1, 21–27, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 4, tokens ~210k (consultants + planner×1 opus).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
