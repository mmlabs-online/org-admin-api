# CLAUDE.md — org-admin-api

Admin API server for the whole **mmlabs** ecosystem: internal staff use it (via an admin web UI) to manage organizations, users, products and access across mmlabs services. Other mmlabs services may also call it.

## Stack

- Python 3.12, **FastAPI**, Pydantic v2, pydantic-settings
- **PostgreSQL 16**, SQLAlchemy 2.0 (async, `asyncpg`), Alembic
- Tooling: `uv`, `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-asyncio` + `httpx`

## Commands

```bash
make install      # uv sync
make db-up        # docker compose Postgres (creates org_admin + org_admin_test)
make migrate      # alembic upgrade head
make dev          # uvicorn --reload on :8000, docs at /docs
make migration m="add users"
make check        # ruff + mypy + pytest — must pass before every commit
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
docs/                   # ideas → design → plans (+ adr, templates). See docs/WORKFLOW.md
.claude/agents/         # subagents (mostly adapted from msitarzewski/agency-agents)
.claude/commands/       # /idea /design /plan /implement /review /ship
```

`app/modules/organizations/` is the **reference module** — copy its shape for new domains.

## Conventions

- Layering: router (HTTP only) → service (business rules, raises `AppError` subclasses) → repository (SQLAlchemy only). Routers never touch the ORM directly; services never raise `HTTPException`.
- New models must be imported in `app/db/models.py`, and every schema change ships with a hand-reviewed Alembic migration (CI runs `alembic check`).
- API: `/api/v1/<plural-noun>`, `PATCH` for partial updates, list endpoints return `Page[T]` (`limit` ≤ 100, `offset`). Errors always use `{"error": {"code", "message", "details"}}`.
- IDs are UUIDs; timestamps are `timestamptz` (`created_at`, `updated_at`).
- Settings come only from env vars via `app/core/config.py` (see `.env.example`). Never hardcode secrets.
- Tests: every endpoint gets happy path + 404 + validation/conflict tests. Use the `client` fixture.
- Commits: conventional commits (`feat(orgs): …`), reference the plan task (`T3`).

## Workflow

Follow `docs/WORKFLOW.md`: `/idea` → `/design` → `/plan` → `/implement` → `/review` → `/ship`. Don't skip to code for new features without an accepted design; bug fixes may go straight to `/implement`.

## Not done yet (planned)

Authentication & RBAC for admin staff, audit log, service-to-service auth — see `docs/ideas/0001-admin-platform.md`. Until auth lands, **endpoints are unauthenticated: do not deploy publicly.**
