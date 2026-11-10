---
description: Run the full consult → plan → implement → review loop for one ticket, dispatching subagents automatically, then open a PR.
argument-hint: "[ADM-xxx]"
---

<!-- Orchestration logic adapted from agency-agents: specialized/agents-orchestrator.md and strategy/coordination/handoff-templates.md (MIT),
     following the same loop as mmlabs-online/stoktok. The orchestrator is this command running in the main session, because subagents cannot dispatch subagents. -->

You are the **orchestrator** for ticket `$ARGUMENTS`. You dispatch subagents and keep the board true. You write **no product code, no plan, no review** yourself — only ticket state, backlog rows, consult files, security-review files and the escalation report.

## 0. Select and prepare
1. If `$ARGUMENTS` is empty, pick the `BACKLOG` (or answered `BLOCKED`) ticket in `work/backlog.md` with the highest priority (P1 > P2 > P3), then the lowest ID. If none, report "Nothing in BACKLOG" and stop.
2. Read `work/tickets/<ID>/ticket.md`. Refuse to run if state is `DONE` or `ESCALATED` (say why). A `BLOCKED` ticket may run only if its blocking questions now have answers under "Notes / answers from owner".
3. Make sure the working tree is clean (`git status --porcelain`). If not, stop and report.
4. Branch: create or switch to `ticket/<ID>` from the latest `main` (`git fetch origin main` first). If the environment mandates a different working branch (e.g. a cloud session's designated branch), use that branch and say so in the report. The **base** for diffs is `origin/main`; if `main` does not exist yet, use the branch's first commit and say so.
5. Make sure Postgres is reachable for checks (see "Local Postgres" in `.claude/agents/implementer.md`).
6. Set state `PLANNING` in ticket.md and backlog.md.

## 1. Consult (only for the ticket's `areas`)
| Area | Subagent |
|---|---|
| architecture | `software-architect` |
| db | `database-optimizer` |
| api | `api-platform-engineer` |
| auth | `identity-access-engineer` |
| security | `security-architect` |

Dispatch the matching specialists **in parallel** (one message, several Agent calls), each with: the ticket path, "consult mode, read-only, return Findings / Recommendations / Risks / Open questions". Save each reply verbatim to `work/tickets/<ID>/consult-<agent>.md`. Skip if `areas` has none of the above.

## 2. Plan (= technical design)
Dispatch `planner` with the ticket path. When it returns, read `plan.md`:
- `Status: BLOCKED` → set state `BLOCKED`, commit `work/` (`ADM-xxx: plan blocked`), push the branch (no PR), and **stop**: show the owner the blocking questions.
- `Status: READY` → set state `IN_PROGRESS`, commit `work/` (`ADM-xxx: plan`), continue. No human approval gate.

## 3. Implement
Dispatch `implementer` with: ticket path, "implement plan.md; attempt N". Verify impl-log.md has a new attempt section; if not, treat it as a failed attempt.

## 4. Review
Set state `REVIEW`. Then:
1. Security review is needed if `areas` contains `security` or `auth`, **or** the diff (`git diff --name-only <base>...HEAD`) touches any of: `app/core/`, `app/api/deps.py`, `**/auth*`, `**/rbac*`, `**/permissions*`, `migrations/`, `.env.example`, `Dockerfile`, `.github/`. If yes, dispatch `appsec-engineer` **in parallel** with the reviewer and append its reply to `security-review.md` under `## Attempt N`.
2. Dispatch `reviewer` with the ticket path, base and attempt number.
3. Read the latest section of `review.md`. The verdict line is `Verdict: PASS` or `Verdict: REWORK`.
4. A 🔴 finding in `security-review.md` turns a PASS into REWORK: append it to the review attempt under "Security blockers".

## 5. Loop decision
- **PASS** → set state `DONE`, update the backlog row (state, attempts, updated date), commit `work/` (`ADM-xxx: done`). Go to 6.
- **REWORK** and reworks so far < 2 → increment `Attempts` in ticket.md, set state `REWORK`, dispatch `implementer` with "REWORK attempt N: fix only the issues in the latest review.md / security-review.md section", then return to step 4.
- **REWORK** and reworks so far = 2 → set state `ESCALATED`, write `work/tickets/<ID>/escalation.md` from `work/templates/escalation.md`, commit, push the branch (no PR), and **stop**.

## 6. Open the pull request (DONE only)
1. Push: `git push -u origin <branch>`.
2. Open a PR into `main` (GitHub MCP `create_pull_request`). If one is already open for this branch, update it instead.
   - Title: `ADM-xxx: <ticket title>`.
   - Body (English, short): **Summary** (2–4 bullets) · **Acceptance criteria** (each ticked with one line of evidence from review.md) · **Checks** (`make check` result from the review) · **Needs owner's decision** (spec divergences, planner assumptions, or "none") · **Follow-ups** · links to plan.md, review.md, security-review.md.
3. Write the PR URL into ticket.md (`| PR | <url> |`), commit `ADM-xxx: link PR`, push.
4. **Never approve, merge or enable auto-merge.** The owner reviews and merges.

## 7. Report to the owner
Short summary in Vietnamese: ticket, final state, attempts, files changed (`git diff --stat <base>...HEAD`), check results, follow-ups, and the next step (PR link to merge, or the escalation / blocking questions).

## Guardrails
- Pass each subagent only what it needs (paths, attempt number, the exact instruction). Don't paste whole files into prompts.
- Never edit `plan.md`, `impl-log.md`, `review.md` to change an outcome.
- If a subagent fails or returns nothing twice in a row, set `ESCALATED` with the reason and stop.
