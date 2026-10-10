# Early opportunity detection — model early-v1

Goal: find coins whose demand is starting to rise **before** the price has already moved, and measure whether that works. Separate from the ranking model (`docs/SCORING.md`). Code: `backend/src/autolp/selection/early.py` (thresholds in `EarlyParams`), evaluation in `early_report.py`.

An alert is a research signal. It never places or suggests an order.

## Signals

Computed every 15 minutes (with the scoring run) for **every** active token from the same point-in-time features. Each signal is binary; a signal whose input is missing does not fire and is stored as `null`.

| Signal | Fires when | Input |
|---|---|---|
| Volume acceleration | 1h volume ≥ 1.5 × prior hourly baseline | baseline = (24h volume − 1h volume) / 23 (Jupiter) |
| Buyer growth | unique traders, last hour ≥ 1.3 × the hour before | Jupiter `numTraders` 1h, now vs. ~1h ago — **proxy**: wallet-level unique *buyers* are not available in free data |
| Buying pressure | buy-volume share, 1h ≥ 55% | Jupiter buy / (buy + sell) volume |
| Liquidity growth | price-neutral pool depth ≥ 1.10 × 6h ago | √(base × quote reserves) of the primary pool (DEX Screener); unchanged by price for constant-product pools |
| Price restraint | 1h price change < +8% | Jupiter 1h price change |
| Positive trend | price > EMA20 | EMA over our 5-minute price samples (≥ 20 samples) |
| Participation quality | Jupiter organic-score label is high or medium | **proxy** for "no strong evidence of artificial trading"; missing or low → not fired |

**Score** = 100 × fired / 7, so it only takes the values k/7.

| Score | State | Signals |
|---|---|---|
| ≥ 85 | High priority | 6–7 of 7 |
| 70–84 | Emerging | 5 of 7 |
| 50–69 | Monitor | 4 of 7 |
| < 50 | No signal | ≤ 3 |

Choices not fixed by the master plan: the liquidity window is 6 hours (10% growth within 1 hour is rare for real deposits); "buyer growth" and "participation quality" use the proxies above.

## Alerts

- Raised when an **eligible** token (all Phase 3 hard rules) reaches score ≥ 70, at most once per token per 6 hours. Ineligible tokens are scored and stored but not alerted, so a broader alert universe can be tested later from `early_scores` without new data.
- Stored with: signals fired, score, state, price, market cap, liquidity, and the **pre-alert price move** over 1h and 4h (to see whether alerts merely follow a move).
- **Entry check:** within minutes, an executable $1,000 USDC→token buy quote is taken together with a $10 reference quote; `entry_cost_pct` is the cost of size. No quote within 30 minutes → `entry_quote_missed`. *Entry available* = cost ≤ 2%.

## Measuring predictive value

- **Matched controls**, chosen at alert time: up to 3 eligible tokens with no early signal (score < 50) at the same evaluation and no alert in the last 6h, nearest by |Δlog10 market cap| + |Δlog10 liquidity|. Same timestamp = same market regime.
- **Outcomes** (`alert_observations`, for alerts and controls alike): returns at 1h, 4h, 12h, 24h from the 5-minute Jupiter price nearest each horizon (±10 min; otherwise recorded as missing, never interpolated), and 24h maximum favourable / adverse excursion.
- **Report** (`/api/early/report`, Early signals page): per horizon, alert and control mean / median / win rate, and **uplift** = alert return − mean of its own controls, with n and a 95% interval (mean ± 1.96·SE). Evidence of predictive value: positive uplift whose interval excludes zero, at a sample of roughly 30+ alerts, while median pre-alert moves stay small.

## Limitations

- Returns are measured from the 5-minute snapshot price at the alert, not from an executed fill; the entry quote reports what a fill would have cost.
- The control pool is small while only ~15–25 tokens are eligible, so some alerts will have fewer than 3 controls (or none — those don't count toward uplift).
- Proxies (traders for buyers, Jupiter organic label) are labelled as such; replacing them needs wallet-level trade data (Birdeye or on-chain parsing).
