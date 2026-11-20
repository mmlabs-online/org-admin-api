# ADM-039: Usage metrics, defaults and periods (staff config)

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/86 |

## Goal
Staff configure, per app, the AI usage metrics (e.g. ricotdin chat turns, speech-to-text seconds; tudocuatui image analyses) with a default limit every member gets (free-trial-like) and a reset period.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ61, OQ63, OQ64, OQ68.

## Scope
- Tables for metrics (app, key, display name, unit, default limit, period `day`|`week`|`month` defaulting to the app's setting / weekly, status) with migration + `downgrade()` (R2); refused for the `admin` app.
- Admin permissions `admin:usage:read` / `admin:usage:write` (seed migration, `is_admin`, granted to no role; `admin:*` covers them).
- Bearer CRUD under `/api/v1/apps/{key}/usage-metrics` (`Page[T]`, detail, create, PATCH, archive), audited (R4).
- One repository helper computing the calendar period start in Asia/Ho_Chi_Minh (day 00:00, week Monday, month 1st), tested at boundaries.

## Acceptance criteria
- [ ] CRUD with 401/403/404/409/422 tests; audit rows on every change; `x-permission` declared.
- [ ] Period helper correct at Sunday 23:59:59 / Monday 00:00 ICT and month end.
- [ ] Migration round-trip; `alembic check` clean.

## Notes / answers from Ming
- 2026-11-20 (Ming, chat): "merge cho tôi #86" — explicit owner instruction to merge this High-tier PR; merged by claude (CI red only on the pre-existing kit lint).
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 13–20, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 4, tokens ~229k (consultants + planner×1 opus).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
### 2026-11-20 — claude — DONE (PR #86, waiting for Ming's merge)
- Agent calls (build): 3, tokens ~253k (implementer×1 sonnet 115k, reviewer×1 opus 72k, appsec-engineer×1 66k). Ticket total incl. plan: 7 calls, ~482k.
- Done: usage_metrics table + permission seed (migration 8c2f6a1d4e93), four routes, period helper, ADR-0024 + D33; review PASS; appsec PASS (no 🔴/🟡; ADM-044 allowlist note). PR https://github.com/mmlabs-online/org-admin-api/pull/86.
- In progress / next: Ming merges #86 (High); then ADM-030.
