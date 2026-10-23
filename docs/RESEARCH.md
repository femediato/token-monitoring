# Strategy research log

Findings from the research sweeps (`docs/BACKTESTING.md`, section 11). Every entry gives:
- the grid, the window and the digests, so it can be reproduced;
- what was concluded, and what was not.

Sweep reports are written to `reports/sweeps/` (not committed).

## Strategy scorecard (as of 2026-10-23)

What we know about each strategy, from the paper account `main` (live quotes, 11–23 Oct, $10,000 start) and from the sweeps (round 2: 13 days, paper-calibrated costs). **No strategy has made money in any test so far.**

| Strategy | Paper trades | Paper P/L | Win rate | Profit factor | Sweep, development (round 2) | Status |
|---|---|---|---|---|---|---|
| A — Momentum breakout | 14 | −$188 | 43% | 0.43 | −7.4% (38 trades); no better than random entries | losing, least bad; rarely triggers |
| B — Pullback | 39 | −$844 | 21% | 0.35 | −11.9% (56 trades) | losing |
| C — Range | 63 | −$1,573 | 16% | 0.31 | −21.9% (61 trades) | **worst; candidate to switch off** |
| D — Reversal (buy the dip) | — | — | — | — | −8% to −27% across 61 variants; worse than random entries | **failed in research; not enabled** |
| strat-v1 (A+B+C) | 116 | −$2,605 (equity −26.1%) | 21% | 0.33 | −22.4% (77 trades) | the `main` account, kept as control |

