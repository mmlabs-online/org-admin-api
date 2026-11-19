---
description: Shape a new idea into the spec and propose tickets — run before /ticket when scope is new.
argument-hint: "<idea or problem statement>"
---

<!-- spaceM project-kit (optional command; needs the product-manager agent). -->

Idea: `$ARGUMENTS`. Read `.claude/loop.config.md` first.

1. Dispatch `product-manager` with: the idea, "update the first scope source in `.claude/loop.config.md` (add or amend the relevant section, keep Open questions current), then return the sections changed, open questions, and proposed tickets (title, goal, areas, priority)".
2. If the idea needs a technical view to be sized, dispatch `software-architect` in consult mode in parallel and pass its notes to Ming — do not write them into the spec as decisions.
3. Do not create tickets or commit. Reply in Vietnamese: what changed in the spec, open questions for Ming, and the proposed tickets — Ming then runs `/ticket` for the ones he wants.
