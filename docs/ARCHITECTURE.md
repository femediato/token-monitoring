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

**Phase 5 (as built)**: `strategy_signals` (H) — every entry check per token, strategy and 15-minute run, with setup levels; parameters are versioned in code (`strategies/params.py`, `strat-v1`). See `docs/STRATEGIES.md`.

**Phase 6 (as built)**: `backtest_runs` — one row per stored backtest report (parameters, splits, trades, equity curves, baselines, input and result digests). See `docs/BACKTESTING.md`.

**Phases 6-7**: `backtest_runs`, paper `orders`, `fills`, `positions`.

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

## 6. Phase 2 — as built

### Jobs (collector process, wall-clock aligned, each run recorded in `collector_runs`)

| Job | Every | Source | Writes |
|---|---|---|---|
| discovery | 15 min | Jupiter top-trending / top-traded / top-organic-score × 5m/1h/6h/24h (12 req) | `tokens`, `token_discoveries` (list + rank = inclusion reason) |
| snapshots_active | 5 min | Jupiter Tokens V2 search, 100 mints/req | `market_snapshots`, `market_window_stats` |
| snapshots_dormant | 60 min | same, dormant tokens | same |
| pools | 5 min | DEX Screener: primary pools by address (30/req); tokens without a known pool via the token endpoint | `pools`, `pool_snapshots` (reserves) |
| pool_discovery | 60 min | DEX Screener token-pairs, one request per active token | `pools`, `pool_snapshots`, `tokens.primary_pool` = deepest pool (resets candle backfill when it changes) |
| candles | 15 min | GeckoTerminal OHLCV (8 candles), 24 tokens: eligible first, then by liquidity (≥ $100K) | `candles_5m` |
| candle_backfill | 15 min | GeckoTerminal, 14 days for 2 new tokens/run (eligible first) | `candles_5m` |
| security | 60 min | Helius `getMultipleAccounts` (mint), refresh 24h | `token_security` |
| holders | 60 min | Helius largest accounts + owner resolution, 25 tokens/run, refresh 24h | `holder_snapshots` |
| quotes | 15 min | Jupiter quote, sell $1,000 → USDC, tokens with liq ≥ $150K and 24h vol ≥ $500K | `swap_quotes` |
| tracking | 60 min | — | active ↔ dormant transitions |

### Data rules

- Every provider response is stored verbatim in `raw_observations`; normalized rows carry `raw_observation_id` and share the response's `fetched_at` as `observed_at`.
- Universe tiers: **active** (polled every 5 min), **dormant** (below $25K liquidity / $25K 24h volume for 24h; polled hourly), **excluded** (non-meme asset class). Nothing is deleted, so tokens that later fail stay in the dataset (no survivorship bias).
- Classifier v1 (`autolp/universe.py`) works by exclusion because Jupiter's `meme` tag is sparse: excluded tags (stocks, RWA, stable, LST, DeFi, infra, major), excluded mints, manual list; otherwise `meme` (tag or meme launchpad) or `meme_candidate`.
- Security: active mint/freeze authority and risky Token-2022 extensions (permanent delegate, transfer hook, transfer fee > 0, pausable, non-transferable, default-frozen, mint close authority) → `risky`. Missing/unparseable account → `unknown`, never `ok`.
- Holders (method v1): top-20 accounts from RPC; owners resolved; owners that are program-controlled accounts (AMM vaults, bonding curves, lockers) are excluded as `program`, incinerator as `burn` (also removed from supply). Unresolvable owners are counted, so missing data never lowers concentration.
- Candles: a candle is `is_complete` once its 5-minute bucket has closed at fetch time; complete candles are never overwritten.
- Quotes: `slippage_pct = (notional − USDC out) / notional`, against the latest Jupiter price; failures (no route, disabled pool) are stored with the error.
- Staleness: `market_snapshots.source_updated_at` records the provider's own update time; lag is reported by monitoring.

### Monitoring

- `GET /monitoring/jobs?hours=24` — per-job runs, failures, coverage, max gap, status (ok / degraded / stale / never_ran).
- `GET /monitoring/freshness` — universe size per tier, stale active tokens, provider lag.
- Collector self-check every 15 min logs `collection_job_unhealthy` / `collection_data_stale` warnings.
- `make report HOURS=72` — acceptance report. PASS requires every job to have run, 5-minute jobs ≥ 95% coverage with no gap > 30 min, median per-token coverage ≥ 95%, ≤ 5% stale active tokens.

### Request budget (observed in the first live run)

Jupiter ≈ 12 + ~4 + ~50 requests per 15 min (discovery, snapshots, quotes) — well under 1 req/s. DEX Screener ≈ 10 req / 5 min. GeckoTerminal ≈ 1.5 req/min average, 6/min peak: the advertised 30/min free limit is not sustainable in practice (constant 429s above ~4/min on 2026-10-10), so 5-minute candles cover only the 30 most liquid active tokens. Phase 3 should point this budget at watchlist candidates. Helius ≈ 80 calls/hour ≈ 60K/month of the 1M free credits.

## 7. Data explorer (built before Phase 3)

Read-only Next.js app (`frontend/`, `localhost:3000`) over a read-only API (`/api/*`, `backend/src/autolp/api/explorer.py`). The browser only talks to the Next.js server, which proxies `/api/*` to the internal API.

| Page | Shows |
|---|---|
| Universe `/` | Every token with its latest snapshot: price, market cap, liquidity, 24h volume, 1h/24h change, trades, unique traders, buy share, top-10 concentration (adjusted), $1K sell slippage, security status, class. Filters, sorting, paging, search by symbol/name/mint. |
| Token `/tokens/<mint>` | Price (5-min candles from the primary pool, or snapshot prices when no candles), liquidity, buy vs sell volume, unique traders; activity windows (5m/1h/6h/24h); pools with reserves; on-chain security; largest holders with exclusions; sell quotes; discovery lists and tracking history. |
| Discovery `/discovery` | Every tracking-status change (`tracking_events`) with its reason. |
| Collection health `/health` | Job coverage, gaps, failures, data freshness, requests per provider, universe and row counts. |

Rules: values are shown exactly as stored; missing values render as "—" and unchecked security as "Unchecked", never as safe. Colors follow the validated reference palette (dataviz skill); status badges always pair color with a label.

`tracking_events` (migration 0003) records every active/dormant/excluded transition with its reason; existing tokens were seeded with a `backfill:` event.

## 8. Phase 5 — trading strategies

Three rule sets (momentum breakout, pullback, range), shared risk rules and a deterministic paper decision engine; specification and interpretations in `docs/STRATEGIES.md`. A `strategy_signals` collector job records every check for the eligible universe every 15 minutes (Strategies page); `make replay` replays the decision engine over stored data and prints a digest of the decisions.

## 9. Phase 6 — backtesting

`autolp.backtest` runs the strat-v1 decision engine over stored point-in-time data for our selection and six comparison universes: a fixed selection, the recorded watchlist, top 30 by market cap, top 30 by volume, random matched tokens, and early-alert entries. Each universe runs under a base and a stress execution scenario.

The window is split 45/15/30 into development, validation and a sealed holdout. A strategy-independent forward-return test measures selection uplift. Reports go to `backtest_runs`, are shown on the Backtests page and are produced by `make backtest`. The engine gained a pluggable execution model (`strategies/execution.py`): entry delay, cost multiplier, stop slippage and deterministic swap failures. Candle latency is modelled for signals only. The live job and the Phase 5 replay keep fill model v0. Full specification: `docs/BACKTESTING.md`.
