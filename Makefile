UV ?= $(shell command -v uv || echo $(HOME)/.local/bin/uv)
TEST_DATABASE_URL ?= postgresql+asyncpg://autolp:autolp@localhost:5432/autolp_test

.PHONY: up down logs ps restart migrate test test-unit test-integration lint fmt report replay

up:                ## build and start the whole stack
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

restart:
	docker compose restart

report:            ## collection acceptance report, e.g. make report HOURS=72
	docker compose exec -T api python -m autolp.monitoring.report --hours $(or $(HOURS),24)

replay:            ## deterministic strategy decision replay, e.g. make replay HOURS=24 END=2026-10-12T00:00
	docker compose exec -T api python -m autolp.strategies.replay --hours $(or $(HOURS),24) $(if $(END),--end $(END))

migrate:
	docker compose run --rm migrate

test-unit:
	cd backend && $(UV) run pytest tests/unit -q

test-integration:  ## requires `make up` (db running)
	cd backend && DATABASE_URL=$(TEST_DATABASE_URL) APP_ENV=test $(UV) run pytest tests/integration -q

test: test-unit test-integration

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check . && $(UV) run mypy src

fmt:
	cd backend && $(UV) run ruff check --fix . && $(UV) run ruff format .
