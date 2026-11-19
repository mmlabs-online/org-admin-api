# ADM-031: Bug reports — retention purge

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | S |
| Areas | db, security |
| Gate | plan-approval |
| Depends on | ADM-030 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
Bug-report data does not grow forever and screenshots (PII) are not kept longer than needed.

## Scope reference
`docs/spec-v1.md` §5.12 Retention (OQ29).

## Scope
- Extend the cleanup CLI: delete Drive files 90 days after a report's `handled_at` (a reopened report keeps them until done again) and mark their rows deleted; delete reports 365 days after creation (rows and remaining files).
- Dry-run by default, batched, summary counts only; audited like the existing purges.

## Acceptance criteria
- [ ] Attachments of reports done > 90 days ago are deleted in Drive and marked; reopened or recent ones are kept.
- [ ] Reports older than 365 days are deleted with their attachments.
- [ ] Dry-run changes nothing; `--apply` does; a Drive failure leaves the row for the next run and is counted.
- [ ] No content, Drive ids or emails in output or logs.

## Technical notes
- Files: cleanup CLI module, bug_reports repository, tests; `docs/deploy.md` (schedule).

## Test plan
- CLI tests with backdated rows and the fake Drive.

## Notes / answers from Ming

## Handoff
