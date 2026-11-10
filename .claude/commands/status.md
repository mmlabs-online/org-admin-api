---
description: Summarize the ticket board.
---

Read `work/backlog.md` and each listed `work/tickets/*/ticket.md` (state, attempts). Reply in Vietnamese, short:

1. Counts per state.
2. Tickets that need the owner: `BLOCKED` (show the blocking questions from plan.md) and `ESCALATED` (one-line reason from escalation.md).
3. `DONE` tickets not yet merged into `main`: show the PR link from ticket.md and whether it is open or merged; flag a `DONE` ticket with no PR link.
4. The next ticket `/run` would pick.

If backlog.md and a ticket.md disagree on state, report the mismatch; do not fix it silently. Do not modify any file.
