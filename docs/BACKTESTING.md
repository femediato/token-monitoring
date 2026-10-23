# Backtesting — bt-v2

Phase 6 of the master plan: test whether the selection model (v1.1) and the trading rules (strat-v1) have an observable edge, against control groups, with realistic costs. Code: `backend/src/autolp/backtest/`; parameters in `backtest/params.py` (changing one is a backtest change: bump `BACKTEST_VERSION` and record it here). Execution assumptions are in `strategies/execution.py`.

A backtest is research on stored data. It places no orders and is not evidence of future profit.

## 1. Running it

```
make backtest                       # whole history, holdout sealed, stored in the DB
make backtest DAYS=90 UNSEAL=1      # last 90 days including the holdout
make backtest ARGS="--set strategy.range.max_hold_hours=6 --no-store"
make backtest ARGS="--out /tmp/bt"  # also write report.md/json, trades.csv, equity.csv
```

The report prints as Markdown and is stored in `backtest_runs`. The **Backtests** page shows stored runs: gates, the comparison table, equity and drawdown curves, the selection test, trades, assumptions and every parameter. The API is `/api/backtests` and `/api/backtests/{id}`.

`--set` overrides any parameter, for example `strategy.<…>` (strat-v1) or `backtest.<…>` (bt-v2). Overrides are stored with the run and shown on the page.

## 2. Window and splits

| | Plan | bt-v1, bt-v2 |
|---|---|---|
| Research window | 90 days with point-in-time data | default: from the first model v1.1 evaluation (2026-10-10 18:04 UTC) to now |
| Development | days 1–45 | first 50% of the window |
| Validation | days 46–60 | next 16.7% |
| Holdout | days 61–90, untouched | last 33.3%. Not simulated unless `--unseal-holdout` (`UNSEAL=1`) |

The split proportions are the plan's (45/15/30). On a shorter window they apply proportionally. One simulation runs through the splits in order, so positions carry across split boundaries.

Each trade counts in the split where it was entered. Equity metrics (return, drawdown, Sharpe/Sortino, exposure) use the marked equity curve inside the split.

**Point-in-time data starts on 2026-10-10.** There is no earlier selection history: no evaluations, snapshots, security checks or quotes. Backfilled candles do reach back to 2026-08-29, but only for tokens that were alive when they were backfilled. Using them alone would add survivorship bias, and there are no selection inputs for that period, so the backtest does not use them. A 90-day window exists from about **2027-01-08**. Until then every report carries an "insufficient data" notice, and the gates read "not enough data".

## 3. No look-ahead, no survivorship

- **Signals** see only:
  - candles that had closed and reached the signal job, modelled as `candle_latency_minutes` = 10 (the live GeckoTerminal delay);
  - evaluations made at or before the decision.
- **Fills, exits and marks** use the price path itself, with no latency: the 5-minute candles. A fill is the open of a candle that starts after the decision, so it is never known at decision time.
- **Universes** are rebuilt at every evaluation from `score_evaluations` rows made at that time. Those rows include every tracked token, including tokens that later went dormant, failed or were excluded. Nothing is filtered on what happened afterwards.
- **Forward returns** for the selection test stop at the end of each split. The development split never sees validation prices.

## 4. Execution model (fill model v1)

| | base | paper (bt-v2) | stress |
|---|---|---|---|
| Entry | open of the first candle after the decision | same, plus a 0.7% worse fill | open of the first candle ≥ 5 min after the decision |
| One-way cost | quoted `sell_cost_pct` × 1.0 | × 1.0 | × 1.5 (the Phase 9 cost gate) |
| Non-stop exits | at the level or the open | 0.7% worse | at the level or the open |
| Stop, trailing stop, stop gap, no-data exit | fill 1% below the trigger | 1.7% below | 5% below |
| Failed swaps | 2% | 0% | 5% |

