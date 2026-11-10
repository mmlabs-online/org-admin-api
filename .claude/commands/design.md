---
description: Stage 2 — produce a technical design for an accepted idea doc
argument-hint: <path to docs/ideas/NNNN-*.md>
---

Idea doc: $ARGUMENTS

1. Read the idea doc. If its status is not `Accepted`, warn the owner and ask whether to continue anyway.
2. Read `CLAUDE.md`, `docs/design/` (existing designs), `docs/adr/`, and the current code under `app/` to understand what already exists.
3. Delegate in parallel (one message, several Agent calls) and ask each for concrete proposals, not essays:
   - **software-architect** — module boundaries, domain model, key trade-offs (name the alternatives rejected).
   - **database-optimizer** — PostgreSQL tables, columns, constraints, indexes, migration strategy.
   - **api-platform-engineer** — endpoint list (method, path, request/response schema, status codes, pagination, versioning).
   - **security-architect** — threat model for this feature, authz rules per endpoint, audit-log requirements.
   - **identity-access-engineer** — only if the feature touches authentication, sessions, tokens, or RBAC.
4. Merge their output into `docs/design/NNNN-<slug>.md` (same NNNN as the idea) following `docs/templates/design.md`. Resolve conflicts between agents explicitly in a "Decisions" section. Significant, long-lived decisions also get an ADR in `docs/adr/`.
5. Set `Status: Draft` and reply with the file path, the decisions, and open questions. Do not plan or write code.
