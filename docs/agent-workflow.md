# Agent workflow — usage guide (org-admin-api)

How to drive the agent ticket loop in this repo: one ticket with `/run`, several with `/run-batch`.
The loop comes from the spaceM project-kit (version in `.claude/kit.lock`); this repo's settings are in
[`.claude/loop.config.md`](../.claude/loop.config.md). Rules R1–R11 in [`CLAUDE.md`](../CLAUDE.md) always apply.

## Before you start

- Open Claude Code **inside this repo** (in spaceM: `cd ~/spaceM/projects/org-admin-api && claude`).
- For a batch, a **sonnet** session is enough: `claude --model sonnet`. The orchestrator only does bookkeeping;
  planner, reviewer, security and identity agents still run on opus from their own files.
- Checks need Postgres: `make install && make db-up` (the loop runs this itself as its Prepare step).

## Commands

| Command | What it does |
|---|---|
| `/ticket "<description>" [P1-P3]` | New `USR-XX` / `ADM-XXX` ticket + board row |
| `/bug "<what is wrong>" [S1-S3]` | New `BUG-XXX` with repro and root cause; then A) record PR, B) record another, C) fix the batch now |
| `/idea "<idea>"` | `product-manager` updates `docs/spec-v1.md` and proposes tickets |
| `/run [ID]` | One ticket, full loop, ends with a PR. No ID: first runnable ticket **by priority** |
| `/run-batch …` | Several tickets: plan all → one approval round → build + PR each (below) |
| `/status` | Board summary, open PRs, what needs you, next runnable ticket |
| `/solo "<change>"` | Tiny change outside the loop (no High path, no schema, no auth) |

## The loop for one ticket (`/run`)

```
claim → CONSULT (by Areas: architecture, db, api, auth, security — all that match)
      → PLAN (plan.md = technical design: data model / migration, API contract, tests)
      → GATE: plan touches a High path → stop, BLOCKED until you write "Plan approved (date)" in ticket.md
      → IMPLEMENT → REVIEW (reviewer + appsec-engineer for security/auth/core changes)
      → ≤ 2 reworks, else ESCALATED (escalation.md for you) → PR → you merge → MERGED
```

High paths (gate + never merged by an agent): `migrations/`, `app/db/`, `app/core/`, `app/api/deps.py`, auth,
admin auth, tokens, sessions, RBAC, app credentials, permissions, email, `Dockerfile`, `docker-compose.yml`,
`release.yml`, `risk-tiers.yml`, `.env.example`. Full list: `.github/risk-tiers.yml`.

## Many tickets (`/run-batch`)

### Examples

```
/run-batch --next 3                         # the 3 first runnable tickets by priority
/run-batch USR-23 USR-24 USR-27             # these tickets (dependencies first, then priority)
/run-batch --next 3 --plan-only             # plans + approval message only, then stop
/run-batch --next 5 --auto-merge --one      # build one ticket at a time, merging what the rules allow
/run-batch --resume                         # continue the open batch
```

### What happens

1. **Select.** Skips `DONE`/`MERGED`/`ESCALATED`, claimed tickets, unmet `Depends on`, and `ADM-007`
   (left `IN_PROGRESS` on purpose). Writes `work/batches/<date>-<n>.md` on `main`.
2. **Plan every ticket** (consult + planner; no code). Each plan gets a predicted tier.
3. **One approval message to you**: a table (ID, title, predicted tier, plan link) and numbered questions with
   defaults. Answer once, in chat, for example:

   > duyệt hết; câu 1 theo mặc định; câu 2: tách endpoint invitations sang ticket sau; cho merge USR-27

   - Plan approval is required for the batch, and per ticket for those touching a High path.
   - "cho merge" only matters for **Medium** tickets with `--auto-merge`.
4. **Build each approved ticket** in order: implement → review → ≤ 2 reworks → PR.
5. **Report**: PRs with their tier, merged by agent / waiting for you, agent calls and tokens per ticket.

### Merge rule (`--auto-merge`)

The agent squash-merges a PR only when the ticket passed review, every CI check is green, there is no conflict and
no "changes requested", **and** the tier from the real diff (`.github/scripts/risk_tier.py`, same as the `risk:` label) is:

| Tier | In this repo | Merged by the agent? |
|---|---|---|
| Low | only docs, `work/`, `tests/`, `*.md` | yes (`Auto-merge Low: on`, Ming 2026-11-19) |
| Medium | most API work outside the High paths (e.g. a new list filter in `app/modules/organizations/`) | only if you wrote "cho merge" for it in this batch |
| High | anything in the High paths | **never** — you review and merge |

After each merge the agent runs `make check` on `main`; if it fails, the batch stops (nothing is reverted).
In practice most `USR-*` tickets here are High, so a batch ends with PRs for you to review, in a suggested order.

### When it stops

| Batch phase | Why | You |
|---|---|---|
| `WAITING-APPROVAL` | plans are ready | answer the round → `/run-batch --resume` |
| `WAITING-MERGE` | a PR it may not merge (High, Medium without "cho merge", CI red, conflict), or the next ticket depends on an unmerged PR | review/merge → `/run-batch --resume` |
| `WAITING-MING` | a ticket ended `BLOCKED` or `ESCALATED` | answer in `ticket.md` / `escalation.md` → `/run-batch --resume` |
| `DONE` | all tickets finished | merge the remaining PRs in the suggested order |

With `--one`, the batch also stops after each built ticket: run `/clear`, then `/run-batch --resume --one`. This keeps
the session's context (and token cost) small on long batches.

## Things only you do

- Approve plans for High tickets and answer blocking questions (in `ticket.md` → *Notes / answers from owner*, or in
  the batch round).
- Merge High PRs (and any PR the batch did not merge).
- Release: push `release/vX.Y.Z` from `main` (see `docs/deploy.md`), then merge the deployment PR. Agents never
  push `release/*` and never touch the production database, signing keys or `IDENTITY_HMAC_KEY`.
- Items in `work/ming-todo.md`.

## Tuning (`.claude/loop.config.md`)

| Setting | Now | Change it to… |
|---|---|---|
| Pick order | `priority` | `board` to run strictly in row order |
| Max consultants | `0` (all matching areas) | `2` to save tokens on tickets with many areas |
| Model overrides | — | e.g. `implementer: opus` for hard tickets |
| Batch size | `5` | larger or smaller default for `/run-batch` |
| Auto-merge Low | `on` | `off` (also change `auto_merge` in spaceM `bootstrap/projects.yaml`) |

Commands, agents and templates are kit-managed: change them in spaceM (`skills-library/project-kit`) and run
`/apply-kit org-admin-api` there, not by editing them here.
