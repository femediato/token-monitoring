"""Backtest bt-v1: execution model, universes, metrics, splits and report determinism."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from autolp.backtest.data import BacktestData, BtEvaluation
from autolp.backtest.metrics import Point, daily_returns, max_drawdown_pct, summarize
from autolp.backtest.params import BT
from autolp.backtest.run import apply, parse_sets
from autolp.backtest.runner import backtest, splits
from autolp.backtest.universes import (
    Candidate,
    Timeline,
    early_rule,
    fixed_timeline,
    random_matched,
    watchlist_timeline,
)
from autolp.strategies.data import History
from autolp.strategies.engine import Engine
from autolp.strategies.execution import ExecutionParams
from autolp.strategies.replay import grid
from tests.unit.test_strategies import T0, momentum_history


def run_ex(h: History, start: datetime, end: datetime, ex: ExecutionParams) -> Engine:
    e = Engine(ex=ex)
    for t in grid(start, end):
        held = [x.mint for x in e.positions] + [x.setup.mint for x in e.pending]
        e.step(t, h.views(t, held))
    return e


START, END = T0 + timedelta(hours=2, minutes=5), T0 + timedelta(hours=3)


# ------------------------------------------------------------------ execution model


def test_v0_execution_is_unchanged() -> None:
    h = momentum_history(["A"], [1.06, 1.10, 1.25, 1.25])
    e = run_ex(h, START, END, ExecutionParams())
    t = e.closed[0]
    assert t["entry_price"] == 1.06 and t["exit_fill_price"] == t["exit_price"]
    assert t["cost_pct"] == 0.5


def test_entry_delay_and_cost_multiplier() -> None:
    h = momentum_history(["A"], [1.06, 1.10, 1.25, 1.25])
    e = run_ex(h, START, END, ExecutionParams(entry_delay_minutes=10, cost_multiplier=1.5))
    t = e.closed[0]
    assert t["entry_price"] == 1.10  # open of the candle starting >= 10 min after the decision
    assert t["cost_pct"] == pytest.approx(0.75)
    fill = next(d for d in e.decisions if d.action == "fill")
    assert fill.data["fill_price"] == pytest.approx(1.10 * 1.0075)


def test_stop_slippage_applies_to_stops_only() -> None:
    h = momentum_history(["A"], [1.06, 1.0, 0.9, 0.9])
    e = run_ex(h, START, END, ExecutionParams(stop_slippage_pct=5))
    t = e.closed[0]
    assert t["reason"] in ("stop", "stop_gap")
    assert t["exit_fill_price"] == pytest.approx(t["exit_price"] * 0.95)


def test_failed_entry_swap_is_cancelled() -> None:
    h = momentum_history(["A"], [1.06, 1.10])
    e = run_ex(h, START, END, ExecutionParams(swap_failure_pct=100))
    assert [(d.action, d.reason) for d in e.decisions][-1] == ("cancel", "swap_failed")
    assert e.positions == [] and e.closed == []


class FailFirstExit(ExecutionParams):
    def fails(self, *key: object) -> bool:
        return key[0] == "exit" and key[2] == 0


def test_failed_exit_is_retried_at_the_next_open() -> None:
    h = momentum_history(["A"], [1.06, 1.10, 1.25, 1.30, 1.31])
    e = run_ex(h, START, END, FailFirstExit())
    acts = [(d.action, d.reason) for d in e.decisions]
    assert ("exit_failed", "target") in acts
    t = e.closed[0]
    assert t["reason"] == "target_retry" and t["exit_price"] == 1.25  # next candle's open


def test_failures_are_deterministic_and_near_the_rate() -> None:
    ex = ExecutionParams(swap_failure_pct=5, seed="s")
    draws = [ex.fails("entry", i) for i in range(4000)]
    assert draws == [ex.fails("entry", i) for i in range(4000)]
    assert 0.035 < sum(draws) / 4000 < 0.065


def test_candle_latency_delays_signals_not_fills() -> None:
    h = momentum_history(["A"], [1.06, 1.10, 1.25, 1.25])
    h.candle_latency = timedelta(minutes=10)
    # The breakout candle (02:00-02:05) reaches the signal step at 02:15.
    assert h.views(START)["A"].bars[-1].start < T0 + timedelta(hours=2)
    e = run_ex(h, START, END + timedelta(minutes=30), ExecutionParams())
    order = next(d for d in e.decisions if d.action == "order")
    assert order.at == T0 + timedelta(hours=2, minutes=15)
    fill = next(d for d in e.decisions if d.action == "fill")
    assert fill.data["bar"] == (T0 + timedelta(hours=2, minutes=15)).isoformat()


# ------------------------------------------------------------------ universes


def test_timeline_is_point_in_time() -> None:
    tl = Timeline()
    tl.add(T0, ["A"])
    tl.add(T0 + timedelta(hours=1), ["B"])
    assert tl.at(T0 - timedelta(minutes=1)) == frozenset()
    assert tl.at(T0 + timedelta(minutes=59)) == {"A"}
    assert tl.at(T0 + timedelta(hours=1)) == {"B"}
    assert fixed_timeline(tl, T0 + timedelta(minutes=30)).at(T0 + timedelta(hours=5)) == {"A"}


def test_random_matching_is_deterministic_and_matched() -> None:
    cands = [Candidate("SEL", 1e7, 1e6)] + [
        Candidate(f"C{i}", 1e7 * (1 + i / 10), 1e6) for i in range(10)
    ]
    cands.append(Candidate("FAR", 1e12, 1e10))
    a = random_matched(["SEL"], cands, T0)
    assert a == random_matched(["SEL"], cands, T0)
    assert len(a) == 1 and a[0] in {f"C{i}" for i in range(BT.universe.random_nearest)}
    picks = {random_matched(["SEL"], cands, T0 + timedelta(hours=k))[0] for k in range(30)}
    assert len(picks) > 1 and "FAR" not in picks and "SEL" not in picks


def test_watchlist_timeline_replays_events() -> None:
    tl = watchlist_timeline(
        [(T0, "A", "add"), (T0, "B", "add"), (T0 + timedelta(hours=6), "A", "remove")]
    )
    assert tl.at(T0) == {"A", "B"} and tl.at(T0 + timedelta(hours=7)) == {"B"}


def test_early_rule_fires_only_right_after_an_alert() -> None:
    h = momentum_history(["A"], [1.06, 1.10])
    at = T0 + timedelta(hours=2, minutes=10)
    rule = early_rule({"A": [at - timedelta(minutes=3)]})
    sig = rule(h.view("A", at), h.p)
    assert sig.setup is not None and sig.setup.target == pytest.approx(1.06 * 1.15)
    later = rule(h.view("A", at + timedelta(minutes=5)), h.p)
    assert later.setup is None and later.checks["early_alert"] is False


# ------------------------------------------------------------------ metrics and splits


def curve(values: list[float], step: timedelta = timedelta(days=1)) -> list[Point]:
    return [Point(T0 + i * step, v, 0.0) for i, v in enumerate(values)]


def trade(pnl: float, at: datetime = T0, **kw: Any) -> dict[str, Any]:
    return {
        "entry_at": at.isoformat(),
        "pnl_usd": pnl,
        "return_pct": pnl / 10,
        "size_usd": 100.0,
        "qty": 100.0,
        "entry_price": 0.99,
        "exit_price": 1.0,
        "proceeds_usd": 100.0,  # = qty x exit price: no exit cost
        "strategy": "momentum",
        "reason": "target" if pnl > 0 else "stop",
        **kw,
    }


def test_drawdown_and_profit_factor() -> None:
    assert max_drawdown_pct([100, 120, 90, 130, 117]) == pytest.approx(25.0)
    m = summarize([trade(30), trade(-10), trade(-5)], curve([100, 115]), T0, T0 + timedelta(days=2))
    assert m["trades"] == 3 and m["profit_factor"] == pytest.approx(2.0)
    assert m["win_rate_pct"] == pytest.approx(100 / 3) and m["net_return_pct"] == pytest.approx(15)
    assert m["expectancy_usd"] == pytest.approx(5.0)
    assert m["avg_cost_pct"] == pytest.approx(1.0)  # 1 entry + 0 at exit (see trade())


def test_sharpe_needs_two_weeks_of_daily_returns() -> None:
    short = summarize([], curve([100 + i for i in range(10)]), T0, T0 + timedelta(days=30))
    assert short["sharpe"] is None and short["return_days"] == 9
    vals = [100 * (1.01 if i % 2 else 0.995) ** i for i in range(30)]
    long = summarize([], curve(vals), T0, T0 + timedelta(days=30))
    assert long["sharpe"] is not None and long["sortino"] is not None
    assert len(daily_returns(curve(vals))) == 29


def test_trades_belong_to_the_split_they_were_entered_in() -> None:
    m = summarize(
        [trade(5), trade(7, T0 + timedelta(days=3))], curve([100] * 5), T0, T0 + timedelta(days=2)
    )
    assert m["trades"] == 1 and m["net_pnl_usd"] == 5


def test_splits_follow_the_45_15_30_proportions() -> None:
    parts = splits(T0, T0 + timedelta(days=90))
    assert [(p.name, (p.end - p.start).days) for p in parts] == [
        ("development", 45),
        ("validation", 15),
        ("holdout", 30),
    ]


def test_overrides_are_typed_and_scoped() -> None:
    sp, bp = apply(
        parse_sets(["strategy.range.max_hold_hours=6", "backtest.candle_latency_minutes=5"])
    )
    assert sp.range.max_hold_hours == 6 and bp.candle_latency_minutes == 5
    assert sp.momentum == apply({})[0].momentum
    with pytest.raises(SystemExit):
        apply({"strategy.nope": 1})


# ------------------------------------------------------------------ whole report


def small_data() -> BacktestData:
    h = momentum_history(["A", "B"], [1.06, 1.10, 1.25, 1.25] + [1.25] * 30)
    h.candle_latency = timedelta(minutes=BT.candle_latency_minutes)
    # B fails a critical check: never tradeable, whatever the universe says.
    h.evals["B"] = [
        BtEvaluation(e.at, e.eligible, e.buying, e.features, True) for e in h.evals["B"]
    ]
    sel = Timeline()
    sel.add(T0, ["A", "B"])
    other = Timeline()
    other.add(T0, ["B"])
    return BacktestData(
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=5),
        h,
        {"selection": sel, "mcap": other},
        {},
        T0,
        "digest",
        {"tokens": 2, "evaluations": 0, "candles": 0},
    )


def test_report_is_reproducible_and_holdout_sealed() -> None:
    a, b = backtest(small_data()), backtest(small_data())
    assert a["result_digest"] == b["result_digest"]
    assert a["window"]["sealed"] and set(a["runs"][0]["metrics"]) == {
        "development",
        "validation",
        "all",
    }
    sel = next(r for r in a["runs"] if r["universe"] == "selection" and r["scenario"] == "base")
    assert [t["mint"] for t in sel["trades"]] == ["A"]  # B is critical
    mcap = next(r for r in a["runs"] if r["universe"] == "mcap")
    assert mcap["trades"] == [] and mcap["decisions"] == {}
    assert {g["status"] for g in a["gates"]} == {"insufficient_data"}
    assert not a["data"]["sufficient"]
    unsealed = backtest(small_data(), sealed=False)
    assert "holdout" in unsealed["runs"][0]["metrics"]
    assert unsealed["result_digest"] != a["result_digest"]
