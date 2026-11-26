# ADM-045: Usage event retention purge

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P3 |
| Size | S |
| Areas | db |
| Gate | plan-approval |
| Depends on | ADM-042 |
| Needs Ming | — |
| Attempts | 1 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/101 |

## Goal
The per-use ledger stays bounded while per-period totals are kept.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ69.

## Scope
- Cleanup CLI: delete usage events older than 90 days (dry-run default, batched, counts only); keep per-period totals.

## Acceptance criteria
- [ ] Old events deleted, recent kept, totals untouched; dry-run changes nothing.

## Notes / answers from Ming
- Plan approved (2026-11-25, batch 2026-11-25-1) — Ming: "duyệt hết; câu 1–23 theo mặc định" (all batch questions answered with the defaults).

## Handoff

### 2026-11-26 — claude — DONE (waiting for merge)
- Agent calls: 7, tokens ~321k total (database-optimizer×1 26k, planner×1 opus 82k, implementer×2 sonnet 68k+26k, reviewer×2 opus 51k+25k, appsec-engineer×1 43k)
- Done: usage-events-purge CLI (dry-run default), batched SKIP LOCKED delete, retention config + validator, audit summaries, docs. Review REWORK attempt 1 (test isolation) → PASS attempt 2 (4244 passed); appsec PASS (3 💭). PR https://github.com/mmlabs-online/org-admin-api/pull/101 (tier high). M-7 added.
- Next: Ming merges #101; then close batch 2026-11-25-1.

### 2026-11-26 — merged (batch 2026-11-25-1)
- PR [#101](https://github.com/mmlabs-online/org-admin-api/pull/101) merged by the agent at 1db5e70 on Ming's explicit instruction ("merge cho tôi #101") — owner exception to the High-tier no-agent-merge rule, logged.
- CI `check` red only on the 13 known kit-lint errors in `.github/scripts/risk_tier.py`; tier check green. Local suite was green at review (4244 passed).
- Ming next: migration none for this ticket; M-7 (add `usage-events-purge` to the ops workflow, dry run first).