- **Failed swaps.** A failed entry is cancelled. A failed exit is retried at the next candle's open, whatever the price then; the reason is recorded as `…_retry`. Failures are deterministic: a hash of the seed and the order's identity. A rerun gives the same failures.
- **Within a candle**, the stop is assumed to trade first. A candle that opens beyond a level fills at the open.
- **Fees** are $0.05 per swap.
- **Source of the assumptions.** `base` and `stress` are assumptions made before any paper fill existed. `paper` (added in bt-v2) is calibrated on the first 43 paper trades (11–15 Oct 2026):
  - realised cost ≈ the quoted cost (total realised ÷ total quoted ≈ 1.0);
  - fills 0.6–0.8% worse than the candle price the model uses (price moved before the quote);
  - stops filled on average 1.7% below the trigger (1-minute polling);
  - no failed swap.
- **bt-v2 also changes the failure seed** (`seed` = the version), so `base` and `stress` runs differ from bt-v1 runs over the same data.
- **Live job and replay unchanged.** The live job and the Phase 5 replay still use fill model v0: no delay, quoted cost, stops at the trigger, no failures.

## 5. Universes compared

The strat-v1 rules and risk limits are identical for every universe. A token's `eligible` check reads as "member of this universe at this time and not failing a critical safety check". Everything else is unchanged: fresh candles, fresh evaluation, a cost quote ≤ 2%, and each rule's conditions.

| Universe | Plan comparison | Definition |
|---|---|---|
| `selection` | our top-30 selection, dynamically rebalanced | top 30 eligible tokens by total score at every 15-minute evaluation. This is the live strategy universe. |
| `fixed` | fixed watchlist | `selection` as it stood at the window start, never rebalanced; critical failures still block |
| `watchlist` | (our recorded top-30 watchlist) | membership replayed from `watchlist_events`; strict entry rules, so usually very small |
| `mcap` | top 30 by market cap | at every hourly evaluation, among tokens with no critical safety failure and ≥ $300k market cap and ≥ $50k liquidity |
| `volume` | top 30 by trading volume | as `mcap`, ranked by 24h volume |
| `random` | random eligible tokens matched by size and liquidity | see note 1 below |
| `early` | early detection enabled vs. disabled | `selection` plus a backtest-only entry on every early alert (Phase 4); see note 2 below |

**Notes**

1. **`random`.** Hourly, each selected token is replaced by one token drawn at random from the 5 nearest non-selected candidates. Nearness is |Δlog₁₀ market cap| + |Δlog₁₀ liquidity|. The draw uses a fixed seed, so it is reproducible.
   - Why not eligible tokens: the plan says "eligible", but only about 20 tokens are eligible and all of them are already in `selection`. The controls are therefore matched non-selected tokens instead.
2. **`early`.** An alert raised in the 5 minutes before a decision triggers an entry, subject to the shared checks; exits are +15% / −7% / 4 h, as strategy A.
   - Why an entry rule: alerts are raised only for eligible tokens, which are already in `selection`. Adding alerted tokens to the universe would therefore change nothing.
   - `selection` is "early detection disabled".

**Baselines need candles.** A universe whose members lack fresh candles cannot trade. The report shows candle coverage per run. Live candles are fetched eligible-first (24 tokens every 15 minutes), so baselines currently have lower coverage, about 50–70% against 99% for `selection`. That handicaps them in the trading comparison. The selection test below does not depend on candles.

## 6. Selection test (strategy-independent)

At every whole hour, the equal-weight return of each universe's members over the next 1, 4 and 24 hours. Prices come from the evaluations, which exist for every evaluated token, alive or not.

- **Missing:** a member with no fresh price at the start or the end counts as missing and is reported.
- **Net:** subtracts the quoted round-trip cost (2 × `sell_cost_pct`) where a quote existed.
- **Uplift:** our selection's mean minus the baseline's.

This test measures the value of the selection itself, with no strategy rules involved. Strategies trade rarely, so for a long time it will be the more statistically useful comparison.

## 7. Metrics

| Metric | Definition |
|---|---|
| Net return | marked equity at the end of the split ÷ equity at its start − 1 |
| Win rate | share of trades with P/L > 0 |
| Profit factor | gross profits ÷ gross losses (blank with no losing trade) |
| Maximum drawdown | largest fall from a running peak of 5-minute marked equity |
| Expectancy | mean P/L per trade, in $ and % |
| Sharpe / Sortino | annualised (√365) from daily equity returns, risk-free rate 0. Not shown with fewer than 14 daily returns. |
| Slippage / costs | per trade: (size − qty × entry price) + (qty × exit trigger − proceeds), as % of size. This is entry cost and fee, exit cost and fee, and stop slippage. |
| Exposure | mean marked value of open positions ÷ equity |
| Selection uplift | section 6, plus each universe's trading results side by side |

