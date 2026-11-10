---
description: Make a small direct change outside the ticket loop (typo, config tweak, tiny fix).
argument-hint: "<description>"
---

Do this small change directly, without the plan/review loop: `$ARGUMENTS`

1. **Size check first.** If it needs more than ~50 changed lines, more than 3 files, a migration, auth/permission logic, or secrets — stop and suggest `/ticket` instead.
2. Make the smallest change that does it. Rules R1–R10 in `CLAUDE.md` still apply.
3. Run `make check`. Do not finish with failing checks.
4. Append one line to `work/solo-log.md`: date, summary, files touched, check result.
5. Commit on the current branch as `solo: <summary>`. Do not push.
