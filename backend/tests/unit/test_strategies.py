"""Strategy rules, risk rules and determinism of the paper decision engine (strat-v1)."""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autolp.config import CollectionSettings
from autolp.strategies import rules
from autolp.strategies.bars import Bar, ema, hour_bar
from autolp.strategies.data import History
from autolp.strategies.engine import Engine
from autolp.strategies.params import PARAMS, RiskParams
from autolp.strategies.replay import run
from autolp.strategies.risk import Position, Sizing, check_exit, size_position
from autolp.strategies.rules import Evaluation, MarketView, Setup

T0 = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
M5 = timedelta(minutes=5)


def bars_from(closes: list[float], start: datetime = T0, spread: float = 0.001) -> list[Bar]:
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(start + i * M5, o, max(o, c) + spread, min(o, c) - spread, c, 1000.0))
        prev = c
    return out


def features(**kw: Any) -> dict[str, Any]:
    f = {
        "relvol_ratio": 2.0,
        "risk_flags": [],
        "organic_label": "high",
        "sell_cost_pct": 0.5,
        "quote_age_minutes": 5.0,
        "liquidity_usd": 500_000.0,
        "buy_share_1h": 0.6,
    }
    f.update(kw)
    return f


def view(
    bars: list[Bar],
    *,
    eligible: bool = True,
    buying: float | None = 70,
    prev: dict[str, Any] | None = None,
    lag: timedelta = timedelta(minutes=2),
    **kw: Any,
) -> MarketView:
    at = bars[-1].end + lag
    ev = Evaluation(at - timedelta(minutes=1), eligible, buying, features(**kw))
    pv = Evaluation(ev.at - timedelta(hours=1), True, 60, features(**(prev or {})))
    return MarketView("MINT", at, tuple(bars), ev, pv)


# ------------------------------------------------------------------ bars


def test_ema_is_seeded_with_the_simple_mean() -> None:
    assert ema([1, 2, 3], 3) == 2
    assert ema([1, 2], 3) is None
    assert ema([1, 2, 3, 5], 3) == pytest.approx(3.5)  # 5*0.5 + 2*0.5


def test_hour_bar_aggregates_one_clock_hour() -> None:
    bars = bars_from([1.0] * 11 + [1.2] + [1.0] * 3)
    hb = hour_bar(bars, T0)
    assert hb is not None and hb.candles == 12 and hb.high == pytest.approx(1.201)
    assert hour_bar(bars, T0 - timedelta(hours=1)) is None


# ------------------------------------------------------------------ strategy A


def breakout_bars(prev_close: float = 1.0, last_close: float = 1.05) -> list[Bar]:
    # 2 full hours flat at 1.0 (high 1.001), then the breakout candle at 02:00.
    closes = [1.0] * 23 + [prev_close, last_close]
    return bars_from(closes, T0)


def test_momentum_setup_with_stop_target_and_hold() -> None:
    sig = rules.momentum(view(breakout_bars()))
    assert sig.failed == [], sig.checks
    s = sig.setup
    assert s is not None and s.entry_ref == 1.05
    assert s.stop == pytest.approx(1.05 * 0.93) and s.target == pytest.approx(1.05 * 1.15)
    assert s.max_hold_hours == 4 and s.cost_pct == 0.5


def test_momentum_requires_a_fresh_cross_and_every_condition() -> None:
    # The 02:00 candle already closed above the 01:00 hour's high: 02:05 is not a new cross.
    already = bars_from([1.0] * 24 + [1.03, 1.05])
    assert rules.momentum(view(already)).checks["breakout"] is False
    assert rules.momentum(view(breakout_bars(), relvol_ratio=1.2)).failed == ["relative_volume"]
    assert rules.momentum(view(breakout_bars(), buying=59)).failed == ["buying_pressure"]
    assert rules.momentum(view(breakout_bars(), organic_label="low")).failed == ["no_risk_warnings"]
    # Missing safety input is never read as "no warning".
    missing = rules.momentum(view(breakout_bars(), organic_label=None))
    assert missing.checks["no_risk_warnings"] is None and missing.setup is None


def test_common_gates_block_stale_or_ineligible_inputs() -> None:
    b = breakout_bars()
    assert rules.momentum(view(b, lag=timedelta(minutes=30))).failed == ["fresh_candles"]
    assert rules.momentum(view(b, eligible=False)).failed == ["eligible"]
    assert rules.momentum(view(b, quote_age_minutes=50.0)).failed == ["cost_estimate"]
    assert rules.momentum(view(b, sell_cost_pct=2.5)).failed == ["cost_estimate"]
    nocost = rules.momentum(view(b, sell_cost_pct=None))
    assert nocost.checks["cost_estimate"] is None and nocost.setup is None


