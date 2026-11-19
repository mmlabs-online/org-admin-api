# org-admin-api

Admin API server for the whole **mmlabs** ecosystem: internal staff use it (via an admin web UI) to manage organizations, staff access, products and entitlements across mmlabs services. It is also **mmlabs Identity**: one user account (email + password) for every mmlabs app, with tokens apps verify via JWKS (spec §5.8). Other mmlabs services may call it too.

- Product spec: [docs/spec-v1.md](docs/spec-v1.md) — source of truth for scope. If this file, a ticket and the spec disagree on scope, the spec wins; flag the conflict.
- Decisions: [docs/adr/](docs/adr/) — notably [ADR-0001 multi-app authorization](docs/adr/0001-multi-app-authorization.md). Identity plan, data model, API and decision log D1–D12: [docs/identity-architecture.md](docs/identity-architecture.md).
- Workflow (tickets, agents, commands): see **Workflow** below.

---

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, **FastAPI**, Pydantic v2, pydantic-settings |
| DB | **PostgreSQL 17** (the shared mmlabs Postgres in production, database `org_admin`), SQLAlchemy 2.0 async (`asyncpg`), Alembic |
| Tooling | `uv`, `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-asyncio` + `httpx` |
| Runtime | Docker image (uvicorn only; migrations run as a separate deploy step). Deployed on the Mac runner via `mmlabs-online/deployment` — see `docs/deploy.md` |
| CI/CD | GitHub Actions: `ci.yml` (lint, typecheck, tests on Postgres, `alembic check`); `release.yml` (push branch `release/vX.Y.Z` from main → tag, image → deployment PR) |

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
| R3 | **Every endpoint belongs to exactly one auth class** (D14, `docs/identity-architecture.md` §4): (1) **Bearer** — admin endpoints under `/api/v1/*`: access token with `aud=admin` + a declared `require_permission("admin:…")` (once ADM-002 lands; until then new endpoints note the permission they will need), except the self-scoped reads listed in ADR-0008 (USR-11: `GET /api/v1/me/permissions`), which declare no permission and return only the caller's own data; (2) **cookie** — identity endpoints under `/api/v1/auth/*` that act for a signed-in account (accounts, token, join, logout, orgs, resend verification, bug reports — D28): `mm_device` cookie + `Origin` check + `X-MM-App` (R11); (3) **public** — health endpoints, `/.well-known/jwks.json`, the role catalog `GET /api/v1/apps/{key}/roles` (D17; rate-limited, 404 for unknown/disabled/`admin` apps), and `/api/v1/auth` signup, login, verify-email, password forgot/reset (rate-limited, Turnstile where required). (4) **app credential** (D25, ADR-0016) — only `PUT /api/v1/apps/{key}/manifest`: `Authorization: Bearer mmak_<key_id>_<secret>`, an opaque per-app key issued by staff (`admin:*`), stored only as a SHA-256 hash, accepted only for its own app (another app → 403) and on no other route; no CORS; per-IP rate-limited. No refresh tokens. |
| R4 | **Every mutating admin action writes an audit entry** in the same transaction (once ADM-004 lands). |
| R5 | **Config from env only** via `app/core/config.py`; keys listed in `.env.example` without values. No secrets, tokens or passwords in code, logs or error details. |
| R6 | **One error shape**: `{"error": {"code", "message", "details"}}` via `app/core/errors.py`. Codes: 201 create, 204 delete, 401, 403, 404, 409, 412 (stale If-Match, ADM-024), 422. |
| R7 | **Stay inside the ticket's plan.** Anything else found goes to the follow-ups list, not into the diff. |
| R8 | **No PASS without green `make check`.** Every new endpoint has tests: happy path, 404, validation/conflict, and 401/403 once auth exists. |
| R9 | **API conventions:** `/api/v1/<plural-noun>`, `PATCH` for partial updates, lists return `Page[T]` (`limit` ≤ 100, `offset`), UUID ids, `timestamptz`. No breaking changes inside v1. Exceptions (D18, D23): apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…` paths, and the role catalog is a single document, not `Page[T]`; `GET /api/v1/me/permissions` (singular `me`, a single document, not `Page[T]`); `PUT /api/v1/apps/{key}/manifest` (singular `manifest`, a single document; D25); `GET /api/v1/stats/dashboard` (a single document, not `Page[T]`; D26). |
| R10 | **Non-goals are hard limits** (spec §3): no billing, no social login / full OIDC provider / hosted login UI in v1. End-user identity *is* in scope (spec §5.8). |
| R11 | **Credentials, sessions and tokens** (details: `docs/identity-architecture.md` §5): Argon2id for passwords; device-session, reset and verification tokens stored only as hashes; signing keys from env, never in the repo, with `kid`; never log passwords, tokens or full emails in errors. **Cookie** `mm_device`: opaque, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`, host-only (no `Domain`). **Rotation:** on login and at most every ~24 h, 60 s grace for the previous value; reuse after the grace revokes the whole device session. **Brute force:** no hard lockout — per-email exponential backoff after 5 failures (cap 15 min), per-IP ~20/min, Turnstile after 3 failures (always on sign-up/forgot), identical error for unknown email and wrong password. **CORS/CSRF:** allow-origin echoes only `apps.origins` (with credentials); every cookie-authenticated POST checks `Origin` and requires `X-MM-App: <key>`. **Verified email** is required to join an app or get any app token. |

