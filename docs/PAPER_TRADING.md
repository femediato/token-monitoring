# Paper trading — paper-v2

Phase 7 of the master plan: run the strategies continuously on live data without placing real orders. Code: `backend/src/autolp/paper/`; parameters in `paper/params.py` (changing one is a paper-trading change: bump `PAPER_VERSION` and record it here). Entry and exit rules, sizing and risk limits are the current strategy version (`docs/STRATEGIES.md`): strat-v1 until 2026-10-25, strat-v1.1 (Range off) since. Each position records its `strategy_version`.

**Nothing is signed or sent.** The engine only requests Jupiter swap *quotes* (read-only, the same endpoint as the `quotes` job). There is no wallet, no key and no transaction code.

## 1. What runs

The `paper_trading` job in the collector runs every minute (at :30). Each tick:

1. **Interrupted orders.** An order still pending after 2 minutes, because the process stopped between the decision and its quote, is cancelled as `interrupted`.
2. **Exits.** Each open position is marked with a small sell quote, about $10 of the held tokens. The position then exits if:
   - **stop:** the mark is at or below the stop;
   - **target:** the mark is at or above the target;
   - **time:** the holding limit has passed.

   The exit is filled with a full-size sell quote of every held token. If the mark quote fails, there is no price, so a stop or target cannot trigger: missing data is not treated as safe or as a signal. Only the holding limit still triggers then. A failed exit quote is logged as `exit_failed` and retried every minute at whatever the price is then. The reason gets a `_retry` suffix, as in the backtest.
3. **Entries.**
   - **Evaluation:** the three strategies are evaluated on the same point-in-time views as the live signal job and the replay (`strategies/data.py`).
   - **Once per setup:** each setup is recorded once per strategy, token and signal candle, so a restart never decides twice.
   - **Risk rules:** the setup then goes through the shared risk rules (`risk.admit`, the same function the backtest engine uses).
   - **Fill:** an accepted order is filled at once with a full-size buy quote, or cancelled as `swap_failed` if no quote comes back.
4. **Equity.** Cash plus open positions at their latest mark is stored in `paper_equity`.
5. **Backtest-model price.** Fills old enough to have candles get the price the backtest fill model would have used for the same order (section 4).

## 2. Fills

A fill is a pair of Jupiter quotes taken one after the other, in the same direction:

- a **reference quote** of $10 (for buys) or about $10 of tokens (for sells, the mark);
- the **full-size quote**: the order size minus the fee in USDC (buy), or every held token (sell).

The full-size quote is the paper execution:

- **Tokens and USDC:** exactly its input and output amounts.
- **Realised cost:** how much worse the full size is than the reference, in percent. For a buy it is fill price ÷ reference − 1; for a sell, 1 − fill price ÷ reference.
- **Fee:** a $0.05 network fee per swap, as in strat-v1.
- **Sizing:** uses the setup's quoted cost estimate, as everywhere else. Every fill records the realised cost next to that estimate.

Both quotes' raw responses are stored in `raw_observations`, and their ids are kept on the fill.

**What a quote does not model.**

- **Landing a transaction:** latency, priority-fee auctions and failed or reverted transactions.
- **MEV:** sandwiching between the quote and inclusion.

A paper fill is therefore an optimistic bound on a live fill. The plan requires 30 days of paper trading before live execution is even considered for exactly this reason.

**Candle delay.** Signals still see candles about 10 minutes after they close. Each fill event records `slip_vs_entry_ref_pct`: how far the fill was from the signal candle's close.

## 3. Restarts and auditing

- **Restart-safe.** All state is in the database:
  - the account's cash;
  - positions (pending, open, closed or cancelled);
  - fills, events and equity marks.

  Each step commits as soon as it happens. After a restart the next tick carries on: open positions are managed, and decided signals are not decided again (unique index on the signal key). An order cut off between decision and quote is cancelled as interrupted.
- **One engine at a time.** A Postgres advisory lock prevents two engines from running at once.
- **Audit trail.** `paper_events` holds every signal, rejection (with the risk rule), order (with sizing and equity), fill, cancel, failed exit and exit. `paper_fills` holds every quote attempt, including failures with their error.

## 4. Reconciliation (Phase 7 acceptance)

`paper/report.py` `reconcile` rebuilds everything from the fills and checks it against what the engine recorded.

Per position, the events must be complete:

- signal and order;
- then fill or cancel;
- then exit.

The amounts must also match:

