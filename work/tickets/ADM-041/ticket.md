# ADM-041: Usage key scopes (ADR-0022, amends ADR-0021)

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P2 |
| Size | S |
| Areas | architecture, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/83 |

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
- 2026-11-20 (Ming, chat): "merge cho tôi #83" — Merge approved (2026-11-20, batch 2026-11-20-1); merged by claude at Ming's request (CI red only on the pre-existing kit lint in .github/scripts/risk_tier.py).
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 1, 21–27, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 4, tokens ~210k (consultants + planner×1 opus).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
### 2026-11-20 — claude — DONE (PR #83, waiting for Ming's merge)
- Agent calls (build): 3, tokens ~148k (implementer×1 sonnet 38k, reviewer×1 sonnet 41k, appsec-engineer×1 68k). Ticket total incl. plan: 7 calls, ~358k.
- Done: renumber solo (5a0ba90), ADR-0022, CLAUDE.md R3 class 4, D31, spec OQ61 note; review PASS; appsec no 🔴, 5 🟡 ADR clarifications listed in the PR for Ming. PR https://github.com/mmlabs-online/org-admin-api/pull/83.
- In progress / next: Ming merges #83 (Medium); then ADM-033.
- Gotchas: D30 row still missing (ADM-034 adds it).
