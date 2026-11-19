# Batch <YYYY-MM-DD>-<n>

| Field | Value |
|---|---|
| Phase | PLAN / APPROVAL / WAITING-APPROVAL / BUILD / WAITING-MERGE / WAITING-MING / DONE |
| Flags | <e.g. --auto-merge --max 5> |
| Started | YYYY-MM-DD HH:MM (claude) |
| Updated | YYYY-MM-DD HH:MM |

## Tickets (run order)
| # | ID | Title | Depends on | Tier (pred → real) | Plan | Plan approved | Merge approved | State | PR | Merged by |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | — | medium → | READY | [ ] | [ ] (Medium only) | | | agent / Ming |

## Questions for Ming (numbered across the batch)
1. <ticket> — <question> — default: <…>

## Answers from Ming
<Quoted with date; mirrored into each ticket.md.>

## Left out of this batch
- <ID> — <reason: depends on an unmerged ticket / claimed / waits on M-n / --max>

## Usage
| ID | Agent calls | Tokens in/out | Reworks |
|---|---|---|---|

## Log
- YYYY-MM-DD HH:MM — <event: plan done, approved, PR opened, merged, stopped (why)>
