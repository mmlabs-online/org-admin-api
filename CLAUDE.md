# org-admin-api

Admin API server for the whole **mmlabs** ecosystem: internal staff use it (via an admin web UI) to manage organizations, staff access, products and entitlements across mmlabs services. Other mmlabs services may call it too.

- Product spec: [docs/spec-v1.md](docs/spec-v1.md) — source of truth for scope. If this file, a ticket and the spec disagree on scope, the spec wins; flag the conflict.
- Decisions: [docs/adr/](docs/adr/).
- Workflow (tickets, agents, commands): see **Workflow** below.

---

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, **FastAPI**, Pydantic v2, pydantic-settings |
| DB | **PostgreSQL 17** (the shared mmlabs Postgres in production, database `org_admin`), SQLAlchemy 2.0 async (`asyncpg`), Alembic |
| Tooling | `uv`, `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-asyncio` + `httpx` |
| Runtime | Docker image (uvicorn only; migrations run as a separate deploy step). Deployed on the Mac runner via `mmlabs-online/deployment` — see `docs/deploy.md` |
| CI/CD | GitHub Actions: `ci.yml` (lint, typecheck, tests on Postgres, `alembic check`); `release.yml` (tag `vX.Y.Z` → image → deployment PR) |

Do not change the stack without the owner's explicit decision.

```bash
make install      # uv sync
make db-up        # docker compose Postgres (org_admin + org_admin_test)
make migrate      # alembic upgrade head
make dev          # uvicorn --reload on :8000, docs at /docs
make migration m="add users"
make check        # ruff + format check + mypy + pytest — must pass before every commit
```

## Layout

```
app/
  main.py               # create_app(): middleware, error handlers, routers
  core/                 # config, errors (error envelope), pagination
  db/                   # Base + mixins, async engine/session, models registry (db/models.py)
  api/                  # deps (SessionDep, PageDep), v1 router aggregation
  modules/<domain>/     # models, schemas, repository, service, router  ← one folder per domain
migrations/             # Alembic (async env, reads DATABASE_URL from settings)
tests/                  # API tests against a real Postgres test DB, rolled back per test
```

`app/modules/organizations/` is the **reference module** — copy its shape for new domains.

---

## Architecture rules (reviewer enforces these)

| ID | Rule |
|---|---|
| R1 | **Layering.** router (HTTP only) → service (business rules, raises `AppError` subclasses) → repository (SQLAlchemy only). Routers never touch the ORM; services never raise `HTTPException`. |
| R2 | **Every schema change ships with a hand-reviewed Alembic migration** with a working `downgrade()`. New models are imported in `app/db/models.py`. `alembic check` reports no drift. |
| R3 | **Every `/api/v1` endpoint is authenticated and declares its permission** (once ADM-002/ADM-003 land). Until then, new endpoints note the permission they will need. Health endpoints stay public. |
| R4 | **Every mutating admin action writes an audit entry** in the same transaction (once ADM-004 lands). |
| R5 | **Config from env only** via `app/core/config.py`; keys listed in `.env.example` without values. No secrets, tokens or passwords in code, logs or error details. |
| R6 | **One error shape**: `{"error": {"code", "message", "details"}}` via `app/core/errors.py`. Codes: 201 create, 204 delete, 401, 403, 404, 409, 422. |
| R7 | **Stay inside the ticket's plan.** Anything else found goes to the follow-ups list, not into the diff. |
| R8 | **No PASS without green `make check`.** Every new endpoint has tests: happy path, 404, validation/conflict, and 401/403 once auth exists. |
| R9 | **API conventions:** `/api/v1/<plural-noun>`, `PATCH` for partial updates, lists return `Page[T]` (`limit` ≤ 100, `offset`), UUID ids, `timestamptz`. No breaking changes inside v1. |
| R10 | **Non-goals are hard limits** (spec §3): no end-user auth for products, no billing. |

---

## Workflow

One orchestrated loop per ticket. **The main session is the orchestrator** (subagents cannot dispatch other subagents), driven by `/run`. Same model as `mmlabs-online/stoktok`.