## 8. Gates (Phase 9, indicative)

The report evaluates the `selection` universe against three gates:

- out-of-sample profit factor > 1.3 (validation, plus the holdout when unsealed);
- maximum drawdown < 15%;
- out-of-sample net return > 0 in the stress scenario (1.5× costs).

Each gate reads "not enough data" until there are at least 30 out-of-sample trades. The gates are research minimums, not proof of future profitability.

## 9. Reproducibility

Every run stores:

- the full strategy and backtest parameters and any overrides;
- the window and splits;
- an **input digest**: SHA-256 of every evaluation, candle, universe membership and alert loaded;
- a **result digest**: window, parameters and the decision digest of every run.

The same window over the same stored data gives the same two digests. Stored data for a past window can still change, for example when candles are backfilled for a newly tracked token. When it does, the input digest changes and shows it.

## 10. Limitations

- **Data length.** About a day of point-in-time data at release. No conclusion about an edge is possible until weeks of data exist.
- **Range max hold.** The 12-hour maximum hold for strategy C is still provisional. Calibrate it on the development split only, with `--set strategy.range.max_hold_hours=…`, once there are enough range trades, then confirm on validation.
- **Primary pool.** Candles come from each token's current primary pool, as in the strategy replay.
- **Liquidity.** Liquidity for sizing comes from the evaluation (up to 20 minutes old). The cost estimate is valid only up to the $1,000 quoted size, which also caps every position.

## 11. Strategy research sweeps (sweep-v2)

```
make sweep GRID=round1 [END=2026-10-15T17:00]   # runs on the host against autolp_dev
```

A sweep runs many variants of the strategies on the same stored data and ranks them. Code: `backtest/sweep.py`; grids: `research/grids/*.json` (versioned with the code); output: `reports/sweeps/<grid>-<end>/report.{md,json}` (not committed: reproducible from the grid, the code and the data).

- **Variants** are parameter overrides, with the same keys as `--set`. A group expands into the cartesian product of its axes. Variants cannot change data inputs (`strategy.data.*`, `backtest.*`), because the data is loaded once.
- **Every variant** runs on its universe (`selection` unless the grid says otherwise) under the `base`, `paper` and `stress` scenarios. sweep-v2 added per-variant universes, including the research-only `liquid` universe: every baseline candidate (no critical failure, ≥ $300k market cap, ≥ $50k liquidity) that has 5-minute candles in the window, eligible or not. It tests whether the eligibility filter helps. Stored backtests (`make backtest`) do not load it, so their inputs and digests are unchanged. Processes run in parallel; the simulation is pure, so serial and parallel runs give the same digests.
- **Research options** (`docs/STRATEGIES.md`, section 10) are off by default, so the strat-v1 decisions are unchanged (same replay digest).

**Multiple-testing discipline.** With a few days of data and a hundred variants, the best-looking variant is partly luck. So:

1. **Ranking:** variants are ranked on the **development** split only, in the `paper` scenario, by net return. Variants with fewer than `min_trades` trades are listed last.
2. **Validation** is shown alongside and never used for the ranking. The holdout stays sealed.
3. **Random baseline:** each strategy variant is compared with **random entries** under the same exits and checks (`random`, section 10 of STRATEGIES.md). The "vs random" column is the difference in expectancy per trade. A strategy that does not beat random entries has no entry timing edge, whatever its return.
4. **Robustness:** each grid point shows its **worst neighbour**, the worst development return one step away along one axis. A good result surrounded by bad neighbours is noise.
5. **Stress:** the development return at 1.5× costs is shown.
6. **No adoption from a backtest alone:** a shortlist goes to parallel paper accounts (`docs/PAPER_TRADING.md`), which collect data the sweep never saw.

Round 1 (`research/grids/round1.json`) was defined after the first paper results (11–15 Oct). That window is in-sample for every idea in it.
