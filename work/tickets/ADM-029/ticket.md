# ADM-029: Bug reports — staff list, detail, mark done and reopen

| Field | Value |
|---|---|
| State | BLOCKED |
| Priority | P2 |
| Size | M |
| Areas | api, auth, db |
| Gate | plan-approval |
| Depends on | ADM-028 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
Staff see every app's bug reports in admin-pan and mark a report done (recording who handled it), or reopen it.

## Scope reference
`docs/spec-v1.md` §5.12: OQ22, OQ25, OQ28, OQ32.

## Scope
- New admin permissions `admin:bug-reports:read` and `admin:bug-reports:write` (seed migration, `is_admin`, granted to no role; `admin:*` covers both).
- `GET /api/v1/bug-reports` → `Page[T]` across all apps, filters (app, status, reporter, date range), stable sort; `Cache-Control: no-store` (reporter email is PII).
- `GET /api/v1/bug-reports/{id}` → detail with reporter id/email/display name and attachment metadata (empty until ADM-030).
- `PATCH /api/v1/bug-reports/{id}`: `status` `done` (sets handler = caller, `handled_at` = now) or `open` (reopen clears both); `resolution_note` (staff only). Optional `If-Match` (ADM-024 pattern, 412).
- Each change audited in the same transaction (R4), ids and status only, never note or description text; setting the current state again writes nothing.

## Out of scope
- Attachment download (ADM-030), staff delete, notifications, dashboard tile (§5.7 Later).

## Acceptance criteria
- [ ] List/detail require `admin:bug-reports:read`; PATCH requires `admin:bug-reports:write`; 401/403 tests, `x-permission` declared, BEARER map updated.
- [ ] Mark done records the caller as handler and the time; reopen clears them; a DB constraint keeps status and handler fields consistent.
- [ ] Audit rows for done, reopen and note edits, without text content; no row for a no-op.
- [ ] 404 unknown id; 422 invalid status; 412 on stale `If-Match`.
- [ ] Filters, paging and sort covered by tests; reports from several apps listed together.

## Technical notes
- Files: `app/modules/bug_reports/` (router, service, repository, schemas), permission seed migration, tests, OpenAPI contract test.

## Test plan
- API tests per criterion; audit row assertions.

## Notes / answers from Ming

Waiting for Ming: plan approval (High tier) — batch 2026-11-19-3, blocking questions 1–7.

## Handoff
### 2026-11-19 — claude — BLOCKED (waiting plan approval, batch 2026-11-19-3)
- Agent calls: 4, tokens ~255k (database-optimizer×1 26k, api-platform-engineer×1 35k, identity-access-engineer×1 52k, planner×1 opus 142k)
- Done: consult (3), plan.md READY, predicted tier high (permission seed migration).
- In progress / next: Ming answers batch questions 1–7 + plan approval → `/run-batch --resume`.
- Gotchas: ADR number 0020; ADM-030 (next batch) will rebase on this ticket's bug_reports changes.