```
/idea "..."     →  spec section + proposed tickets (optional, for new scope)
/ticket "..."   →  BACKLOG
/run [ADM-xxx]  →  CONSULT (specialists by area) → PLAN (= technical design) → IMPLEMENT → REVIEW ─PASS→ DONE → PR (owner merges)
                                                                                 ^                │
                                                                                 └──── REWORK ────┘  max 2 reworks → ESCALATED (stop, ask owner)
```

- No human gate between PLAN and IMPLEMENT. The loop stops only when: the planner returns blocking questions (`BLOCKED`), reworks are exhausted (`ESCALATED`), or the ticket is `DONE`.
- Each ticket runs on branch `ticket/ADM-xxx` (or the session's mandated branch in cloud sessions). On `DONE` the loop **opens a PR into `main`**. **The owner approves and merges** — agents never merge.

### Commands (`.claude/commands/`)
| Command | Purpose |
|---|---|
| `/idea <idea>` | Shape new scope into `docs/spec-v1.md` and propose tickets |
| `/ticket <description> [P1-P3]` | Create `work/tickets/ADM-xxx/ticket.md` and a backlog row |
| `/run [ADM-xxx]` | Run the full loop for a ticket (no ID = highest-priority BACKLOG ticket) |
| `/status` | Board summary from `work/backlog.md` |
| `/solo <description>` | Small direct change outside the loop (still must pass checks; logged) |

### Agents (`.claude/agents/`)
Workflow roles:
| Agent | Model | Writes | Based on (agency-agents) |
|---|---|---|---|
| `planner` | opus | `plan.md` only | `project-management/project-manager-senior.md` + `engineering/engineering-software-architect.md` |
| `implementer` | sonnet | code + `impl-log.md` | `engineering/engineering-minimal-change-engineer.md` |
| `reviewer` | opus | `review.md` only | `engineering/engineering-code-reviewer.md` + `testing/testing-reality-checker.md` + `testing/testing-api-tester.md` |

Specialists (read-only; `/run` saves their output to the ticket folder):
| Agent | Used when |
|---|---|
| `software-architect` | ticket `areas` include `architecture` (also `/idea`) |
| `database-optimizer` | `db` |
| `api-platform-engineer` | `api` |
| `identity-access-engineer` | `auth` |
| `security-architect` | `security` (at PLAN) |
| `appsec-engineer` | REVIEW, when areas include `security`/`auth` or the diff touches core, deps, auth, migrations, env, Docker or CI |
| `product-manager` | `/idea` only (may edit `docs/spec-v1.md`) |

### Files (`work/`)
```
work/backlog.md                 board: one row per ticket
work/solo-log.md                log of /solo changes
work/templates/                 ticket, plan, impl-log, review, escalation
work/tickets/ADM-xxx/
  ticket.md                     goal, acceptance criteria, areas, state, attempts, PR
  consult-<agent>.md            specialist notes (optional)
  plan.md                       planner output (technical design)
  impl-log.md                   implementer output (appended per attempt)
  review.md                     reviewer verdict (appended per attempt)
  security-review.md            appsec output (when triggered)
  escalation.md                 only when ESCALATED
```

Ticket states: `BACKLOG` → `PLANNING` → `IN_PROGRESS` → `REVIEW` → `DONE`, plus `REWORK`, `BLOCKED`, `ESCALATED`.

---

## Conventions

- Language: code, comments, docs, commit messages in English. Reports to the owner in Vietnamese.
- Commits: `ADM-xxx: <imperative summary>` (or `solo: <summary>`).
- Secrets: only in `.env` (git-ignored) and the deploy environment.
- Third-party agent definitions come from msitarzewski/agency-agents (MIT) — see `THIRD_PARTY_NOTICES.md`.
- ⚠️ Until ADM-002 is done, the API is unauthenticated. The owner accepted deploying it to `api-admin.mmlabs.online` without a Cloudflare Access gate (2026-11-10, spec §6.1); keep ADM-002 high priority.
