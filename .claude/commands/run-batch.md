---
description: Run several tickets in one go — plan all, one approval round with Ming, then build each and open its PR (optionally merge Low/approved-Medium PRs and continue).
argument-hint: "[ID ...] [--next N] [--max N] [--auto-merge] [--plan-only] [--one] [--resume]"
---

<!-- spaceM project-kit. Builds on /run: every per-ticket step (claim, consult, plan, gate, implement, review,
     rework, PR, Handoff, board on main) is exactly the /run step named here; this file only adds batching,
     the single approval round and the merge rule. Settings: `.claude/loop.config.md` → Batch, Cost controls. -->

You are the **batch orchestrator** for: `$ARGUMENTS`. Read `.claude/loop.config.md` and `.claude/commands/run.md` first;
follow `run.md` for every per-ticket step and its guardrails. Reports to Ming are in Vietnamese.

## Arguments
- `ID ...` — the tickets to run, in any order. Or `--next N` — the first N runnable tickets in pick order (`run.md` step 0.2)
  (a row that depends only on tickets earlier in this batch counts as runnable when `--auto-merge` is set).
- `--max N` — at most N tickets (default: **Batch size** in `loop.config.md`, else 5). Extra IDs are listed as "next batch".
- `--auto-merge` — after each PR, merge it when the **merge rule** below allows, then continue with the next ticket.
- `--plan-only` — stop after the approval round (phase 1).
- `--resume` — continue the open batch file in `work/batches/` (after Ming answered, merged a PR, or a stop).
- `--one` — in phase BUILD, finish **one** ticket (through its PR, and its merge with `--auto-merge`), record it in the
  batch file and stop cleanly. Keeps this session's context small: Ming runs `/clear` (or opens a new session), then
  `/run-batch --resume --one` for the next ticket.

## Batch file
Shared state for the batch (CLAUDE.md §9): `work/batches/<YYYY-MM-DD>-<n>.md` from `work/templates/batch.md`,
committed on `main` like the board (`chore: batch — <id> <event>`, pull first, push). One open batch per project:
if one is open and `--resume` was not given, stop and ask Ming whether to resume it.

## 0. Select
1. `git switch main && git pull --ff-only`, then sync the board as `run.md` step 0.1.
2. Resolve the ticket list. Drop and list (with the reason) tickets that are `DONE`, `MERGED`, `ESCALATED`, claimed by
   someone else (claim < 12 h), wait on an unticked `Needs Ming` item, or depend on a ticket that is neither `MERGED`
   nor in this batch. Without `--auto-merge`, a ticket that depends on another ticket of this batch is moved to
   "next batch" (it cannot start before that PR is merged).
3. Order: S1 bugs first, then dependencies before dependents, then pick order. Apply `--max`.
4. Create the batch file (tickets, order, flags, phase `PLAN`), commit on `main`, push.