# ------------------------------------------------------------------ strategy B


def pullback_bars(depth: float = 0.07) -> list[Bar]:
    up = [1.0 + 0.003 * i for i in range(110)]  # steady uptrend to ~1.327
    top = up[-1]
    down = [top * (1 - depth * k / 4) for k in range(1, 5)]  # 4 candles down
    bars = bars_from(up + down)
    last = bars[-1]
    # Recovery candle: bullish and closing above the previous candle's high.
    rec = Bar(last.end, last.close, last.close * 1.035, last.close * 0.999, last.close * 1.03)
    return [*bars, rec]


def test_pullback_setup() -> None:
    sig = rules.pullback(view(pullback_bars(), prev={"buy_share_1h": 0.5}))
    assert sig.failed == [], (sig.checks, sig.values)
    assert 5 <= sig.values["retrace_pct"] <= 10
    assert sig.setup is not None and sig.setup.max_hold_hours == 8


def test_pullback_rejects_deep_retrace_and_flat_buying() -> None:
    deep = rules.pullback(view(pullback_bars(depth=0.15), prev={"buy_share_1h": 0.5}))
    assert "retrace" in deep.failed
    flat = rules.pullback(view(pullback_bars(), prev={"buy_share_1h": 0.6}))
    assert flat.failed == ["buying_improves"]
    no_prev = view(pullback_bars())
    no_prev = MarketView(no_prev.mint, no_prev.at, no_prev.bars, no_prev.evaluation, None)
    assert rules.pullback(no_prev).checks["buying_improves"] is None


# ------------------------------------------------------------------ strategy C


def range_bars() -> list[Bar]:
    closes = [1.05 - 0.05 * math.cos(2 * math.pi * i / 24) for i in range(69)]
    bars = bars_from(closes)
    t = bars[-1].end
    recent = [
        Bar(t, 1.012, 1.013, 1.004, 1.006),
        Bar(t + M5, 1.006, 1.007, 1.0005, 1.002),
        Bar(t + 2 * M5, 1.002, 1.013, 1.001, 1.012),  # bullish reversal
    ]
    return [*bars, *recent]


def test_range_setup_counts_tests_and_reward_risk() -> None:
    sig = rules.range_trade(view(range_bars(), sell_cost_pct=0.2))
    assert sig.failed == [], (sig.checks, sig.values)
    assert sig.values["support_tests"] >= 2 and sig.values["resistance_tests"] >= 2
    s = sig.setup
    assert s is not None and s.stop < sig.values["support"] < s.entry_ref < s.target
    assert sig.values["reward_risk"] >= 2 and s.max_hold_hours == 12


def test_range_rejects_when_costs_eat_the_reward() -> None:
    sig = rules.range_trade(view(range_bars(), sell_cost_pct=1.5))
    assert sig.failed == ["reward_risk"]


def test_range_needs_a_full_window() -> None:
    sig = rules.range_trade(view(range_bars()[-40:]))
    assert sig.checks["range_found"] is None and sig.setup is None


# ------------------------------------------------------------------ risk


def position(**kw: Any) -> Position:
    base: dict[str, Any] = {
        "id": 1,
        "strategy": "momentum",
        "mint": "M",
        "signal_at": T0,
        "entry_at": T0,
        "entry_price": 1.0,
        "qty": 100.0,
        "size_usd": 100.0,
        "stop": 0.93,
        "target": 1.15,
        "exit_by": T0 + timedelta(hours=4),
        "cost_pct": 0.5,
        "planned_risk_usd": 8.0,
        "checked_until": T0,
    }
    base.update(kw)
    return Position(**base)


def test_exit_assumes_stop_first_and_fills_gaps_at_the_open() -> None:
    p = position()
    both = Bar(T0, 1.0, 1.2, 0.9, 1.1)
    assert check_exit(p, both).reason == "stop"  # type: ignore[union-attr]
    gap = Bar(T0, 0.85, 0.9, 0.8, 0.88)
    ex = check_exit(p, gap)
    assert ex is not None and ex.reason == "stop_gap" and ex.price == 0.85
    up = Bar(T0, 1.0, 1.16, 0.99, 1.1)
    assert check_exit(p, up) == check_exit(p, up) and check_exit(p, up).price == 1.15  # type: ignore[union-attr]
    late = Bar(T0 + timedelta(hours=3, minutes=55), 1.0, 1.01, 0.99, 1.0)
    ex = check_exit(p, late)
    assert ex is not None and ex.reason == "time" and ex.price == 1.0
    assert check_exit(p, Bar(T0, 1.0, 1.01, 0.99, 1.0)) is None