- quantity = the entry fill's tokens;
- the entry spent no more than the order size;
- the exit sold exactly the tokens bought;
- proceeds = exit USDC − fee;
- P/L = proceeds − (entry USDC + fee);
- failed exit attempts = failed exit fills.

For the account, cash must equal starting equity − every entry (USDC + fee) + every exit (USDC − fee).

The result is shown on the Paper portfolio page, per position in its detail view, and by `make paper-report`.

**Backtest-model comparison.** Each fill is compared with fill model v0 of the backtest:

| Fill | Model price |
|---|---|
| Entry | open of the first candle starting at or after the decision, plus the quoted cost |
| Stop or target exit | the trigger level, minus the quoted cost |
| Time exit | the next candle's open, minus the quoted cost |

"Shortfall" is how much worse the paper fill was than that. The report sets the observed values next to the bt-v1 base assumptions:

- realised ÷ quoted cost (assumed × 1.0);
- stop fill below trigger (assumed 1%);
- failed swaps (assumed 2%).

Those assumptions should be updated from these observations once there are enough of them.

## 5. Running it

```
make paper-report    # summary, execution quality and reconciliation
```

The **Paper portfolio** page shows:

- the 30-day and 100-trade progress;
- equity and drawdown;
- open positions and orders with unrealised P/L;
- execution quality against the backtest;
- closed trades and the latest decisions.

Clicking a position shows its full timeline and fills.

The API is `/api/paper`, `/api/paper/positions/{id}` and `/api/paper/events?kind=`.

## 6. Limitations

- **Duration.** The plan requires at least 30 days of forward paper trading (Phase 9 also wants 100 trades) before live execution is considered. The paper account started on 2026-10-11 at 16:58 UTC.
- **Optimistic fills.** Quotes are executions without landing risk (section 2).
- **Polling.** Exits are checked every minute by polling. A wick through the stop between two polls is not seen, which is also true of a live bot that polls. The backtest instead assumes the stop trades inside the candle. Comparing the two is part of the point.
- **Reference price.** The mark is a small *sell* quote, so it includes the pool fee: it is a bid, not a mid. Stops therefore trigger slightly earlier than on a mid price; targets slightly later.
- **Shared rate limit.** The Jupiter rate limit (1 request a second) is shared with the collector. A tick may wait behind the 15-minute quotes job; the quote's own timestamp is the fill time.
- **First observation.** A read-only live check on 2026-10-11 (USELESS, $1,000) gave:
  - buy cost against the $10 reference: 0.19%, against a 0.02% quoted sell-cost estimate;
  - round-trip loss: 0.21%.

  This is one sample; it shows the kind of gap the comparison is there to measure.

## 7. Paper-v2: parallel accounts and research options (2026-10-15)

paper-v2 changes nothing for the `main` account: same rules (strat-v1), same fills, same limits. Its record from 11 Oct continues as the control.

**Parallel accounts** (`paper/accounts.py`). `ACCOUNTS` lists the accounts the `paper_trading` job ticks every minute, `main` first.
- Each account has a name, a description and strategy-parameter overrides, using the research options of `docs/STRATEGIES.md`, section 10.
- Each account keeps its own cash, positions, events, equity marks and advisory lock. A failure in a research account is logged and does not stop `main`.
- An account's overrides are part of its identity: a different rule set gets a new account name, so no record mixes two rule sets.
- Research accounts forward-test variants shortlisted by the research sweeps (`docs/BACKTESTING.md`, section 11) on data the sweeps never saw. Today only `main` runs.

**Research options in paper trading:**
- **Breakeven and trailing stop:** applied to the minute marks. After each mark that triggers no exit, the stop is raised from the highest mark since the entry (`high_price`). It applies from the next tick and is logged as a `stop_moved` event. An exit on a raised stop is `trail`.
  - `initial_stop` and `atr` are stored per position (migration 0009).
  - Positions opened before paper-v2 have no `initial_stop` and never move their stop.
- **Entry drift guard:** when the full-size buy quote's reference price is more than `max_entry_drift_r` × R above the signal close, the order is cancelled as `entry_drift`. No cash moves; the quote is still stored as a fill row.
- **Breaker:** the losing-streak and drawdown pause is replayed from the stored exits and equity marks at every decision, so it survives restarts.
- **Filters, ATR exits, slippage-aware sizing:** these work through the shared rules and `risk.admit`, as in the backtest.

**Report fix.** The observed cost multiplier ("paper fills vs. the backtest") is now total realised cost ÷ total quoted cost. The earlier mean of per-fill ratios was dominated by fills with a near-zero estimate: it showed 2.97×, while costs actually ran at about 1.0× the quote.
