.PHONY: install dev db-up db-down migrate migration lint format typecheck test check

install:        ## Install deps (incl. dev)
	uv sync

dev:            ## Run the API with auto-reload
	uv run uvicorn app.main:app --reload

db-up:          ## Start local Postgres via docker compose
	docker compose up -d db

db-down:
	docker compose down

migrate:        ## Apply migrations
	uv run alembic upgrade head

migration:      ## Create a migration: make migration m="add users"
	uv run alembic revision --autogenerate -m "$(m)"

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy app

test:
	uv run pytest -q

check: lint typecheck test   ## Everything CI runs
