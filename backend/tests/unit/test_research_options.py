"""Research options (off by default): ATR exits, stop management, filters, the random-entry
baseline, entry-drift guard, slippage-aware sizing, the breaker, and the sweep runner."""

from dataclasses import replace
from datetime import timedelta

import pytest

from autolp.backtest.data import BacktestData
from autolp.backtest.sweep import expand, rank, run_sweep
from autolp.backtest.universes import Timeline
from autolp.strategies import rules
from autolp.strategies.bars import Bar, atr
from autolp.strategies.data import History
from autolp.strategies.engine import Engine
from autolp.strategies.params import (
    PARAMS,
    ExitParams,
    FilterParams,
    ManageParams,
    RiskParams,
    StrategyParams,
)
from autolp.strategies.replay import run
from autolp.strategies.risk import Breaker, check_exit, manage_stop, size_position
from autolp.strategies.rules import Evaluation
from tests.unit.test_strategies import (
    T0,
    breakout_bars,
    features,
    momentum_history,
    position,
    setup,
    view,
)


def test_atr_is_the_mean_true_range_including_gaps() -> None:
    bars = [Bar(T0, 1, 1.1, 0.9, 1.0), Bar(T0, 1.0, 1.05, 0.95, 1.0), Bar(T0, 1.2, 1.3, 1.2, 1.25)]
    # True ranges: 0.1, then max(1.3, 1.0) - min(1.2, 1.0) = 0.3 (gap up counts).
    assert atr(bars, 2) == pytest.approx(0.2)
    assert atr(bars, 3) is None


def test_defaults_add_no_check_and_keep_strat_v1_levels() -> None:
    sig = rules.momentum(view(breakout_bars()))
    assert set(sig.checks) == {
        "fresh_candles", "fresh_evaluation", "eligible", "cost_estimate",
        "breakout", "relative_volume", "buying_pressure", "no_risk_warnings",
    }  # fmt: skip
    assert sig.setup is not None and sig.setup.atr == 0.0


def atr_params(**kw: float) -> StrategyParams:
    x = ExitParams(15, 7, 4, **{"mode": "atr", "atr_mult": 3, "reward_risk": 2, **kw})  # type: ignore[arg-type]
    return replace(PARAMS, momentum=replace(PARAMS.momentum, exit=x), atr_candles=10)


def test_atr_exits_scale_with_volatility_and_include_costs() -> None:
    b = breakout_bars()  # spread 0.001 around flat 1.0 candles: tiny ATR
    sig = rules.momentum(view(b), atr_params())
    s = sig.setup
    assert s is not None and s.atr > 0
    # 3 x ATR is far below the 2% floor, so the stop sits 2% below the entry.
    assert s.stop == pytest.approx(1.05 * 0.98)
    # Net reward = 2 x (2% + 1% round trip) + 1% round trip = 7%.
    assert s.target == pytest.approx(1.05 * 1.07)
    capped = rules.momentum(view(b), atr_params(min_stop_pct=0.1, max_stop_pct=0.2)).setup
    assert capped is not None and capped.stop == pytest.approx(1.05 * 0.998)


def test_atr_exits_need_enough_candles() -> None:
    p = replace(atr_params(), atr_candles=500)
    sig = rules.momentum(view(breakout_bars()), p)
    assert sig.checks["atr"] is None and sig.setup is None


def test_breakeven_and_trailing_raise_the_stop_from_the_next_bar() -> None:
    pos = position(stop=0.95, target=2.0, cost_pct=0.5, atr=0.02)
    m = ManageParams(breakeven_at_r=1, trail_atr=2, trail_after_r=2)
    # High 1.05 = 1R: breakeven (entry + 1% round-trip cost); no trailing yet.
    manage_stop(pos, Bar(T0, 1.0, 1.05, 0.99, 1.04), m)
    assert pos.stop == pytest.approx(1.01)
    # High 1.12 > 2R: trail 2 ATR below it.
    manage_stop(pos, Bar(T0, 1.04, 1.12, 1.03, 1.1), m)
    assert pos.stop == pytest.approx(1.08)
    manage_stop(pos, Bar(T0, 1.1, 1.11, 1.09, 1.1), m)  # never lowered
    assert pos.stop == pytest.approx(1.08)
    ex = check_exit(pos, Bar(T0, 1.09, 1.1, 1.07, 1.08))
    assert ex is not None and ex.reason == "trail" and ex.price == pytest.approx(1.08)
    # Without options nothing moves.
    still = position(stop=0.95)
    manage_stop(still, Bar(T0, 1.0, 1.5, 0.99, 1.4), ManageParams())
    assert still.stop == 0.95


