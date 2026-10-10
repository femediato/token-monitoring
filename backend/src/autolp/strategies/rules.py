"""Entry rules for strategies A (momentum), B (pullback) and C (range), version strat-v1.

Pure functions of a point-in-time `MarketView`: completed 5-minute candles and the latest
selection-model evaluation at or before `at`. Every check is recorded as True / False /
None (input missing), and a setup is produced only when every check is True. A setup is a
paper decision; sizing and portfolio limits are applied in `risk.py`.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from autolp.strategies.bars import Bar, ema, floor_hour, hour_bar
from autolp.strategies.params import PARAMS, StrategyParams

STRATEGIES = ("momentum", "pullback", "range")


@dataclass(frozen=True)
class Evaluation:
    """The parts of a score_evaluations row the strategies read."""

    at: datetime
    eligible: bool
    buying: float | None
    features: dict[str, Any]


@dataclass(frozen=True)
class MarketView:
    mint: str
    at: datetime
    bars: tuple[Bar, ...]  # completed candles, ascending
    evaluation: Evaluation | None  # latest at or before `at`
    previous: Evaluation | None  # about an hour before `evaluation`


@dataclass(frozen=True)
class Setup:
    strategy: str
    mint: str
    at: datetime
    signal_bar: datetime  # start of the signal candle
    entry_ref: float  # close of the signal candle
    stop: float
    target: float
    max_hold_hours: float
    cost_pct: float  # expected one-way execution cost, %


@dataclass
class Signal:
    strategy: str
    mint: str
    at: datetime
    checks: dict[str, bool | None] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    setup: Setup | None = None

    @property
    def failed(self) -> list[str]:
        return [k for k, v in self.checks.items() if v is not True]


def _contiguous(a: Bar, b: Bar) -> bool:
    return a.end == b.start


def _common(sig: Signal, view: MarketView, p: StrategyParams) -> None:
    """Data freshness, eligibility and a usable execution-cost estimate."""
    d, r = p.data, p.risk
    bars, ev = view.bars, view.evaluation
    sig.checks["fresh_candles"] = bool(bars) and (view.at - bars[-1].end) <= timedelta(
        minutes=d.max_candle_age_minutes
    )
    sig.checks["fresh_evaluation"] = ev is not None and (view.at - ev.at) <= timedelta(
        minutes=d.max_evaluation_age_minutes
    )
    sig.checks["eligible"] = ev.eligible if ev else None
    f = ev.features if ev else {}
    cost, age = f.get("sell_cost_pct"), f.get("quote_age_minutes")
    if ev is None or cost is None or age is None:
        sig.checks["cost_estimate"] = None
    else:
        age_now = age + (view.at - ev.at).total_seconds() / 60
        sig.values.update(cost_pct=cost, quote_age_minutes=round(age_now, 1))
        sig.checks["cost_estimate"] = age_now <= r.max_quote_age_minutes and cost <= r.max_cost_pct


def _finish(sig: Signal, view: MarketView, stop: float, target: float, hold: float) -> Signal:
    if all(v is True for v in sig.checks.values()):
        sig.setup = Setup(
            sig.strategy,
            view.mint,
            view.at,
            view.bars[-1].start,
            view.bars[-1].close,
            stop,
            target,
            hold,
            sig.values["cost_pct"],
        )
    return sig


def momentum(view: MarketView, p: StrategyParams = PARAMS) -> Signal:
    """A: the last 5-minute close crosses above the high of the previous completed clock
    hour, with relative volume, buying pressure and no risk warnings."""
    mp = p.momentum
    sig = Signal("momentum", view.mint, view.at)
    _common(sig, view, p)
    bars, ev = list(view.bars), view.evaluation
    f = ev.features if ev else {}
    if len(bars) >= 2 and _contiguous(bars[-2], bars[-1]):
        last, prev = bars[-1], bars[-2]
        ref = hour_bar(bars, floor_hour(last.start) - timedelta(hours=1))
        if ref is None or ref.candles < mp.min_hour_candles:
            sig.checks["breakout"] = None
        else:
            sig.values["prev_hour_high"] = ref.high
            sig.checks["breakout"] = last.close > ref.high >= prev.close
    else:
        sig.checks["breakout"] = None
    relvol = f.get("relvol_ratio")
    sig.values["relative_volume"] = relvol
    sig.checks["relative_volume"] = None if relvol is None else relvol >= mp.min_relative_volume
    buying = ev.buying if ev else None
    sig.values["buying_score"] = buying
    sig.checks["buying_pressure"] = None if buying is None else buying >= mp.min_buying_score
    flags, label = f.get("risk_flags"), f.get("organic_label")
    # Missing safety inputs are never read as "no warning".
    sig.checks["no_risk_warnings"] = (
        None if flags is None or label is None else not flags and label != "low"
    )
    if not bars:
        return sig
    entry = bars[-1].close
    x = mp.exit
    return _finish(
        sig,
        view,
        entry * (1 - x.stop_loss_pct / 100),
        entry * (1 + x.take_profit_pct / 100),
        x.max_hold_hours,
    )


def pullback(view: MarketView, p: StrategyParams = PARAMS) -> Signal:
    """B: EMA20 > EMA50, a 5-10% retrace from the recent high, improving buying pressure,
    and a completed 5-minute candle that confirms the recovery."""
    pp = p.pullback
    sig = Signal("pullback", view.mint, view.at)
    _common(sig, view, p)
    bars = list(view.bars)
    closes = [b.close for b in bars]
    if len(closes) >= 2 * pp.ema_slow:
        fast, slow = ema(closes, pp.ema_fast), ema(closes, pp.ema_slow)
        assert fast is not None and slow is not None
        sig.values.update(ema_fast=fast, ema_slow=slow)
        sig.checks["uptrend"] = fast > slow
    else:
        sig.checks["uptrend"] = None

    if len(bars) > pp.high_lookback_candles:
        window = bars[-pp.high_lookback_candles - 1 : -1]
        hi = max(b.high for b in window)
        i = max(j for j, b in enumerate(window) if b.high == hi)  # most recent high
        low = min(b.low for b in [*window[i + 1 :], bars[-1]])
        retrace = (hi - low) / hi * 100
        sig.values.update(local_high=hi, pullback_low=low, retrace_pct=round(retrace, 3))
        sig.checks["retrace"] = (
            pp.min_retrace_pct <= retrace <= pp.max_retrace_pct and bars[-1].close < hi
        )
    else:
        sig.checks["retrace"] = None

    ev, prev = view.evaluation, view.previous
    now_share = ev.features.get("buy_share_1h") if ev else None
    prev_share = prev.features.get("buy_share_1h") if prev else None
    if now_share is None or prev_share is None:
        sig.checks["buying_improves"] = None
    else:
        sig.values["buy_share_change"] = round(now_share - prev_share, 4)
        sig.checks["buying_improves"] = now_share - prev_share > pp.min_buy_share_improvement

    if len(bars) >= 2 and _contiguous(bars[-2], bars[-1]):
        last, before = bars[-1], bars[-2]
        sig.checks["recovery_candle"] = last.bullish and last.close > before.high
    else:
        sig.checks["recovery_candle"] = None
    if not bars:
        return sig
    entry = bars[-1].close
    x = pp.exit
    return _finish(
        sig,
        view,
        entry * (1 - x.stop_loss_pct / 100),
        entry * (1 + x.take_profit_pct / 100),
        x.max_hold_hours,
    )


def _touches(idx: list[int], gap: int) -> int:
    """Count touches at least `gap` candles apart (greedy, in time order)."""
    n, last = 0, -(10**9)
    for i in idx:
        if i - last >= gap:
            n, last = n + 1, i
    return n


def swing_points(bars: list[Bar], k: int) -> tuple[list[int], list[int]]:
    lows, highs = [], []
    for i in range(k, len(bars) - k):
        around = bars[i - k : i + k + 1]
        if bars[i].low == min(b.low for b in around):
            lows.append(i)
        if bars[i].high == max(b.high for b in around):
            highs.append(i)
    return lows, highs


def range_trade(view: MarketView, p: StrategyParams = PARAMS) -> Signal:
    """C: a range with >= 2 prior support and resistance tests, price back near support, a
    reversal candle, and reward/risk >= 2 after expected round-trip costs."""
    rp = p.range
    sig = Signal("range", view.mint, view.at)
    _common(sig, view, p)
    bars = list(view.bars)
    keys = ("range_found", "support_tests", "resistance_tests", "near_support", "reversal")
    if len(bars) < rp.window_candles:
        sig.checks.update(dict.fromkeys(keys))
        sig.checks["reward_risk"] = None
        return sig
    window = bars[-rp.window_candles :]
    prior, recent = window[: -rp.recent_candles], window[-rp.recent_candles :]
    res, sup = max(b.high for b in prior), min(b.low for b in prior)
    width_pct = (res - sup) / sup * 100
    tol = rp.touch_tolerance * (res - sup)
    stop = sup * (1 - rp.stop_buffer_pct / 100)
    target = res - tol
    lows, highs = swing_points(prior, rp.swing_neighbours)
    gap = rp.min_touch_gap_candles
    s_tests = _touches([i for i in lows if prior[i].low <= sup + tol], gap)
    r_tests = _touches([i for i in highs if prior[i].high >= res - tol], gap)
    recent_low = min(b.low for b in recent)
    last, before = bars[-1], bars[-2]
    sig.values.update(
        support=sup,
        resistance=res,
        width_pct=round(width_pct, 3),
        support_tests=s_tests,
        resistance_tests=r_tests,
    )
    sig.checks["range_found"] = rp.min_width_pct <= width_pct <= rp.max_width_pct
    sig.checks["support_tests"] = s_tests >= rp.min_support_tests
    sig.checks["resistance_tests"] = r_tests >= rp.min_resistance_tests
    sig.checks["near_support"] = stop < recent_low <= sup + tol and last.close < target
    sig.checks["reversal"] = (
        _contiguous(before, last) and last.bullish and last.close > before.close
    )
    cost = sig.values.get("cost_pct")
    if cost is None:
        sig.checks["reward_risk"] = None
    else:
        rt = 2 * cost / 100
        entry = last.close
        reward = (target - entry) / entry - rt
        risk = (entry - stop) / entry + rt
        rr = reward / risk if risk > 0 else 0.0
        sig.values["reward_risk"] = round(rr, 3)
        sig.checks["reward_risk"] = reward > 0 and rr >= rp.min_reward_risk
    return _finish(sig, view, stop, target, rp.max_hold_hours)


RULES = {"momentum": momentum, "pullback": pullback, "range": range_trade}


def evaluate(view: MarketView, p: StrategyParams = PARAMS) -> list[Signal]:
    return [RULES[name](view, p) for name in p.enabled]