---

## Workflow

<!-- spaceM project-kit 1.1.2 — inserted by /apply-kit. Settings: `.claude/loop.config.md`. Kit files: `.claude/kit.lock`. -->

One orchestrated loop per ticket. **The main session is the orchestrator** (subagents cannot dispatch other subagents), driven by `/run`.

```
/ticket "..."   →  BACKLOG
/bug "..."      →  BACKLOG (BUG-xxx; then: record PR, record another, or fix the batch now)
/run [ID]       →  CONSULT → PLAN ─(gate: High path → Ming approves)→ IMPLEMENT → REVIEW ─PASS→ DONE → PR → Ming merges → MERGED
                                                                         ^            │
                                                                         └── REWORK ──┘  max 2 reworks → ESCALATED (stop, ask Ming)
/solo "..."     →  small direct change outside the loop (logged in work/solo-log.md)
/status         →  board summary
/run-batch …    →  several tickets: plan all → one approval round → build + PR each (--auto-merge: merge Low / approved Medium, then continue)
```

- Each ticket runs on branch `ticket/<ID>` (or the session's mandated branch). The board `work/backlog.md` changes only on `main`; tickets run in the pick order of `loop.config.md` (default: row order).
- A ticket is claimed with `Claimed by: claude@<timestamp>` and every run appends to `## Handoff` in its `ticket.md`.
- **Ming approves and merges every PR.** The only exception is `/run-batch --auto-merge`: Low PRs when `Auto-merge Low` is on, Medium PRs Ming approved in the batch round; High never.
- Project settings (ticket prefix, scope sources, checks, consult table, review triggers, High paths, guardrails): `.claude/loop.config.md`.
- Ticket states: `BACKLOG` → `PLANNING` → `IN_PROGRESS` → `REVIEW` → `DONE` → `MERGED`, plus `REWORK`, `BLOCKED`, `ESCALATED`.

Files: `work/backlog.md` (board), `work/ming-todo.md` (Ming-only steps), `work/decisions.md`, `work/solo-log.md`,
`work/templates/`, `work/batches/` (`/run-batch` state), `work/tickets/<ID>/` (`ticket.md`, `consult-*.md`, `plan.md`, `impl-log.md`, `review.md`, review-agent files, `escalation.md`).

Kit-managed files (`.claude/commands/`, `.claude/agents/`, `work/templates/`, `.github/scripts/risk_tier.py`,
`.github/workflows/risk-tier.yml`) come from spaceM `skills-library/project-kit`; change them there, not here, so every project gets the fix.

**Usage guide (commands, `/run-batch`, merge rules, what to answer): [docs/agent-workflow.md](docs/agent-workflow.md).**

This repo: tickets are picked by **priority** (P1 > P2 > P3, then board order); every High path (migrations, `app/core/`,
auth, tokens, sessions, RBAC, email, release) stops at PLAN for the owner's approval and is never merged by an agent.

Agents (`.claude/agents/`): `planner` (opus, `plan.md` = technical design), `implementer` (sonnet), `reviewer` (opus);
consultants by area — `software-architect` (architecture), `database-optimizer` (db), `api-platform-engineer` (api),
`identity-access-engineer` (auth), `security-architect` (security); `appsec-engineer` reviews security/auth changes
(`security-review.md`); `product-manager` for `/idea` (may edit `docs/spec-v1.md`).

## Conventions

- Language: code, comments, docs, commit messages in English. Reports to the owner in Vietnamese.
- Ticket IDs: **`USR-XX`** for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8; ADR-0001); **`ADM-XXX`** for everything else; **`BUG-XXX`** for bugs, created only by `/bug` (template `work/templates/bug.md`, ADR-0012). Every bug fix ships with a regression test that fails before the fix.
- Commits: `<ID>: <imperative summary>` (e.g. `USR-01: …`, `ADM-004: …`; or `solo: <summary>`).
- Secrets: only in `.env` (git-ignored) and the deploy environment.
- Third-party agent definitions come from msitarzewski/agency-agents (MIT) — see `THIRD_PARTY_NOTICES.md`.
- ⚠️ Until ADM-002 is done, the API is unauthenticated. The owner accepted deploying it to `api.mmlabs.online` without a Cloudflare Access gate (2026-11-10, spec §6.1); keep ADM-002 high priority.
