# ADM-030b: Bug reports: wire the attachment sweep to the attachment table

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
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
`attachments-sweep --apply` can delete Drive files that no attachment row references, so orphans from failed or timed-out uploads are cleaned up. Split out of ADM-030 (Ming, batch 2026-11-20-1, Q8).

## Scope reference
`docs/spec-v1.md` §5.12 (attachment storage, sweep); ADR-0018 (attachment storage); ADR-0023 (bug-report attachments); ADM-027 security review 💭3; ADM-030 plan split section.

## Scope
- A DB-backed `ReferenceChecker` over `bug_report_attachments` that matches `(attachment_id, drive_file_id)` pairs (not attachment id alone), so an orphan whose attachment id also exists on a row is still found.
- Wire it into `python -m app.cli attachments-sweep` so `--apply` is no longer refused; keep dry-run default, the 24 h grace floor, `--max-deletes` and the owner marker.
- Rows with `file_deleted_at` set count as unreferenced for their Drive file.

## Out of scope
- Retention purge of reports and attachments (ADM-031).

## Acceptance criteria
- [ ] Dry run lists only unreferenced files older than the grace period; `--apply` deletes exactly those (fake Drive tests).
- [ ] A Drive file whose attachment id exists but with a different file id is treated as unreferenced.
- [ ] A reference-check failure aborts with nothing deleted.
- [ ] No Drive ids in CLI output or logs.

## Notes / answers from Ming

## Handoff
