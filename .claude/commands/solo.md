---
description: Make a small direct change outside the ticket loop (typo, config tweak, tiny fix).
argument-hint: "<description>"
---

<!-- spaceM project-kit. Project-specific values live in `.claude/loop.config.md`. -->

Do this small change directly, without the plan/review loop: `$ARGUMENTS`

1. **Size check first.** If it needs more than ~50 changed lines, more than 3 files, touches a **High path**, or hits
   anything listed under **/solo limits** in `.claude/loop.config.md` — stop and suggest `/ticket` instead.
2. Make the smallest change that does it. The rules in `CLAUDE.md` still apply.
3. Run the **Checks** listed in `loop.config.md`. Do not finish with failing checks.
4. Append one row to `work/solo-log.md`: date, summary, files touched, check result.
5. Commit on the current branch as `solo: <summary>`. Do not push.
