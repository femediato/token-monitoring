# Trading strategies — strat-v1

Phase 5 of the master plan: three independent entry/exit rule sets with shared risk rules, producing **paper decisions only**. Nothing here places an order, signs a transaction or holds a key (`live_trading_enabled` is frozen to false). Code: `backend/src/autolp/strategies/` — every threshold is in `params.py`; changing one is a strategy change (bump `STRATEGY_VERSION`, record it here).

The selection model decides *what* may be traded (eligibility, `docs/SCORING.md`); these rules decide *when* to enter and exit and *how much*.

## 1. Inputs (point-in-time)

| Input | Source | Freshness |
|---|---|---|
| 5-minute OHLC candles of the token's primary pool | GeckoTerminal (`candles_5m`, completed candles only) | last candle closed ≤ 25 min before the decision |
| Eligibility, Buying-pressure score, features (relative volume, 1h buy share, liquidity, risk flags, organic label, sell cost) | latest `score_evaluations` row (model v1.1) at or before the decision | ≤ 20 min old |
| "One hour earlier" (for strategy B) | the evaluation 45–75 min before the latest one | — |
| Execution cost | latest paired $1,000 sell quote (`sell_cost_pct`) | ≤ 45 min old, ≤ 2% |

A candle is used only once it has closed (`bucket + 5 min ≤ t`); an evaluation only once it was made. The same loader (`data.py`) serves the live job and the replay, so a replay of a period reproduces what the live job saw — given the same stored rows.

**Universe:** tokens eligible in their latest fresh evaluation. Ineligible tokens are not checked; an open position is still managed after its token becomes ineligible.

## 2. Checks shared by every strategy

`fresh_candles`, `fresh_evaluation`, `eligible`, `cost_estimate` (a fresh sell quote with cost ≤ 2%). Each check is stored as passed / failed / missing; a missing input never counts as a pass, so stale data means no trade.

## 3. Strategy A — Momentum breakout

| Rule (plan) | Implementation |
|---|---|
| Coin passes eligibility screening | shared `eligible` |
| Price closes above the previous completed 1-hour high | the last completed 5-minute candle closes above the high of the previous **clock** hour (≥ 10 of its 12 candles present), and the candle before it closed at or below that high — a fresh cross, so one breakout gives one signal |
| Relative volume ≥ 1.5× baseline | `relvol_ratio` = V1h / (V24h / 24) ≥ 1.5 |
| Buying-pressure score ≥ 60 | Buying category score (model v1.1) ≥ 60 |
| No active risk warnings | no security risk flags **and** Jupiter organic label present and not `low` |
| Exit | +15% target, −7% stop, 4 h |

## 4. Strategy B — Pullback entry

| Rule (plan) | Implementation |
|---|---|
| EMA20 above EMA50 | EMAs of 5-minute closes, seeded with the simple mean; needs ≥ 100 candles |
| Retraced 5–10% from a recent local high | high = highest high of the 48 candles (4 h) before the signal candle; pullback low = lowest low since that high, including the signal candle; (high − low) / high in [5%, 10%] and the close still below the high |
| Buying pressure improves over the preceding hour | 1h buy-volume share now > the share in the evaluation ~1 h earlier |
| A completed five-minute candle confirms recovery | the signal candle is bullish and closes above the previous candle's high |
| Exit | +15% target, −7% stop, 8 h |

## 5. Strategy C — Range trading

| Rule (plan) | Implementation |
|---|---|
| Range | over the last 72 candles (6 h), excluding the 3 most recent: resistance R = highest high, support S = lowest low; width (R − S)/S between 4% and 30% |
| ≥ 2 prior support and ≥ 2 resistance tests | swing lows (lowest of ±2 candles) within 15% of the range width of S, at least 30 min apart; same for swing highs near R |
| Price returns near support | lowest low of the 3 recent candles within the touch band of S and above the stop |
| A completed candle confirms reversal | the signal candle is bullish and closes above the previous close |
| Reward/risk ≥ 2:1 after expected costs | target = R − touch band, stop = S − 1.5%; reward = (target − entry)/entry − round-trip cost, risk = (entry − stop)/entry + round-trip cost |
| Exits | target near resistance, stop below support, max hold **12 h — provisional**, to be calibrated in Phase 6 (the plan leaves it open) |

Round-trip cost = 2 × the one-way `sell_cost_pct` of the paired $1,000 quote (the buy side is assumed to cost the same; Phase 4 entry quotes will test that).

## 6. Shared risk rules

| Rule | Implementation |
|---|---|
| No leverage | size ≤ cash |
| Max planned loss per trade 0.5% of equity | size = (0.5% × equity − 2 fees) / (stop distance + round-trip cost) |
| ≤ 3 simultaneous positions | open + pending orders |
| ≤ 1.5% combined planned open risk | sum of planned losses at the stops |
| Stop new positions after a 3% daily loss | marked equity vs. equity at the first step of the UTC day |
| No trade without a fresh cost estimate | shared `cost_estimate` check |
| Reject if liquidity is insufficient | size capped at the quoted size ($1,000 — costs beyond it are unknown) and at 1% of liquidity; below $25 → rejected |
| (added) one position per token | across strategies |

Paper equity starts at $10,000; fees are $0.05 per swap. Setups in the same step are considered in strategy order (A, B, C) and then by mint — a fixed, documented tie-break so decisions are deterministic.

## 7. Exits and fills (fill model v0)

- Entry: an accepted order fills at the **open of the next candle**, plus the one-way cost and a fee; no fill within 10 min → cancelled.
- Exits are checked on every completed candle. The order of high and low inside a candle is unknown, so **the stop is assumed to trade first** when both levels are inside one candle. A candle opening beyond a level fills at the open (gap). The time exit fills at the close of the candle that reaches the holding limit.
- Exit proceeds = price × (1 − one-way cost) − fee.

The Phase 6 backtest runs the same engine with fill model v1 (`strategies/execution.py`, `docs/BACKTESTING.md` section 4): entry delay, a cost multiplier, stop slippage and failed swaps. The live job and `make replay` keep v0.

## 8. Live job and replay

- `strategy_signals` job (collector, every 15 min at +10 min, after the candle job): stores every check of every strategy for every eligible token (`strategy_signals` table) — no portfolio, no orders. Explorer: **Strategies** page.
- The candle job now fetches eligible tokens first (24 tokens every 15 min, was 30 every 30 min), so the strategy universe has candles about 10 minutes old.
- `make replay HOURS=24 [END=2026-10-12T00:00]` runs the decision engine over stored data and prints a summary and a SHA-256 digest of every decision. Same stored data + same `END` → same digest (Phase 5 acceptance). It is a decision check, not a backtest.

## 9. Limitations

- Candles cover only tokens in the candle job; a token without recent candles is checked but cannot set up (`fresh_candles` fails).
- Live candles arrive ~10 min after they close (GeckoTerminal free tier, about 4 requests a minute). Phase 7 has to decide whether that delay is acceptable or a faster source is needed.
- Replays use each token's current primary pool, as the scoring replays do.
- History for replays starts when model v1.1 evaluations began (2026-10-10). The plan's 90-day backtest window needs 90 days of stored evaluations, or a Phase 6 recomputation of evaluations from the stored snapshots (available since 2026-10-09).