def test_engine_trails_and_trail_exits_pay_stop_slippage() -> None:
    base = atr_params(reward_risk=10)  # target out of reach
    p = replace(
        base, momentum=replace(base.momentum, manage=ManageParams(trail_atr=1, trail_after_r=1))
    )
    h = momentum_history(["A"], [1.06, 1.12, 1.18, 1.0, 1.0])
    e = run(h, T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=3), p)
    assert [t["reason"] for t in e.closed] == ["trail"]
    assert e.closed[0]["pnl_usd"] > 0


def test_filters_fail_on_missing_input_and_bound_the_trend() -> None:
    p = replace(PARAMS, filters=FilterParams(min_breadth_pct=50, max_ret_4h_pct=10))
    v = view(breakout_bars(), ret_4h_pct=5.0)
    assert rules.momentum(v, p).checks["regime"] is None  # no breadth -> no trade
    ok = rules.momentum(replace(v, breadth=60.0), p)
    assert ok.failed == [] and ok.setup is not None
    assert rules.momentum(replace(v, breadth=40.0), p).failed == ["regime"]
    hot = view(breakout_bars(), ret_4h_pct=25.0)
    assert rules.momentum(replace(hot, breadth=60.0), p).failed == ["token_trend"]
    unknown = rules.momentum(replace(view(breakout_bars()), breadth=60.0), p)
    assert unknown.checks["token_trend"] is None


def test_breadth_counts_fresh_eligible_tokens_only() -> None:
    h = History()
    at = T0 + timedelta(hours=1)
    rets = {"A": 5.0, "B": -1.0, "C": 2.0, "D": None}
    for m, r in rets.items():
        h.evals[m] = [Evaluation(at - timedelta(minutes=5), True, 70, features(ret_4h_pct=r))]
    h.evals["E"] = [Evaluation(at - timedelta(minutes=5), False, 70, features(ret_4h_pct=9.0))]
    h.evals["F"] = [Evaluation(at - timedelta(hours=1), True, 70, features(ret_4h_pct=9.0))]
    assert h.breadth(at) is None  # 3 tokens < min_breadth_tokens (8)
    h.p = replace(PARAMS, filters=FilterParams(min_breadth_tokens=3))
    h._breadth.clear()
    assert h.breadth(at) == pytest.approx(200 / 3)


def test_random_entries_are_deterministic_and_use_the_shared_checks() -> None:
    p = replace(PARAMS, enabled=("random",))
    v = view(breakout_bars())
    draws = [
        rules.random_entry(v, replace(p, random=replace(p.random, seed=str(i)))) for i in range(200)
    ]
    hits = [s for s in draws if s.setup is not None]
    assert 0 < len(hits) < 20  # ~2%
    assert [s.setup for s in draws] == [
        rules.random_entry(v, replace(p, random=replace(p.random, seed=str(i)))).setup
        for i in range(200)
    ]
    always = replace(p, random=replace(p.random, prob_pct=100))
    assert rules.random_entry(view(breakout_bars(), eligible=False), always).failed == ["eligible"]


def test_entry_drift_guard_cancels_a_fill_far_above_the_signal() -> None:
    p = replace(PARAMS, risk=replace(PARAMS.risk, max_entry_drift_r=0.25))
    # Signal at 1.05 with a 7% stop (R = 0.0735); the next open 1.08 is 0.41 R higher.
    h = momentum_history(["A"], [1.08, 1.1])
    e = run(h, T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=2, minutes=30), p)
    assert ("cancel", "entry_drift") in [(d.action, d.reason) for d in e.decisions]
    assert e.positions == [] and e.closed == []
    ok = run(momentum_history(["A"], [1.06]), T0 + timedelta(hours=2, minutes=5),
             T0 + timedelta(hours=2, minutes=30), p)  # fmt: skip
    assert len(ok.positions) == 1


def test_sizing_can_budget_for_stop_slippage() -> None:
    plain = size_position(setup(), 10_000, 10_000, 1e6, 1000, RiskParams())
    slip = size_position(
        setup(), 10_000, 10_000, 1e6, 1000, RiskParams(expected_stop_slippage_pct=2)
    )
    assert not isinstance(plain, str) and not isinstance(slip, str)
    assert slip.size_usd == pytest.approx(49.9 / 0.10) and slip.size_usd < plain.size_usd
    assert slip.planned_risk_usd == pytest.approx(50.0)


