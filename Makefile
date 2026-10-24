UV ?= $(shell command -v uv || echo $(HOME)/.local/bin/uv)
TEST_DATABASE_URL ?= postgresql+asyncpg://autolp:autolp@localhost:5432/autolp_test

.PHONY: up down logs ps restart migrate test test-unit test-integration lint fmt report replay backtest sweep events paper-report

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

backtest:          ## backtest report, e.g. make backtest [DAYS=90] [UNSEAL=1] [ARGS="--set strategy.range.max_hold_hours=6"]
	docker compose exec -T api python -m autolp.backtest.run $(if $(DAYS),--days $(DAYS)) $(if $(UNSEAL),--unseal-holdout) $(ARGS)

events:            ## event study on the host, e.g. make events GRID=round1 [END=2026-10-24T12:00]
	cd backend && $(UV) run python -m autolp.backtest.events --grid ../research/events/$(or $(GRID),round1).json $(if $(END),--end $(END))

sweep:             ## strategy research sweep on the host, e.g. make sweep GRID=round1 [END=2026-10-15T17:00]
	cd backend && $(UV) run python -m autolp.backtest.sweep --grid ../research/grids/$(or $(GRID),round1).json $(if $(END),--end $(END))

paper-report:      ## paper trading summary, execution quality and reconciliation
	docker compose exec -T api python -m autolp.paper.report

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
