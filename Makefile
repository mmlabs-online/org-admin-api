.PHONY: install dev db-up db-down migrate migration grant-super-admin register-app lint format typecheck test check

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

grant-super-admin:  ## Grant admin:super-admin: make grant-super-admin email=you@example.com
	@test -n "$(email)" || { echo "usage: make grant-super-admin email=you@example.com"; exit 64; }
	@uv run python -m app.cli grant-super-admin --email '$(email)'

register-app:  ## Register/update an app: make register-app ARGS='--key x --name X --origin https://x.mmlabs.online'
	@test -n "$(ARGS)" || { echo "usage: make register-app ARGS='--key <key> --name <name> --origin <origin> ...'"; exit 64; }
	@uv run python -m app.cli register-app $(ARGS)

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
