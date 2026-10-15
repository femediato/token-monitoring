# Strategy research log

Findings from the research sweeps (`docs/BACKTESTING.md`, section 11). Every entry gives:
- the grid, the window and the digests, so it can be reproduced;
- what was concluded, and what was not.

Sweep reports are written to `reports/sweeps/` (not committed).

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
