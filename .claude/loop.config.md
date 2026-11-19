# Loop config — org-admin-api

Project-specific settings for the spaceM project-kit commands (`/run`, `/run-batch`, `/ticket`, `/bug`, `/solo`,
`/status`, `/idea`) and the `planner` / `implementer` / `reviewer` agents. Written by `/apply-kit`, then owned by this
project: a kit upgrade never overwrites this file. Keep it short; rules themselves live in `CLAUDE.md` (R1–R11).
Usage guide: [docs/agent-workflow.md](../docs/agent-workflow.md).

## Identity
- Ticket prefix: `ADM` (tickets `ADM-001`…; bugs `BUG-001`…).
- Ticket IDs: `USR-XX` for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8; ADR-0001); `ADM-XXX`
  for everything else; `BUG-XXX` for bugs (`/bug`, ADR-0012).
- Owner: Ming (called "owner" in this repo). Reports in Vietnamese. Ming merges every PR, except what
  `/run-batch --auto-merge` may merge under its merge rule (see Batch).

## Selection
- Pick order: `priority` (P0 > P1 > P2 > P3, then board order).
- Dependencies are only the `Depends on …` lines in each `ticket.md`. The "Order of work" line in `work/backlog.md`
  is ordering history, not dependencies (Ming, 2026-11-19).
- `ADM-007` stays `IN_PROGRESS` without a claim (pipeline already in use); `/run` and `/run-batch` skip it until Ming
  closes it.

## Scope sources
- `docs/spec-v1.md` (source of truth for scope; it wins over a ticket — flag the conflict)
- `docs/adr/` (accepted decisions), `docs/identity-architecture.md` (identity plan, decision log D1–D25)
- `work/decisions.md`

## Prepare
- `make install && make db-up` (uv sync; docker compose Postgres with `org_admin` + `org_admin_test`).
  No Docker: `service postgresql start` and create `org_admin` / `org_admin_test` (user `postgres`, password
  `postgres`); record it in impl-log.

## Checks
| Check | Command |
|---|---|
| all (ruff, format check, mypy --strict, pytest) | `make check` |
| migrations, when the diff touches `migrations/` or models | `uv run alembic check` |

Bug test level: pytest against the Postgres test DB; a scripted reproduction recorded in impl-log only when pytest
cannot see the bug.

## Plan approval gate
- Policy: `high-paths`

## High paths
<!-- Mirror of `high:` in .github/risk-tiers.yml. -->
- `migrations/**`, `app/db/**`, `alembic.ini`
- `app/core/**`, `app/api/deps.py`
- `app/modules/auth/**`, `app/modules/admin_auth/**`, `app/modules/tokens/**`, `app/modules/sessions/**`,
  `app/modules/rbac/**`, `app/modules/app_credentials/**`, `app/modules/email/**`, `**/permissions*`
- `Dockerfile`, `docker-compose.yml`, `.github/workflows/release.yml`, `.github/risk-tiers.yml`, `.env.example`

## Consult
<!-- Area → specialist agent (read-only, at PLAN). -->
| Area | Agent |
|---|---|
| architecture | `software-architect` |
| db | `database-optimizer` |
| api | `api-platform-engineer` |
| auth | `identity-access-engineer` |
| security | `security-architect` |

## Review triggers
<!-- Run in parallel with `reviewer`. A 🔴 finding turns PASS into REWORK. -->
| Agent | Output file | Areas | Changed paths |
|---|---|---|---|
| `appsec-engineer` | `security-review.md` | security, auth | `app/core/`, `app/api/deps.py`, `**/auth*`, `**/rbac*`, `**/permissions*`, `**/tokens/`, `**/sessions/`, `migrations/`, `.env.example`, `Dockerfile`, `.github/` |

## Cost controls
- Fast lane: `on` — only tickets with `Size: XS|S` and Gate `none`; tickets without a Size field take the full lane.
- Small-diff review: `on` — diff < 100 changed lines and no High path → reviewer on sonnet.
- Max consultants: `0` (no limit; Ming, 2026-11-19).

## Model overrides
- —

## Batch
- Batch size: `5`
- Auto-merge Low: `on` — mirror of `auto_merge` in spaceM `bootstrap/projects.yaml` (Ming, 2026-11-19). Low = docs,
  `work/**`, tests only (see `.github/risk-tiers.yml`). Medium needs Ming's "cho merge" in the batch round; High
  (every path above) is never merged by an agent.
- Merge method: `squash`

## /solo limits
- Any High path, any schema change, anything touching auth, tokens, sessions, RBAC, secrets or the error envelope.

## Domain focus
- **Rules R1–R11** in `CLAUDE.md` (layering, reversible migrations, one auth class per endpoint, audit in the same
  transaction, config from env, one error envelope, tests, API conventions, credentials/sessions/tokens).
- **Module shape:** copy `app/modules/organizations/` (models → schemas → repository → service → router); register
  models in `app/db/models.py` and routers in `app/api/router.py`.
- **Plans are the technical design:** fill `Data model / migration` (tables, columns, constraints, indexes,
  downgrade) and `API contract` (method, path, schemas, status codes, required `admin:*` permission). A decision
  that outlives the ticket gets an ADR file `docs/adr/NNNN-<slug>.md` in the plan's file list.
- **Migrations:** `make migration m="..."` then hand-review names, server defaults and a working `downgrade()`;
  `uv run alembic check` clean. Split when a ticket needs more than one migration.
- **Tests:** every new endpoint has happy path, 404, validation/conflict (409/422) and 401/403; pagination bounds.
- **Review for:** async misuse (sync I/O, missing `await`), N+1 queries, transaction/session misuse, unbounded
  lists, mass assignment through schemas, races on unique fields, timezone-naive datetimes, PII in errors or logs.
- Cross-repo: tickets requested by admin-pan name the PAN ticket; API follow-ups for admin-pan go to Follow-ups.

## Guardrails
- Never read `.env*` values; never touch the signing keys (`JWT_SIGNING_KEY`), `IDENTITY_HMAC_KEY` or their files.
- Never run Alembic, the CLI (`app.cli`) or the ops workflow against the production database; production steps in
  `docs/deploy.md` are Ming's.
- Never push `release/*` (that is a production release).