## 1. Phase PLAN (every ticket, one at a time, in order)
For each ticket: `run.md` steps 0.4–0.7 (clean tree, branch `ticket/<ID>`, prepare, claim), step 1 (consult, with
**Cost controls**) and step 2 up to the plan only: dispatch `planner`, read `plan.md`, commit `<ID>: plan`, push the
branch. Do not apply the plan-approval stop here — every plan is reviewed in the round below.
A plan for a ticket that depends on an earlier batch ticket must say which earlier plan it builds on (`## Assumptions`).
Predict each ticket's tier: `python3 -I .github/scripts/risk_tier.py --files <plan's file list>` (`high` if the plan
names a High path). Record status, tier and blocking questions in the batch file.

## 2. Approval round (one message to Ming)
Switch to `main`, update the batch file (phase `APPROVAL`), commit, push. Then send **one** message in Vietnamese:
- a table: ID · title · predicted tier · plan link · blocking questions (numbered across the batch, each with a default);
- what Ming must answer: (a) the blocking questions; (b) plan approval — required for tickets whose Gate is
  `plan-approval`, and for the batch as a whole; (c) with `--auto-merge`: **"cho merge"** per **Medium** ticket.
  Low tickets merge without asking only if **Auto-merge Low** is `on` in `loop.config.md`; **High tickets are never
  merged by an agent**, whatever Ming answers here.
- Accept answers in chat ("duyệt hết; merge T-031, T-032; câu 2 theo mặc định") or as ticks Ming commits in the batch file.
Record each answer in the ticket's *Notes / answers from Ming* with today's date: `Plan approved (YYYY-MM-DD, batch <id>)`
and, when given, `Merge approved (YYYY-MM-DD, batch <id>)`; mirror them in the batch file. Release the claims of
tickets left unanswered or BLOCKED (they leave the batch).
With `--plan-only`, or when Ming is not answering in this session: clear every claim, append Handoff entries, set the
batch phase `WAITING-APPROVAL`, report, and stop. `--resume` continues from here.

## 3. Phase BUILD (approved tickets, one at a time, in order)
For each ticket: re-claim, re-plan only if its blocking answers changed the plan (`run.md` step 2), then `run.md`
steps 3–6 (implement, review with review agents, rework ≤ 2, rebase, PR, Handoff, board on `main`).
After the PR is open:
- **Without `--auto-merge`:** continue with the next ticket.
- **With `--auto-merge`:** wait for CI on the PR (`gh pr checks <url> --watch`, at most 30 minutes), then apply the
  **merge rule**. If it allows: squash-merge (`gh pr merge <url> --squash --delete-branch`, or the GitHub MCP with
  `merge_method: squash`), set `MERGED` on the board and in ticket.md on `main`, then run the **Checks** on the updated
  `main`. If it does not allow, or merging fails: go to **Stop and wait**.

A ticket that ends `ESCALATED` or `BLOCKED` → **Stop and wait** (later tickets may depend on it).
With `--one`: after the first ticket of this run is finished (PR open, merged if the rule allowed), record it in the batch
file (phase stays `BUILD`), clear the claim, write the Handoff, and stop with "next: `/clear`, then `/run-batch --resume --one`".

### Merge rule (all must hold; otherwise do not merge)
1. The PR was opened by this batch, its ticket is `DONE` with `Verdict: PASS`, and no review-agent 🔴 is open.
2. Every CI check on the PR is green, the PR is mergeable (no conflict) and has no "changes requested" review.
3. Tier now, from the real diff: `python3 -I .github/scripts/risk_tier.py --base origin/main` on the PR branch
   (and the `risk:` label, when the `risk-tier` workflow ran, must agree):
   - `low` → **Auto-merge Low** is `on` in `loop.config.md`;
   - `medium` → the ticket has `Merge approved (…, batch <this id>)` from Ming;
   - `high` → **never**.

### Stop and wait
Leave the open PR as is. Clear the claim, append the Handoff entry (why it stopped, the PR link), set the batch phase
`WAITING-MERGE` (or `WAITING-MING` for ESCALATED/BLOCKED), commit on `main`, push, and stop. The report names what Ming
must do ("merge PR …", "answer …") and says: then `/run-batch --resume`. Never revert, never force-merge, never lower a tier.
If the Checks fail on `main` after a merge: stop the same way, report the failing check and the merged PR. Do not revert.

## 4. Final report
When every ticket is done (or after a stop), set the batch phase (`DONE` or the waiting phase) and report in Vietnamese:
per ticket — final state, PR, tier, merged by agent / waiting for Ming; agent calls and tokens per ticket (from the
Handoff entries) and the batch total by agent; PRs left for Ming in suggested merge order; tickets moved to the next batch; next command to run.

## Guardrails
- Everything in `run.md` → Guardrails and `loop.config.md` → Guardrails.
- Merge only under the merge rule; never approve a PR, never enable GitHub auto-merge, never merge a PR this batch did
  not open, never merge into anything but `main`. Never push to `release/*` (a release stays Ming's step).
