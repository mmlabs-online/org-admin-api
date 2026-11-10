---
description: Stage 5 — multi-agent review of the current branch diff
argument-hint: [base branch, default main]
---

Base branch: $ARGUMENTS (use `main` if empty)

1. Collect the diff: `git diff <base>...HEAD` plus uncommitted changes. Note the plan task(s) it implements.
2. Run in parallel (one message, several Agent calls), each given the diff and the relevant design/plan:
   - **code-reviewer** — correctness, maintainability, async/DB pitfalls (N+1, missing transactions, session misuse).
   - **appsec-engineer** — authn/authz on every endpoint, injection, mass assignment, secrets in logs, OWASP API Top 10.
   - **api-tester** — missing test cases; write them if cheap, otherwise list them.
3. Verify each finding yourself against the code; drop false positives. Fix the confirmed blockers (or list them if the owner prefers).
4. Run `make check`, then ask **reality-checker** for a go / needs-work verdict using the actual test output as evidence.
5. Reply with: blockers (fixed / open), suggestions, verdict.
