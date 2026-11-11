# org-admin-api

Admin API server for the mmlabs ecosystem — FastAPI + PostgreSQL.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker (or a local PostgreSQL 17).

```bash
cp .env.example .env
make install
make db-up        # Postgres on :5432 with org_admin and org_admin_test databases
make migrate
make dev          # http://localhost:8000/docs
```

Deploy: see [`docs/deploy.md`](docs/deploy.md) (push branch `release/vX.Y.Z` from main → api.mmlabs.online).

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

> ⚠️ No authentication yet (ADM-002). Deployed without an access gate by owner decision (2026-11-10) — treat all data as exposed until auth ships.

## How we work

Work is driven by tickets and Claude Code subagents (same loop as stoktok):

```
/ticket "..."   # add a ticket to work/backlog.md
/run            # agents consult → plan → implement → review → open a PR, automatically
/status         # board summary
```

Scope lives in [`docs/spec-v1.md`](docs/spec-v1.md); the workflow, agents and rules are in [`CLAUDE.md`](CLAUDE.md).
Agent definitions are adapted from [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) (MIT, see `THIRD_PARTY_NOTICES.md`).
