---
description: Create a new ticket in work/ from a short description.
argument-hint: "<description> [P1|P2|P3]"
---

Create a ticket from: `$ARGUMENTS`

1. Read `work/backlog.md`. Pick the prefix: **`USR-XX`** (2 digits) if the ticket is about users, identity, login/tokens, or authorization (roles/permissions, apps registry) — spec §5.3/§5.8; otherwise **`ADM-XXX`** (3 digits). The new ID is the highest existing ID with that prefix (including the "Renamed / merged" table) + 1. Never reuse an ID.
2. Create `work/tickets/<ID>/ticket.md` from `work/templates/ticket.md`:
   - **Goal**: one or two sentences, in English.
   - **Spec reference**: the section(s) of `docs/spec-v1.md` this ticket implements. If none fits, say so and ask the owner whether it is in scope (or suggest `/idea` first) before creating the ticket.
   - **Acceptance criteria**: 3–7 testable bullets derived from the description and the spec. Do not invent requirements.
   - **Areas**: any of `architecture`, `db`, `api`, `auth`, `security` that genuinely apply.
   - **Priority**: from the argument if given, else `P2`. State `BACKLOG`, attempts `0`.
3. Add a row to the table in `work/backlog.md`, and add the ticket's dependencies to the "Order of work" line and to "Depends on …" in its ticket.md.
4. If the description clearly contains more than one deliverable, propose a split instead and ask which to create.
5. Do not commit. Show the owner the ticket in a short Vietnamese summary.
