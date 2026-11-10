---
description: Stage 3 — break an accepted design into PR-sized tasks
argument-hint: <path to docs/design/NNNN-*.md>
---

Design doc: $ARGUMENTS

1. Read the design. If it is not `Accepted`, warn and ask before continuing.
2. Delegate to the **sprint-prioritizer** subagent: split the design into ordered tasks using `docs/templates/plan.md`. Each task = one PR (≈ ≤ 400 lines, ≤ 1 migration), with ID (`T1`, `T2`…), dependencies, files/modules touched, acceptance criteria, and the tests that prove it.
3. Order: schema/migrations → repository/service → endpoints → authz/audit → docs. Mark the MVP cut line.
4. Write `docs/plans/NNNN-<slug>.md` with `Status: Draft` and reply with the task table.
