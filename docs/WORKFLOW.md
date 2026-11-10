# Development workflow

Every feature goes through four stages. Each stage produces a file in `docs/` so decisions are reviewable in git, and each stage has a slash command and a set of agents (`.claude/agents/`).

```
 /idea  ──►  /design  ──►  /plan  ──►  /implement  ──►  /review  ──►  /ship
  docs/ideas   docs/design    docs/plans    app/ + tests      findings       PR
```

| Stage | Command | Lead agent | Supporting agents | Output |
|---|---|---|---|---|
| 1. Idea / PRD | `/idea <topic>` | `product-manager` | — | `docs/ideas/NNNN-<slug>.md` |
| 2. Technical design | `/design <idea file>` | `software-architect` | `backend-architect`, `database-optimizer`, `api-platform-engineer`, `security-architect`, `identity-access-engineer` (auth topics) | `docs/design/NNNN-<slug>.md` (+ ADRs in `docs/adr/`) |
| 3. Plan | `/plan <design file>` | `sprint-prioritizer` | — | `docs/plans/NNNN-<slug>.md` (task list) |
| 4. Implement | `/implement <plan file> [task id]` | `fastapi-developer` | `database-optimizer` (migrations), `minimal-change-engineer` (bug fixes) | code + tests |
| 5. Review | `/review` | `code-reviewer` | `appsec-engineer`, `api-tester`, then `reality-checker` gate | review notes in chat / PR |
| 6. Ship | `/ship` | — | `technical-writer` (docs), `devops-automator` (CI/deploy changes) | commit, push, PR |

## Rules

- **Numbering:** idea, design and plan for the same feature share the same `NNNN` (e.g. `0002-rbac`).
- **Status header:** every doc starts with `Status: Draft | In review | Accepted | Superseded`. Only `Accepted` designs get planned; only planned tasks get implemented.
- **Human checkpoints:** the owner approves at the end of `/idea`, `/design` and `/plan` (set `Status: Accepted`). Agents never mark their own docs accepted.
- **Small slices:** a plan task should be one PR-sized change (≈ ≤ 400 lines diff, one migration at most).
- **Evidence over claims:** `/review` and `reality-checker` require test output, not descriptions.
- **Bug fixes** skip idea/design: use `/implement` with a description and the `minimal-change-engineer` mindset.

## Templates

- `docs/templates/idea.md`
- `docs/templates/design.md`
- `docs/templates/plan.md`
- `docs/templates/adr.md`
