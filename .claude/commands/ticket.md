---
description: Create a new ticket in work/ from a short description.
argument-hint: "<description> [P0|P1|P2|P3]"
---

<!-- spaceM project-kit. Project-specific values live in `.claude/loop.config.md`. -->

Create a ticket from: `$ARGUMENTS`. Read `.claude/loop.config.md` first.

If the description is a bug (something that worked or should work and does not), use `/bug` instead.

1. Read `work/backlog.md` and the **Scope sources** in `loop.config.md`.
   - If the description matches a scoped item (feature, spec section) that has no ticket yet, reference it and use its
     priority, size and dependencies.
   - The ID follows **Ticket IDs** in `loop.config.md`: when it defines scope-item IDs (e.g. `F-xxx` from the feature
     list) and the ticket is for such an item, reuse that item's ID (`.n` suffix for a split). Otherwise the ID is the
     **Ticket prefix** followed by the highest existing number with that prefix + 1, zero-padded to 3 digits
     (e.g. `ADM-001`).
2. Create `work/tickets/<ID>/ticket.md` from `work/templates/ticket.md`:
   - **Goal**: one or two sentences, in English.
   - **Scope reference**: the section of a scope source this implements. If none fits, say so and ask Ming whether it
     is in scope before creating the ticket.
   - **Acceptance criteria**: 3–7 testable bullets from the description and the scope sources. Do not invent requirements.
   - **Areas**: only areas listed in the **Consult** or **Review triggers** tables of `loop.config.md` that genuinely
     apply (plus `security` when relevant).
   - **Gate**: `plan-approval` if the **Plan approval gate** policy in `loop.config.md` covers the ticket (it will touch a
     High path, or its priority is covered), else `none`.
   - **Depends on** / **Needs Ming**: ticket IDs (`<repo> <ID>` for another repo) and `M-n` items from
     `work/ming-todo.md`, or `—`.
   - Priority from the argument if given, else the scope item's priority, else `P2`. State `BACKLOG`, attempts `0`.
3. If the ticket is larger than **M** (about one day), propose a split into `<ID>.1`, `<ID>.2`… instead, and
   ask Ming which to create.
4. Insert a row into `work/backlog.md` after the last ticket it depends on (backlog order is run order).
5. Do not commit. Show Ming the ticket in a short Vietnamese summary.
