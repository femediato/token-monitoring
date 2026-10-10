# Auto LPing

Solana meme coin monitoring, coin selection, early-opportunity detection, backtesting and
paper trading. **No live trading in v1.** See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
for the design, schema and phase plan.

## Requirements

- Docker with the Compose plugin
- [uv](https://docs.astral.sh/uv/) (for running tests and tooling on the host)

## Quick start

```bash
cp .env.example .env      # add JUPITER__API_KEY / HELIUS__API_KEY when available
make up                   # db + migrations + api + collector, waits until healthy
curl localhost:8000/health/ready
make test                 # unit + integration (integration uses the autolp_test DB)
```

| Command | Purpose |
|---|---|
| `make up` / `make down` | Start / stop the stack (data persists in the `pgdata` volume) |
| `make logs` | Follow JSON logs from all services |
| `make test-unit` | Unit tests, no services needed |
| `make test-integration` | Integration tests against TimescaleDB (`make up` first) |
| `make lint` / `make fmt` | ruff + mypy / auto-format |
| `make report HOURS=72` | Collection acceptance report (coverage, gaps, staleness) |

## Services

| Service | Role |
|---|---|
| `db` | PostgreSQL 16 + TimescaleDB; `autolp_<env>` plus `autolp_test` |
| `migrate` | One-shot `alembic upgrade head` before app services start |
| `api` | FastAPI: `/health`, `/health/ready`, `/monitoring/jobs`, `/monitoring/freshness` |
| `collector` | Scheduled collection from Jupiter, DEX Screener, GeckoTerminal, Helius; every run recorded in `collector_runs` |

## Conventions

- All timestamps are timezone-aware UTC.
- Every provider response is stored verbatim in `raw_observations` before normalization.
- Tokens are identified by exact mint address.
- Missing data is stored as missing (NULL), never defaulted to a value that looks safe.
- Secrets only via environment / `.env` (gitignored). Use read-only API keys.
