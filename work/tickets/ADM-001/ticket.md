# ADM-001: Scaffold FastAPI + PostgreSQL service, tooling and CI

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | architecture, db |
| Attempts | 1 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Bootstrap the service: FastAPI app, async SQLAlchemy + Alembic on PostgreSQL, error envelope, pagination, health checks, the reference `organizations` module, tests, Docker and CI.

## Spec reference
§5.1 Organizations, §6 Non-functional

## Acceptance criteria
- [x] `make check` (ruff, mypy --strict, pytest) passes — 8 tests green on Postgres 16
- [x] `/healthz` and `/readyz` (checks DB) respond 200
- [x] Organizations CRUD under `/api/v1/organizations` with pagination, 404, 409 and 422 in the shared error envelope
- [x] Alembic migration for `organizations`; `alembic check` reports no drift
- [x] Dockerfile, docker-compose (Postgres 16) and GitHub Actions CI

## Notes / answers from owner
Done during repo bootstrap, before the ticket loop existed (no plan/review files).
