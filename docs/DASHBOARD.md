# Dashboard (Phase 8, analytics-v1)

The dashboard is the Next.js app on `localhost:3000`. It is read-only: every figure
comes from stored observations through the FastAPI `/api` endpoints, and nothing on it
places an order. Data refreshes every minute.

The navigation lists the plan's five screens first, then the research and data pages
behind them (strategy checks, backtests, eligibility, universe, discovery, collection
health).

| Screen | Page | Shows | API |
|---|---|---|---|
| Top-30 watchlist | `/watchlist` | rank, token, price, market cap, liquidity, total and the five component scores, score change (Δ 1h), coverage, eligibility status; membership changes (7 days) | `/api/watchlist`, `/api/watchlist/events` |
| Early opportunities | `/early` | signal board ranked by score with the seven triggering signals, recent activity (1h return and volume), alerts (14 days) with their subsequent 1/4/12/24h performance, alerts vs. matched controls | `/api/early/*` |
| Trading signals | `/signals` | every paper signal: interactive 5-minute candle chart with the entry reference, take-profit and stop-loss over the holding period, buy and exit markers labelled with the strategy letter and result, signals not taken; table with the risk decision, the paper result and the ideal outcome | `/api/analytics/signals`, `/api/analytics/chart/{mint}` |
| Paper portfolio | `/paper` | virtual balance, open positions with unrealised P/L, portfolio exposure against the limits, closed trades, every decision, the full audit trail of each position | `/api/paper`, `/api/paper/positions/{id}` |
| Performance analytics | `/performance` | equity curve, drawdown, strategy comparison, signal accuracy, daily results, trading costs, paper fills vs. the backtest fill model | `/api/analytics/performance`, `/api/analytics/signals` |

The token detail page (`/tokens/{mint}`) uses the same annotated chart whenever the token
has strategy signals in the selected range.

## Signal accuracy (`autolp.analytics.signals`)

Every signal the paper engine recorded is followed on the completed 5-minute candles of
the token's primary pool (the strategies' own price source). Rejected signals are
included: they show what the risk rules passed up.

- **Entry:** the open of the first candle starting at or after the decision.
- **Exit:** the backtest's exit rules (`strategies.risk.check_exit`). The stop is assumed
  to trade first when a candle touches both levels, a candle opening beyond a level fills
  at its open, and the time exit falls at the holding limit.
- **Net result:** the quoted one-way cost is deducted on entry and on exit. There is no
  stop slippage, failed swap or fee, so this measures the signal before execution.
- **Status:**
  - `target`, `stop` and `time` are resolved outcomes;
  - `open` means the signal is still inside its holding period;
  - `waiting` means no candle after the decision has arrived yet;
  - `no_data` means the candles needed to resolve it are missing, e.g. after a change of
    primary pool.

  Missing data is never counted as a win or a loss.
- **Accuracy:** take-profit hits and positive results are computed over resolved signals
  only.
- **Execution gap:** for the signals that were traded, the paper return is set next to
  the ideal result of the same signal.

This is an after-the-fact report on purpose. It only uses candles after the decision, so
it can't leak into decisions.

## Performance (`autolp.analytics.performance`)

- **Strategy comparison:** trades, win rate, profit factor, expectancy, net P/L, P/L
  before costs, best and worst trade, average hold and exits, for each enabled strategy
  (also those without trades) and all strategies together.
- **Daily results:** realised P/L of the trades that closed each UTC day. The equity
  change compares the day's last mark with the previous day's, so it includes open
  positions.
- **Trading costs:** each trade's costs come from its two fills.
  - Entry cost is the amount paid minus quantity × the reference price.
  - Exit cost is quantity × the reference price minus the proceeds.
  - Both include the swap fees.
  - **Stop overshoot** is how far below its stop a stop exit's reference price was. That
    is a price move caught late by the 1-minute polling, reported separately from fees.

## Portfolio exposure (`paper.report.exposure`)

- **Marked value:** the last small sell quote of each open position.
- **Planned open risk:** set at entry, including pending orders, against
  `max_open_risk_pct` of equity. This is the same quantity the risk check limits.
- **Risk to stop now:** what the open positions would lose, before costs, if each exited
  exactly at its stop now.
- **Capital in positions over time:** taken from the equity marks.

## Charts

- **Library:** charts use lightweight-charts.
- **Colours:**
  - Up and down candles use the diverging blue/red pair.
  - Strategy identity uses the fixed categorical slots 1–3 (validated for colour-vision
    deficiency in light and dark mode), always together with the strategy letter.
  - Markers and labels use ink tokens, never series colours.
- **Levels:** dashed (target, stop) and dotted (entry reference) lines span each signal's
  holding period. The selected signal is drawn stronger, others fainter.
- **Hover:** a readout shows each candle's OHLC values. The table under each chart is
  the table view of the same data.
