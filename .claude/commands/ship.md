---
description: Stage 6 — final checks, docs, commit, push and open a PR
---

1. Run `make check`. Stop if anything fails.
2. If endpoints, config or setup changed, ask **technical-writer** to update `README.md` / relevant docs (keep it short). If CI/Docker changed, ask **devops-automator** to sanity-check it.
3. Commit with a conventional message (`feat(orgs): …`, `fix(auth): …`) referencing the plan task ID.
4. Push the current branch and open a PR (only if the owner asked for one) whose description links the idea/design/plan docs and lists test evidence.
