---
description: Shape a new idea into the spec (docs/spec-v1.md) and propose tickets — run before /ticket when scope is new.
argument-hint: "<idea or problem statement>"
---

Idea: `$ARGUMENTS`

1. Dispatch `product-manager` with: the idea, "update docs/spec-v1.md (add or amend the relevant section, keep Open questions current), then return the sections changed, open questions, and proposed tickets (title, goal, areas, priority)".
2. If the idea needs a technical view to be sized, dispatch `software-architect` in consult mode in parallel and pass its notes to the owner — do not write them into the spec as decisions.
3. Do not create tickets or commit. Reply in Vietnamese: what changed in the spec, open questions for the owner, and the proposed tickets — the owner then runs `/ticket` for the ones they want.