def test_breaker_pauses_after_a_losing_streak_and_a_drawdown() -> None:
    p = RiskParams(max_consecutive_losses=3, drawdown_pause_pct=5, pause_hours=24)
    b = Breaker(peak=10_000)
    for i in range(2):
        b.on_exit(T0 + timedelta(hours=i), -10, p)
    b.on_exit(T0 + timedelta(hours=2), 5, p)  # a win resets the streak
    assert b.blocked(T0 + timedelta(hours=3)) is None
    for i in range(3):
        b.on_exit(T0 + timedelta(hours=3 + i), -10, p)
    assert b.blocked(T0 + timedelta(hours=6)) == "loss_streak"
    assert b.blocked(T0 + timedelta(hours=29)) is None
    b.on_mark(T0 + timedelta(hours=30), 9_400, p)
    assert b.blocked(T0 + timedelta(hours=31)) == "drawdown_pause" and b.peak == 9_400
    assert Breaker(peak=10_000).blocked(T0) is None


def test_engine_rejects_while_paused() -> None:
    e = Engine(replace(PARAMS, risk=replace(PARAMS.risk, max_consecutive_losses=1)))
    e.breaker.on_exit(T0, -1, e.p.risk)
    h = momentum_history(["A"], [])
    at = T0 + timedelta(hours=2, minutes=5)
    out = e.step(at, h.views(at))
    assert [(d.action, d.reason) for d in out.decisions] == [
        ("signal", None),
        ("reject", "loss_streak"),
    ]


# ------------------------------------------------------------------ sweep


def sweep_data() -> BacktestData:
    h = momentum_history(["A", "B"], [1.06, 1.0, 0.97, 0.9, 0.95, 1.3] + [1.2] * 60)
    start, end = T0 + timedelta(hours=2), T0 + timedelta(hours=9)
    tl = Timeline()
    tl.add(T0, ["A", "B"])
    return BacktestData(start, end, h, {"selection": tl}, {}, T0, "digest")


GRID = {
    "name": "t",
    "variants": [
        {"name": "v1", "set": {}},
        {"name": "rand", "set": {"strategy.enabled": ["random"]}},
    ],
    "groups": [
        {
            "prefix": "mom",
            "baseline": "rand",
            "set": {"strategy.enabled": ["momentum"]},
            "grid": {
                "strategy.momentum.exit.take_profit_pct": [10, 15],
                "strategy.momentum.exit.stop_loss_pct": [4, 7],
            },
        }
    ],
}


def test_grid_expansion_and_validation() -> None:
    vs = expand(GRID)
    assert [v.name for v in vs][:3] == ["v1", "rand", "mom take_profit_pct=10 stop_loss_pct=4"]
    assert (
        len(vs) == 6
        and vs[2].baseline == "rand"
        and vs[2].axes
        == {
            "strategy.momentum.exit.take_profit_pct": 0,
            "strategy.momentum.exit.stop_loss_pct": 0,
        }
    )
    with pytest.raises(SystemExit, match="cannot change"):
        expand({"variants": [{"name": "x", "set": {"strategy.data.candles_needed": 10}}]})
    with pytest.raises(SystemExit, match="duplicate"):
        expand({"variants": [{"name": "x"}, {"name": "x"}]})


def test_sweep_is_deterministic_serial_or_parallel_and_ranks_on_development() -> None:
    vs = expand(GRID)
    serial = run_sweep(sweep_data(), vs, ["base", "paper"], workers=1)
    parallel = run_sweep(sweep_data(), vs, ["base", "paper"], workers=2)
    digests = [{s: r["scenarios"][s]["digest"] for s in r["scenarios"]} for r in serial]
    assert digests == [{s: r["scenarios"][s]["digest"] for s in r["scenarios"]} for r in parallel]
    assert serial[0]["scenarios"]["base"]["trades"]  # v1 traded the breakouts
    rows = rank(vs, serial, "paper", min_trades=1)
    assert [r["rank"] for r in rows] == list(range(1, 7))
    mom = next(r for r in rows if r["name"] == "mom take_profit_pct=10 stop_loss_pct=4")
    assert mom["baseline"] == "rand" and "worst_neighbour_return_pct" in mom
