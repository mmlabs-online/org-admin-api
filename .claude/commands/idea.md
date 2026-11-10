---
description: Stage 1 — turn a topic into a PRD-style idea doc in docs/ideas/
argument-hint: <topic or problem statement>
---

Topic: $ARGUMENTS

1. Read `CLAUDE.md`, `docs/WORKFLOW.md`, and existing files in `docs/ideas/` and `docs/design/` so you don't duplicate or contradict accepted work.
2. Pick the next free number `NNNN` (4 digits, shared across ideas/design/plans) and a short kebab-case slug.
3. Delegate to the **product-manager** subagent with: the topic, the context that this is the admin API for the whole mmlabs ecosystem (internal staff users, other mmlabs services as clients), and `docs/templates/idea.md` as the required structure.
4. Write the result to `docs/ideas/NNNN-<slug>.md` with `Status: Draft`.
5. Reply with: the file path, a 5-line summary, and the **open questions** the owner must answer before `/design`. Do not start the design.
