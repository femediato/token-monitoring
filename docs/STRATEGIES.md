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

The Phase 6 backtest runs the same engine with fill model v1 (`strategies/execution.py`, `docs/BACKTESTING.md` section 4): entry delay, a cost multiplier, stop slippage and failed swaps. The live signal job and `make replay` keep v0. Paper trading (Phase 7, `docs/PAPER_TRADING.md`) fills with executable Jupiter quotes instead.

## 8. Live job and replay

- `strategy_signals` job (collector, every 15 min at +10 min, after the candle job): stores every check of every strategy for every eligible token (`strategy_signals` table) — no portfolio, no orders. Explorer: **Strategies** page.
- The candle job now fetches eligible tokens first (24 tokens every 15 min, was 30 every 30 min), so the strategy universe has candles about 10 minutes old.
- `make replay HOURS=24 [END=2026-10-12T00:00]` runs the decision engine over stored data and prints a summary and a SHA-256 digest of every decision. Same stored data + same `END` → same digest (Phase 5 acceptance). It is a decision check, not a backtest.

## 9. Limitations

- Candles cover only tokens in the candle job; a token without recent candles is checked but cannot set up (`fresh_candles` fails).
- Live candles arrive ~10 min after they close (GeckoTerminal free tier, about 4 requests a minute). Paper trading (Phase 7) runs with this delay; its fills record how far price had moved from the signal candle's close, which shows what the delay costs.
- Replays use each token's current primary pool, as the scoring replays do.
- History for replays starts when model v1.1 evaluations began (2026-10-10). The plan's 90-day backtest window needs 90 days of stored evaluations, or a Phase 6 recomputation of evaluations from the stored snapshots (available since 2026-10-09).

## 10. Research options (off by default)

Added for the strategy research sweeps (`docs/BACKTESTING.md`, section 11). Every option is off by default; with the defaults the decisions are exactly strat-v1's (same replay digest), and no check is added to a signal. A variant that turns options on is identified by its overrides, and becomes a strategy version only when it is adopted.

| Option | Parameters | Effect |
|---|---|---|
| ATR exits | `<strategy>.exit.mode = "atr"`, `atr_mult`, `reward_risk`, `min_stop_pct`, `max_stop_pct` | stop = entry − `atr_mult` × ATR (clamped to [min, max] %); target set so the net reward is `reward_risk` × the net risk, with the round-trip cost on both sides. ATR = mean true range of the last `atr_candles` (48) 5-minute candles. |
| Range ATR stop | `range.atr_stop_mult` | the range stop is the wider of support − 1.5% and entry − k × ATR; the 2:1 reward/risk check uses it |
| Breakeven | `<strategy>.manage.breakeven_at_r` | once the high since entry is this many R above the entry (R = entry − initial stop), the stop moves to the entry plus the round-trip cost |
| Trailing stop | `<strategy>.manage.trail_atr`, `trail_after_r` | once the high is `trail_after_r` R up, the stop trails `trail_atr` × ATR below the highest high. Raised only after a candle completes, so it applies from the next candle; an exit on a raised stop is `trail` and pays stop slippage |
| Market regime | `filters.min_breadth_pct`, `min_breadth_tokens` | new check `regime`: the share of eligible tokens (latest fresh evaluation) with a positive `ret_4h_pct` must be at least this. Fewer than `min_breadth_tokens` tokens → missing → no trade |
| Token trend | `filters.min_ret_4h_pct`, `max_ret_4h_pct` | new check `token_trend`: the token's own 4h return within the bounds; missing → no trade |
| Token 1h trend | `filters.min_ret_1h_pct`, `max_ret_1h_pct` | new check `token_trend_1h`: the same for the 1h return, e.g. skip tokens that just pumped; missing → no trade |
| Entry drift guard | `risk.max_entry_drift_r` | an entry whose fill price is more than this × R above the signal close is cancelled (`entry_drift`) |
| Slippage-aware sizing | `risk.expected_stop_slippage_pct` | sizing budgets for the stop overshoot: loss per $ = stop distance + round-trip cost + this |
| Breaker | `risk.max_consecutive_losses`, `drawdown_pause_pct`, `pause_hours` | no new entries for `pause_hours` after that many losses in a row (`loss_streak`) or after equity falls that far below its peak (`drawdown_pause`) |
| Random entries | `enabled = ["random"]`, `random.prob_pct`, `random.exit`, `random.manage` | research baseline only: enters on a deterministic pseudo-random `prob_pct` of candles (hash of seed, token and candle), with the shared checks, the filters and the given exits |

## 11. Strategy D — Reversal (research only)

"Buy the dip": buy a token after a sharp fall from its recent high, betting on a short bounce. Research only: it is not in `enabled`, so neither the live job nor `main` trades it. It is tested in the sweeps (round 2, `docs/RESEARCH.md`) and would need its own approval before any paper account trades it.

| Check | Rule (parameters in `strategies/params.py`, `ReversalParams`) |
|---|---|
| Shared checks | section 2, plus any filters (section 10) |
| `drop` | the last close is `min_drop_pct` (15) to `max_drop_pct` (40) % below the highest high of the last `lookback_candles` (48 = 4 hours); fewer candles → missing |
| `oversold` (optional) | `max_rsi` > 0: Wilder RSI(`rsi_candles` = 14) of the 5-minute closes ≤ `max_rsi` |
| `recovery_candle` (optional) | `confirm`: the last candle is bullish and closes above the previous candle's high |
| `buying` (optional) | `min_buy_share` > 0: the evaluation's 1h buy share ≥ this |
| `no_risk_warnings` | as strategy A |

Exits: `reversal.exit`, default +6% target, −6% stop, 2-hour maximum hold (or ATR exits, section 10), and `reversal.manage` for breakeven/trailing.

Known approaches this draws on: short-term oversold bounces (RSI-2 / RSI-14 thresholds, Connors-style), Bollinger-band or z-score reversion to a moving average, and capitulation reversals (a sharp fall on high volume, then selling exhaustion and a reversal candle). The common failure mode in meme coins is the falling knife: a token that falls because it is dying keeps falling. Hence the cap on the fall, the safety checks, and short holds.

