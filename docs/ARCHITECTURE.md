# Auto LPing — Architecture & Phase 1 Plan

Status: proposal, pending approval. Dates in UTC.

## 1. Plan assessment — changes adopted

| # | Issue in master plan | Resolution |
|---|---|---|
| 1 | 90-day point-in-time backtest assumes history that mostly does not exist (unique buyers, holder distribution, liquidity reserves are not available retroactively; DEX Screener has no history at all). | Collector starts as early as possible and is the long pole. Backfill only what is genuinely point-in-time (OHLCV via Birdeye if available). Full-feature backtests run on self-collected data; the 90-day window starts at collector go-live. |
| 2 | Unique buyers/sellers and buy/sell volume split are not in DEX Screener (only trade counts). | Jupiter Tokens V2 is the primary source (buy/sell volume, trade and trader counts). Fields a provider lacks are nullable; scoring treats missing as missing, never as zero or "good". |
| 3 | "Meme coin" is undefined. | Explicit, versioned classifier: exclude stablecoins, LSTs, wrapped majors, Jupiter-strict-list blue chips; record reason code. |
| 4 | Binary 7-signal early score only takes values k/7·100. | Thresholds effectively mean: ≥6/7 high priority (85.7), 5/7 emerging (71.4), 4/7 monitor (57.1). Documented; continuous score later. |
| 5 | "Liquidity growth adjusted for price" is underspecified. | Measure growth of the quote-side reserve (SOL/USDC) of the main pool, which is not inflated by base-token price moves. |
| 6 | "Top-10 concentration after exclusions" needs owner resolution. | Resolve largest token accounts → owners; exclude AMM pool vaults, burn/incinerator, known lockers; store excluded list per snapshot. |
| 7 | 100 paper trades across 3 strategies is a thin sample for gating. | Keep the gate, but report per-strategy confidence intervals; parameters frozen (pre-registered) before holdout/paper. |
| 8 | Market cap vs FDV ambiguous. | Use circulating market cap where provided, FDV fallback flagged in features. |
| 9 | Host has 3 GB RAM, no Docker. | Docker Compose remains the deployment target; Timescale tuned small; frontend built only in Phase 8. |

## 2. Repository structure

```
.
├── docker-compose.yml          # db, api, collector (frontend added Phase 8)
├── Makefile                    # make up / test / migrate / lint
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini, migrations/
│   ├── src/autolp/
│   │   ├── config.py           # pydantic-settings, env profiles dev/test/prod
│   │   ├── logging.py          # structlog JSON, UTC
│   │   ├── db/                 # engine, session, ORM models
│   │   ├── providers/          # dexscreener, birdeye, solana_rpc, jupiter; shared http (retry, rate limit)
│   │   ├── collector/          # scheduled jobs, run bookkeeping, gap detection   (Phase 2)
│   │   ├── selection/          # eligibility, scoring, watchlist                  (Phase 3)
│   │   ├── early/              # early-opportunity model + outcome evaluation     (Phase 4)
│   │   ├── strategies/ backtest/ paper/                                           (Phases 5-7)
│   │   └── api/                # FastAPI app, health, read endpoints
│   └── tests/{unit,integration,fixtures}
├── frontend/                   # Next.js + TS (Phase 8; MVP may ship a minimal version)
└── docs/
```

Processes are separate: `collector` (writes market data only) and `api` (reads). Strategy/paper engines will be a third process and never call collectors directly.

## 3. Database schema (TimescaleDB on Postgres 16)

All timestamps `timestamptz`, UTC. Hypertables marked (H).

**Phase 1**
- `collector_runs` — id, job, started_at, finished_at, status, rows_written, error. Basis for gap/staleness monitoring.
- `raw_observations` (H) — fetched_at, source, endpoint, params jsonb, http_status, payload jsonb, payload_sha256. Every provider response is persisted before normalization.
- `tokens` — mint (PK), symbol, name, decimals, first_seen_at, pair_created_at, discovery_source, classification, classification_reason.

