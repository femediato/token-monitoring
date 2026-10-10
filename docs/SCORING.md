# Selection model v1.1

Implements *Solana Meme Coin Screener — Strategy v1.0* (2026-10-09) with the changes listed in section 6, approved on 2026-10-10. Code: `backend/src/autolp/selection/` (`params.py` holds every threshold and weight). Any change to a value is a model change: bump `MODEL_VERSION` and record it here.

The model separates three decisions: **eligibility** (hard rules), **ranking** (0–100 score) and **entry** (trading strategies, Phase 5). A high score is never a buy signal.

## 1. Evaluation cycle

| When | What |
|---|---|
| Every 15 min (`:04, :19, :34, :49`) | Features, eligibility and scores for every active token → `score_evaluations`. Critical safety failures remove watchlist members immediately. |
| Hourly (the `:04` run) | Eligible tokens are ranked (`rank`). This is the evaluation used for membership. |
| 00/06/12/18 UTC hourly run | Non-safety membership changes (entries, retention, swaps). |

Every evaluation stores the raw features, component scores, category scores, total, coverage, eligibility reasons and model version, so each decision can be recomputed. Inputs are read point-in-time: only rows observed at or before the evaluation time are used.

## 2. Eligibility (hard rules)

| Rule | Threshold | Reason code |
|---|---|---|
| Fresh market snapshot | ≤ 15 min old | `stale_snapshot` |
| Token age (first pool) | ≥ 7 days | `age`, `age_unknown` |
| Market cap | $1M – $300M | `market_cap_below/above/unknown` |
| Liquidity (Jupiter, all pools) | ≥ $150K | `liquidity` |
| 24h volume | ≥ $500K | `volume_24h` |
| 24h trades | ≥ 500 | `trades_24h` |
| 24h unique traders | ≥ 150 | `traders_24h` |
| Deepest pool age | ≥ 48 h | `pool_age`, `pool_age_unknown` |
| Mint / freeze authority, Token-2022 restrictions | none active | `security:<flag>` **critical** |
| Unparseable mint account | — | `security_unknown` **critical** |
| Security check | ≤ 48 h old | `security_missing` |
| Top-10 owner concentration (adjusted) | ≤ 40% | `top10_concentration` |
| Holder analysis (method v2) | ≤ 48 h old | `holders_missing` |
| $1,000 sell execution cost | ≤ 2% | `sell_cost` |
| Sell quote | ≤ 2 h old; a route must exist | `quote_missing`; `no_sell_route` **critical** |

Critical flags: `mint_authority_active`, `freeze_authority_active`, `permanent_delegate`, `transfer_hook`, `transfer_fee`, `pausable`, `non_transferable`, `default_frozen`, `mint_close_authority`. Missing or stale safety data always makes a token ineligible.

## 3. Score

`N(x; a, b) = 100 · clamp((x − a) / (b − a), 0, 1)`. Ineligible tokens are not scored.

**Total** `S = 0.25·Activity + 0.25·Momentum + 0.20·Liquidity + 0.15·Buying + 0.15·Risk`

### Activity (25%)
| Component | Weight | Input | Score |
|---|---|---|---|
| Relative volume | 35% | V1h / (V24h / 24) | N(·; 0.5, 3) |
| Trader growth | 30% | unique traders 1h now / 1h ago | N(·; 0.7, 1.5) |
| Transaction acceleration | 20% | trades 1h now / 1h ago | N(·; 0.7, 1.75) |
| Volume consistency | 15% | 5-min buckets (last 12) with volume ≥ V1h/24 | 100 · active / observed (≥ 10 observed) |

### Momentum (25%)
| Component | Weight | Input | Score |
|---|---|---|---|
| 1h return | 30% | Jupiter 1h price change | 0 at −5%, 100 at +8%, 0 at +30% |
| 4h return | 30% | own 5-min price samples | 0 at −10%, 100 at +15%, 0 at +60% |
| Trend strength | 25% | EMA20/EMA50 − 1 on 5-min price samples (≥ 50 samples) | N(·; 0, 0.04) |
| Distance from 4h high | 15% | % below the 4h sampled high | 100 within 10%, 0 at 30% |

### Liquidity (20%)
| Component | Weight | Input | Score |
|---|---|---|---|
| DEX liquidity | 35% | log10(USD liquidity) | N(·; 5.176, 6.301) — $150K → 0, $2M → 100 |
| Volume / liquidity | 20% | V24h / liquidity | 0→100 over 0–1; 100 over 1–5; 100→0 over 5–15 |
| Sell execution cost | 30% | paired-quote cost of a $1K sell, % | 100 − N(·; 0.2, 2.0) |
| Liquidity stability | 15% | √(base × quote reserves) of the primary pool, now / 24h ago | N(·; 0.7, 1.0) |

### Buying pressure (15%)
| Component | Weight | Input | Score |
|---|---|---|---|
| Buy-volume share | 55% | buy USD / total USD, 1h | N(·; 0.45, 0.65) |
| Net buying vs liquidity | 30% | (buy − sell USD, 1h) / liquidity | N(·; −0.05, 0.10) |
| Buying persistence | 15% | 5-min buckets (last 12) with buy > sell volume | 100 · positive / observed |

