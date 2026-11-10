# org-admin-api

Admin API server for the mmlabs ecosystem — FastAPI + PostgreSQL.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker (or a local PostgreSQL 16).

```bash
cp .env.example .env
make install
make db-up        # Postgres on :5432 with org_admin and org_admin_test databases
make migrate
make dev          # http://localhost:8000/docs
```

Run everything CI runs:

```bash
make check
```

## Endpoints (so far)

| Method | Path | |
|---|---|---|
| GET | `/healthz` | liveness |
| GET | `/readyz` | readiness (checks DB) |
| GET/POST | `/api/v1/organizations` | list (paginated) / create |
| GET/PATCH/DELETE | `/api/v1/organizations/{id}` | read / update / delete |

> ⚠️ No authentication yet — do not expose publicly. Auth + RBAC is the next planned feature.

## How we work

Ideas, designs and plans live in `docs/` and are produced with Claude Code slash commands and subagents. See [`docs/WORKFLOW.md`](docs/WORKFLOW.md) and [`CLAUDE.md`](CLAUDE.md).

Subagents in `.claude/agents/` are adapted from [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) (MIT, see `.claude/agents/LICENSE-agency-agents`).
