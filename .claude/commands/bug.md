---
description: Record a bug as a BUG-XXX ticket (report, repro, expected/actual, root cause) for /run.
argument-hint: "<what is wrong> [S1|S2|S3]"
---

Record a bug from: `$ARGUMENTS` (plus any log excerpt or screenshot the owner attached).

1. **ID**: the highest existing `BUG-XXX` in `work/tickets/` + 1, 3 digits (first is `BUG-001`). Never reuse an ID.
   Search `work/tickets/BUG-*/ticket.md` first: if a bug that is not `DONE` already covers it, add the report to that
   ticket's *Notes / answers from owner* instead and stop.
2. **Investigate read-only** before writing (no product code changes, no migrations, never read `.env`):
   - Find the code path (file:line). Try to reproduce: a pytest run against the Postgres test DB (see "Local
     Postgres" in `.claude/agents/implementer.md`; a throwaway test outside `tests/`, not committed), or a request
     against `make dev`. Record the result in **Root cause** as *confirmed* or *suspected*.
   - `Found in`: the version the owner named (release tags `vX.Y.Z`), else "unknown". `Introduced in`: from
     `git log -S` / `git tag --contains` when you can tell, else "unknown".
3. Create `work/tickets/<ID>/ticket.md` from `work/templates/bug.md`. All sections filled or "unknown".
   - **Severity**: S1 = security (auth bypass, missing permission check, token/secret/password leak, broken
     isolation between apps or accounts), data loss, or a core flow is impossible (sign-up, login, verify email,
     password reset, app token, JWKS, admin endpoints); S2 = a feature is broken but there is a workaround, or a wrong
     status code / error envelope (R6) that a client relies on; S3 = cosmetic (messages, docs, OpenAPI text).
     Priority: S1 → P1, S2 → P1 or P2, S3 → P3, unless the owner said otherwise.
   - **Areas**: as for `/ticket` (`architecture`, `db`, `api`, `auth`, `security`). A bug in R3/R11 behaviour
     (auth classes, cookies, tokens, CORS, rate limits) is at least `auth, security`, so `/run` consults the
     specialists and runs `appsec-engineer` at review.
   - Acceptance criteria always include the regression test criterion from the template.
   - Never copy passwords, tokens, cookies, signing keys or full emails from logs or screenshots (R5, R11). Do not
     commit screenshots; describe them.
4. **Board row** in `work/backlog.md` (same columns as other tickets): S1 bugs go **above** the first `BACKLOG` row;
   others at the end of the open rows. Add hard dependencies, if any, to the "Hard dependencies" line.
5. **Commit the record on the batch branch** (ADR-0012): branch `bug/<first ID>` from the latest `origin/main`,
   created by the first `/bug` of a batch (in a cloud session with a mandated branch, use that branch and say so).
   Later bugs of the same batch reuse it. Commit `<ID>: record bug` and push (`git push -u origin <branch>`). The
   record commit holds only `work/tickets/<ID>/ticket.md` and its `work/backlog.md` row.
6. Show the owner the bug (short Vietnamese: ID, severity, what is wrong, root cause confirmed/suspected, affected
   versions) and **ask with `AskUserQuestion`** (header `Bug`), listing every bug recorded in this batch so far:
   - **A. Mở PR và kết thúc** — go to 7 (record PR).
   - **B. Ghi nhận thêm bug** — reply "Mô tả bug tiếp theo (kèm log/ảnh nếu có)" and end the turn. The owner's next
     message is the next bug: run steps 1–6 again for it on the same batch branch (no need to type `/bug` again), then
     ask again.
   - **C. Sửa luôn rồi mở PR** — go to 8.
   An answer that is none of these (Other): follow it if clear, else ask again.
7. **Record PR** (option A): open a PR into `main` (GitHub MCP `create_pull_request`; if one is open for the branch,
   update it). Title `<ID>: record bug — <short title>`, or `Record bugs <ID>, <ID>…` for a batch. Body (English,
   short), per bug: severity, found in / introduced in, actual vs expected in one line, root cause (confirmed /
   suspected, file:line), link to its ticket; then "Fix: `/run <ID>` after merge". The ticket's `PR` field stays `—`
   (it is for the fix PR). Report the PR link and stop.
8. **Fix now** (option C): fix every bug of the batch on the batch branch, S1 first, then in record order, one at a
   time, following `/run` step 0 items 5–6 and steps 1–5 (Postgres, state, consult, plan, implement, review incl. `appsec-engineer` triggers,
   rework ≤ 2, the bug-ticket rule) with these differences:
   - Skip `/run` step 0 items 1–4 (no ticket selection, no new branch): stay on the batch branch.
   - A bug whose plan is `BLOCKED` or that ends `ESCALATED` stops there (blocking questions / escalation.md as in
     `/run`); continue with the others.
   - Then open **one PR** into `main` (update it if open), title `<ID>: fix <short title>` or `Fix bugs <ID>, <ID>…`,
     body as in `/run` step 6.2 for each `DONE` bug (summary, acceptance criteria with evidence incl. the regression
     test, `make check`, needs owner's decision, follow-ups, links), plus a list of bugs left `BLOCKED` /
     `ESCALATED` and why. Write the PR URL into each `DONE` ticket's `PR` field, commit `<ID>: link PR`, push.
   - Report like `/run` step 7 (Vietnamese), for every bug of the batch.
9. **Never approve, merge or enable auto-merge.** The owner merges; until then `/run` on `main` cannot see the bugs.
