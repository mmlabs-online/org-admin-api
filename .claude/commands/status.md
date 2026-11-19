---
description: Summarize the ticket board.
---

<!-- spaceM project-kit. -->

Read `work/backlog.md`, each listed `work/tickets/*/ticket.md` (state, attempts, PR, Claimed by) and
`work/ming-todo.md`. Reply in Vietnamese, short:

1. Counts per state, and progress (`MERGED` / total).
2. Tickets that need Ming: `BLOCKED` (the blocking questions from plan.md, or "plan approval"), `ESCALATED`
   (one-line reason from escalation.md), and open items in `work/ming-todo.md`.
3. `DONE` tickets: show the PR link and its state (`gh pr view <url> --json state,mergeable`, or the GitHub MCP).
   Flag a `DONE` ticket with no PR link, and PRs with merge conflicts.
4. Active claims (`Claimed by` set), flagging any older than 12 hours.
5. Open bugs (`BUG-xxx` not `MERGED`), by severity S1/S2/S3, with `Found in`.
6. The next ticket `/run` would pick, or why nothing is runnable.

If backlog.md and a ticket.md disagree on state, report the mismatch. The only file change allowed: sync the board on `main`
(pull first): set merged tickets to `MERGED`, mirror states from ticket branches, commit `chore: board — sync`,
push. Change nothing else.
