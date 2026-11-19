---
description: Record a bug as a BUG-xxx ticket (report, repro, expected/actual, root cause) for /run.
argument-hint: "<what is wrong> [S1|S2|S3]"
---

<!-- spaceM project-kit. Project-specific values live in `.claude/loop.config.md`. -->

Record a bug from: `$ARGUMENTS` (plus any screenshot Ming attached). Read `.claude/loop.config.md` first.

1. ID: the highest existing `BUG-xxx` in `work/tickets/` + 1, zero-padded to 3 digits (first is `BUG-001`).
   Search `work/tickets/BUG-*/ticket.md` first: if an open bug already covers it, add the report to that
   ticket's *Notes / answers from Ming* instead and stop.
2. Investigate read-only before writing (no product code changes):
   - Find the code path (file:line). Try to reproduce at the bug test level named in `loop.config.md` (or a one-off
     scripted check when that level cannot see it). Record the result in **Root cause** as *confirmed* or *suspected*.
   - `Found in`: the version Ming named, else "unknown". `Introduced in`: from `git log -S` / `git tag --contains`
     when you can tell, else "unknown".
3. Create `work/tickets/<ID>/ticket.md` from `work/templates/bug.md`. All sections filled or "unknown".
   - **Severity**: S1 = data loss, security, or a core flow is impossible; S2 = a feature is broken but there is a
     workaround, or the UI misleads; S3 = cosmetic. Priority: S1 → P0, S2 → P0 or P1, S3 → P2, unless Ming said otherwise.
   - **Gate**: `plan-approval` if the fix touches a High path (`loop.config.md` → High paths), else `none`.
   - Acceptance criteria always include the regression test criterion from the template.
   - Do not commit screenshots; describe them. Never copy emails or personal data from a screenshot.
4. Insert a board row in `work/backlog.md`: S1 bugs go **above** the first runnable `BACKLOG` row; others after the
   last ticket they depend on, else at the end of the open rows.
5. Commit the record on the **batch branch**: branch `bug/<first ID>` from the latest `origin/main`, created by the
   first `/bug` of a batch (in a cloud session with a mandated branch, use that branch and say so). Later bugs of the
   same batch reuse it. Commit `chore: board — add <ID> (<short title>)` and push (`git push -u origin <branch>`).
   The record commit holds only `work/tickets/<ID>/ticket.md` and its `work/backlog.md` row.
6. Show Ming the bug (short Vietnamese: ID, severity, what is wrong, root cause confirmed/suspected, affected versions)
   and **ask with `AskUserQuestion`** (header `Bug`), listing every bug recorded in this batch so far:
   - **A. Mở PR và kết thúc** — go to 7 (record PR).
   - **B. Ghi nhận thêm bug** — reply "Mô tả bug tiếp theo (kèm ảnh nếu có)" and end the turn. Ming's next message is
     the next bug: run steps 1–6 again for it on the same batch branch (no need to type `/bug` again), then ask again.
   - **C. Sửa luôn rồi mở PR** — go to 8.
   An answer that is none of these (Other): follow it if clear, else ask again.
7. **Record PR** (option A): open a PR into `main` (GitHub MCP `create_pull_request`; if one is open for the branch,
   update it). Title `<ID>: record bug — <short title>`, or `Record bugs <ID>, <ID>…` for a batch. Body (English,
   short), per bug: severity, found in / introduced in, actual vs expected in one line, root cause (confirmed/suspected,
   file:line), link to its ticket; then "Fix: `/run <ID>` after merge" and the attribution line the session requires.
   Append `- YYYY-MM-DD — claude — recorded; record PR <url>` under each ticket's `## Handoff` (the `PR` field stays for
   the fix PR), commit, push. Report the PR link and stop.
8. **Fix now** (option C): fix every bug of the batch on the batch branch, S1 first, then in record order, one at a
   time, following `/run` steps 1–5 (consult, plan, implement, review, rework ≤ 2, regression test rule) with these
   differences:
   - Skip `/run` step 0 (no switch to `main`, no new branch, no board sync) and the board-on-`main` step (6b): the
     batch branch carries the board rows; set each row's state there, together with ticket.md.
   - A bug with `Gate: plan-approval` stops at `BLOCKED` after its plan (Ming must approve); continue with the others.
     A bug that ends `ESCALATED` gets its escalation.md; continue with the others.
   - Then open **one PR** into `main` (update it if open), title `<ID>: fix <short title>` or `Fix bugs <ID>, <ID>…`,
     body as in `/run` step 6 for each `DONE` bug (summary, acceptance criteria with evidence, checks, follow-ups,
     links), plus a list of bugs left `BLOCKED` / `ESCALATED` and why. Write the PR URL into each `DONE` ticket's `PR`
     field, clear `Claimed by`, append the Handoff entries, commit, push.
   - Report like `/run` step 7, for every bug of the batch.
9. **Never approve, merge or enable auto-merge.** Ming merges; until then `/run` cannot see the bugs on `main`.