def setup(stop: float = 0.93, cost: float = 0.5) -> Setup:
    return Setup("momentum", "M", T0, T0, 1.0, stop, 1.15, 4, cost)


def test_sizing_limits_loss_at_stop_to_half_a_percent() -> None:
    r = RiskParams()
    s = size_position(setup(), 10_000, 10_000, 1e6, 1000, r)
    assert isinstance(s, Sizing) and s.binding == "risk"
    assert s.planned_risk_usd == pytest.approx(50.0)
    assert s.size_usd == pytest.approx(49.9 / 0.08)


def test_sizing_caps_and_rejections() -> None:
    r = RiskParams()
    tight = size_position(setup(stop=0.98), 10_000, 10_000, 1e6, 1000, r)
    assert isinstance(tight, Sizing) and tight.binding == "quoted_size" and tight.size_usd == 1000
    thin = size_position(setup(), 10_000, 10_000, 50_000, 1000, r)
    assert isinstance(thin, Sizing) and thin.binding == "liquidity" and thin.size_usd == 500
    assert size_position(setup(), 10_000, 10_000, 1_000, 1000, r) == "below_min_order:liquidity"
    assert size_position(setup(), 10_000, 10_000, None, 1000, r) == "liquidity_unknown"
    assert size_position(setup(), 10_000, 10, 1e6, 1000, r) == "below_min_order:cash"


def test_quoted_size_matches_the_collected_quote() -> None:
    assert PARAMS.risk.quoted_size_usd == CollectionSettings().quote_sell_usd


# ------------------------------------------------------------------ engine


def momentum_history(mints: list[str], after: list[float]) -> History:
    """For each mint: a breakout at 02:00 (signal candle 02:00-02:05), then `after` closes."""
    h = History()
    for m in mints:
        bars = breakout_bars()
        bars += bars_from(after, bars[-1].end, spread=0.0) if after else []
        h.bars[m] = bars
        h.evals[m] = [
            Evaluation(T0 + timedelta(minutes=15 * k + 4), True, 70, features())
            for k in range(0, 40)
        ]
    return h


def test_engine_fills_next_open_and_exits_at_target() -> None:
    h = momentum_history(["A"], [1.06, 1.10, 1.25, 1.25])
    start, end = T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=3)
    e = run(h, start, end)
    actions = [(d.action, d.reason) for d in e.decisions]
    assert actions[:3] == [("signal", None), ("order", "risk"), ("fill", None)]
    assert ("exit", "target") in actions
    trade = e.closed[0]
    assert trade["entry_price"] == 1.06  # open of the candle after the signal
    assert trade["exit_price"] == pytest.approx(1.05 * 1.15)  # levels set at the signal
    assert trade["pnl_usd"] > 0 and e.positions == []


def test_engine_enforces_position_limit_and_one_position_per_token() -> None:
    h = momentum_history(["A", "B", "C", "D"], [1.05] * 6)
    e = run(h, T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=2, minutes=30))
    orders = [d.mint for d in e.decisions if d.action == "order"]
    rejects = [(d.mint, d.reason) for d in e.decisions if d.action == "reject"]
    assert orders == ["A", "B", "C"] and ("D", "max_positions") in rejects
    assert len(e.positions) == 3
    assert e.open_risk() == pytest.approx(150.0)  # 3 x 0.5% of 10,000: at the 1.5% cap


def test_a_signal_candle_is_considered_once() -> None:
    h = momentum_history(["A"], [])  # no candle after the breakout arrives
    e = run(h, T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=2, minutes=25))
    assert [(d.action, d.reason) for d in e.decisions] == [
        ("signal", None),
        ("order", "risk"),
        ("cancel", "no_fill_bar"),
    ]


def test_daily_loss_stop_blocks_new_entries() -> None:
    e = Engine()
    e.day = T0.date()
    e.day_start_equity = e.cash
    e.cash -= 0.031 * e.cash
    h = momentum_history(["A"], [])
    at = T0 + timedelta(hours=2, minutes=5)
    out = e.step(at, h.views(at))
    assert [(d.action, d.reason) for d in out.decisions] == [
        ("signal", None),
        ("reject", "daily_loss_stop"),
    ]


def test_same_input_gives_identical_decisions() -> None:
    h = momentum_history(["A", "B"], [1.06, 1.0, 0.97, 0.9, 0.95, 1.3])
    start, end = T0 + timedelta(hours=2), T0 + timedelta(hours=7)
    a, b = run(h, start, end), run(h, start, end)
    assert a.decisions and [d.as_dict() for d in a.decisions] == [d.as_dict() for d in b.decisions]
    assert a.digest() == b.digest()
    assert ("exit", "stop") in [(d.action, d.reason) for d in a.decisions]