### Token & holder risk (15%, higher = safer)
| Component | Weight | Input | Score |
|---|---|---|---|
| Top-10 concentration | 40% | top-10 owners after exclusions, fraction | 100 − N(·; 0.15, 0.40) |
| Largest holder | 25% | largest owner after exclusions, fraction | 100 − N(·; 0.03, 0.15) |
| Liquidity control | 20% | — | constant 50, reason `not_assessed` |
| Trading quality | 15% | Jupiter organic-score label | high 100 / medium 50 / low 0, reason `proxy:jupiter_organic_score_label` |

**Missing inputs.** A component with no data is skipped; its category is the weighted mean of the remaining components, and the total the weighted mean of available categories. `coverage` is the share of total model weight that had data. A coin needs coverage ≥ 80% to enter the watchlist.

**Groups.** Established: market cap ≥ $50M. Emerging: $5M–$50M and Activity ≥ 50. Breakout: $1M–$50M, EMA20 > EMA50, |1h return| < 5% and within 10% of the 4h high. Otherwise `other`. Assigned in that order.

## 4. Watchlist

- At most 30 members; group targets 10/10/10, filled in the order established, emerging, breakout; unused slots go to the best remaining qualified coins (any group).
- **Entry:** eligible, coverage ≥ 80%, score ≥ 70 in this and the previous hourly evaluation. Nobody enters below 70, so the list can hold fewer than 30.
- **Removal:** critical safety failure (immediately, any 15-minute run); ineligible at a membership window; score < 55 in two consecutive hourly evaluations; outscored by ≥ 10 points by a qualifying non-member in two consecutive hourly evaluations (swap).
- Score-based removals and swaps: at most 6 per UTC day. Ineligibility and safety removals are not capped.
- Every change is a `watchlist_events` row with a reason code (`qualified:<group>`, `qualified:unfilled_slot`, `replaces:<mint>`, `outscored_by:<mint>`, `score_below_retain`, `ineligible:<reasons>`, `critical:<flags>`).

Thresholds 70/55 are kept from the spec and reviewed after 7 days of recorded scores (2026-10-17).

## 5. Holder analysis (method v2)

From the 20 largest token accounts (RPC `getTokenLargestAccounts`): accounts are aggregated by owner; excluded owners are
- `burn` — the incinerator (also removed from the denominator);
- `infrastructure:<label>` — listed in `reference/infrastructure_wallets.json` (exchanges, custodians; each entry needs a verified label and source);
- `program` — the owner is off the ed25519 curve (a program-derived address: AMM vault authorities, multisigs, lockers, bonding curves) or its account is owned by a non-System program.

Unresolved owners are counted. Top-10 and largest-holder figures are fractions of supply minus burns. The off-curve test was validated against 920 associated-token-account derivations from live data. The explorer's Eligibility page lists wallets that appear among the largest holders of many tokens as candidates for the infrastructure list.

## 6. Changes from spec v1.0

| # | Spec v1.0 | v1.1 | Why |
|---|---|---|---|
| 1 | Activity ratios normalized from 1.0 | Lower bounds 0.5 / 0.7 / 0.7 | With a 1.0 floor, 66–78% of tokens scored 0 on three of four components, so Activity was ~constant. Flat activity now lands at 20–38. |
| 2 | Consistency: buckets with ≥ 1% of hourly volume | ≥ 1/24 of hourly volume (half an even share) | 1% saturated at 100 for 73% of tokens. |
| 3 | Unique buyer/seller ratio (30% of Buying) | Removed; weights 55/30/15 | No wallet-level buyer/seller counts in free data; Jupiter's fields are undocumented. Returns if Birdeye is enabled. |
| 4 | Cost vs. reported price | Paired quote ($10 reference + $1,000) | Snapshot prices up to 15 min old produced impossible negative slippage. |
| 5 | Liquidity stability on USD liquidity | √(base × quote reserves) of the same pool | Invariant to price for constant-product pools, as the spec requires. |
| 6 | Volume/liquidity below 1 undefined | Linear 0 → 100 over 0–1 | Fill the gap. |
| 7 | EMAs on 5-min candles; 4h return | Own 5-min price samples for every token | Candles cover only 30 tokens; Jupiter has no 4h window. Sampled highs slightly understate intrabar highs. |
| 8 | Liquidity control / wallet clustering | Constant 50 (`not_assessed`) / Jupiter organic label proxy | No data source yet; the constant does not change ranking. |
| 9 | Exclusions "after documented exclusions" | Holder method v2 (owner aggregation, PDA detection, curated infra list) | v1 counted Raydium pool vaults as whales. |
| 10 | Transfer restrictions | Any Token-2022 risk flag incl. transfer fee is critical | Conservative reading of "no unexpected restrictions". |
| 11 | Missing data | Category = mean of available components; coverage ≥ 80% to enter | Explicit rule. |
| 12 | Cadence (master plan vs spec) | Score 15 min, rank hourly, membership at 6-hour boundaries | Combines both documents. |
| 13 | "Unfilled slots go to highest-scoring" vs "entry ≥ 70" | Only coins ≥ 70 fill slots | Resolves the conflict. |
| 14 | Group criteria | Concrete rules (section 3) | "Rising activity" and "consolidation" were undefined. |

## 7. Known limitations

- Replays use each token's *current* tracking status and primary pool; scores themselves only use point-in-time observations.
- Liquidity stability needs 24 h of pool snapshots; the 4h return and EMAs need 4–5 h of samples. Until then those components are missing and coverage is lower.
- Holder concentration only sees the 20 largest token accounts.
- Jupiter's organic score is a third-party black box, used only as a labelled proxy.
