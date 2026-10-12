"""Dashboard analytics (analytics-v1): signal outcomes, accuracy, performance, exposure."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from autolp.analytics.performance import by_strategy, cost_breakdown, daily, group_stats
from autolp.analytics.signals import accuracy, net_return_pct, signal_outcome
from autolp.db.models import PaperPosition
from autolp.paper.report import exposure
from autolp.strategies.bars import FIVE_MIN, Bar
from autolp.strategies.params import PARAMS

T0 = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
DECIDED = T0 + timedelta(minutes=7, seconds=30)  # after the 00:00 candle closed
LATE = T0 + timedelta(days=2)


def bars(*ohlc: tuple[float, float, float, float], start: datetime = T0) -> list[Bar]:
    return [Bar(start + FIVE_MIN * i, *x) for i, x in enumerate(ohlc)]


FLAT = (1.0, 1.0, 1.0, 1.0)


def outcome(bs: list[Bar], now: datetime = LATE, hold: float = 1.0) -> Any:
    return signal_outcome(DECIDED, 0.9, 1.2, hold, 0.5, bs, now)


def test_entry_is_the_first_open_after_the_decision_and_target_hits() -> None:
    # 00:00 and 00:05 start before the decision: never used (no look-back into the signal).
    o = outcome(bars((5, 5, 5, 5), (5, 5, 5, 5), (1.0, 1.1, 0.95, 1.05), (1.05, 1.25, 1.0, 1.2)))
    assert o.status == "target" and o.exit_reason == "target"
    assert o.entry_at == T0 + 2 * FIVE_MIN and o.entry_price == 1.0 and o.exit_price == 1.2
    assert o.gross_pct == pytest.approx(20)
    assert o.net_pct == pytest.approx(net_return_pct(1.0, 1.2, 0.5))
    assert o.best_pct == pytest.approx(25) and o.worst_pct == pytest.approx(-5)


def test_stop_is_assumed_first_when_a_candle_touches_both_levels() -> None:
    o = outcome(bars(FLAT, FLAT, (1.0, 1.3, 0.85, 1.0)))
    assert o.status == "stop" and o.exit_price == 0.9


def test_gaps_fill_at_the_open() -> None:
    o = outcome(bars(FLAT, FLAT, FLAT, (0.8, 0.85, 0.7, 0.75)))
    assert (o.status, o.exit_reason, o.exit_price) == ("stop", "stop_gap", 0.8)


def test_time_exit_at_the_holding_limit() -> None:
    o = outcome(bars(*[FLAT] * 2, *[(1.0, 1.05, 0.95, 1.01)] * 14))
    assert o.status == "time" and o.exit_at == T0 + 2 * FIVE_MIN + timedelta(hours=1)
    assert o.exit_price == 1.01


def test_open_waiting_and_missing_data_are_never_a_result() -> None:
    running = bars(FLAT, FLAT, FLAT, FLAT)
    now = T0 + timedelta(minutes=25)
    o = outcome(running, now=now)
    assert o.status == "open" and o.net_pct is not None  # marked, but not resolved
    assert outcome([], now=DECIDED + timedelta(minutes=30)).status == "waiting"
    assert outcome([], now=LATE).status == "no_data"
    # Candles stop before the holding limit and never come back.
    assert outcome(running, now=LATE).status == "no_data"
    # A candle that has not closed yet at `now` is not used.
    assert (
        outcome(
            bars(FLAT, FLAT, (1.0, 1.3, 1.0, 1.25)), now=T0 + 14 * 60 * timedelta(seconds=1)
        ).status
        == "waiting"
    )


def row(
    strategy: str, decision: str, status: str, net: float | None, paper: float | None = None
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "decision": decision,
        "paper_return_pct": paper,
        "outcome": {"status": status, "net_pct": net},
    }


def test_accuracy_counts_only_resolved_signals() -> None:
    rows = [
        row("momentum", "taken", "target", 14.0, 12.0),
        row("momentum", "taken", "stop", -8.0, -9.5),
        row("momentum", "rejected", "time", 1.0),
        row("momentum", "rejected", "open", 3.0),
        row("range", "rejected", "no_data", None),
    ]
    a = accuracy(rows)
    assert list(a) == ["momentum", "range", "all"]
    m = a["momentum"]
    assert (m["all"]["signals"], m["all"]["resolved"]) == (4, 3)
    assert m["all"]["target_hit_pct"] == pytest.approx(100 / 3)
    assert m["all"]["positive_pct"] == pytest.approx(200 / 3)
    assert m["taken"]["paper_n"] == 2 and m["taken"]["paper_mean_return_pct"] == pytest.approx(1.25)
    assert m["taken"]["ideal_mean_net_pct"] == pytest.approx(3.0)
    assert a["range"]["all"]["resolved"] == 0 and a["range"]["all"]["positive_pct"] is None
    assert a["all"]["all"]["status"] == {"no_data": 1, "open": 1, "stop": 1, "target": 1, "time": 1}


def trade(
    strategy: str, pnl: float, exit_at: datetime, reason: str = "time", **kw: Any
) -> dict[str, Any]:
    t = {
        "strategy": strategy,
        "entry_at": (exit_at - timedelta(hours=2)).isoformat(),
        "exit_at": exit_at.isoformat(),
        "size_usd": 1000.05,  # $1,000 swapped at 0.5% cost + $0.05 fee
        "qty": 995.0,
        "entry_price": 1.0,
        "exit_price": 1.0 + (pnl + 5.0 + 0.05) / 995,  # reference price at the exit
        "proceeds_usd": 1000.05 + pnl,
        "pnl_usd": pnl,
        "return_pct": pnl / 1000.05 * 100,
        "reason": reason,
        "fees_usd": 0.1,
        **kw,
    }
    return t


def test_strategy_stats_and_costs() -> None:
    day = T0 + timedelta(hours=12)
    ts = [
        trade("momentum", 50, day, "target"),
        trade("momentum", -30, day, "stop"),
        trade("range", -10, day),
    ]
    s = group_stats(ts[:2])
    assert (s["trades"], s["wins"], s["win_rate_pct"]) == (2, 1, 50)
    assert s["profit_factor"] == pytest.approx(50 / 30) and s["net_pnl_usd"] == 20
    assert s["avg_hold_hours"] == pytest.approx(2) and s["exits"] == {"stop": 1, "target": 1}
    c = s["costs"]
    assert c["entry_usd"] == pytest.approx(2 * 5.05)
    assert c["total_usd"] == pytest.approx(c["entry_usd"] + c["exit_usd"])
    assert s["pnl_before_costs_usd"] == pytest.approx(20 + c["total_usd"])
    out = by_strategy(ts, PARAMS.enabled)
    assert list(out) == ["momentum", "pullback", "range", "all"]
    assert out["pullback"]["trades"] == 0 and out["pullback"]["profit_factor"] is None
    assert out["all"]["trades"] == 3


def test_stop_overshoot_is_the_reference_price_below_the_stop() -> None:
    t = trade("momentum", -80, T0, "stop", exit_trigger_price=0.95)
    t["exit_price"] = 0.93
    assert cost_breakdown(t)["stop_overshoot_usd"] == pytest.approx(995 * 0.02)
    assert cost_breakdown({**t, "reason": "time"})["stop_overshoot_usd"] == 0


def test_daily_rows_include_days_without_trades() -> None:
    d1, d3 = T0 + timedelta(hours=5), T0 + timedelta(days=2, hours=5)
    rows = daily(
        [trade("momentum", 100, d1), trade("range", -50, d3)],
        [
            (date(2026, 10, 12), 10_100.0),
            (date(2026, 10, 13), 10_200.0),
            (date(2026, 10, 14), 10_150.0),
        ],
        10_000.0,
    )
    assert [r["day"] for r in rows] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert [r["trades"] for r in rows] == [1, 0, 1]
    assert rows[0]["return_pct"] == pytest.approx(1.0)
    assert rows[1]["pnl_usd"] == 0 and rows[1]["return_pct"] == pytest.approx(100 / 101)


def pos(
    id_: int, status: str, strategy: str, value_price: float | None, risk: float
) -> PaperPosition:
    return PaperPosition(
        id=id_, status=status, strategy=strategy, token_mint=f"M{id_}", qty=1000.0,
        entry_price=1.0, last_price=value_price, stop=0.9, planned_risk_usd=risk,
    )  # fmt: skip


def test_exposure_against_the_portfolio_limits() -> None:
    book = [
        pos(1, "open", "momentum", 1.1, 50),
        pos(2, "open", "range", 0.85, 40),
        pos(3, "pending", "range", None, 30),
    ]
    x = exposure(book, 10_000, PARAMS.risk)
    assert x["positions"] == 3 and x["max_positions"] == PARAMS.risk.max_positions
    assert x["invested_usd"] == pytest.approx(1100 + 850)
    assert x["planned_risk_usd"] == 120 and x["planned_risk_pct"] == pytest.approx(1.2)
    # Position 2 is already below its stop: nothing more to lose to the stop.
    assert x["risk_to_stop_usd"] == pytest.approx(200)
    assert x["by_strategy"] == {"momentum": pytest.approx(1100), "range": pytest.approx(850)}
