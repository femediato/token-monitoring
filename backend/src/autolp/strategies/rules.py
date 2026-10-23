"""Entry rules for strategies A (momentum), B (pullback) and C (range), version strat-v1.

Pure functions of a point-in-time `MarketView`: completed 5-minute candles and the latest
selection-model evaluation at or before `at`. Every check is recorded as True / False /
None (input missing), and a setup is produced only when every check is True. A setup is a
paper decision; sizing and portfolio limits are applied in `risk.py`.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from autolp.strategies.bars import Bar, atr, ema, floor_hour, hour_bar, rsi
from autolp.strategies.params import PARAMS, ExitParams, ManageParams, StrategyParams

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
    # Share of eligible tokens with a positive 4h return at `at` (filters.min_breadth_pct);
    # None when too few tokens have one.
    breadth: float | None = None


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
    atr: float = 0.0  # ATR at the signal (price units), for stop management; 0 = unused


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
    _filters(sig, view, p)


def _filters(sig: Signal, view: MarketView, p: StrategyParams) -> None:
    """Optional entry filters (off by default; strat-v1 adds no check here)."""
    fp = p.filters
    if fp.min_breadth_pct > 0:
        sig.values["breadth_pct"] = view.breadth
        sig.checks["regime"] = None if view.breadth is None else view.breadth >= fp.min_breadth_pct
    bounds = (
        ("token_trend", "ret_4h_pct", fp.min_ret_4h_pct, fp.max_ret_4h_pct),
        ("token_trend_1h", "ret_1h_pct", fp.min_ret_1h_pct, fp.max_ret_1h_pct),
    )
    for check, key, lo, hi in bounds:
        if lo is None and hi is None:
            continue
        ev = view.evaluation
        r = ev.features.get(key) if ev else None
        sig.values[key] = r
        lo_ = lo if lo is not None else float("-inf")
        hi_ = hi if hi is not None else float("inf")
        sig.checks[check] = None if r is None else lo_ <= r <= hi_


def _atr(sig: Signal, bars: list[Bar], p: StrategyParams, needed: bool) -> float:
    """ATR of the signal's candles when an option needs it; a missing ATR fails the
    `atr` check (no setup)."""
    if not needed:
        return 0.0
    a = atr(bars, p.atr_candles)
    if a is None or not bars or bars[-1].close <= 0:
        sig.checks["atr"] = None
        return 0.0
    sig.values["atr_pct"] = round(a / bars[-1].close * 100, 4)
    sig.checks["atr"] = a > 0
    return a


def _levels(
    sig: Signal, bars: list[Bar], x: ExitParams, m: ManageParams, p: StrategyParams
) -> tuple[float, float, float]:
    """Stop, target and ATR for the fixed-percentage or ATR exit modes."""
    entry = bars[-1].close
    a = _atr(sig, bars, p, x.mode == "atr" or m.trail_atr > 0)
    if x.mode != "atr" or a <= 0:
        return entry * (1 - x.stop_loss_pct / 100), entry * (1 + x.take_profit_pct / 100), a
    dist = min(max(x.atr_mult * a / entry * 100, x.min_stop_pct), x.max_stop_pct)
    rt = 2 * sig.values.get("cost_pct", 0.0)
    # Net reward = reward_risk x net risk, with the round-trip cost on both sides.
    reward = x.reward_risk * (dist + rt) + rt
    sig.values.update(stop_pct=round(dist, 4), target_pct=round(reward, 4))
    return entry * (1 - dist / 100), entry * (1 + reward / 100), a


def _finish(
    sig: Signal, view: MarketView, stop: float, target: float, hold: float, a: float = 0.0
) -> Signal:
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
            a,
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
    stop, target, a = _levels(sig, bars, mp.exit, mp.manage, p)
    return _finish(sig, view, stop, target, mp.exit.max_hold_hours, a)


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
    stop, target, a = _levels(sig, bars, pp.exit, pp.manage, p)
    return _finish(sig, view, stop, target, pp.exit.max_hold_hours, a)


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
    a = _atr(sig, bars, p, rp.atr_stop_mult > 0 or rp.manage.trail_atr > 0)
    if rp.atr_stop_mult > 0 and a > 0:
        stop = min(stop, bars[-1].close - rp.atr_stop_mult * a)
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
    return _finish(sig, view, stop, target, rp.max_hold_hours, a)


def _no_risk_warnings(sig: Signal, view: MarketView) -> None:
    f = view.evaluation.features if view.evaluation else {}
    flags, label = f.get("risk_flags"), f.get("organic_label")
    # Missing safety inputs are never read as "no warning".
    sig.checks["no_risk_warnings"] = (
        None if flags is None or label is None else not flags and label != "low"
    )


def reversal(view: MarketView, p: StrategyParams = PARAMS) -> Signal:
    """D (research): the close is min-max % below the highest high of the lookback, with
    optional oversold RSI, recovery candle and buying share, and no risk warnings."""
    rp = p.reversal
    sig = Signal("reversal", view.mint, view.at)
    _common(sig, view, p)
    bars = list(view.bars)
    if len(bars) >= rp.lookback_candles:
        hi = max(b.high for b in bars[-rp.lookback_candles :])
        drop = (hi - bars[-1].close) / hi * 100 if hi > 0 else 0.0
        sig.values.update(local_high=hi, drop_pct=round(drop, 3))
        sig.checks["drop"] = rp.min_drop_pct <= drop <= rp.max_drop_pct
    else:
        sig.checks["drop"] = None
    if rp.max_rsi > 0:
        r = rsi([b.close for b in bars], rp.rsi_candles)
        sig.values["rsi"] = None if r is None else round(r, 2)
        sig.checks["oversold"] = None if r is None else r <= rp.max_rsi
    if rp.confirm:
        if len(bars) >= 2 and _contiguous(bars[-2], bars[-1]):
            last, before = bars[-1], bars[-2]
            sig.checks["recovery_candle"] = last.bullish and last.close > before.high
        else:
            sig.checks["recovery_candle"] = None
    if rp.min_buy_share > 0:
        ev = view.evaluation
        share = ev.features.get("buy_share_1h") if ev else None
        sig.values["buy_share_1h"] = share
        sig.checks["buying"] = None if share is None else share >= rp.min_buy_share
    _no_risk_warnings(sig, view)
    if not bars:
        return sig
    stop, target, a = _levels(sig, bars, rp.exit, rp.manage, p)
    return _finish(sig, view, stop, target, rp.exit.max_hold_hours, a)


def random_entry(view: MarketView, p: StrategyParams = PARAMS) -> Signal:
    """Research baseline: a deterministic pseudo-random entry on prob_pct of the candles,
    with the shared checks, the filters and the configured exits."""
    rp = p.random
    sig = Signal("random", view.mint, view.at)
    _common(sig, view, p)
    bars = list(view.bars)
    if not bars:
        sig.checks["draw"] = None
        return sig
    key = f"{rp.seed}|{view.mint}|{bars[-1].start.isoformat()}".encode()
    u = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
    sig.checks["draw"] = u < rp.prob_pct / 100
    stop, target, a = _levels(sig, bars, rp.exit, rp.manage, p)
    return _finish(sig, view, stop, target, rp.exit.max_hold_hours, a)


RULES = {
    "momentum": momentum,
    "pullback": pullback,
    "range": range_trade,
    "reversal": reversal,
    "random": random_entry,
}


def manage_params(p: StrategyParams, strategy: str) -> ManageParams:
    """The stop-management options of a strategy (defaults for strategies without)."""
    m = getattr(getattr(p, strategy, None), "manage", None)
    return m if isinstance(m, ManageParams) else ManageParams()


def evaluate(view: MarketView, p: StrategyParams = PARAMS) -> list[Signal]:
    return [RULES[name](view, p) for name in p.enabled]
