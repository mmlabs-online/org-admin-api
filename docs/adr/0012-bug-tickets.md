# ADR-0012: Bug tickets (`BUG-XXX`) and the `/bug` command

Status: Accepted (owner, 2026-11-16 — port of the mmlabs bug workflow already used in TuDoCuaTui)
Date: 2026-11-16

## Context
The ticket loop (`/ticket` → `/run`) is shaped around new scope: a goal, a spec reference and
acceptance criteria. A bug report needs other facts first — where it was found, how to reproduce
it, expected vs actual, the root cause — and its fix is only trustworthy with a test that would
have caught it. Bugs filed as `ADM-XXX` / `USR-XX` lose that structure, and nothing stops a "fix"
from landing without proof that the bug existed or is gone.

## Decision
1. **Ticket ID `BUG-XXX`** (3 digits), alongside `USR-XX` and `ADM-XXX`, created only by `/bug`
   from `work/templates/bug.md`: Type, Severity, Found in / Introduced in (release tags
   `vX.Y.Z`), environment, steps, expected/actual, root cause marked *confirmed* or *suspected*
   with file:line. `/ticket` redirects bug reports to `/bug`. `/bug` investigates read-only first
   and dedupes against open (not `DONE`) bug tickets.
2. **Severity → priority.** S1 = security (auth bypass, missing permission check, leaked
   token/secret/password, broken isolation), data loss, or a core identity/admin flow impossible
   → P1. S2 = broken with a workaround, or a wrong status code / error envelope a client relies on
   → P1 or P2. S3 = cosmetic → P3. `/run` with no ID picks a ready S1 bug before anything else;
   `/status` lists open bugs by severity.
3. **Same loop.** Bugs run through `/run` (consult → plan → implement → review, max 2 reworks).
   Areas follow the normal rules, so a bug in R3/R11 behaviour gets the auth/security specialists
   and `appsec-engineer`. No extra human gate is added: the repo has none between PLAN and
   IMPLEMENT.
4. **Regression test required.** Every fix ships with a test that reproduces the bug and fails
   before the fix: a pytest test against the Postgres test DB. Only when pytest cannot see the bug
   (Docker image, deploy, CI, real SMTP or Cloudflare) a scripted reproduction outside the repo is
   accepted, its command and output recorded in impl-log and re-run by the reviewer. The
   implementer records the failing run before the fix; the reviewer gives REWORK without that
   evidence.
5. **Batches.** `/bug` commits the record (`<ID>: record bug`, ticket.md + board row only) on a
   batch branch `bug/<first ID>` from `origin/main`, then asks the owner: A) open a record PR into
   `main` and stop; B) record another bug on the same branch and ask again; C) fix the whole batch
   now through the `/run` loop on that branch and open one PR. Agents never approve or merge.
6. **No personal data in bug records.** Passwords, tokens, cookies, signing keys and full emails
   from logs or screenshots are never copied into a ticket (R5, R11).

## Consequences
- A record PR (option A) must be merged before `/run` on `main` can see the bug.
- One PR for a batch (option C) is larger to review; a bug that ends `BLOCKED` or `ESCALATED`
  does not hold back the others and is listed in the PR body.
- Bugs only reproducible outside pytest rely on a recorded manual script until the repo gets a
  harness for that layer; adding one is a separate ticket.