**A — Momentum breakout** (buy a close above the previous hour's high on volume and buying pressure).
- It never reached its +15% target, in paper (0 of 14) or in round 1 (0 of 16 signals). Most trades end at the 4-hour time limit.
- It buys exactly what the forward study below shows mean-reverting: tokens that just pumped. After a 1h rise of more than 10%, the next 4 hours averaged −7.0%, and not one of 14 days was positive.
- Skipping tokens that rose more than 3% in the last hour helped momentum + pullback steadily (−12.3% → −6.5% development, +1.3 pp expectancy per trade). Validation was still −4.2%.
- Lesson: buying strength in this market buys the top.

**B — Pullback** (buy a 5–10% dip within an EMA uptrend, on improving buying).
- Two-thirds of its paper trades hit the −7% stop. In round 1 the median best price after entry was only +1.7%, so the bounce rarely developed.
- After 15 Oct it was nearly flat in paper (−$63 on 22 trades). That is too few trades to call it fixed.
- Lesson: an "uptrend" on 5-minute EMAs is too short-lived to rely on.

**C — Range** (buy at range support with the stop 1.5% below it, needing 2:1 reward-to-risk).
- 81% of its paper trades hit the stop, and it lost −$1,178 of the −$1,311 lost since 15 Oct.
- The stop sits inside normal 5-minute noise for these tokens; the median stop distance was 3.2%, and half of the round-1 stops came within an hour.
- Neither wider ATR-based stops nor trailing fixed it (round 1).
- Lesson: support levels in meme coins don't hold often enough for a tight stop.

**D — Reversal / buy the dip** (round 2, below). Buying after a 10–40% fall from the 4-hour high lost in every one of 61 variants. These covered fall depth, a confirmation candle, RSI oversold, buying share, fixed and ATR exits, holds of 1–4h and two universes. Most did worse than random entries with the same exits. Fallen tokens keep falling over the following hours.

**What the losses have in common:**
- The market fell for most of the window. Across all ~1,250 tracked tokens, the next-24h return was positive on only 2 of 13 days.
- A long-only strategy that holds for hours is fighting that drift.
- Stops fill about 1.7% past their level, and fills are about 0.7% worse than the candle price, so every stop-out costs more than planned.

## Round 1: 2026-10-15

- **Grid:** `research/grids/round1.json`, 108 variants × 3 scenarios (base, paper, stress); `make sweep GRID=round1 END=2026-10-15T17:00`.
- **Window:** 2026-10-10 18:04 → 2026-10-15 17:00 (4.96 days).
- **Splits:** development until 10-13 05:30, validation until 10-14 01:20; holdout sealed.
- **Digests:** input `c7164ef5fa529f5b…`, sweep `8a0129e8cfcc02a3…`.
- **Code:** bt-v2, sweep-v1, strat-v1 with research options.
- **Context:** defined after the first paper results (−12.9% in 4 days), so this window is in-sample for every idea tested.

### Results (development, paper-calibrated costs)

**No variant made money robustly.**
- Of 104 variants with at least 10 trades, one had a positive development return: token 4h return between 0 and +5%, +1.2% on 16 trades.
- That variant lost on validation (−2.6%), and its neighbours lost up to −4.7%. That is noise, not an edge.
- No variant was positive on both development and validation.
- Not trading (cash) beat every variant.

**Median development return by family:**

| Family | Median | Best | Random entries, same exits (median) |
|---|---|---|---|
| Momentum, fixed exits | −3.8% | −2.7% | −6.1% (4h) |
| Momentum, ATR exits | −3.9% | −2.9% | −6.9% (4h) |
| Pullback, fixed exits | −3.9% | −2.1% | −6.4% (8h) |
| Pullback, ATR exits | −3.8% | −1.6% | −7.5% (8h) |
| Range (ATR stop, R/R) | −7.3% | — | — |
| strat-v1 + market regime ≥ 50% | −2.9% | −0.5% (60%, 7 trades) | — |
| strat-v1 + entry-drift guard | −8.9% | −8.8% | — |
| strat-v1 (current, all three) | −9.5% | | |

**What the sweep says:**
- **Entry timing of momentum and pullback is better than random but not good enough.**
  - Both families lost about 2–3 points less than random entries under the same exits.
  - Only 15 of 40 strategy variants beat their own random twin on expectancy per trade, so even this is weak.
- **Range trading is the main source of the backtest loss:** v1 all three −9.5%, range alone −9.4%, momentum alone −3.8%, pullback alone −2.1%.
  - Neither wider ATR stops nor trailing fixed it.
  - The live paper account lost most on pullback instead (−$781 against −$394 for range). That disagreement is itself a sign of how noisy a few days are.
- **Exits are not the fix.** Fixed vs. ATR stops, targets from 6% to 15%, trailing, breakeven: every family stays negative across its grid.
- **Trading less loses less.** The regime filter (≥ 50–60% of eligible tokens up over 4h) and the breaker cut trades and losses, but did not turn them into gains.
- **The selection itself picks falling tokens in this window.** The strategy-independent selection test of the same data (bt-v2 report) measured mean forward returns:

  | Horizon | Selected tokens | Matched random tokens |
  |---|---|---|
  | 4h | −1.3% | −0.0% |
  | 24h | −7.5% | −1.4% |

  The top-30 by score, which favours recent buying and volume, mean-reverted. Overlapping horizons make the effective sample much smaller than the 1,000+ observations, but this is the largest effect in the data.

### Not concluded

- **Nothing about the long run.** Five days in one falling market is one regime. The Phase 9 gates need more than one regime and at least 30 days.
- **Nothing was adopted, and no research paper account was started.** None of the variants passed development, validation and stress together.

### Next

- **Rerun the sweeps as data grows,** e.g. weekly. The splits are proportional, so data seen now becomes development data later.
- **Round 2 should test the selection, not the exits:**
  - universes that exclude tokens already extended over 4h/24h;
  - lower-ranked eligible tokens;
  - the matched-random universe as a trading universe.
- **Research paper accounts** (`docs/PAPER_TRADING.md`, section 7) are ready for any variant that passes. Choosing one is a decision for the owner.

## Forward-return study: 2026-10-23

- **Script:** `research/scripts/forward_returns.py` (it documents its export command).
- **Window:** 2026-10-10 18:00 → 2026-10-23 20:00 (13 days), hourly evaluations.
- **Population:** 73,841 hourly observations of baseline candidates (no critical failure, market cap ≥ $300k, liquidity ≥ $50k), including 6,096 eligible ones.
- **Strategy-independent:** it asks what happens after a token rises or falls.

**Mean forward return of all candidates:**

| Horizon | Mean | Median | Positive | Positive days |
|---|---|---|---|---|
| 4h | −0.38% | −0.16% | 43% | 4 of 14 |
| 24h | −2.37% | −1.56% | 39% | 2 of 13 |

The whole market drifted down. Eligible tokens did slightly worse (24h: −3.2% vs −2.3%). Ineligible tokens also have 18% missing later prices, mostly dead tokens, which flatters them. So over 13 days the selection is about as bad as the market, not much worse. The 5-day result (−7.5% vs −1.4%) was mostly one bad stretch.

**By the token's previous move** (candidates, mean next return; days = positive days):

| Previous move | Next 1h | Next 4h | Next 24h |
|---|---|---|---|
| 4h return ≥ +15% | | −4.6% (0 of 14 days) | −11.9% (0 of 13) |
| 1h return > +10% | −3.1% (0 of 14) | −7.0% (0 of 14) | −13.9% (0 of 13) |
| 4h return −15% to −30% | | +0.5% (8 of 14) | −7.1% (2 of 13) |
| 4h return < −30% | | −3.3% | −16.7% |
| 4h < −15% and still falling in the last hour | +0.9% (11 of 14) | +0.3% | −8.2% |
| 4h < −15% and the last hour up > 2% ("turning") | −1.2% | −3.9% | −17.1% |

**What it says:**
1. **Pumps reverse, consistently.** This is the strongest and most stable effect in the data: on no day did a token that pumped more than 10% in an hour go on to rise on average. Strategy A buys these.
2. **Dips bounce only briefly, and by less than the costs.** A token down 15–30% that is still falling gains about 0.9% in the next hour. That is less than a round trip costs (about 0.7% quoted, plus about 0.7% fill shortfall, plus stop slippage). After 24 hours it is down about 8%.
3. **Waiting for confirmation makes it worse.** A "turning up" hour after a fall is itself a small pump, and it reverses.
4. Because of overlapping observations the effective sample is much smaller than n, and this is one falling market.

## Round 2: 2026-10-23

- **Grid:** `research/grids/round2.json`, 104 variants × 3 scenarios; `make sweep GRID=round2 END=2026-10-23T20:00` (19 minutes on 3 workers).
- **Window:** 2026-10-10 18:04 → 2026-10-23 20:00 (13.1 days).
- **Splits:** development until 10-17 07:00, validation until 10-19 11:20; holdout sealed.
- **Digests:** input `11170ea8d8d35dac…`, sweep `f312ae4d6aaa34ba…`.
- **Code:** bt-v2, sweep-v2 (per-variant universes, `liquid` universe), strat-v1 with research options, strategy D (reversal) and the 1h token filter.
- **Context:** defined after 13 days of paper losses and the forward-return study, so this window is in-sample for every idea tested.

### Results (development, paper-calibrated costs)

**Again, no variant made money. Cash (no trades) beat all 103 others.** The best development results are all losses: −6.0% to −8.3%.

**Token universe.** Every row has the same strat-v1 rules (or the same random entries), only the tokens differ:

| Universe | strat-v1, dev | Random entries 15/7/4h, dev |
|---|---|---|
| `selection` (our eligible tokens) | −22.4% (77 trades) | −15.5% (109) |
| `liquid` (any liquid token with candles) | −20.8% (86) | −19.0% (98) |
| `volume` (top 30 by volume) | −17.7% (90) | −17.4% (108) |
| `mcap` (top 30 by market cap) | −10.7% (57) | −12.7% (82) |
| `random` (matched non-selected) | −2.0% (5, too few) | −6.0% (32) |

- Our selection is not clearly worse than other liquid tokens at the trading level.
- Larger tokens lost less: `mcap` lost about half as much.
- The `random` universe barely trades, because candles are collected for only 24 tokens and eligible ones come first. Its results say little.

**Strategies:**

| Variant | Dev return | Dev trades | Validation | Note |
|---|---|---|---|---|
| strat-v1 all three | −22.4% | 77 | −7.4% | |
| Range only | −21.9% | 61 | −4.6% | the bulk of v1's loss |
| Pullback only | −11.9% | 56 | −3.7% | |
| Momentum only | −7.4% | 38 | +0.1% (7 trades) | expectancy −3.3% per trade vs −2.3% for random entries |
| Momentum + pullback | −12.3% | 61 | −4.6% | |
| … skipping tokens up > 15% / 10% / 5% / 3% in the last hour | −10.8% / −9.5% / −8.2% / −6.5% | 65 / 60 / 54 / 47 | −4.5% / −4.9% / −5.0% / −4.2% | improves steadily as the filter tightens; still negative |
| strat-v1 + the same 1h filter | −21.1% to −22.4% | | | range dominates, filter barely matters |
| strat-v1 + 4h filter | −21.4% to −21.6% | | | no effect |

**Strategy D, reversal (61 variants):**
- Development return was −8.3% to −27.1%. The best, "deep falls ≥ 40%" (−8.3%, 32 trades) and "RSI ≤ 20" (−8.7%, 57 trades), have neighbours at −19% and −17%. That is noise.
- Against random entries with the same exits, 55 of 61 variants did worse per trade.
- No setting helped: a confirmation candle, RSI, buying share, ATR exits or holds of 1, 2 or 4h. On the `liquid` universe it was −23.5% to −26.1%.
- This matches the forward-return study: dips bounce by less than the costs, and keep falling over hours.

### Concluded

- **Range should not be traded in its current form.** It is the largest loser in paper and in both sweeps.
- **Don't buy right after a pump.** It is the most consistent effect in the data, and the 1h filter is the only option whose effect grew steadily with its setting. It is a candidate rule for any long strategy, but not enough by itself.
- **Buying the dip (D) doesn't work here.** It is not adopted and stays off.
- **The eligibility filter is not the main problem at the trading level.** The whole market fell. Larger caps fell less, which may be worth testing as a universe.

### Not concluded

- Whether anything works in a rising or sideways market: all 13 days were mostly one regime.
- Whether momentum + pullback with the 1h filter is better than random. Its expectancy is about −2.4% per trade, close to random entries (−2.3%).

### Next

- **Decision for the owner:** switch off Range in `main`? Or pause `main`, or keep it as the control? Changing `main` means a new strategy version.
- **Keep collecting.** Rerun round 2 weekly. A rising market in the data is what would make any long strategy testable.
- **Possible round 3:**
  - momentum + pullback with the 1h filter on the `mcap` universe;
  - a market regime gate (breadth) combined with the 1h filter;
  - shorter holds.
- **Short selling** is where "pumps reverse" would pay. It is out of scope for v1 (no live trading, spot only), so it is not tested.