**Phase 2**
- `pools` — address (PK), token_mint, dex_id, quote_mint, created_at, is_primary.
- `market_snapshots` (H) — token_mint, observed_at, source, price_usd, market_cap, fdv, liquidity_usd, volume_{5m,1h,24h}, buys/sells_{5m,1h,24h}, buy/sell_volume_{5m,1h,24h}, traders_{1h,24h}, net_buyers_1h, holder_count, raw_observation_id. Unique (token_mint, source, observed_at).
- `candles_5m` (H) — token_mint, pool, bucket, o/h/l/c, volume_usd, source. PK (token_mint, source, bucket).
- `pool_reserves` (H) — pool, observed_at, reserve_base, reserve_quote, liquidity_usd.
- `holder_snapshots` — token_mint, observed_at, holder_count, top10_pct_raw, top10_pct_adjusted, excluded jsonb.
- `token_security` — token_mint, checked_at, mint_authority, freeze_authority, is_mutable, flags jsonb.
- `swap_quotes` — token_mint, quoted_at, side, amount_usd, out_amount, price_impact_pct, route jsonb.

**Phase 3-4**
- `eligibility_evaluations`, `score_evaluations` (model_version, component scores, raw features jsonb), `watchlist_events` (add/remove/upgrade + reason code), `watchlist_members`.
- `early_alerts` (signals fired, score, state, model_version), `alert_outcomes` (1h/4h/12h/24h returns, MFE, MAE, entry_feasible).

**Phases 5-7**: `strategy_configs` (versioned params), `signals`, `orders`, `fills`, `positions`, `backtest_runs`.

## 4. Dependencies

Backend: fastapi, uvicorn, sqlalchemy 2 (async) + asyncpg, alembic, pydantic-settings, httpx, tenacity, apscheduler 3.x, structlog, numpy/pandas (Phase 3+).
Dev: pytest, pytest-asyncio, respx (HTTP mocks), ruff, mypy.
Infra: timescale/timescaledb:latest-pg16, Docker Compose.
Tooling: uv for Python env/lockfile.

### External data sources (free tiers; keys not yet confirmed)

| Source | Role | Budget / cadence |
|---|---|---|
| Jupiter (free key) | Primary: discovery, price, volume, liquidity, buy/sell volume & counts, trader counts, preliminary holder/audit data (Tokens V2); swap quotes for slippage checks | 1 req/s, unlimited credits. Watchlist: all 30 mints in one Tokens V2 request every 60 s |
| DEX Screener (no key) | Secondary: pool-level liquidity/reserves, volume, price, buy/sell counts | 30 addresses per request every 5 min |
| Helius (free key) | On-chain verification: mint/freeze authority, supply, largest holders | 1M credits/month; per-endpoint credit costs tracked in `collector_runs` |
| Birdeye | Disabled initially; enable only for specific missing data | 30k CU/month free |
| GeckoTerminal | Optional historical OHLCV backfill | 30 req/min |

Usage is minimised by batching, one shared collector + cache, slow metadata refresh, and deep (Helius) checks only for shortlisted coins. Timestamped snapshots stored from day one for backtesting. Scope: monitoring and paper trading only.

## 5. Phase 1 plan

1. Repo skeleton, `pyproject.toml`, uv lockfile, ruff/mypy config.
2. Config: `APP_ENV` = dev/test/prod, secrets only via env / `.env` (gitignored), `.env.example` documented.
3. Structured JSON logging with UTC timestamps and request/job ids.
4. DB: async engine, ORM models for Phase 1 tables, Alembic migration that enables the timescaledb extension and creates hypertables.
5. Shared HTTP client: timeouts, tenacity retry with jitter on 429/5xx, per-provider token-bucket rate limiter, raw-response persistence hook.
6. FastAPI app with `/health` (process) and `/health/ready` (DB reachable, migrations at head).
7. Collector process skeleton: APScheduler, `collector_runs` bookkeeping, graceful shutdown; a no-op heartbeat job.
8. Docker Compose: db (named volume), migrate (one-shot), api, collector; `make up` starts everything.
9. Tests: unit (config, retry/rate-limit with respx, logging) and integration (migrations up/down, write/read raw observation, restart persistence) against a real Timescale container.

Acceptance: `make up` starts the stack; `make test` passes; `docker compose restart` preserves data.
