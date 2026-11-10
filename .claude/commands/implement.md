---
description: Stage 4 — implement one task from a plan (or a bug fix)
argument-hint: <path to docs/plans/NNNN-*.md> [task id] | <bug description>
---

Input: $ARGUMENTS

1. If the input is a plan file: read it, pick the given task ID (or the first task not marked done whose dependencies are done). Read the linked design.
   If the input is a bug description: skip planning, apply the **minimal-change-engineer** approach (smallest diff that fixes it + a regression test).
2. Delegate the implementation to the **fastapi-developer** subagent with the task text, acceptance criteria, and relevant design excerpts. If the task changes the DB schema, ask **database-optimizer** to review the migration afterwards.
3. Run `make check` yourself and include the real output summary. If it fails, fix or report — never claim it passes without running it.
4. Tick the task in the plan file (`- [x] T3 …`) and reply with: what changed, commands run, results, follow-ups.
