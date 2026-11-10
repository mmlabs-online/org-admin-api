---
description: Create a new ticket in work/ from a short description.
argument-hint: "<description> [P1|P2|P3]"
---

Create a ticket from: `$ARGUMENTS`

1. Read `work/backlog.md`. The new ID is the highest existing `ADM-xxx` + 1, zero-padded to 3 digits.
2. Create `work/tickets/<ID>/ticket.md` from `work/templates/ticket.md`:
   - **Goal**: one or two sentences, in English.
   - **Spec reference**: the section(s) of `docs/spec-v1.md` this ticket implements. If none fits, say so and ask the owner whether it is in scope (or suggest `/idea` first) before creating the ticket.
   - **Acceptance criteria**: 3–7 testable bullets derived from the description and the spec. Do not invent requirements.
   - **Areas**: any of `architecture`, `db`, `api`, `auth`, `security` that genuinely apply.
   - **Priority**: from the argument if given, else `P2`. State `BACKLOG`, attempts `0`.
3. Append a row to the table in `work/backlog.md`.
4. If the description clearly contains more than one deliverable, propose a split instead and ask which to create.
5. Do not commit. Show the owner the ticket in a short Vietnamese summary.
